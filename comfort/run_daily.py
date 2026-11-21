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
import learn  # noqa: E402
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


def _unwrap(doc):
    """Accept either a bare document or a data-store export wrapped as {"id", "data", "version"}."""
    if isinstance(doc, dict) and isinstance(doc.get("data"), dict) and ("version" in doc or "id" in doc):
        return doc["data"]
    return doc


def load_history(path):
    """Observation records from earlier runs: a .jsonl file, or a directory of saved run documents."""
    rows = []
    if not path or not os.path.exists(path):
        return rows
    if os.path.isdir(path):
        for f in sorted(pathlib.Path(path).rglob("*.json")):
            try:
                rows.extend(_unwrap(json.loads(f.read_text())).get("observations") or [])
            except (json.JSONDecodeError, AttributeError):
                pass
    else:
        for line in open(path):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return sorted((r for r in rows if isinstance(r, dict)), key=lambda r: str(r.get("ts")))


def load_samples(path):
    """Hourly sample documents saved by sample.py (bare or data-store export)."""
    rows = []
    if path and os.path.isdir(path):
        for f in sorted(pathlib.Path(path).rglob("*.json")):
            try:
                doc = _unwrap(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict) and doc.get("ts") and isinstance(doc.get("zones"), dict):
                rows.append(doc)
    return rows


# Per-zone tuning the household can set outside the intake form (so saving the form
# never wipes it). Only these keys are read; anything else in the file is ignored.
TUNING_KEYS = {
    "trim_f": (-3.0, 1.0),      # added to every occupied target, after learning's own trim
    "empty_f": (55.0, 76.0),    # ceiling for long unoccupied stretches (only ever lowers)
    "max_lead_h": (0.0, 4.0),   # longest warm-up started ahead of a rise; 0 = rise on time
    "coast_h": (0.0, 3.0),      # how early a drop starts, to let the slab coast
}


def load_tuning(path):
    """{device_id: {key: value}} from a tuning file shaped {"zones": {device_id: {...}}}."""
    if not path or not os.path.exists(path):
        return {}
    doc = _unwrap(json.load(open(path)))
    out = {}
    for zid, raw in ((doc or {}).get("zones") or {}).items():
        if not isinstance(raw, dict):
            continue
        clean = {}
        for key, (lo, hi) in TUNING_KEYS.items():
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[key] = min(hi, max(lo, float(value)))
        if clean:
            out[zid] = clean
    return out


def trim_from_learning(learned):
    """Small setpoint correction (-1..+1 F) from how the room sits relative to its
    setpoint in the hours around heating. Rooms that are warm with the heat long
    off are not evidence about the heating and are excluded by learn.py."""
    error = learned.get("settled_error_f")
    if error is None or abs(error) < 0.75:
        return 0.0
    return max(-1.0, min(1.0, -error / 2))


def describe_learning(learned):
    parts = []
    if learned.get("warmup_rate_f_per_h"):
        parts.append(f"warms {learned['warmup_rate_f_per_h']:g}°F per hour")
    if learned.get("overshoot_f") is not None:
        parts.append(f"rises {learned['overshoot_f']:g}°F more after the heat stops, peaking {learned.get('overshoot_peak_h', 0):g} h later")
    if learned.get("cooldown_rate_f_per_h"):
        parts.append(f"cools {learned['cooldown_rate_f_per_h']:g}°F per hour with the heat off")
    if not parts:
        return f"Measured behaviour: not enough data yet ({learned.get('samples', 0)} hourly samples); using intake-form answers"
    return f"Measured behaviour from {learned.get('samples', 0)} hourly samples: " + "; ".join(parts)


def fmt_time(minute_of_day):
    h, m = divmod(minute_of_day, 60)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--history", help="history .jsonl file or directory of saved run documents")
    ap.add_argument("--samples", help="directory of hourly sample documents")
    ap.add_argument("--tuning", help="per-zone tuning file {\"zones\": {device_id: {trim_f, empty_f, max_lead_h, coast_h}}}")
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
        profile = _unwrap(json.load(open(args.profile)))
    if not profile or not profile.get("zones"):
        profile = default_profile(devices)
    known = {d["id"] for d in devices}
    zones = [z for z in profile["zones"] if z.get("device_id") in known]
    tuning = load_tuning(args.tuning)
    for z in zones:
        z["tuning"] = tuning.get(z["device_id"], {})
        for key in ("empty_f", "max_lead_h", "coast_h"):
            if key in z["tuning"]:
                z[key] = z["tuning"][key]
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
    samples = load_samples(args.samples)
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
        learned = learn.learn_zone(samples, zid)
        zone["learned"] = learned
        trim = max(-3.0, min(1.0, trim_from_learning(learned) + zone["tuning"].get("trim_f", 0.0)))
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
        mode = st.get("schedule_mode")
        if mode is None:
            # The service sometimes returns a partial device record; fall back to the stored schedule.
            mode = (nle.get_schedule(zid) or {}).get("schedule_mode")
            entry_note = "thermostat state was incomplete on this read (humidity assumed 50%)"
        else:
            entry_note = None
        if mode != "HEAT" or st.get("target_type") not in ("heat", None):
            zone_problems.append(f"schedule mode is {mode}, expected HEAT")
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
            "tuning": zone.get("tuning") or {},
            "learned": learned,
            "problems": zone_problems,
            "note": entry_note,
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
        lines.append(f"Now {cur}, humidity {st.get('humidity') if st.get('humidity') is not None else 'unknown '}%, heating {'on' if st.get('heating_now') else 'off'}.")
        for p in z["problems"]:
            lines.append(f"- Problem: {p}")
        if z.get("note"):
            lines.append(f"- Note: {z['note']}")
        tun = z.get("tuning") or {}
        if tun:
            lines.append("- Household tuning: " + ", ".join(f"{k} {v:g}" for k, v in sorted(tun.items())))
        if z["trim_f"]:
            lines.append(f"- Setpoints shifted {z['trim_f']:+.1f}°F (learned from how the room runs against its setpoint, plus any household tuning)")
        lines.append("- " + describe_learning(z["learned"]))
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
    (out / "latest.json").write_text(json.dumps({"report_md": report, "run_at": result["run_at"], "applied": result["applied"]}))
    (out / "run_id.txt").write_text(now.strftime("%Y-%m-%dT%H%M"))
    print(report)


if __name__ == "__main__":
    main()
