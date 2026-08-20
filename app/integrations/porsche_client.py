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

Verlangt Porsche fuer den Login-Versuch ein Captcha, wird das Captcha-Bild
gespeichert und ueber `get_pending_captcha_image()`/`submit_captcha()` der
GUI zur Verfuegung gestellt -- der Login wird also direkt in der App geloest,
ohne externe CLI.

Monkeypatch fuer einen Bug in `pyporscheconnectapi.oauth2.OAuth2Client.
fetch_authorization_code`: nach dem Passwort-Schritt haengt die Bibliothek
den zurueckgegebenen "resume"-Pfad blind an `https://{AUTHORIZATION_SERVER}`
an (`f"https://{AUTHORIZATION_SERVER}{resume_path}"`). Porsches Login-Seite
liefert dort inzwischen (nach einer Auth0-ACUL-Migration) teils schon eine
ABSOLUTE URL statt eines relativen Pfads, wodurch eine kaputte Doppel-URL
wie "https://identity.porsche.comhttps://identity.porsche.com/..." entsteht
-- httpx versucht dann den Host "identity.porsche.comhttps:" aufzuloesen,
was als "[Errno -2] Name or service not known" auffaellt. Der Patch
normalisiert das Ergebnis von `login_with_identifier` immer auf einen
relativen Pfad, unabhaengig davon, welches Format Auth0 gerade liefert.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import pyporscheconnectapi.oauth2 as _oauth2_module
from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.exceptions import (
    PorscheCaptchaRequiredError,
    PorscheExceptionError,
    PorscheWrongCredentialsError,
)

_LOG = logging.getLogger("porsche_client")

AUTH_SERVER = "https://identity.porsche.com"

_original_login_with_identifier = _oauth2_module.OAuth2Client.login_with_identifier


async def _patched_login_with_identifier(self, state):
    result = await _original_login_with_identifier(self, state)
    if result and result.startswith("http"):
        parsed = urlparse(result)
        result = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return result


_oauth2_module.OAuth2Client.login_with_identifier = _patched_login_with_identifier
CHARGING_STATES = {"CHARGING", "INSTANT_CHARGING", "INITIALISING"}
ERROR_STATES = {"CHARGING_ERROR"}

_lock = asyncio.Lock()
_cached_key: tuple[str, str] | None = None
_cached_connection: Connection | None = None
_cached_controller: PorscheConnectAccount | None = None
_pending_captcha: dict | None = None


class PorscheError(Exception):
    pass


class PorscheCaptchaNeeded(PorscheError):
    """Login blockiert, bis das per `image` angezeigte Captcha geloest wird."""

    def __init__(self, image: str) -> None:
        self.image = image
        super().__init__("Porsche verlangt ein Captcha zum Einloggen.")


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


def _normalize_captcha_image(image: str) -> str:
    if image.startswith("data:") or image.startswith("http"):
        return image
    return f"{AUTH_SERVER}{image}"


async def _log_request(request: httpx.Request) -> None:
    _LOG.info("Porsche-API-Anfrage: %s %s", request.method, request.url)


async def _log_response(response: httpx.Response) -> None:
    _LOG.info("Porsche-API-Antwort: %s fuer %s", response.status_code, response.request.url)
    if response.status_code >= 400:
        await response.aread()
        _LOG.info("Porsche-API-Fehlerbody: %s", response.text[:1000])
    if response.status_code in (301, 302, 303, 307, 308):
        _LOG.info("Porsche-API-Redirect-Location: %s", response.headers.get("location"))


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(event_hooks={"request": [_log_request], "response": [_log_response]})


async def _set_controller(email: str, password: str, connection: Connection, controller: PorscheConnectAccount) -> None:
    global _cached_key, _cached_connection, _cached_controller
    if _cached_connection is not None and _cached_connection is not connection:
        await _cached_connection.close()
    _cached_key = (email, password)
    _cached_connection = connection
    _cached_controller = controller


