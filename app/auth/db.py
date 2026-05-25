"""Tabela de usuários no mesmo SQLite do AI (ai_history.db)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import bcrypt

from app.ai.db import get_conn  # mesmo banco

VALID_ROLES = {"gestor", "operador"}


@dataclass
class User:
    id: int
    username: str
    role: str  # 'gestor' | 'operador'
    must_change_password: bool
    created_at: float


def _init():
    with get_conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('gestor','operador')),
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
        """)


def _row_to_user(row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        must_change_password=bool(row["must_change_password"]),
        created_at=row["created_at"],
    )


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_user_by_username(username: str) -> tuple[User, str] | None:
    """Devolve (User, password_hash) ou None."""
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        return None
    return _row_to_user(row), row["password_hash"]


def list_users() -> list[User]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [_row_to_user(r) for r in rows]


def count_users() -> int:
    with get_conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return row["n"]


def create_user(username: str, password: str, role: str, must_change_password: bool = False) -> User:
    if role not in VALID_ROLES:
        raise ValueError(f"Role inválido: {role}. Use {VALID_ROLES}")
    username = username.strip().lower()
    if not username or len(username) < 3:
        raise ValueError("Nome de usuário deve ter pelo menos 3 caracteres")
    if not password or len(password) < 4:
        raise ValueError("Senha deve ter pelo menos 4 caracteres")
    with get_conn() as c:
        existing = c.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise ValueError(f"Usuário '{username}' já existe")
        c.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), role, int(must_change_password), time.time()),
        )
    out = get_user_by_username(username)
    if not out:
        raise RuntimeError("Falha ao criar usuário")
    return out[0]


def delete_user(user_id: int):
    with get_conn() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))


def change_password(user_id: int, new_password: str):
    if not new_password or len(new_password) < 4:
        raise ValueError("Senha deve ter pelo menos 4 caracteres")
    with get_conn() as c:
        c.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(new_password), user_id),
        )


def set_role(user_id: int, role: str):
    if role not in VALID_ROLES:
        raise ValueError(f"Role inválido: {role}")
    with get_conn() as c:
        c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def init_default_admin():
    """Cria admin/admin se nenhum usuário existir ainda. Força troca de senha no 1o login."""
    _init()
    if count_users() == 0:
        create_user("admin", "admin", role="gestor", must_change_password=True)


# Inicializa na importação
init_default_admin()
