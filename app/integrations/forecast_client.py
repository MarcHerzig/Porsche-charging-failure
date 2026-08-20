"""PV-Forecast ueber die kostenlose Open-Meteo-API (kein API-Key nötig).

Liefert pro Tag die Globalstrahlungssumme (kWh/m²) und Sonnenschein-Dauer als
Proxy fuer "wie sonnig wird es" -- ohne Kenntnis der Anlagengroesse (kWp) ist
keine exakte kWh-Ertragsprognose moeglich, die Globalstrahlung reicht aber
aus, um Tage mit viel/wenig Sonne zu erkennen.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .. import request_log

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 10


class ForecastError(Exception):
    pass


@dataclass
class DayForecast:
    date: str
    radiation_kwh_m2: float
    sunshine_hours: float
    precipitation_mm: float
    sunrise: str
    sunset: str


@dataclass
class ForecastResult:
    days: list[DayForecast]
    utc_offset_seconds: int


async def get_forecast(lat: float, lon: float, days: int = 5) -> ForecastResult:
    if lat is None or lon is None:
        raise ForecastError("Keine Koordinaten (lat/lon) fuer den Forecast konfiguriert.")

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "shortwave_radiation_sum,sunshine_duration,precipitation_sum,sunrise,sunset",
        "timezone": "auto",
        "forecast_days": days,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(FORECAST_URL, params=params)
    except httpx.HTTPError as exc:
        raise ForecastError(f"Open-Meteo nicht erreichbar: {exc}") from exc

    request_log.record(method="GET", url=str(response.url), status=response.status_code, source="forecast")

    if response.status_code != 200:
        raise ForecastError(f"Open-Meteo Fehler ({response.status_code}): {response.text[:200]}")

    body = response.json()
    utc_offset_seconds = int(body.get("utc_offset_seconds", 0))
    daily = body.get("daily", {})
    dates = daily.get("time", [])
    radiation = daily.get("shortwave_radiation_sum", [])
    sunshine = daily.get("sunshine_duration", [])
    precipitation = daily.get("precipitation_sum", [])
    sunrises = daily.get("sunrise", [])
    sunsets = daily.get("sunset", [])

    result = []
    for i, date in enumerate(dates):
        rad_mj = radiation[i] if i < len(radiation) else 0.0
        sun_s = sunshine[i] if i < len(sunshine) else 0.0
        precip_mm = precipitation[i] if i < len(precipitation) else 0.0
        sunrise = sunrises[i] if i < len(sunrises) else ""
        sunset = sunsets[i] if i < len(sunsets) else ""
        result.append(
            DayForecast(
                date=date,
                radiation_kwh_m2=round((rad_mj or 0.0) / 3.6, 2),
                sunshine_hours=round((sun_s or 0.0) / 3600, 1),
                precipitation_mm=round(precip_mm or 0.0, 1),
                # ISO-Format "2026-08-21T06:12", passend zur Zeitzone durch
                # timezone=auto -- nur der Zeitanteil wird spaeter benoetigt.
                sunrise=sunrise[-5:] if sunrise else "",
                sunset=sunset[-5:] if sunset else "",
            )
        )
    return ForecastResult(days=result, utc_offset_seconds=utc_offset_seconds)