async def _get_controller(email: str, password: str, session_json: str | None):
    async with _lock:
        if _cached_controller is not None and _cached_key == (email, password):
            return _cached_connection, _cached_controller

        token = json.loads(session_json) if session_json else {}
        # Eigener Client, statt auf pyporscheconnectapi's geteilten Default
        # zu vertrauen -- siehe Modul-Docstring.
        client = _new_client()
        connection = Connection(email, password, async_client=client, token=token)
        controller = PorscheConnectAccount(connection=connection)
        await _set_controller(email, password, connection, controller)
        return connection, controller


async def _invalidate() -> None:
    global _cached_key, _cached_connection, _cached_controller
    if _cached_connection is not None:
        await _cached_connection.close()
    _cached_key, _cached_connection, _cached_controller = None, None, None


async def _fetch_status(connection: Connection, controller: PorscheConnectAccount, vin: str | None) -> PorscheStatus:
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

    return PorscheStatus(
        vin=vehicle.vin,
        status=status,
        is_error=status in ERROR_STATES or "ERROR" in status,
        is_charging=status in CHARGING_STATES,
        battery_percent=battery,
        lat=lat,
        lon=lon,
        session_json=json.dumps(connection.token),
    )


async def check_status(
    email: str,
    password: str,
    session_json: str | None,
    vin: str | None = None,
) -> PorscheStatus:
    global _pending_captcha

    if not email or not password:
        raise PorscheError("Porsche-Zugangsdaten fehlen.")

    connection, controller = await _get_controller(email, password, session_json)

    try:
        status = await _fetch_status(connection, controller, vin)
        _pending_captcha = None
        return status
    except PorscheError:
        raise
    except PorscheCaptchaRequiredError as exc:
        await _invalidate()
        image = _normalize_captcha_image(exc.captcha)
        _pending_captcha = {"image": image, "state": exc.state, "email": email, "password": password}
        raise PorscheCaptchaNeeded(image) from exc
    except PorscheWrongCredentialsError as exc:
        await _invalidate()
        raise PorscheError("Porsche-Login abgelehnt: Email/Passwort falsch.") from exc
    except PorscheExceptionError as exc:
        await _invalidate()
        raise PorscheError(f"Porsche-Connect-API-Fehler: {exc.message or exc}") from exc
    except Exception as exc:  # noqa: BLE001 - z.B. der geteilte-Client-Bug oben
        await _invalidate()
        raise PorscheError(f"Unerwarteter Porsche-Connect-Fehler: {exc}") from exc


def get_pending_captcha_image() -> str | None:
    return _pending_captcha["image"] if _pending_captcha else None


async def submit_captcha(code: str, vin: str | None = None) -> PorscheStatus:
    global _pending_captcha

    if not _pending_captcha:
        raise PorscheError("Kein offener Captcha-Vorgang.")
    if not code:
        raise PorscheError("Bitte den Captcha-Code eingeben.")

    email = _pending_captcha["email"]
    password = _pending_captcha["password"]
    state = _pending_captcha["state"]

    client = _new_client()
    connection = Connection(email, password, captcha_code=code, state=state, async_client=client, token={})
    controller = PorscheConnectAccount(connection=connection)

    try:
        status = await _fetch_status(connection, controller, vin)
        await _set_controller(email, password, connection, controller)
        _pending_captcha = None
        return status
    except PorscheCaptchaRequiredError as exc:
        await connection.close()
        image = _normalize_captcha_image(exc.captcha)
        _pending_captcha = {"image": image, "state": exc.state, "email": email, "password": password}
        raise PorscheCaptchaNeeded(image) from exc
    except PorscheWrongCredentialsError as exc:
        await connection.close()
        _pending_captcha = None
        raise PorscheError("Porsche-Login abgelehnt: Email/Passwort falsch.") from exc
    except PorscheExceptionError as exc:
        await connection.close()
        raise PorscheError(f"Falscher Captcha-Code oder Porsche-Fehler: {exc.message or exc}") from exc
    except Exception as exc:  # noqa: BLE001
        await connection.close()
        raise PorscheError(f"Unerwarteter Porsche-Connect-Fehler: {exc}") from exc


async def close() -> None:
    await _invalidate()
