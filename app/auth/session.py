"""Sessão via cookie HTTPOnly assinado (itsdangerous).
Cookies duram 7 dias, renovados a cada request."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Cookie, HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.auth.db import User, get_user_by_username

COOKIE_NAME = "session"
COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 dias

# Secret pra assinar cookies. Em produção, defina SESSION_SECRET no .env
SECRET = os.environ.get("SESSION_SECRET") or "dev-only-secret-change-in-prod-32chars-min"
_serializer = URLSafeTimedSerializer(SECRET, salt="session-v1")


def create_session_cookie(username: str) -> str:
    return _serializer.dumps({"u": username})


def decode_session_cookie(token: str) -> Optional[str]:
    """Retorna o username da sessão, ou None se inválido/expirado."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=COOKIE_MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


def current_user(request: Request) -> Optional[User]:
    """FastAPI dependency: devolve User logado ou None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    username = decode_session_cookie(token)
    if not username:
        return None
    res = get_user_by_username(username)
    return res[0] if res else None


def require_user(request: Request) -> User:
    """Dependency: 401 se não autenticado."""
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Login necessário")
    return u


def require_gestor(request: Request) -> User:
    """Dependency: 403 se não for gestor."""
    u = require_user(request)
    if u.role != "gestor":
        raise HTTPException(status_code=403, detail="Permissão de gestor necessária")
    return u
