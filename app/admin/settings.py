"""Modo manutenção: flag persistida no SQLite (mesma base do AI/users)."""
from __future__ import annotations

import time

from app.ai.db import get_conn


def _init():
    with get_conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """)


_init()


def _set(key: str, value: str):
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO admin_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )


def _get(key: str, default: str = "") -> str:
    with get_conn() as c:
        row = c.execute("SELECT value FROM admin_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def is_maintenance_mode() -> bool:
    return _get("maintenance_mode", "0") == "1"


def set_maintenance_mode(active: bool, message: str = ""):
    _set("maintenance_mode", "1" if active else "0")
    if message or not active:
        _set("maintenance_msg", message)


def get_maintenance_msg() -> str:
    return _get("maintenance_msg", "Sistema em manutenção. Volte em alguns minutos.")


def get_session_max_age_days() -> int:
    try:
        return int(_get("session_max_age_days", "7"))
    except ValueError:
        return 7


def set_session_max_age_days(days: int):
    if days < 1 or days > 365:
        raise ValueError("Use entre 1 e 365 dias")
    _set("session_max_age_days", str(days))
