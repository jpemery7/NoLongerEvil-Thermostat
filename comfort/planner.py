"""Turn a household profile, a weather forecast and thermostat state into a
weekly setpoint schedule per zone.

Method (see knowledge.md for sources and reasoning):
  1. For every 30-minute slot, work out who is in each zone and whether they
     are asleep or awake.
  2. Give each person a target: the operative temperature at which the ISO 7730
     PMV model predicts neutrality for their clothing and activity, anchored to
     the thermostat reading they say they like. Sleepers get a sleep target.
  3. Translate operative temperature into the air temperature the thermostat
     must hold, compensating for colder walls and windows on cold days.
  4. Combine the people present so the worst-off person is as close as possible.
  5. Shift setpoint changes earlier to allow for slow radiant-floor response.
"""
import datetime as dt
import math
from statistics import mean

from pythermalcomfort.models import pmv_ppd_iso

SLOT_MIN = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
WEEK_MIN = 7 * 24 * 60
HARD_MIN_F, HARD_MAX_F = 55.0, 76.0  # never schedule outside this, whatever the profile says
REF_TOUT_C = 7.0  # typical Seattle heating-season outdoor temperature
MAX_EVENTS_PER_DAY = 8
TRANSITION_MIN = 90  # time spent in the bedroom zone after waking and before bed

ENVELOPE_K = {"poor": 0.12, "average": 0.08, "good": 0.05, "excellent": 0.03}
WINDOW_K = {"single": 0.03, "double": 0.0, "triple": -0.01}
RADIANT_FLOOR_CREDIT = 0.03  # a warm floor raises mean radiant temperature
# Indoor winter clothing plus the insulation of a chair or sofa.
BASE_CLO = {"light": 0.7, "typical": 1.0, "warm": 1.2}
CLOTHING_ADAPTS = {"none": 0.0, "some": 0.1, "a_lot": 0.4}
ZONE_MET = {"living": 1.1, "office": 1.1, "kitchen": 1.25, "play": 1.3, "bedroom": 1.0, "basement": 1.1, "guest": 1.0}
SLEEP_DEFAULT_F = {"infant": 69.0, "child": 67.0, "teen": 66.0, "adult": 65.5, "older": 68.0}
BEDDING_F = {"light": 1.5, "medium": 0.0, "heavy": -1.5}
VULNERABLE = {"infant", "older"}
WARMUP_RATE_F_PER_H = {"slow": 0.5, "medium": 1.0, "fast": 2.0, "unknown": 0.75}
SUN_FACTOR = {"low": 0.0, "medium": 0.5, "high": 1.0}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def f2c(f):
    return (f - 32) * 5 / 9


def c2f(c):
    return c * 9 / 5 + 32


def clo_dynamic(tout_c):
    """ASHRAE 55 dynamic clothing model, driven by 6 a.m. outdoor temperature."""
    if tout_c < -5:
        return 1.0
    if tout_c < 5:
        return 0.818 - 0.0364 * tout_c
    if tout_c < 26:
        return 10 ** (-0.1635 - 0.0066 * tout_c)
    return 0.46


_neutral_cache = {}


def neutral_operative_c(met, clo, rh, pmv_target=0.0):
    """Operative temperature at which PMV equals pmv_target (still air)."""
    key = (round(met, 2), round(clo, 2), round(rh / 5) * 5, round(pmv_target, 2))
    if key in _neutral_cache:
        return _neutral_cache[key]
    lo, hi = 12.0, 34.0
    for _ in range(24):
        mid = (lo + hi) / 2
        pmv = pmv_ppd_iso(tdb=mid, tr=mid, vr=0.1, rh=key[2], met=key[0], clo=key[1], limit_inputs=False).pmv
        if pmv < key[3]:
            lo = mid
        else:
            hi = mid
    _neutral_cache[key] = (lo + hi) / 2
    return _neutral_cache[key]


def envelope_k(house, zone):
    k = ENVELOPE_K.get(house.get("insulation", "average"), 0.08) + WINDOW_K.get(house.get("windows", "double"), 0.0)
    if "radiant" in (zone.get("heater_delivery") or "in-floor-radiant"):
        k -= RADIANT_FLOOR_CREDIT
    return max(k, 0.01)


def air_for_operative_c(top_c, tout_c, k):
    """Air temperature needed for a given operative temperature.

    Mean radiant temperature is modelled as t_air - k * (t_air - t_out).
    """
    return (top_c - k / 2 * tout_c) / (1 - k / 2)


def _hhmm(text, default):
    try:
        h, m = str(text).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


