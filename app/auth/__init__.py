"""Autenticação e autorização do app.

Roles:
- gestor: acesso total (config, criação de usuários, todas as páginas)
- operador: leitura (relatórios, OEE, IA, dashboards). Sem config nem users.
"""
from app.auth.db import User, get_user_by_username, list_users, create_user, delete_user, change_password, count_users, init_default_admin
from app.auth.session import (
    create_session_cookie, decode_session_cookie, COOKIE_NAME,
    current_user, require_user, require_gestor,
)

__all__ = [
    "User", "get_user_by_username", "list_users", "create_user", "delete_user",
    "change_password", "count_users", "init_default_admin",
    "create_session_cookie", "decode_session_cookie", "COOKIE_NAME",
    "current_user", "require_user", "require_gestor",
]
