"""SQLite local para historico de conversas da pagina /ai."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

# Em Docker, monte um volume em /app/data e defina DATA_DIR=/app/data
_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent.parent)
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "ai_history.db"


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


@contextmanager
def get_conn():
    c = _conn()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,         -- 'user' | 'assistant' | 'tool'
            content TEXT NOT NULL,       -- JSON: pode ser string ou lista de blocks
            created_at REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
        CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
        """)


def create_conversation(title: str, provider: str, model: str) -> str:
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    with get_conn() as c:
        c.execute(
            "INSERT INTO conversations (id, title, provider, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cid, title[:80], provider, model, now, now),
        )
    return cid


def touch_conversation(cid: str, title: str | None = None):
    with get_conn() as c:
        if title:
            c.execute("UPDATE conversations SET updated_at = ?, title = ? WHERE id = ?", (time.time(), title[:80], cid))
        else:
            c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), cid))


def add_message(cid: str, role: str, content):
    payload = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    with get_conn() as c:
        c.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (cid, role, payload, time.time()),
        )
    touch_conversation(cid)


def list_conversations(limit: int = 50) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, title, provider, model, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str) -> dict | None:
    with get_conn() as c:
        conv = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if not conv:
            return None
        msgs = c.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (cid,),
        ).fetchall()
    messages = []
    for m in msgs:
        content = m["content"]
        try:
            parsed = json.loads(content)
            messages.append({"role": m["role"], "content": parsed, "created_at": m["created_at"]})
        except (json.JSONDecodeError, TypeError):
            messages.append({"role": m["role"], "content": content, "created_at": m["created_at"]})
    return {**dict(conv), "messages": messages}


def delete_conversation(cid: str):
    with get_conn() as c:
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
        c.execute("DELETE FROM conversations WHERE id = ?", (cid,))


# Inicializa na importacao
init_db()
