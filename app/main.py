from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import db, scheduler
from .integrations import easee_client, porsche_client, solar_client
from .integrations.easee_client import EaseeError
from .integrations.geocode_client import GeocodeError, search as geocode_search

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Porsche Solar Charge Guard")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Cache-Busting: haengt an /static-URLs eine pro Prozess-Start feste Version
# an, damit ein Deploy nicht hinter einem CDN/Browser-Cache (z.B. Cloudflare)
# haengen bleibt, ohne dass jemand manuell einen Cache-Purge ausloesen muss.
templates.env.globals["asset_version"] = str(int(time.time()))


@app.on_event("startup")
async def _startup() -> None:
    scheduler.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    scheduler.stop()
    await easee_client.close()
    await porsche_client.close()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/live")
async def get_live():
    return scheduler.LIVE


@app.get("/api/settings")
async def get_settings():
    return db.get_settings()


class SettingsUpdate(BaseModel):
    mode: str | None = None
    threshold_w: float | None = None
    start_debounce_min: int | None = None
    stop_debounce_min: int | None = None
    curfew_enabled: bool | None = None
    curfew_start: str | None = None
    curfew_end: str | None = None
    reboot_cooldown_min: int | None = None
    lat: float | None = None
    lon: float | None = None
    location_name: str | None = None


@app.post("/api/settings")
async def update_settings(payload: SettingsUpdate):
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "curfew_enabled" in fields:
        fields["curfew_enabled"] = int(fields["curfew_enabled"])
    db.update_settings(fields)
    return db.get_settings()


@app.get("/api/credentials")
async def get_credentials():
    return db.get_credentials(decrypted=False)


class CredentialsUpdate(BaseModel):
    porsche_email: str | None = None
    porsche_password: str | None = None
    porsche_vin: str | None = None
    porsche_session: str | None = None
    easee_email: str | None = None
    easee_password: str | None = None
    easee_charger_id: str | None = None
    solar_manager_id: str | None = None
    solar_api_key: str | None = None


@app.post("/api/credentials")
async def update_credentials(payload: CredentialsUpdate):
    fields = {k: v for k, v in payload.model_dump().items() if v is not None and v != ""}
    if "porsche_session" in fields:
        try:
            json.loads(fields["porsche_session"])
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Session-Token ist kein gueltiges JSON: {exc}") from exc
    db.update_credentials(fields)
    db.add_event("credentials_updated", "Zugangsdaten aktualisiert")
    # Sofort einen Check ausloesen, statt auf den naechsten Poll (bis zu 15
    # Min bei Porsche) zu warten -- das Dashboard soll nach dem Speichern
    # zeitnah aktuelle Werte zeigen.
    asyncio.create_task(scheduler.refresh_solar_easee())
    asyncio.create_task(scheduler.refresh_porsche())
    return db.get_credentials(decrypted=False)


class GeocodeQuery(BaseModel):
    query: str


@app.post("/api/geocode")
async def geocode(payload: GeocodeQuery):
    try:
        result = await geocode_search(payload.query)
    except GeocodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.update_settings({"lat": result.lat, "lon": result.lon, "location_name": result.display_name})
    await scheduler.refresh_forecast()
    return {"display_name": result.display_name, "lat": result.lat, "lon": result.lon}


@app.post("/api/test/solar")
async def test_solar():
    creds = db.get_credentials(decrypted=True)
    try:
        await solar_client.get_current_point(creds["solar_manager_id"], creds["solar_api_key"])
        return {"ok": True}
    except solar_client.SolarManagerError as exc:
        return {"ok": False, "detail": str(exc)}


@app.post("/api/test/easee")
async def test_easee():
    creds = db.get_credentials(decrypted=True)
    try:
        await easee_client.get_state(creds["easee_email"], creds["easee_password"], creds["easee_charger_id"])
        return {"ok": True}
    except easee_client.EaseeError as exc:
        return {"ok": False, "detail": str(exc)}


@app.post("/api/test/porsche")
async def test_porsche():
    creds = db.get_credentials(decrypted=True)
    try:
        status = await porsche_client.check_status(
            creds["porsche_email"], creds["porsche_password"], creds["porsche_session"], creds["porsche_vin"]
        )
        db.update_credentials({"porsche_session": status.session_json})
        return {"ok": True}
    except porsche_client.PorscheError as exc:
        return {"ok": False, "detail": str(exc)}


@app.get("/api/porsche/captcha")
async def get_porsche_captcha():
    return {"image": porsche_client.get_pending_captcha_image()}


class CaptchaSubmit(BaseModel):
    code: str


@app.post("/api/porsche/captcha")
async def submit_porsche_captcha(payload: CaptchaSubmit):
    creds = db.get_credentials(decrypted=True)
    try:
        status = await porsche_client.submit_captcha(payload.code, creds["porsche_vin"])
    except porsche_client.PorscheCaptchaNeeded as exc:
        return {"ok": False, "captcha_needed": True, "image": exc.image}
    except porsche_client.PorscheError as exc:
        return {"ok": False, "captcha_needed": False, "detail": str(exc)}

    db.update_credentials({"porsche_session": status.session_json})
    scheduler.LIVE.update(
        porsche_status=status.status,
        porsche_battery=status.battery_percent,
        porsche_error=None,
        porsche_connected=True,
        porsche_captcha_pending=False,
    )
    db.add_event("porsche_captcha_solved", "Captcha geloest, Porsche-Login erfolgreich")
    return {"ok": True}


@app.get("/api/log")
async def get_log(limit: int = 15):
    return db.get_events(limit=limit)


@app.post("/api/refresh")
async def refresh_live():
    await asyncio.gather(scheduler.refresh_solar_easee(), scheduler.refresh_porsche())
    return {"ok": True}


@app.post("/api/reboot")
async def reboot():
    try:
        await scheduler.manual_reboot()
    except EaseeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}
