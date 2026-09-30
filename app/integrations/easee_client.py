"""Wrapper um die pyeasee-Bibliothek fuer Status-Abfrage und Reboot der
konfigurierten Easee-Wallbox.

Das eigentliche Ein-/Ausschalten des Ladens laeuft NICHT hierueber, sondern
ueber Solar Manager (siehe solar_client.set_car_charger_mode) -- Solar
Manager hat eine eigene Easee-Anbindung und ueberschreibt direkte Easee-
Ladebefehle (pause/resume, sogar manuell in der offiziellen Easee-App)
innerhalb von Sekunden mit seiner eigenen Ladelogik. Dieses Modul bleibt
fuer Status-Telemetrie (chargerOpMode/reasonForNoCurrent fuers Dashboard)
und fuer den Reboot bei anhaltendem Porsche-Ladefehler zustaendig.

Haelt eine einzige eingeloggte Verbindung ueber Prozess-Laufzeit im Cache
und nutzt sie fuer alle Aufrufe wieder -- ein Login pro Aufruf (alle 30s im
Regel-Loop) treibt Easee's Login-Rate-Limit sehr schnell in ein HTTP 429.
Nur bei geaenderten Zugangsdaten oder einem Fehler wird neu verbunden.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import aiohttp
from pyeasee import ChargerState, Easee
from pyeasee.exceptions import NotFoundException

from .. import request_log

_lock = asyncio.Lock()
_cached_key: tuple[str, str] | None = None
_cached_easee: Easee | None = None
_cached_session: aiohttp.ClientSession | None = None
_cached_chargers: list | None = None

_REASON_CODE_RE = re.compile(r"^\((-?\d+)\)")


class EaseeError(Exception):
    pass


@dataclass
class EaseeState:
    op_mode: str
    reason_for_no_current: str
    reason_code: int | None
    has_current: bool


def _parse_reason(raw: str) -> tuple[int | None, bool]:
    match = _REASON_CODE_RE.match(raw)
    if not match:
        return None, True
    code = int(match.group(1))
    # 0 = "No reason, charging or ready to charge" -- alles andere schraenkt
    # den tatsaechlich fliessenden Strom ein oder unterbindet ihn ganz.
    return code, code == 0


async def _on_request_end(session, trace_config_ctx, params) -> None:
    request_log.record(
        method=params.method,
        url=str(params.url),
        status=params.response.status,
        source="easee",
    )


def _new_session() -> aiohttp.ClientSession:
    trace_config = aiohttp.TraceConfig()
    trace_config.on_request_end.append(_on_request_end)
    return aiohttp.ClientSession(trace_configs=[trace_config])


async def _get_client(email: str, password: str) -> Easee:
    global _cached_key, _cached_easee, _cached_session, _cached_chargers

    async with _lock:
        if _cached_easee is not None and _cached_key == (email, password):
            return _cached_easee

        if _cached_easee is not None:
            await _cached_easee.close()
            await _cached_session.close()

        session = _new_session()
        easee = Easee(email, password, session=session)
        try:
            await easee.connect()
        except Exception as exc:  # noqa: BLE001 - pyeasee raises assorted exception types
            await easee.close()
            await session.close()
            _cached_key, _cached_easee, _cached_session, _cached_chargers = None, None, None, None
            raise EaseeError(f"Easee-Login fehlgeschlagen: {exc}") from exc

        _cached_key, _cached_easee, _cached_session, _cached_chargers = (email, password), easee, session, None
        return easee


async def _invalidate() -> None:
    global _cached_key, _cached_easee, _cached_session, _cached_chargers
    if _cached_easee is not None:
        await _cached_easee.close()
    if _cached_session is not None:
        await _cached_session.close()
    _cached_key, _cached_easee, _cached_session, _cached_chargers = None, None, None, None


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


# Easee-Beobachtungs-IDs: 109 = ChargerOpMode, 96 = ReasonForNoCurrent.
_OBS_OPMODE, _OBS_REASON = 109, 96


async def _state_aus_observations(charger) -> dict:
    """Denselben Status ueber `/state/{id}/observations` holen.

    `GET /api/chargers/{id}/state` antwortet bei Easee seit dem 01.10.2026
    mit einem leeren 404 (Lukas sieht es in Postman genauso), waehrend
    `/details` und `/config` gehen. Die Beobachtungen liefern dieselben beiden
    Werte; pyeasee uebersetzt sie wie gewohnt in Klartext.
    """
    obs = (await charger.get_observations(_OBS_OPMODE, _OBS_REASON))["observations"]
    werte = {o["id"]: o["value"] for o in obs}
    if _OBS_OPMODE not in werte or _OBS_REASON not in werte:
        raise EaseeError("Easee meldet keine Beobachtungen fuer diese Wallbox.")
    return ChargerState(
        {"chargerOpMode": werte[_OBS_OPMODE], "reasonForNoCurrent": werte[_OBS_REASON]},
        False,
    )


async def get_state(email: str, password: str, charger_id: str) -> EaseeState:
    _, charger = await _get_charger(email, password, charger_id)
    try:
        try:
            state = await charger.get_state()
        except NotFoundException:
            # Kein Verbindungsfehler: die Verbindung NICHT verwerfen. Vorher
            # loeste jedes 404 ein neues Login plus `/api/chargers` aus — alle
            # ~90 s — und trieb den Account ins Rate-Limit (429).
            state = await _state_aus_observations(charger)
        reason = str(state["reasonForNoCurrent"])
        reason_code, has_current = _parse_reason(reason)
        return EaseeState(
            op_mode=str(state["chargerOpMode"]),
            reason_for_no_current=reason,
            reason_code=reason_code,
            has_current=has_current,
        )
    except EaseeError:
        raise
    except NotFoundException as exc:
        raise EaseeError(
            "Easee liefert fuer diese Wallbox weder /state noch Beobachtungen (404)."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        await _invalidate()
        raise EaseeError(f"Easee-Statusabfrage fehlgeschlagen: {exc!r}") from exc


async def reboot(email: str, password: str, charger_id: str) -> None:
    _, charger = await _get_charger(email, password, charger_id)
    try:
        await charger.reboot()
    except EaseeError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _invalidate()
        raise EaseeError(f"Easee-Reboot fehlgeschlagen: {exc}") from exc


async def close() -> None:
    await _invalidate()
