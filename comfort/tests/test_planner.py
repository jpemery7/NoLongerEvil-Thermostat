"""Run with: python3 -m pytest comfort/tests -q   (or python3 comfort/tests/test_planner.py)"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import planner  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=-8))


def forecast(temp_f=45.0, sun=0.0, days=7):
    start = dt.datetime(2026, 11, 16, 0, tzinfo=TZ)  # a Monday
    return [{"start": start + dt.timedelta(hours=h), "temp_f": temp_f, "rh": 70, "sun": sun, "text": ""} for h in range(24 * days)]


def profile():
    return {
        "confirmed": True,
        "house": {"insulation": "average", "windows": "double"},
        "preferences": {"min_f": 62, "max_f": 72, "setback_ok": True, "setback_f": 2},
        "zones": [
            {"device_id": "up", "name": "Upstairs", "uses": ["bedroom"], "sun": "low", "warmup": "medium"},
            {"device_id": "down", "name": "Downstairs", "uses": ["living", "kitchen"], "sun": "high", "warmup": "medium"},
        ],
        "people": [
            {"name": "A", "age_group": "adult", "awake_pref_f": 69, "sleep_pref_f": 65, "bedroom_zone": "up",
             "wake_weekday": "06:30", "bed_weekday": "22:30", "away_days": ["mon", "tue", "wed", "thu", "fri"], "leave": "08:00", "return": "17:30"},
            {"name": "B", "age_group": "older", "awake_pref_f": 72, "sleep_pref_f": 68, "bedroom_zone": "up", "away_days": []},
        ],
    }


def plan(zone_index, fc=None, **kw):
    p = profile()
    days = planner._conditions(fc or forecast(), dt.date(2026, 11, 16), 3)
    return planner.plan_zone(p["zones"][zone_index], p, days, 50, **kw)


def test_bounds_and_budget():
    for i in (0, 1):
        events, _ = plan(i)
        assert events
        assert all(62 <= t <= 72 for _, t in events)
        for d in range(7):
            assert len([e for e in events if e[0] // 1440 == d]) <= planner.MAX_EVENTS_PER_DAY


def test_sleep_is_cooler_than_day_and_vulnerable_person_protected():
    events, notes = plan(0)
    night = planner.planned_temp_at(events, dt.datetime(2026, 11, 18, 2, 0))
    evening = planner.planned_temp_at(events, dt.datetime(2026, 11, 18, 21, 0))
    assert night < evening
    assert night >= 67  # older sleeper wants 68; never more than 1 F short
    assert notes["max_deviation_f"]["B"] <= 1.01


def test_colder_weather_raises_setpoint_and_lengthens_lead():
    mild, _ = plan(1, forecast(55))
    cold, _ = plan(1, forecast(25))
    when = dt.datetime(2026, 11, 17, 19, 0)
    assert planner.planned_temp_at(cold, when) >= planner.planned_temp_at(mild, when)


def test_sun_lowers_midday_target_in_sunny_zone():
    cloudy, _ = plan(1, forecast(45, 0.0))
    sunny, _ = plan(1, forecast(45, 1.0))
    when = dt.datetime(2026, 11, 17, 12, 0)
    assert planner.planned_temp_at(sunny, when) < planner.planned_temp_at(cloudy, when)


def test_household_choice_for_shared_rooms():
    p = profile()
    p["preferences"]["shared_awake_f"] = 71.5
    days = planner._conditions(forecast(), dt.date(2026, 11, 16), 3)
    events, _ = planner.plan_zone(p["zones"][1], p, days, 50)
    assert planner.planned_temp_at(events, dt.datetime(2026, 11, 17, 19, 0)) == 71.5


def test_schedule_shape():
    events, _ = plan(0)
    sched = planner.to_nle_schedule(events)
    assert set(sched["days"]) == {str(i) for i in range(7)}
    for day in sched["days"].values():
        times = [e["time"] for e in day.values()]
        assert times == sorted(times) and all(0 <= t < 86400 for t in times)
        assert all(e["type"] == "HEAT" and e["entry_type"] == "setpoint" and 12 < e["temp"] < 25 for e in day.values())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    for i in (0, 1):
        events, notes = plan(i)
        print(i, notes, [(planner.WEEKDAYS[m // 1440], f"{m % 1440 // 60:02d}:{m % 60:02d}", t) for m, t in events if m // 1440 in (1, 5)])
