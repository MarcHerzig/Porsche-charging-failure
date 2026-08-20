"""Hintergrund-Loops: PV/Easee-Regelschleife (schnell), Porsche-Fehler-Check
(langsam, 15 Min) und PV-Forecast (alle paar Stunden).

Live-Werte werden im Prozessspeicher gehalten (kein Verlauf/Chart gefordert);
persistiert wird nur, was einen Container-Neustart ueberleben muss: die
Debounce-Timer, der zuletzt angewandte Lade-Zustand, Reboot-Zeitpunkt und
Porsche-Fehlerbeginn.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta, timezone

from . import config, control, db
from .integrations import easee_client, forecast_client, porsche_client, solar_client

_LOG = logging.getLogger("scheduler")

LIVE: dict = {
    "pv_watts": None,
    "consumption_w": None,
    "solar_error": None,
    "solar_updated_at": None,
    "easee_op_mode": None,
    "easee_reason": None,
    "easee_has_current": None,
    "easee_error": None,
    "porsche_status": None,
    "porsche_battery": None,
    "porsche_error": None,
    "porsche_updated_at": None,
    "porsche_connected": None,
    "porsche_distance_km": None,
    "porsche_is_home": None,
    "porsche_captcha_pending": False,
    "forecast": [],
    "forecast_error": None,
    "utc_offset_seconds": 0,
    "charging_active": None,
}


HOME_RADIUS_KM = 0.3


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _parse_hhmm(value: str) -> dtime | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def _todays_sun_times() -> tuple[dtime | None, dtime | None]:
    """Sunrise/Sunset des heutigen Tages (lokale Zeit) aus dem zuletzt
    geladenen Forecast, falls vorhanden und der erste Eintrag tatsaechlich
    von heute ist (lokales Datum, ueber utc_offset_seconds bestimmt)."""
    forecast = LIVE.get("forecast") or []
    if not forecast:
        return None, None
    local_today = (datetime.now(timezone.utc) + timedelta(seconds=LIVE.get("utc_offset_seconds", 0))).date()
    today_entry = forecast[0]
    if today_entry.get("date") != local_today.isoformat():
        return None, None
    return _parse_hhmm(today_entry.get("sunrise", "")), _parse_hhmm(today_entry.get("sunset", ""))


async def _solar_easee_loop() -> None:
    while True:
        try:
            await _tick_solar_easee()
        except Exception:  # noqa: BLE001 - Loop darf nie aussterben
            _LOG.exception("Fehler im Solar/Easee-Loop")
        await asyncio.sleep(config.SOLAR_EASEE_POLL_SECONDS)


async def _tick_solar_easee() -> None:
    creds = db.get_credentials(decrypted=True)
    settings = db.get_settings()
    runtime = db.get_runtime_state()
    now = datetime.now(timezone.utc)

    pv_watts = None
    try:
        point = await solar_client.get_current_point(creds["solar_manager_id"], creds["solar_api_key"])
        pv_watts = point.production_w
        LIVE.update(
            pv_watts=point.production_w,
            consumption_w=point.consumption_w,
            solar_error=None,
            solar_updated_at=now.isoformat(),
        )
    except solar_client.SolarManagerError as exc:
        LIVE["solar_error"] = str(exc)

    sunrise, sunset = _todays_sun_times()
    decision = control.evaluate(
        pv_watts=pv_watts,
        now=now,
        settings=settings,
        prev_pending_target=bool(runtime["pending_target"]) if runtime["pending_target"] is not None else None,
        prev_condition_since=_parse_ts(runtime["condition_since"]),
        prev_charging_active=bool(runtime["charging_active"]),
        utc_offset_seconds=LIVE.get("utc_offset_seconds", 0),
        sunrise=sunrise,
        sunset=sunset,
    )
    LIVE["charging_active"] = decision.charging_active

    db.update_runtime_state(
        {
            "pending_target": int(decision.raw_should_charge),
            "condition_since": decision.condition_since.isoformat(),
            "last_pv_watts": pv_watts,
        }
    )

    if decision.changed:
        try:
            device_id = await solar_client.find_car_charger_device_id(
                creds["solar_manager_id"], creds["solar_api_key"]
            )
            target_mode = (
                solar_client.CHARGING_MODE_FAST
                if decision.charging_active
                else solar_client.CHARGING_MODE_DO_NOT_CHARGE
            )
            await solar_client.set_car_charger_mode(
                creds["solar_manager_id"], creds["solar_api_key"], device_id, target_mode
            )
            if decision.charging_active:
                db.add_event("charge_start", f"Laden gestartet ({decision.reason})")
            else:
                db.add_event("charge_stop", f"Laden gestoppt ({decision.reason})")
            db.update_runtime_state({"charging_active": int(decision.charging_active)})
            LIVE["solar_error"] = None
        except solar_client.SolarManagerError as exc:
            LIVE["solar_error"] = str(exc)
            db.add_event("solar_manager_error", f"Solar-Manager-Befehl fehlgeschlagen: {exc}")

    try:
        state = await easee_client.get_state(
            creds["easee_email"], creds["easee_password"], creds["easee_charger_id"]
        )
        LIVE["easee_op_mode"] = state.op_mode
        LIVE["easee_reason"] = state.reason_for_no_current
        LIVE["easee_has_current"] = state.has_current
    except easee_client.EaseeError as exc:
        LIVE["easee_error"] = str(exc)


async def _porsche_loop() -> None:
    while True:
        try:
            await _tick_porsche()
        except Exception:  # noqa: BLE001
            _LOG.exception("Fehler im Porsche-Loop")
        await asyncio.sleep(config.PORSCHE_POLL_SECONDS)


async def _tick_porsche() -> None:
    creds = db.get_credentials(decrypted=True)
    settings = db.get_settings()
    runtime = db.get_runtime_state()
    now = datetime.now(timezone.utc)

    try:
        status = await porsche_client.check_status(
            creds["porsche_email"], creds["porsche_password"], creds["porsche_session"], creds["porsche_vin"]
        )
        LIVE["porsche_captcha_pending"] = False
    except porsche_client.PorscheCaptchaNeeded as exc:
        LIVE["porsche_error"] = str(exc)
        LIVE["porsche_connected"] = False
        LIVE["porsche_captcha_pending"] = True
        db.add_event("porsche_captcha", "Porsche verlangt ein Captcha -- im Zugangsdaten-Tab loesen")
        db.update_runtime_state({"last_checked_at": now.isoformat()})
        return
    except porsche_client.PorscheError as exc:
        LIVE["porsche_error"] = str(exc)
        LIVE["porsche_connected"] = False
        LIVE["porsche_captcha_pending"] = False
        db.add_event("porsche_error", f"Porsche-Connect-Fehler: {exc}")
        db.update_runtime_state({"last_checked_at": now.isoformat()})
        return

    db.update_credentials({"porsche_session": status.session_json})

    distance_km = None
    is_home = None
    if status.lat is not None and status.lon is not None and settings["lat"] is not None and settings["lon"] is not None:
        distance_km = round(_haversine_km(status.lat, status.lon, settings["lat"], settings["lon"]), 2)
        is_home = distance_km <= HOME_RADIUS_KM

    LIVE.update(
        porsche_status=status.status,
        porsche_battery=status.battery_percent,
        porsche_error=None,
        porsche_connected=True,
        porsche_updated_at=now.isoformat(),
        porsche_distance_km=distance_km,
        porsche_is_home=is_home,
    )
    db.update_runtime_state(
        {"last_porsche_status": status.status, "last_checked_at": now.isoformat()}
    )

    error_since = _parse_ts(runtime["porsche_error_since"])
    last_reboot = _parse_ts(runtime["last_reboot_at"])

    if status.is_error:
        if error_since is None:
            db.update_runtime_state({"porsche_error_since": now.isoformat()})
            db.add_event("charge_error_detected", f"Ladefehler erkannt (Status: {status.status})")
        else:
            cooldown_min = settings["reboot_cooldown_min"]
            cooldown_ok = last_reboot is None or (now - last_reboot).total_seconds() / 60 >= cooldown_min
            if cooldown_ok:
                try:
                    await easee_client.reboot(
                        creds["easee_email"], creds["easee_password"], creds["easee_charger_id"]
                    )
                    db.update_runtime_state(
                        {"last_reboot_at": now.isoformat(), "porsche_error_since": None}
                    )
                    db.add_event("reboot", "Wallbox nach anhaltendem Ladefehler automatisch neu gebootet")
                except easee_client.EaseeError as exc:
                    db.add_event("reboot_failed", f"Automatischer Reboot fehlgeschlagen: {exc}")
    elif error_since is not None:
        db.update_runtime_state({"porsche_error_since": None})
        db.add_event("charge_error_cleared", f"Ladefehler nicht mehr aktiv (Status: {status.status})")


async def _forecast_loop() -> None:
    while True:
        try:
            await _tick_forecast()
        except Exception:  # noqa: BLE001
            _LOG.exception("Fehler im Forecast-Loop")
        await asyncio.sleep(config.FORECAST_POLL_SECONDS)


async def _tick_forecast() -> None:
    settings = db.get_settings()
    try:
        result = await forecast_client.get_forecast(settings["lat"], settings["lon"])
        LIVE["forecast"] = [d.__dict__ for d in result.days]
        LIVE["utc_offset_seconds"] = result.utc_offset_seconds
        LIVE["forecast_error"] = None
    except forecast_client.ForecastError as exc:
        LIVE["forecast_error"] = str(exc)


async def refresh_forecast() -> None:
    await _tick_forecast()


async def refresh_solar_easee() -> None:
    await _tick_solar_easee()


async def refresh_porsche() -> None:
    await _tick_porsche()


async def manual_reboot() -> None:
    creds = db.get_credentials(decrypted=True)
    await easee_client.reboot(creds["easee_email"], creds["easee_password"], creds["easee_charger_id"])
    db.add_event("reboot", "Wallbox manuell neu gebootet")


_tasks: list[asyncio.Task] = []


def start() -> None:
    _tasks.append(asyncio.create_task(_solar_easee_loop()))
    _tasks.append(asyncio.create_task(_porsche_loop()))
    _tasks.append(asyncio.create_task(_forecast_loop()))


def stop() -> None:
    for task in _tasks:
        task.cancel()
