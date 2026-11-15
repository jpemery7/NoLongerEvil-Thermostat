"""Minimal client for the hosted No Longer Evil API (https://docs.nolongerevil.com).

In Claude cloud sessions the API key is injected by the network proxy, so the
Authorization header sent here is only a placeholder. Outside such a session,
set NLE_API_KEY to a real key.
"""
import os
import time

import requests

BASE = os.environ.get("NLE_API_BASE", "https://nolongerevil.com/api/v1").rstrip("/")
_KEY = os.environ.get("NLE_API_KEY", "proxy-injected")
_HEADERS = {"Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"}
PUSH_DEBOUNCE_S = 16  # docs: wait >= 15 s between schedule pushes


def _get(path):
    r = requests.get(f"{BASE}{path}", headers=_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def list_devices():
    return _get("/devices")["devices"]


def get_status(device_id):
    """Return a flat summary of the fields the planner uses, in Celsius."""
    state = _get(f"/thermostat/{device_id}/status")["state"]

    def bucket(prefix):
        for key, obj in state.items():
            if key.startswith(prefix + "."):
                return obj.get("value", {})
        return {}

    dev, shared = bucket("device"), bucket("shared")
    return {
        "name": shared.get("name") or "",
        "current_c": shared.get("current_temperature"),
        "target_c": shared.get("target_temperature"),
        "target_type": shared.get("target_temperature_type"),
        "heating_now": bool(shared.get("hvac_heater_state")),
        "can_heat": bool(shared.get("can_heat")),
        "can_cool": bool(shared.get("can_cool")),
        "humidity": dev.get("current_humidity"),
        "heater_delivery": dev.get("heater_delivery"),
        "heater_source": dev.get("heater_source"),
        "learning_mode": dev.get("learning_mode"),
        "early_on": dev.get("preconditioning_enabled"),
        "time_to_target": dev.get("time_to_target"),
        "schedule_mode": dev.get("current_schedule_mode"),
        "eco_mode": (dev.get("eco") or {}).get("mode"),
        "lower_safety_c": dev.get("lower_safety_temp"),
    }


def get_schedule(device_id):
    return _get(f"/thermostat/{device_id}/schedule").get("schedule")


def put_schedule(device_id, schedule):
    r = requests.put(
        f"{BASE}/thermostat/{device_id}/schedule",
        headers=_HEADERS,
        json={"schedule": schedule},
        timeout=30,
    )
    r.raise_for_status()
    time.sleep(PUSH_DEBOUNCE_S)
    return r.json() if r.content else {}