def person_times(person, weekday):
    """Wake, bed, leave and return minutes for a weekday index (0 = Monday)."""
    weekend = weekday >= 5
    wake = _hhmm(person.get("wake_weekend" if weekend else "wake_weekday"), 450 if weekend else 390)
    bed = _hhmm(person.get("bed_weekend" if weekend else "bed_weekday"), 1380 if weekend else 1350)
    away = WEEKDAYS[weekday] in (person.get("away_days") or [])
    leave = _hhmm(person.get("leave"), 480) if away else None
    back = _hhmm(person.get("return"), 1050) if away else None
    return wake, bed, leave, back


def person_state(person, weekday, minute, shared_zones):
    """Return ("sleep" | "awake" | "absent", set of zone ids)."""
    wake, bed, leave, back = person_times(person, weekday)
    bedroom = person.get("bedroom_zone")
    if bed < 12 * 60:  # bedtime after midnight
        asleep = bed <= minute < wake
    else:
        asleep = minute < wake or minute >= bed
    if asleep:
        return "sleep", {bedroom} if bedroom else set()
    if leave is not None and leave <= minute < back:
        return "absent", set()
    zones = set()
    evening_start = back if back is not None else 17 * 60
    winding_down = (bed >= 12 * 60 and minute >= bed - TRANSITION_MIN) or (bed < 12 * 60 and (minute >= 24 * 60 - TRANSITION_MIN + bed or minute < bed))
    if minute < wake + TRANSITION_MIN or winding_down:
        if bedroom:
            zones.add(bedroom)
    morning_end = leave if leave is not None else wake + 120
    if minute < morning_end or minute >= evening_start:
        zones |= set(person.get("evening_zones") or shared_zones)
    else:
        zones |= set(person.get("day_zones") or person.get("evening_zones") or shared_zones)
    return "awake", {z for z in zones if z}


def in_bedroom_transition(person, weekday, minute):
    """True while a person is briefly awake in their bedroom zone (just up, or winding down)."""
    wake, bed, _, _ = person_times(person, weekday)
    if wake <= minute < wake + TRANSITION_MIN:
        return True
    if bed >= 12 * 60:
        return bed - TRANSITION_MIN <= minute < bed
    return minute >= 24 * 60 - TRANSITION_MIN + bed or minute < bed


def awake_target_f(person, zone, house, tout_c, tout_6am_c, rh):
    """Thermostat (air) temperature this person wants when awake in this zone."""
    k = envelope_k(house, zone)
    base_clo = BASE_CLO.get(person.get("clothing", "typical"), 1.0)
    # People at home change indoor clothing only a little with the weather.
    adapts = CLOTHING_ADAPTS.get(person.get("clothing_adapts", "some"), 0.1)
    clo = base_clo + adapts * (clo_dynamic(tout_6am_c) - clo_dynamic(REF_TOUT_C))
    # The least active use of the zone governs: people cooking tolerate a room set for sitting.
    met = min(ZONE_MET.get(u, 1.1) for u in (zone.get("uses") or ["living"]))
    pref = person.get("awake_pref_f")
    if pref:
        # A stated preference already reflects how the person sits around at home;
        # only more active zones move it (downwards).
        met = max(met, 1.1)
        reference = c2f(air_for_operative_c(neutral_operative_c(1.1, base_clo, 50), REF_TOUT_C, k))
        offset, shift = float(pref) - reference, 0.0
    else:
        offset, shift = 0.0, 0.3 * float(person.get("tendency", 0))
    top = neutral_operative_c(met, clo, rh or 50, shift)
    return c2f(air_for_operative_c(top, tout_c, k)) + offset


def sleep_target_f(person, zone, house, tout_c):
    k = envelope_k(house, zone)
    pref = person.get("sleep_pref_f")
    if pref:
        base = float(pref)
    else:
        base = SLEEP_DEFAULT_F.get(person.get("age_group", "adult"), 65.5)
        base += 1.0 * float(person.get("tendency", 0)) + BEDDING_F.get(person.get("bedding", "medium"), 0.0)
    # Hold operative temperature constant as outdoor temperature moves away from the reference.
    top = f2c(base) - k / 2 * (f2c(base) - REF_TOUT_C)
    return c2f(air_for_operative_c(top, tout_c, k))


def combine(targets):
    """Minimise the worst deviation, without leaving a vulnerable person more than 1 F short."""
    values = [t for t, _ in targets]
    result = (min(values) + max(values)) / 2
    vulnerable = [t for t, v in targets if v]
    if vulnerable:
        result = max(result, max(vulnerable) - 1.0)
    return result


