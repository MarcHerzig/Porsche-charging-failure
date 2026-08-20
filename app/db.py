"""SQLite-Persistenz: Settings, Zugangsdaten (verschluesselt), Events, Laufzeit-State.

Bewusst synchron/sqlite3 gehalten (kein async-Treiber): alle Schreibzugriffe
sind selten (User-Aktion oder State-Uebergang), ein kurzer blockierender
Zugriff ist hier unproblematisch. Ein Lock schuetzt vor Ueberlappung
zwischen Scheduler-Loop und HTTP-Requests.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config
from . import crypto

_lock = threading.Lock()
_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def _init_schema() -> None:
    with _conn:
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mode TEXT NOT NULL DEFAULT 'smart',
                threshold_w REAL NOT NULL DEFAULT 1400,
                start_debounce_min INTEGER NOT NULL DEFAULT 3,
                stop_debounce_min INTEGER NOT NULL DEFAULT 8,
                curfew_enabled INTEGER NOT NULL DEFAULT 1,
                curfew_start TEXT NOT NULL DEFAULT '21:00',
                curfew_end TEXT NOT NULL DEFAULT '07:00',
                reboot_cooldown_min INTEGER NOT NULL DEFAULT 30,
                lat REAL,
                lon REAL
            );

            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                porsche_email TEXT,
                porsche_password_enc BLOB,
                porsche_vin TEXT,
                porsche_session_enc BLOB,
                easee_email TEXT,
                easee_password_enc BLOB,
                easee_charger_id TEXT,
                solar_base_url TEXT,
                solar_api_key_enc BLOB
            );

            CREATE TABLE IF NOT EXISTS runtime_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                charging_active INTEGER NOT NULL DEFAULT 0,
                condition_since TEXT,
                pending_target INTEGER,
                last_reboot_at TEXT,
                porsche_error_since TEXT,
                last_pv_watts REAL,
                last_porsche_status TEXT,
                last_checked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        _conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        _conn.execute("INSERT OR IGNORE INTO credentials (id) VALUES (1)")
        _conn.execute("INSERT OR IGNORE INTO runtime_state (id) VALUES (1)")


_init_schema()


@contextmanager
def _cursor() -> Iterator[sqlite3.Cursor]:
    with _lock, _conn:
        yield _conn.cursor()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings() -> dict[str, Any]:
    with _cursor() as cur:
        row = cur.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return dict(row)


def update_settings(fields: dict[str, Any]) -> None:
    if not fields:
        return
    allowed = {
        "mode",
        "threshold_w",
        "start_debounce_min",
        "stop_debounce_min",
        "curfew_enabled",
        "curfew_start",
        "curfew_end",
        "reboot_cooldown_min",
        "lat",
        "lon",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _cursor() as cur:
        cur.execute(f"UPDATE settings SET {set_clause} WHERE id = 1", list(fields.values()))


# ---------------------------------------------------------------------------
# Zugangsdaten (Secrets werden verschluesselt gespeichert)
# ---------------------------------------------------------------------------
def get_credentials(decrypted: bool = False) -> dict[str, Any]:
    with _cursor() as cur:
        row = dict(cur.execute("SELECT * FROM credentials WHERE id = 1").fetchone())
    if decrypted:
        row["porsche_password"] = crypto.decrypt(row.pop("porsche_password_enc"))
        row["porsche_session"] = crypto.decrypt(row.pop("porsche_session_enc"))
        row["easee_password"] = crypto.decrypt(row.pop("easee_password_enc"))
        row["solar_api_key"] = crypto.decrypt(row.pop("solar_api_key_enc"))
    else:
        for key in ("porsche_password_enc", "porsche_session_enc", "easee_password_enc", "solar_api_key_enc"):
            row[key] = bool(row.get(key))
    return row


def update_credentials(fields: dict[str, Any]) -> None:
    plain_allowed = {"porsche_email", "porsche_vin", "easee_email", "easee_charger_id", "solar_base_url"}
    secret_map = {
        "porsche_password": "porsche_password_enc",
        "porsche_session": "porsche_session_enc",
        "easee_password": "easee_password_enc",
        "solar_api_key": "solar_api_key_enc",
    }

    updates: dict[str, Any] = {}
    for k, v in fields.items():
        if k in plain_allowed:
            updates[k] = v
        elif k in secret_map and v is not None:
            updates[secret_map[k]] = crypto.encrypt(v)

    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _cursor() as cur:
        cur.execute(f"UPDATE credentials SET {set_clause} WHERE id = 1", list(updates.values()))


# ---------------------------------------------------------------------------
# Laufzeit-State (Debounce-Timer, letzter Reboot, etc. -- restart-sicher)
# ---------------------------------------------------------------------------
def get_runtime_state() -> dict[str, Any]:
    with _cursor() as cur:
        row = cur.execute("SELECT * FROM runtime_state WHERE id = 1").fetchone()
        return dict(row)


def update_runtime_state(fields: dict[str, Any]) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _cursor() as cur:
        cur.execute(f"UPDATE runtime_state SET {set_clause} WHERE id = 1", list(fields.values()))


# ---------------------------------------------------------------------------
# Events / Log
# ---------------------------------------------------------------------------
def add_event(event_type: str, message: str) -> None:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO events (ts, type, message) VALUES (?, ?, ?)",
            (now_iso(), event_type, message),
        )
        cur.execute(
            "DELETE FROM events WHERE id NOT IN ("
            "SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (config.MAX_LOG_EVENTS,),
        )


def get_events(limit: int = 15) -> list[dict[str, Any]]:
    with _cursor() as cur:
        rows = cur.execute(
            "SELECT ts, type, message FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
