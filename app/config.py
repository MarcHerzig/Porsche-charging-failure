"""Zentrale Pfade/Konfiguration, aus Umgebungsvariablen gelesen."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
ENCRYPTION_KEY_FILE = DATA_DIR / "encryption.key"

PORT = int(os.environ.get("PORT", "8000"))

# Polling-Intervalle
SOLAR_EASEE_POLL_SECONDS = 30
PORSCHE_POLL_SECONDS = 15 * 60
FORECAST_POLL_SECONDS = 6 * 60 * 60

MAX_LOG_EVENTS = 200