def _conditions(forecast, start_date, horizon_days):
    """Per-day list of per-slot (tout_c, sun, rh) for 7 days from start_date.

    Days inside the forecast horizon use the forecast; later days reuse the
    hour-by-hour average of the horizon so the rest of the week stays sensible
    until the next daily run replaces it.
    """
    by_day = {}
    for h in forecast:
        by_day.setdefault(h["start"].date(), {})[h["start"].hour] = h
    horizon = [start_date + dt.timedelta(days=i) for i in range(horizon_days + 1)]
    typical = {}
    for hour in range(24):
        rows = [by_day[d][hour] for d in horizon if d in by_day and hour in by_day[d]]
        if not rows:
            rows = [h for h in forecast if h["start"].hour == hour]
        typical[hour] = {
            "temp_f": mean(r["temp_f"] for r in rows) if rows else 45.0,
            "sun": mean(r["sun"] for r in rows) if rows else 0.0,
            "rh": mean((r["rh"] or 60) for r in rows) if rows else 60,
        }
    out = []
    for i in range(7):
        day = start_date + dt.timedelta(days=i)
        use_forecast = day in horizon and day in by_day
        hours = {hr: (by_day[day].get(hr) if use_forecast else None) or typical[hr] for hr in range(24)}
        out.append({"date": day, "forecast": use_forecast, "hours": hours})
    return out


