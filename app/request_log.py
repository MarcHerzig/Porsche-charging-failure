"""Ringpuffer fuer ausgehende Befehle (POST/PUT/DELETE) an Easee/Porsche/
Solar Manager, damit im Log-Tab nachvollziehbar ist, welche Aktionen die
App tatsaechlich rausgeschickt hat -- z.B. um zu sehen, ob ein
'stop_charging' wirklich bei Easee ankam und was die Antwort war.

Bewusst nur nicht-GET-Aufrufe: die alle 30s laufenden Status-Abfragen
(GET .../state usw.) wuerden den Puffer sonst binnen Minuten fluten und
genau die selten gesendeten Befehle wieder rausdraengen.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

MAX_ENTRIES = 60

_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)


def record(method: str, url: str, status: int | None, source: str) -> None:
    if method.upper() == "GET":
        return
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
