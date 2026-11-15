#!/usr/bin/env python3
"""Daily comfort run: read thermostats and forecast, plan, optionally apply.

    python3 comfort/run_daily.py --profile profile.json --history history.jsonl --out out/ [--apply]

Without --apply (or with a profile that is not marked confirmed) nothing is
written to the thermostats. Everything the run learned or decided is written to
<out>/result.json and <out>/report.md. Household data never goes in this repo.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from statistics import median
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import nle  # noqa: E402
import planner  # noqa: E402
import weather  # noqa: E402

HORIZON_DAYS = 3
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def default_profile(devices):
    """Placeholder household used only for dry runs before the intake form is submitted."""
    zones = [
        {"device_id": d["id"], "name": d["label"], "uses": ["living"], "sun": "medium", "warmup": "unknown"}
        for d in devices
    ]
    first = zones[0]["device_id"] if zones else None
    people = [{"name": "Adult (placeholder)", "age_group": "adult", "bedroom_zone": first, "away_days": []}]
    return {"confirmed": False, "placeholder": True, "house": {}, "zones": zones, "people": people, "preferences": {}}


def load_history(path):
    rows = []
    if path and os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def trim_from_history(history, device_id):
    """Small correction from how the zone has tracked earlier plans (-1..+1 F).

    If the room has been running below what was planned at the time of each
    daily check, nudge setpoints up, and vice versa.
    """
    errors = [
        r["current_f"] - r["planned_f"]
        for r in history[-14:]
        if r.get("device_id") == device_id and r.get("planned_f") is not None and r.get("current_f") is not None
        and r.get("planned_kind") != "setback"
    ]
    if len(errors) < 5:
        return 0.0
    bias = median(errors)
    if abs(bias) < 0.75:
        return 0.0
    return max(-1.0, min(1.0, -bias / 2))


def fmt_time(minute_of_day):
    h, m = divmod(minute_of_day, 60)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--history")
    ap.add_argument("--out", default="out")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tz = ZoneInfo(os.environ.get("HOME_TIMEZONE", "America/Los_Angeles"))
    lat = os.environ.get("HOME_LATITUDE", "47.687")
    lon = os.environ.get("HOME_LONGITUDE", "-122.377")
    now = dt.datetime.now(tz)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    devices = nle.list_devices()
    status = {}
    for d in devices:
        status[d["id"]] = nle.get_status(d["id"])
        d["label"] = d.get("name") or status[d["id"]]["name"] or f"Thermostat ...{d['serial'][-4:]}"

    profile = None
    if args.profile and os.path.exists(args.profile):
        profile = json.load(open(args.profile))
    if not profile or not profile.get("zones"):
        profile = default_profile(devices)
    known = {d["id"] for d in devices}
    zones = [z for z in profile["zones"] if z.get("device_id") in known]
    problems = []
    if profile.get("placeholder"):
        problems.append("No household profile yet: planned with placeholder occupants. Fill in the intake form.")
    elif not profile.get("confirmed"):
        problems.append("Household profile is saved but not marked ready to apply.")
    missing = known - {z["device_id"] for z in zones}
    if missing:
        problems.append(f"{len(missing)} thermostat(s) are not assigned to a zone in the profile.")

    forecast = weather.hourly_forecast(lat, lon)
    days = planner._conditions(forecast, now.date(), HORIZON_DAYS)
    history = load_history(args.history)
    outdoor_now = forecast[0]["temp_f"] if forecast else None

    result = {
        "run_at": now.isoformat(),
        "applied": False,
        "problems": problems,
        "zones": [],
        "observations": [],
        "forecast_days": [],
    }
    for day in days[: HORIZON_DAYS + 1]:
        temps = [h["temp_f"] for h in day["hours"].values()]
        sun = [h["sun"] for hr, h in day["hours"].items() if 9 <= hr < 15]
        result["forecast_days"].append(
            {"date": day["date"].isoformat(), "low_f": round(min(temps)), "high_f": round(max(temps)), "midday_sun": round(sum(sun) / len(sun), 2)}
        )

    apply_ok = args.apply and not problems
    for zone in zones:
        zid = zone["device_id"]
        st = status[zid]
        zone.setdefault("heater_delivery", st.get("heater_delivery"))
        trim = trim_from_history(history, zid)
        events, notes = planner.plan_zone(zone, profile, days, st.get("humidity") or 50, trim, bool(st.get("early_on")))
        schedule = planner.to_nle_schedule(events)
        previous = [r for r in history if r.get("device_id") == zid and r.get("events")]
        planned_now = planner.planned_temp_at(previous[-1]["events"], now) if previous else None
        result["observations"].append(
            {
                "ts": now.isoformat(),
                "device_id": zid,
                "current_f": round(planner.c2f(st["current_c"]), 1) if st.get("current_c") is not None else None,
                "target_f": round(planner.c2f(st["target_c"]), 1) if st.get("target_c") is not None else None,
                "humidity": st.get("humidity"),
                "heating": st.get("heating_now"),
                "outdoor_f": outdoor_now,
                "planned_f": planned_now,
                "events": events,
            }
        )
        zone_problems = []
        if not st.get("can_heat"):
            zone_problems.append("thermostat reports it cannot heat")
        if st.get("schedule_mode") != "HEAT":
            zone_problems.append(f"schedule mode is {st.get('schedule_mode')}, expected HEAT")
        if st.get("learning_mode"):
            zone_problems.append("Nest learning is on and may rewrite the schedule")
        if any(not (planner.HARD_MIN_F <= t <= planner.HARD_MAX_F) for _, t in events) or not events:
            zone_problems.append("plan failed its safety bounds check")
        entry = {
            "device_id": zid,
            "name": zone.get("name") or st["name"],
            "events": events,
            "schedule": schedule,
            "notes": notes,
            "trim_f": trim,
            "problems": zone_problems,
            "status": st,
            "applied": False,
        }
        if apply_ok and not [p for p in zone_problems if "learning" not in p]:
            entry["previous_schedule"] = nle.get_schedule(zid)
            nle.put_schedule(zid, schedule)
            readback = nle.get_schedule(zid) or {}
            want = sum(len(d) for d in schedule["days"].values())
            got = sum(1 for d in (readback.get("days") or {}).values() for e in d.values() if e.get("entry_type") == "setpoint")
            entry["applied"] = True
            entry["readback_setpoints"] = [got, want]
            if got != want:
                entry["problems"].append(f"read-back shows {got} setpoints, expected {want}")
        result["zones"].append(entry)
    result["applied"] = any(z["applied"] for z in result["zones"])

    # Human-readable report.
    lines = [f"# Comfort plan, {now:%a %b %-d %Y %-I:%M %p}", ""]
    lines.append("Applied to thermostats: **" + ("yes" if result["applied"] else "no (dry run)") + "**")
    for p in problems:
        lines.append(f"- Note: {p}")
    lines += ["", "## Forecast", "", "| Day | Low | High | Midday sun |", "|---|---|---|---|"]
    for f in result["forecast_days"]:
        d = dt.date.fromisoformat(f["date"])
        lines.append(f"| {d:%a %b %-d} | {f['low_f']}°F | {f['high_f']}°F | {round(f['midday_sun'] * 100)}% |")
    horizon_weekdays = [(now.date() + dt.timedelta(days=i)).weekday() for i in range(1, HORIZON_DAYS + 1)]
    for z in result["zones"]:
        st = z["status"]
        lines += ["", f"## {z['name']}", ""]
        cur = f"{planner.c2f(st['current_c']):.1f}°F" if st.get("current_c") is not None else "unknown"
        lines.append(f"Now {cur}, humidity {st.get('humidity')}%, heating {'on' if st.get('heating_now') else 'off'}.")
        for p in z["problems"]:
            lines.append(f"- Problem: {p}")
        if z["trim_f"]:
            lines.append(f"- History trim: {z['trim_f']:+.1f}°F")
        for wd in horizon_weekdays:
            evs = [(m % 1440, t) for m, t in z["events"] if m // 1440 == wd]
            text = ", ".join(f"{fmt_time(m)} → {t:g}°F" for m, t in evs) or "no changes (holds previous setpoint)"
            lines.append(f"- **{DAY_NAMES[wd]}**: {text}")
        dev = z["notes"]["max_deviation_f"]
        if dev:
            worst = ", ".join(f"{k} {v:g}°F" for k, v in sorted(dev.items(), key=lambda kv: -kv[1]))
            lines.append(f"- Largest gap between a person's own target and the zone setpoint: {worst}")
    report = "\n".join(lines) + "\n"
    result["report_md"] = report
    (out / "report.md").write_text(report)
    (out / "result.json").write_text(json.dumps(result, indent=1, default=str))
    print(report)


if __name__ == "__main__":
    main()