def plan_zone(zone, profile, days, indoor_rh, trim_f=0.0, early_on=False):
    """Return (events, notes). Events are (minute_of_week, temp_f), week starting Monday."""
    house, prefs = profile.get("house", {}), profile.get("preferences", {})
    people = profile.get("people", [])
    zid = zone["device_id"]
    shared = [z["device_id"] for z in profile["zones"] if set(z.get("uses") or []) & {"living", "kitchen"}] or [zid]
    lo = max(HARD_MIN_F, float(prefs.get("min_f", 60)))
    hi = min(HARD_MAX_F, float(prefs.get("max_f", 73)))
    setback = float(prefs.get("setback_f", 2.0)) if prefs.get("setback_ok", True) else 0.0
    vacations = [(dt.date.fromisoformat(v["from"]), dt.date.fromisoformat(v["to"])) for v in prefs.get("vacations", []) if v.get("from") and v.get("to")]
    sun_factor = SUN_FACTOR.get(zone.get("sun", "medium"), 0.5)

    slots = {}  # minute_of_week -> (temp_f or None, kind)
    deviations = {}
    for day in days:
        weekday = day["date"].weekday()
        on_vacation = any(a <= day["date"] <= b for a, b in vacations)
        tout_6am_c = f2c(day["hours"][6]["temp_f"])
        for s in range(SLOTS_PER_DAY):
            minute = s * SLOT_MIN
            hour = day["hours"][minute // 60]
            tout_c = f2c(hour["temp_f"])
            sleepers, awake = [], []
            for p in ([] if on_vacation else people):
                state, zones = person_state(p, weekday, minute, shared)
                vulnerable = p.get("age_group") in VULNERABLE
                if state == "sleep" and zid in zones:
                    sleepers.append((min(hi, max(lo, sleep_target_f(p, zone, house, tout_c))), vulnerable, p))
                elif state == "awake" and zid in zones:
                    want = awake_target_f(p, zone, house, tout_c, tout_6am_c, indoor_rh)
                    regular = set(p.get("evening_zones") or shared) | set(p.get("day_zones") or [])
                    if zid == p.get("bedroom_zone") and zid not in regular and in_bedroom_transition(p, weekday, minute):
                        # Getting up or going to bed: a little warmer than sleeping, not full daytime warmth.
                        want = min(want, sleep_target_f(p, zone, house, tout_c) + 2.5)
                    # Clamp to the house limits before combining, so the compromise is
                    # between temperatures that can actually be scheduled.
                    awake.append((min(hi, max(lo, want)), vulnerable, p))
            if sleepers and awake:
                target = (combine([(t, v) for t, v, _ in sleepers]) + combine([(t, v) for t, v, _ in awake])) / 2
                kind = "mixed"
            elif sleepers:
                target, kind = combine([(t, v) for t, v, _ in sleepers]), "sleep"
            elif awake:
                target, kind = combine([(t, v) for t, v, _ in awake]), "awake"
                if 9 * 60 <= minute < 15 * 60:  # let forecast sun do some of the work
                    target -= 1.0 * sun_factor * hour["sun"]
            else:
                target, kind = None, "vacation" if on_vacation else "empty"
            if target is not None:
                target = min(hi, max(lo, target + trim_f))
                if day["forecast"]:
                    for t, _, p in sleepers + awake:
                        name = p.get("name", "?")
                        deviations[name] = max(deviations.get(name, 0.0), abs(min(hi, max(lo, t)) - target))
            slots[weekday * 1440 + minute] = (target, kind, hour["temp_f"])

    # Fill unoccupied slots: hold through short gaps, set back through long ones.
    order = sorted(slots)
    n = len(order)
    filled = {}
    i = 0
    while i < n:
        target, kind, tout_f = slots[order[i]]
        if target is not None:
            filled[order[i]] = (target, tout_f)
            i += 1
            continue
        j = i
        while j < i + n and slots[order[j % n]][0] is None:
            j += 1
        gap_h = (j - i) * SLOT_MIN / 60
        before = slots[order[(i - 1) % n]][0]
        after = slots[order[j % n]][0]
        # Set back from the temperature the zone must recover to, not the one it is leaving.
        anchor = after if after is not None else (before if before is not None else lo)
        if kind == "vacation":
            value = max(HARD_MIN_F, float(prefs.get("away_f", 58)))
        elif gap_h < 3 or setback == 0:
            value = anchor
        else:
            value = max(lo, anchor - setback)
        for m in range(i, min(j, n)):
            filled[order[m]] = (value, slots[order[m]][2])
        if j >= n:
            for m in range(0, j - n):
                filled[order[m]] = (value, slots[order[m]][2])
        i = j

    # Collapse to change events, rounded to 0.5 F.
    events = []
    prev = None
    for minute in order:
        temp = round(filled[minute][0] * 2) / 2
        if temp != prev:
            events.append([minute, temp, filled[minute][1]])
            prev = temp
    if len(events) > 1 and events[0][1] == events[-1][1]:
        events.pop(0)  # the week is cyclic

    # A slab cannot serve a target that lasts under 90 minutes: hold the previous one instead.
    changed = True
    while changed and len(events) > 2:
        changed = False
        for idx in range(len(events)):
            nxt = events[(idx + 1) % len(events)]
            if (nxt[0] - events[idx][0]) % WEEK_MIN < 90:
                events.pop(idx)
                changed = True
                break
    events = [e for i, e in enumerate(events) if e[1] != events[i - 1][1]] if len(events) > 1 else events

    # Radiant lead: start warm-ups early; start cool-downs early because the slab coasts.
    rate0 = WARMUP_RATE_F_PER_H.get(zone.get("warmup", "unknown"), 0.75)
    coast_h = float(zone.get("coast_h", 1.0))
    # Bedrooms get a shorter lead so the room is not warmed for hours while people sleep.
    max_lead_h = float(zone.get("max_lead_h", 2.0 if "bedroom" in (zone.get("uses") or []) else 3.0))
    shifted = []
    for idx, (minute, temp, tout_f) in enumerate(events):
        before = events[idx - 1][1]
        if temp > before:
            rate = rate0 * min(1.2, max(0.4, 1 - (45 - tout_f) / 60))
            lead_h = 0.0 if early_on else min(max_lead_h, max(0.5, (temp - before) / rate))
        else:
            lead_h = coast_h
        start = minute - int(round(lead_h * 4)) * 15
        # A change that now starts before earlier ones overrides them.
        while shifted and shifted[-1][0] >= start:
            shifted.pop()
        shifted.append((start, temp))
    shifted = sorted((m % WEEK_MIN, t) for m, t in shifted)
    cleaned = []
    for minute, temp in shifted:
        if cleaned and cleaned[-1][0] == minute:
            cleaned[-1] = (minute, temp)
        elif not cleaned or cleaned[-1][1] != temp:
            cleaned.append((minute, temp))

    # A slab cannot follow half-degree wiggles: keep only steps of at least 1 F.
    if len(cleaned) > 2:
        kept = [cleaned[0]]
        for minute, temp in cleaned[1:]:
            if abs(temp - kept[-1][1]) >= 1.0:
                kept.append((minute, temp))
        cleaned = kept

    # Respect the per-day event budget by dropping the smallest steps.
    by_day = {}
    for minute, temp in cleaned:
        by_day.setdefault(minute // 1440, []).append((minute, temp))
    final = []
    for d, evs in by_day.items():
        while len(evs) > MAX_EVENTS_PER_DAY:
            k = min(range(1, len(evs)), key=lambda x: abs(evs[x][1] - evs[x - 1][1]))
            evs.pop(k)
        final.extend(evs)
    final.sort()
    notes = {"max_deviation_f": {k: round(v, 1) for k, v in deviations.items()}, "bounds_f": [lo, hi]}
    return final, notes


def to_nle_schedule(events, name="Comfort plan"):
    """NLE / Nest weekly schedule: day "0" is Monday, times in seconds, temps in Celsius."""
    days = {str(d): {} for d in range(7)}
    for minute, temp_f in events:
        day = days[str(minute // 1440)]
        day[str(len(day))] = {
            "type": "HEAT",
            "time": (minute % 1440) * 60,
            "entry_type": "setpoint",
            "temp": round(f2c(temp_f), 3),
        }
    return {"ver": 2, "name": name, "schedule_mode": "HEAT", "days": days}


def planned_temp_at(events, when):
    """Setpoint the plan calls for at a local datetime."""
    if not events:
        return None
    minute = when.weekday() * 1440 + when.hour * 60 + when.minute
    current = events[-1][1]
    for m, temp in events:
        if m <= minute:
            current = temp
    return current
