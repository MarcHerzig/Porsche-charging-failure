"""Smart-Lade-Logik: Schwellwert + asymmetrische Hysterese + Sperrzone.

Reine Entscheidungslogik, unabhaengig von der DB/den Integrationen, damit sie
sich isoliert nachvollziehen und testen laesst.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Any


def _parse_hhmm(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def _shift_time(base_date: datetime, t: dtime, delta_min: float) -> dtime:
    """Verschiebt eine Uhrzeit um delta_min Minuten (kann negativ sein)."""
    combined = datetime.combine(base_date.date(), t) + timedelta(minutes=delta_min)
    return combined.time()


def effective_curfew_window(
    local_now: datetime,
    settings: dict[str, Any],
    sunrise: dtime | None,
    sunset: dtime | None,
) -> tuple[dtime, dtime]:
    """Ermittelt die tatsaechlich anzuwendende Sperrzone (Von/Bis), in lokaler Zeit.

    Im normalen Fall die manuell eingestellten Werte. Im an Sonnenauf-/
    -untergang gekoppelten Modus wird die Sperrzone stattdessen aus
    sunrise/sunset abgeleitet: sie beginnt `curfew_solar_offset_min` Minuten
    VOR dem Sonnenuntergang (die PV-Leistung reicht kurz davor ohnehin meist
    nicht mehr fuer den Schwellwert) und endet ebenso viele Minuten NACH dem
    Sonnenaufgang.
    """
    if settings.get("curfew_solar_coupled") and sunrise is not None and sunset is not None:
        offset = settings.get("curfew_solar_offset_min", 0) or 0
        start = _shift_time(local_now, sunset, -offset)
        end = _shift_time(local_now, sunrise, offset)
        return start, end
    return _parse_hhmm(settings["curfew_start"]), _parse_hhmm(settings["curfew_end"])


def in_curfew(
    local_now: datetime,
    settings: dict[str, Any],
    sunrise: dtime | None = None,
    sunset: dtime | None = None,
) -> bool:
    start, end = effective_curfew_window(local_now, settings, sunrise, sunset)
    current = local_now.time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    # Ueber Mitternacht hinausgehende Sperrzone, z.B. 21:00 - 07:00
    return current >= start or current < end


@dataclass
class ControlDecision:
    raw_should_charge: bool
    condition_since: datetime
    charging_active: bool
    changed: bool
    reason: str


def evaluate(
    *,
    pv_watts: float | None,
    now: datetime,
    settings: dict[str, Any],
    prev_pending_target: bool | None,
    prev_condition_since: datetime | None,
    prev_charging_active: bool,
    utc_offset_seconds: int = 0,
    sunrise: dtime | None = None,
    sunset: dtime | None = None,
) -> ControlDecision:
    """`now` ist der tatsaechliche Zeitpunkt (fuer Hysterese-/Zeitstempel-
    Buchhaltung, beliebige aware-Zeitzone). Fuer den Sperrzonen-Vergleich
    (Uhrzeit-basiert) wird daraus mit `utc_offset_seconds` die lokale
    Wanduhrzeit am Standort berechnet -- die Sperrzone ist in lokaler Zeit
    gemeint, nicht in UTC.
    """
    mode = settings["mode"]
    local_now = now + timedelta(seconds=utc_offset_seconds)

    if mode == "always":
        raw_should_charge = True
        reason = "Immer-laden-Modus"
    else:
        if settings["curfew_enabled"] and in_curfew(local_now, settings, sunrise, sunset):
            start, end = effective_curfew_window(local_now, settings, sunrise, sunset)
            raw_should_charge = False
            reason = f"Sperrzone aktiv ({start.strftime('%H:%M')}-{end.strftime('%H:%M')})"
        elif pv_watts is None:
            # Keine aktuelle PV-Messung -- sicherheitshalber nicht laden.
            raw_should_charge = False
            reason = "Keine PV-Messung verfuegbar"
        else:
            raw_should_charge = pv_watts >= settings["threshold_w"]
            reason = f"PV {pv_watts:.0f}W vs. Schwellwert {settings['threshold_w']:.0f}W"

    if prev_pending_target is None or prev_pending_target != raw_should_charge:
        condition_since = now
    else:
        condition_since = prev_condition_since or now

    changed = False
    if raw_should_charge != prev_charging_active:
        elapsed_min = (now - condition_since).total_seconds() / 60
        required_min = settings["start_debounce_min"] if raw_should_charge else settings["stop_debounce_min"]
        # Sperrzone und der "Immer laden"-Modus sind bewusste Uebersteuerungen
        # und sollen sofort greifen -- die Hysterese ist nur gegen PV-Flattern
        # im Smart-Modus gedacht, nicht fuer einen manuellen Moduswechsel.
        is_curfew_stop = mode == "smart" and settings["curfew_enabled"] and not raw_should_charge and "Sperrzone" in reason
        is_instant = is_curfew_stop or mode == "always"
        if is_instant or elapsed_min >= required_min:
            changed = True

    charging_active = raw_should_charge if changed else prev_charging_active

    return ControlDecision(
        raw_should_charge=raw_should_charge,
        condition_since=condition_since,
        charging_active=charging_active,
        changed=changed,
        reason=reason,
    )
