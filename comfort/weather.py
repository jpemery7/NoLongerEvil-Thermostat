"""Hourly forecast from the US National Weather Service (api.weather.gov)."""
import datetime as dt
import os
import re

import requests

BASE = os.environ.get("WEATHER_API_BASE", "https://api.weather.gov").rstrip("/")
_HEADERS = {"User-Agent": "home-comfort-scheduler", "Accept": "application/geo+json"}

def _sun_fraction(text, is_day):
    """Fraction of clear sky implied by the NWS short forecast wording."""
    if not is_day:
        return 0.0
    t = text.lower()
    if re.search(r"mostly (sunny|clear)", t):
        return 0.8
    if re.search(r"partly (sunny|cloudy)", t):
        return 0.5
    if "mostly cloudy" in t:
        return 0.2
    if re.search(r"cloudy|overcast|rain|showers|snow|fog|drizzle|thunder", t):
        return 0.0
    if re.search(r"sunny|clear", t):
        return 1.0
    return 0.3


def hourly_forecast(lat, lon):
    """Return a list of hourly dicts (local, timezone-aware) for about 6.5 days."""
    point = requests.get(f"{BASE}/points/{lat},{lon}", headers=_HEADERS, timeout=30)
    point.raise_for_status()
    url = point.json()["properties"]["forecastHourly"]
    last = None
    for _ in range(3):  # the hourly endpoint returns occasional 500s
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        if resp.ok:
            break
        last = resp
    else:
        last.raise_for_status()
    hours = []
    for p in resp.json()["properties"]["periods"]:
        temp = p["temperature"]
        temp_f = temp if p.get("temperatureUnit", "F") == "F" else temp * 9 / 5 + 32
        wind = re.search(r"(\d+)(?!.*\d)", p.get("windSpeed") or "0")
        hours.append(
            {
                "start": dt.datetime.fromisoformat(p["startTime"]),
                "temp_f": float(temp_f),
                "rh": (p.get("relativeHumidity") or {}).get("value"),
                "wind_mph": float(wind.group(1)) if wind else 0.0,
                "sun": _sun_fraction(p.get("shortForecast", ""), p.get("isDaytime", False)),
                "text": p.get("shortForecast", ""),
            }
        )
    return hours
