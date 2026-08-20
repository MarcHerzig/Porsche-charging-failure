"""Wrapper um die pyeasee-Bibliothek fuer Status-Abfrage, Reboot und
Start/Pause der Ladung der konfigurierten Easee-Wallbox.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyeasee import Easee


class EaseeError(Exception):
    pass


@dataclass
class EaseeState:
    op_mode: str
    reason_for_no_current: str


async def _get_charger(email: str, password: str, charger_id: str):
    if not email or not password or not charger_id:
        raise EaseeError("Easee-Zugangsdaten oder Charger-ID fehlen.")

    easee = Easee(email, password)
    try:
        await easee.connect()
        chargers = await easee.get_chargers()
    except Exception as exc:  # noqa: BLE001 - pyeasee raises assorted exception types
        await easee.close()
        raise EaseeError(f"Easee-Login/Abfrage fehlgeschlagen: {exc}") from exc

    charger = next((c for c in chargers if c.id == charger_id), None)
    if charger is None:
        await easee.close()
        raise EaseeError(f"Keine Easee-Wallbox mit ID '{charger_id}' im Account gefunden.")
    return easee, charger


async def get_state(email: str, password: str, charger_id: str) -> EaseeState:
    easee, charger = await _get_charger(email, password, charger_id)
    try:
        state = await charger.get_state()
        return EaseeState(
            op_mode=str(state["chargerOpMode"]),
            reason_for_no_current=str(state["reasonForNoCurrent"]),
        )
    finally:
        await easee.close()


async def reboot(email: str, password: str, charger_id: str) -> None:
    easee, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.reboot()
    finally:
        await easee.close()


async def resume_charging(email: str, password: str, charger_id: str) -> None:
    easee, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.resume()
    finally:
        await easee.close()


async def pause_charging(email: str, password: str, charger_id: str) -> None:
    easee, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.pause()
    finally:
        await easee.close()
