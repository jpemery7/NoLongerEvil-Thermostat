"""Learn how each zone actually behaves from hourly samples.

For every zone this measures, from consecutive samples no more than 90 minutes
apart:

  warmup_rate_f_per_h  how fast the room rises while the heat is on
  overshoot_f          how far the room keeps rising after the heat stops
  overshoot_peak_h     how long after heat-off the room peaks
  cooldown_rate_f_per_h  how fast the room falls with the heat off, past the peak
  settled_error_f      room minus setpoint in the hours around heating

Each value is None until there is enough evidence, and the planner keeps using
the intake-form answer until then.
"""
import datetime as dt
from statistics import median

MAX_GAP_H = 1.5
MIN_WARMUP_PAIRS = 6
MIN_OVERSHOOT_EVENTS = 3
MIN_COOLDOWN_PAIRS = 8
MIN_ERROR_SAMPLES = 8
OVERSHOOT_WINDOW_H = 6
RECENT_HEAT_H = 3


def _series(samples, device_id):
    rows = []
    for s in samples:
        z = (s.get("zones") or {}).get(device_id)
        if not z or z.get("current_f") is None or not isinstance(z.get("heating"), bool):
            # A sample whose heating state was not reported cannot say whether a rise
            # was warm-up or overshoot; treat it as missing rather than "heat off".
            continue
        try:
            ts = dt.datetime.fromisoformat(s["ts"])
        except (KeyError, ValueError):
            continue
        rows.append({"ts": ts, "t": z["current_f"], "target": z.get("target_f"), "heat": z["heating"], "out": s.get("outdoor_f")})
    rows.sort(key=lambda r: r["ts"])
    return rows


def _hours(a, b):
    return (b["ts"] - a["ts"]).total_seconds() / 3600


def learn_zone(samples, device_id):
    rows = _series(samples, device_id)
    out = {
        "samples": len(rows),
        "warmup_rate_f_per_h": None,
        "overshoot_f": None,
        "overshoot_peak_h": None,
        "cooldown_rate_f_per_h": None,
        "settled_error_f": None,
    }
    warm, cool, overshoots, peaks, errors = [], [], [], [], []
    last_heat = None
    post_peak = False
    for i, row in enumerate(rows):
        if row["heat"]:
            last_heat = row["ts"]
            post_peak = False
        if i == 0:
            continue
        prev = rows[i - 1]
        gap = _hours(prev, row)
        if not 0 < gap <= MAX_GAP_H:
            post_peak = False
            continue
        rate = (row["t"] - prev["t"]) / gap
        if prev["heat"] and row["heat"] and rate > 0:
            warm.append(rate)
        if prev["heat"] and not row["heat"]:
            # Heat just stopped: follow the room until it peaks or the heat returns.
            peak, peak_h = row["t"], 0.0
            for later in rows[i + 1:]:
                h = _hours(row, later)
                if later["heat"] or h > OVERSHOOT_WINDOW_H:
                    break
                if later["t"] > peak:
                    peak, peak_h = later["t"], h
            overshoots.append(max(0.0, peak - row["t"]))
            peaks.append(peak_h)
        if not prev["heat"] and not row["heat"]:
            if rate < 0:
                post_peak = True
            if post_peak and rate < 0:
                cool.append(-rate)
        if last_heat is not None and row["target"] is not None and prev["target"] == row["target"]:
            if (row["ts"] - last_heat).total_seconds() / 3600 <= RECENT_HEAT_H:
                errors.append(row["t"] - row["target"])
    if len(warm) >= MIN_WARMUP_PAIRS:
        out["warmup_rate_f_per_h"] = round(median(warm), 2)
    if len(overshoots) >= MIN_OVERSHOOT_EVENTS:
        out["overshoot_f"] = round(median(overshoots), 2)
        out["overshoot_peak_h"] = round(median(peaks), 2)
    if len(cool) >= MIN_COOLDOWN_PAIRS:
        out["cooldown_rate_f_per_h"] = round(median(cool), 2)
    if len(errors) >= MIN_ERROR_SAMPLES:
        out["settled_error_f"] = round(median(errors), 2)
    out["evidence"] = {"warmup_pairs": len(warm), "heat_off_events": len(overshoots), "cooldown_pairs": len(cool), "error_samples": len(errors)}
    return out
