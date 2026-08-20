"""Client fuer die Solar Manager Cloud-API (cloud.solar-manager.ch).

Verbindung erfolgt ueber die Solar Manager ID (smId) des Geraets plus einem
API-Key (Header x-api-key), siehe
https://cloud.solar-manager.ch/swagger.json (Solar Manager External API).
Der Live-Endpoint /v3/users/{smId}/data/stream liefert dieselben
Feldnamen (pW, cW, pWh, t) wie die frueher genutzte lokale Geraete-API.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .. import request_log

API_BASE = "https://cloud.solar-manager.ch"
TIMEOUT_SECONDS = 10


class SolarManagerError(Exception):
    pass


@dataclass
class SolarPoint:
    production_w: float
    consumption_w: float
    interval_production_wh: float
    timestamp: str


async def get_current_point(sm_id: str, api_key: str) -> SolarPoint:
    if not sm_id or not api_key:
        raise SolarManagerError("Solar Manager ID oder API-Key fehlt.")

    url = f"{API_BASE}/v3/users/{sm_id}/data/stream"
    headers = {"Accept": "application/json", "x-api-key": api_key}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
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
