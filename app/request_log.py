"""Ringpuffer fuer ausgehende API-Aufrufe (Easee/Porsche/Solar Manager/
Open-Meteo), damit im Log-Tab nachvollziehbar ist, welche Befehle die App
tatsaechlich rausgeschickt hat -- z.B. um zu sehen, ob ein 'stop_charging'
wirklich bei Easee ankam.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

MAX_ENTRIES = 60

_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)


def record(method: str, url: str, status: int | None, source: str) -> None:
    _entries.appendleft(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "url": url,
            "status": status,
            "source": source,
        }
    )


def get_entries(limit: int = 30) -> list[dict[str, Any]]:
    return list(_entries)[:limit]
