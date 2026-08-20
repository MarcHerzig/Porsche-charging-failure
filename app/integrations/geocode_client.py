"""Orts-Suche ueber die kostenlose Open-Meteo-Geocoding-API (kein API-Key).

Wandelt eine Freitext-Ortsangabe (z.B. "Zuerich" oder "Bern, Schweiz") in
Koordinaten fuer den PV-Forecast um.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_SECONDS = 10


class GeocodeError(Exception):
    pass


@dataclass
class GeocodeResult:
    display_name: str
    lat: float
    lon: float


async def search(query: str) -> GeocodeResult:
    if not query or not query.strip():
        raise GeocodeError("Bitte einen Ort eingeben.")

    params = {"name": query.strip(), "count": 1, "language": "de", "format": "json"}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(GEOCODE_URL, params=params)
    except httpx.HTTPError as exc:
        raise GeocodeError(f"Geocoding nicht erreichbar: {exc}") from exc

    if response.status_code != 200:
        raise GeocodeError(f"Geocoding-Fehler ({response.status_code}): {response.text[:200]}")

    results = response.json().get("results") or []
    if not results:
        raise GeocodeError(f"Kein Ort gefunden fuer '{query}'.")

    best = results[0]
    parts = [best.get("name"), best.get("admin1"), best.get("country")]
    display_name = ", ".join(p for p in parts if p)

    return GeocodeResult(display_name=display_name, lat=best["latitude"], lon=best["longitude"])
