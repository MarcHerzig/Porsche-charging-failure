"""Client fuer die Solar Manager Cloud-API (cloud.solar-manager.ch).

Verbindung erfolgt ueber die Solar Manager ID (smId) des Geraets plus einem
API-Key (Header x-api-key), siehe
https://cloud.solar-manager.ch/swagger.json (Solar Manager External API).
Der Live-Endpoint /v3/users/{smId}/data/stream liefert dieselben
Feldnamen (pW, cW, pWh, t) wie die frueher genutzte lokale Geraete-API.

Die Ladesteuerung laeuft bewusst ueber Solar Manager statt direkt ueber
Easee: Solar Manager hat eine eigene, native Easee-Anbindung (Geraetetyp
"carcharging") und ueberschreibt Easee-seitige pause/resume-Befehle
innerhalb von Sekunden mit seiner eigenen Ladelogik -- live beobachtet
sogar bei einem manuellen Pause in der offiziellen Easee-App. Wenn wir
stattdessen den Lademodus IN Solar Manager selbst umschalten
(chargingMode 0 = "Fast Charge" / 3 = "Do not charge"), gibt es keine
zwei Systeme mehr, die um denselben Charger konkurrieren.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .. import request_log

API_BASE = "https://cloud.solar-manager.ch"
TIMEOUT_SECONDS = 10

_cached_device_id: str | None = None
_cached_device_key: tuple[str, str] | None = None

# chargingMode-Werte fuer PUT /v1/control/car-charger/{sensorId}
CHARGING_MODE_FAST = 0  # "Fast Charge" -- entspricht unserem "soll laden"
CHARGING_MODE_DO_NOT_CHARGE = 3  # "Do not charge" -- entspricht "soll nicht laden"


class SolarManagerError(Exception):
    pass


@dataclass
class SolarPoint:
    production_w: float
    consumption_w: float
    interval_production_wh: float
    timestamp: str


def _headers(api_key: str) -> dict[str, str]:
    return {"Accept": "application/json", "x-api-key": api_key}


async def get_current_point(sm_id: str, api_key: str) -> SolarPoint:
    if not sm_id or not api_key:
        raise SolarManagerError("Solar Manager ID oder API-Key fehlt.")

    url = f"{API_BASE}/v3/users/{sm_id}/data/stream"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_headers(api_key))
    except httpx.HTTPError as exc:
        raise SolarManagerError(f"Verbindung zu Solar Manager fehlgeschlagen: {exc}") from exc

    request_log.record(method="GET", url=url, status=response.status_code, source="solar_manager")

    if response.status_code != 200:
        raise SolarManagerError(
            f"Solar Manager API Fehler ({response.status_code}): {response.text[:200]}"
        )

    data = response.json()
    return SolarPoint(
        production_w=float(data.get("pW", 0)),
        consumption_w=float(data.get("cW", 0)),
        interval_production_wh=float(data.get("pWh", 0)),
        timestamp=str(data.get("t", "")),
    )


async def find_car_charger_device_id(sm_id: str, api_key: str) -> str:
    """Sucht das erste Geraet vom Typ 'carcharging' (z.B. die Easee-Wallbox,
    wie sie Solar Manager selbst verwaltet) und gibt dessen Device-ID zurueck.
    Wird pro (sm_id, api_key) im Prozess gecacht, da sich das Geraet nicht
    laufend aendert.
    """
    global _cached_device_id, _cached_device_key

    if not sm_id or not api_key:
        raise SolarManagerError("Solar Manager ID oder API-Key fehlt.")

    if _cached_device_id is not None and _cached_device_key == (sm_id, api_key):
        return _cached_device_id

    url = f"{API_BASE}/v3/users/{sm_id}/devices"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_headers(api_key))
    except httpx.HTTPError as exc:
        raise SolarManagerError(f"Verbindung zu Solar Manager fehlgeschlagen: {exc}") from exc

    request_log.record(method="GET", url=url, status=response.status_code, source="solar_manager")

    if response.status_code != 200:
        raise SolarManagerError(
            f"Solar Manager Geraete-Abfrage fehlgeschlagen ({response.status_code}): {response.text[:200]}"
        )

    devices = response.json()
    for device in devices:
        if device.get("type") == "carcharging":
            device_id = str(device["_id"])
            _cached_device_id, _cached_device_key = device_id, (sm_id, api_key)
            return device_id

    raise SolarManagerError(
        "Kein Geraet vom Typ 'carcharging' in Solar Manager gefunden -- ist die Easee-Wallbox dort eingebunden?"
    )


async def set_car_charger_mode(sm_id: str, api_key: str, device_id: str, charging_mode: int) -> None:
    url = f"{API_BASE}/v1/control/car-charger/{device_id}"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.put(url, headers=_headers(api_key), json={"chargingMode": charging_mode})
    except httpx.HTTPError as exc:
        raise SolarManagerError(f"Verbindung zu Solar Manager fehlgeschlagen: {exc}") from exc

    request_log.record(method="PUT", url=url, status=response.status_code, source="solar_manager")

    if response.status_code not in (200, 202, 204):
        raise SolarManagerError(
            f"Solar Manager Lademodus-Befehl fehlgeschlagen ({response.status_code}): {response.text[:200]}"
        )
