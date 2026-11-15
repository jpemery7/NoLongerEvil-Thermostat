#!/usr/bin/env python3
"""Hourly sample: what each room actually is, what it is set to, and whether it is heating.

    python3 comfort/sample.py --out out/

Read-only. Writes <out>/sample.json and <out>/sample_id.txt. Samples are taken
whether or not the heat is running, because radiant floors overshoot after the
heat stops and cool-down rates can only be seen with the heat off.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import nle  # noqa: E402
import weather  # noqa: E402


def c2f(c):
    return None if c is None else round(c * 9 / 5 + 32, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    tz = ZoneInfo(os.environ.get("HOME_TIMEZONE", "America/Los_Angeles"))
    now = dt.datetime.now(tz)
    zones = {}
    for d in nle.list_devices():
        st = nle.get_status(d["id"])
        zones[d["id"]] = {
            "current_f": c2f(st.get("current_c")),
            "target_f": c2f(st.get("target_c")),
            "heating": st.get("heating_now"),
            "humidity": st.get("humidity"),
        }
    outdoor = None
    try:
        forecast = weather.hourly_forecast(os.environ.get("HOME_LATITUDE", "47.687"), os.environ.get("HOME_LONGITUDE", "-122.377"))
        outdoor = forecast[0]["temp_f"] if forecast else None
    except Exception as exc:  # a missing outdoor reading must not lose the indoor sample
        print(f"outdoor temperature unavailable: {exc}", file=sys.stderr)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sample = {"ts": now.isoformat(), "outdoor_f": outdoor, "zones": zones}
    (out / "sample.json").write_text(json.dumps(sample))
    (out / "sample_id.txt").write_text(now.strftime("%Y-%m-%dT%H%M"))
    print(f"sampled {len(zones)} zones at {now:%Y-%m-%d %H:%M}, outdoor {outdoor}")


if __name__ == "__main__":
    main()
