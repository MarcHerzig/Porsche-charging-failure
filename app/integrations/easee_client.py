"""Wrapper um die pyeasee-Bibliothek fuer Status-Abfrage, Reboot und
Start/Pause der Ladung der konfigurierten Easee-Wallbox.

Haelt eine einzige eingeloggte Verbindung ueber Prozess-Laufzeit im Cache
und nutzt sie fuer alle Aufrufe wieder -- ein Login pro Aufruf (alle 30s im
Regel-Loop) treibt Easee's Login-Rate-Limit sehr schnell in ein HTTP 429.
Nur bei geaenderten Zugangsdaten oder einem Fehler wird neu verbunden.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pyeasee import Easee

_lock = asyncio.Lock()
_cached_key: tuple[str, str] | None = None
_cached_easee: Easee | None = None
_cached_chargers: list | None = None


class EaseeError(Exception):
    pass


@dataclass
class EaseeState:
    op_mode: str
    reason_for_no_current: str


async def _get_client(email: str, password: str) -> Easee:
    global _cached_key, _cached_easee, _cached_chargers

    async with _lock:
        if _cached_easee is not None and _cached_key == (email, password):
            return _cached_easee

        if _cached_easee is not None:
            await _cached_easee.close()

        easee = Easee(email, password)
        try:
            await easee.connect()
        except Exception as exc:  # noqa: BLE001 - pyeasee raises assorted exception types
            await easee.close()
            _cached_key, _cached_easee, _cached_chargers = None, None, None
            raise EaseeError(f"Easee-Login fehlgeschlagen: {exc}") from exc

        _cached_key, _cached_easee, _cached_chargers = (email, password), easee, None
        return easee


async def _invalidate() -> None:
    global _cached_key, _cached_easee, _cached_chargers
    if _cached_easee is not None:
        await _cached_easee.close()
    _cached_key, _cached_easee, _cached_chargers = None, None, None


async def _get_charger(email: str, password: str, charger_id: str):
    global _cached_chargers

    if not email or not password or not charger_id:
        raise EaseeError("Easee-Zugangsdaten oder Charger-ID fehlen.")

    easee = await _get_client(email, password)

    if _cached_chargers is None:
        try:
            _cached_chargers = await easee.get_chargers()
        except Exception as exc:  # noqa: BLE001
            await _invalidate()
            raise EaseeError(f"Easee-Abfrage fehlgeschlagen: {exc}") from exc

    charger = next((c for c in _cached_chargers if c.id == charger_id), None)
    if charger is None:
        raise EaseeError(f"Keine Easee-Wallbox mit ID '{charger_id}' im Account gefunden.")
    return easee, charger


async def get_state(email: str, password: str, charger_id: str) -> EaseeState:
    _, charger = await _get_charger(email, password, charger_id)
    try:
        state = await charger.get_state()
        return EaseeState(
            op_mode=str(state["chargerOpMode"]),
            reason_for_no_current=str(state["reasonForNoCurrent"]),
        )
    except EaseeError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _invalidate()
        raise EaseeError(f"Easee-Statusabfrage fehlgeschlagen: {exc}") from exc


async def reboot(email: str, password: str, charger_id: str) -> None:
    _, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.reboot()
    except EaseeError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _invalidate()
        raise EaseeError(f"Easee-Reboot fehlgeschlagen: {exc}") from exc


async def resume_charging(email: str, password: str, charger_id: str) -> None:
    _, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.resume()
    except EaseeError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _invalidate()
        raise EaseeError(f"Easee 'resume_charging' fehlgeschlagen: {exc}") from exc


async def pause_charging(email: str, password: str, charger_id: str) -> None:
    _, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.pause()
    except EaseeError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _invalidate()
        raise EaseeError(f"Easee 'pause_charging' fehlgeschlagen: {exc}") from exc


async def close() -> None:
    await _invalidate()
