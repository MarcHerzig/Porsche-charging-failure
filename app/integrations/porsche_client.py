"""Porsche-Connect-Client, abgeleitet vom mitgelieferten Monitor-Script.

Nutzt die Bibliothek `pyporscheconnectapi`. Der Login erfolgt normalerweise
direkt mit Email + Passwort; die zurueckgegebene Session (Token) wird
verschluesselt in der DB zwischengespeichert (statt in einer Datei), damit
nicht bei jedem Poll neu eingeloggt werden muss.

Falls Porsche fuer den Account/die IP doch ein Captcha verlangt, schlaegt der
Login mit einer PorscheExceptionError fehl -- dieser Fall wird nach oben
durchgereicht und im Log/GUI sichtbar gemacht, inkl. Hinweis auf den
manuellen Fallback (Session-Datei extern per `porschecli` erzeugen und
Token-JSON in den Zugangsdaten-Tab einfuegen).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.exceptions import PorscheExceptionError

CHARGING_STATES = {"CHARGING", "INSTANT_CHARGING", "INITIALISING"}
ERROR_STATES = {"CHARGING_ERROR"}


class PorscheError(Exception):
    pass


@dataclass
class PorscheStatus:
    vin: str
    status: str
    is_error: bool
    is_charging: bool
    battery_percent: float | None
    session_json: str


async def check_status(
    email: str,
    password: str,
    session_json: str | None,
    vin: str | None = None,
) -> PorscheStatus:
    if not email or not password:
        raise PorscheError("Porsche-Zugangsdaten fehlen.")

    token = json.loads(session_json) if session_json else {}
    connection = Connection(email, password, token=token)
    controller = PorscheConnectAccount(connection=connection)

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

        is_error = status in ERROR_STATES or "ERROR" in status
        is_charging = status in CHARGING_STATES

        return PorscheStatus(
            vin=vehicle.vin,
            status=status,
            is_error=is_error,
            is_charging=is_charging,
            battery_percent=battery,
            session_json=json.dumps(connection.token),
        )
    except PorscheExceptionError as exc:
        raise PorscheError(f"Porsche-Connect-API-Fehler (evtl. Captcha/Login-Problem): {exc}") from exc
    finally:
        await connection.close()
