# Comfort scheduler

Plans a weekly heating schedule for each Nest thermostat on the hosted No
Longer Evil service, once a day, from:

- the household profile (who lives here, when, and what they like),
- the National Weather Service hourly forecast for the next three days,
- what the thermostats measure (temperature, humidity, heating state),
- how each zone has tracked earlier plans.

`knowledge.md` explains the comfort rules. `planner.py` implements them.

## Run

```bash
pip install -r comfort/requirements.txt
```

```bash
python3 comfort/run_daily.py --profile profile.json --history history.jsonl --out out/
```

That is a dry run. Add `--apply` to write schedules to the thermostats; it only
does so when the profile has `"confirmed": true` and every thermostat is
assigned to a zone. Each run writes `out/report.md` and `out/result.json`,
which includes the schedule that was replaced.

## Private data

This repository is public. The household profile, run history and schedule
backups are kept in a private data store, not here. `comfort/data/` and
`comfort/config/household*.json` are git-ignored.

## Environment

`NLE_API_BASE`, `WEATHER_API_BASE`, `HOME_LATITUDE`, `HOME_LONGITUDE`,
`HOME_TIMEZONE`. In Claude cloud sessions the NLE key is injected by the
network proxy; elsewhere set `NLE_API_KEY`.

## Tests

```bash
python3 comfort/tests/test_planner.py
```
