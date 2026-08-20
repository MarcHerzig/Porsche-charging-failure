"""Smart-Lade-Logik: Schwellwert + asymmetrische Hysterese + Sperrzone.

Reine Entscheidungslogik, unabhaengig von der DB/den Integrationen, damit sie
sich isoliert nachvollziehen und testen laesst.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any


def _parse_hhmm(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def in_curfew(now: datetime, curfew_start: str, curfew_end: str) -> bool:
    start = _parse_hhmm(curfew_start)
    end = _parse_hhmm(curfew_end)
    current = now.time()
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
) -> ControlDecision:
    mode = settings["mode"]

    if mode == "always":
        raw_should_charge = True
        reason = "Immer-laden-Modus"
    else:
        if settings["curfew_enabled"] and in_curfew(now, settings["curfew_start"], settings["curfew_end"]):
            raw_should_charge = False
            reason = f"Sperrzone aktiv ({settings['curfew_start']}-{settings['curfew_end']})"
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
