"""Client fuer die lokale Solar Manager API (GET /v2/point).

Solar Manager Geraete bieten im lokalen Netzwerk einen HTTPS-Endpoint mit
selbstsigniertem Zertifikat und API-Key-Header. Siehe z.B.
https://library.loxone.com/detail/solar-manager-local-api-2009 sowie die
Home-Assistant-Integration https://github.com/bastbu/ha-solarmanager, an der
sich dieser Client orientiert.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

ENDPOINT_POINT = "/v2/point"
TIMEOUT_SECONDS = 10


class SolarManagerError(Exception):
    pass


@dataclass
class SolarPoint:
    production_w: float
    consumption_w: float
    interval_production_wh: float
    timestamp: str


async def get_current_point(base_url: str, api_key: str) -> SolarPoint:
    if not base_url:
        raise SolarManagerError("Keine Solar Manager Base-URL konfiguriert.")

    url = f"{base_url.rstrip('/')}{ENDPOINT_POINT}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        async with httpx.AsyncClient(verify=False, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise SolarManagerError(f"Verbindung zu Solar Manager fehlgeschlagen: {exc}") from exc

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
