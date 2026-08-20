"""Porsche-Connect-Client, abgeleitet vom mitgelieferten Monitor-Script.

Nutzt die Bibliothek `pyporscheconnectapi`. Der Login erfolgt normalerweise
direkt mit Email + Passwort; die zurueckgegebene Session (Token) wird
verschluesselt in der DB zwischengespeichert (statt in einer Datei), damit
nicht bei jedem Poll neu eingeloggt werden muss.

Verbindung/Controller werden pro (email, password) im Prozess wiederverwendet:
`pyporscheconnectapi.Connection` hat einen mutable-default-argument-Bug
(`async_client=httpx.AsyncClient()` wird nur einmal beim Modul-Import
erzeugt und von ALLEN Connection-Instanzen geteilt, sofern man nicht selbst
einen eigenen Client uebergibt) -- schliesst man eine Instanz nach jedem
Aufruf, reisst das jede andere gleichzeitig laufende Anfrage mit ("Cannot
send a request, as the client has been closed."). Deshalb wird hier ein
eigener, dauerhafter httpx.AsyncClient erzeugt und nicht nach jedem Call
geschlossen.

Falls Porsche fuer den Account/die IP doch ein Captcha verlangt, schlaegt der
Login mit einer PorscheExceptionError fehl -- dieser Fall wird nach oben
durchgereicht und im Log/GUI sichtbar gemacht, inkl. Hinweis auf den
manuellen Fallback (Session-Datei extern per `porschecli` erzeugen und
Token-JSON in den Zugangsdaten-Tab einfuegen).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.exceptions import PorscheExceptionError

CHARGING_STATES = {"CHARGING", "INSTANT_CHARGING", "INITIALISING"}
ERROR_STATES = {"CHARGING_ERROR"}

_lock = asyncio.Lock()
_cached_key: tuple[str, str] | None = None
_cached_connection: Connection | None = None
_cached_controller: PorscheConnectAccount | None = None


class PorscheError(Exception):
    pass


@dataclass
class PorscheStatus:
    vin: str
    status: str
    is_error: bool
    is_charging: bool
    battery_percent: float | None
    lat: float | None
    lon: float | None
    session_json: str


async def _get_controller(email: str, password: str, session_json: str | None):
    global _cached_key, _cached_connection, _cached_controller

    async with _lock:
        if _cached_controller is not None and _cached_key == (email, password):
            return _cached_connection, _cached_controller

        if _cached_connection is not None:
            await _cached_connection.close()

        token = json.loads(session_json) if session_json else {}
        # Eigener Client, statt auf pyporscheconnectapi's geteilten Default
        # zu vertrauen -- siehe Modul-Docstring.
        client = httpx.AsyncClient()
        connection = Connection(email, password, async_client=client, token=token)
        controller = PorscheConnectAccount(connection=connection)

        _cached_key = (email, password)
        _cached_connection = connection
        _cached_controller = controller
        return connection, controller


async def _invalidate() -> None:
    global _cached_key, _cached_connection, _cached_controller
    if _cached_connection is not None:
        await _cached_connection.close()
    _cached_key, _cached_connection, _cached_controller = None, None, None


async def check_status(
    email: str,
    password: str,
    session_json: str | None,
    vin: str | None = None,
) -> PorscheStatus:
    if not email or not password:
        raise PorscheError("Porsche-Zugangsdaten fehlen.")

    connection, controller = await _get_controller(email, password, session_json)

    try:
        vehicles = await controller.get_vehicles()
        if not vehicles:
            raise PorscheError("Kein Fahrzeug im Porsche-Connect-Account gefunden.")

        vehicle = next((v for v in vehicles if v.vin == vin), None) if vin else vehicles[0]
        if vehicle is None:
            raise PorscheError(f"Kein Fahrzeug mit VIN '{vin}' gefunden.")

        await vehicle.get_stored_overview()
        summary = vehicle.data.get("CHARGING_SUMMARY", {}) or {}
        status = (summary.get("status") or "UNKNOWN").upper()
        battery = vehicle.data.get("BATTERY_LEVEL", {}).get("percent")
        lat, lon, _heading = vehicle.location

        is_error = status in ERROR_STATES or "ERROR" in status
        is_charging = status in CHARGING_STATES

        return PorscheStatus(
            vin=vehicle.vin,
            status=status,
            is_error=is_error,
            is_charging=is_charging,
            battery_percent=battery,
            lat=lat,
            lon=lon,
            session_json=json.dumps(connection.token),
        )
    except PorscheError:
        raise
    except PorscheExceptionError as exc:
        await _invalidate()
        raise PorscheError(f"Porsche-Connect-API-Fehler (evtl. Captcha/Login-Problem): {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - z.B. der geteilte-Client-Bug oben
        await _invalidate()
        raise PorscheError(f"Unerwarteter Porsche-Connect-Fehler: {exc}") from exc


async def close() -> None:
    await _invalidate()
