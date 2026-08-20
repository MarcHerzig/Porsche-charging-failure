"""PV-Forecast ueber die kostenlose Open-Meteo-API (kein API-Key nötig).

Liefert pro Tag die Globalstrahlungssumme (kWh/m²) und Sonnenschein-Dauer als
Proxy fuer "wie sonnig wird es" -- ohne Kenntnis der Anlagengroesse (kWp) ist
keine exakte kWh-Ertragsprognose moeglich, die Globalstrahlung reicht aber
aus, um Tage mit viel/wenig Sonne zu erkennen.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 10


class ForecastError(Exception):
    pass


@dataclass
class DayForecast:
    date: str
    radiation_kwh_m2: float
    sunshine_hours: float


async def get_forecast(lat: float, lon: float, days: int = 5) -> list[DayForecast]:
    if lat is None or lon is None:
        raise ForecastError("Keine Koordinaten (lat/lon) fuer den Forecast konfiguriert.")

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "shortwave_radiation_sum,sunshine_duration",
        "timezone": "auto",
        "forecast_days": days,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(FORECAST_URL, params=params)
    except httpx.HTTPError as exc:
        raise ForecastError(f"Open-Meteo nicht erreichbar: {exc}") from exc

    if response.status_code != 200:
        raise ForecastError(f"Open-Meteo Fehler ({response.status_code}): {response.text[:200]}")

    daily = response.json().get("daily", {})
    dates = daily.get("time", [])
    radiation = daily.get("shortwave_radiation_sum", [])
    sunshine = daily.get("sunshine_duration", [])

    result = []
    for i, date in enumerate(dates):
        rad_mj = radiation[i] if i < len(radiation) else 0.0
        sun_s = sunshine[i] if i < len(sunshine) else 0.0
        result.append(
            DayForecast(
                date=date,
                radiation_kwh_m2=round((rad_mj or 0.0) / 3.6, 2),
                sunshine_hours=round((sun_s or 0.0) / 3600, 1),
            )
        )
    return result
