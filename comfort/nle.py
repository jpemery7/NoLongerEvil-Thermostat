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


def _get(path, attempts=4):
    """GET with retries: the hosted API occasionally times out or returns a 5xx."""
    for attempt in range(attempts):
        try:
            r = requests.get(f"{BASE}{path}", headers=_HEADERS, timeout=45)
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            error = requests.HTTPError(f"{r.status_code} from {path}")
        except (requests.Timeout, requests.ConnectionError) as exc:
            error = exc
        if attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))
    raise error


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
    # The service often returns only the fields a thermostat last pushed (after a
    # reconnect the shared record can hold as few as four). A missing field means
    # "not reported", never "false": callers get None for anything unknown.
    current = shared.get("current_temperature")
    current_source = "shared"
    if current is None and isinstance(dev.get("backplate_temperature"), (int, float)):
        current, current_source = dev["backplate_temperature"], "backplate"
    heater = shared.get("hvac_heater_state")
    can_heat = shared.get("can_heat")
    if can_heat is None and (heater is True or dev.get("heater_source") or shared.get("target_temperature_type") in ("heat", "range")):
        can_heat = True  # it is heating, has a heat source, or is in heat mode
    expected = ("current_temperature", "target_temperature", "hvac_heater_state", "can_heat")
    return {
        "name": shared.get("name") or "",
        "current_c": current,
        "current_source": current_source if current is not None else None,
        "target_c": shared.get("target_temperature"),
        "target_type": shared.get("target_temperature_type"),
        "heating_now": heater if isinstance(heater, bool) else None,
        "can_heat": can_heat if isinstance(can_heat, bool) else None,
        "can_cool": shared.get("can_cool") if isinstance(shared.get("can_cool"), bool) else None,
        "partial": [k for k in expected if k not in shared],
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
