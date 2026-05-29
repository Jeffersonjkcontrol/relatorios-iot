"""Conhecimento de negócio editável pelo gestor.

Vai parar no system prompt da IA — assim ela responde com contexto da fábrica
(metas, limites, regras de turno, nomes de operadores, etc.).
"""
from __future__ import annotations

import time

from app.ai.db import get_conn


def _init():
    with get_conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS ai_knowledge (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """)


_init()


def _set(key: str, value: str):
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ai_knowledge (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )


def _get(key: str, default: str = "") -> str:
    with get_conn() as c:
        row = c.execute("SELECT value FROM ai_knowledge WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_business_rules() -> str:
    return _get("business_rules", "")


def set_business_rules(text: str):
    _set("business_rules", (text or "").strip())


def get_glossary() -> str:
    return _get("glossary", "")


def set_glossary(text: str):
    _set("glossary", (text or "").strip())


def get_oee_targets() -> str:
    return _get("oee_targets", "")


def set_oee_targets(text: str):
    _set("oee_targets", (text or "").strip())


def build_context_block() -> str:
    """Concatena tudo num bloco pronto para inserir no system prompt da IA."""
    rules = get_business_rules()
    glossary = get_glossary()
    targets = get_oee_targets()
    if not any([rules, glossary, targets]):
        return ""
    parts = []
    parts.append("═" * 60)
    parts.append("CONHECIMENTO ESPECÍFICO DA EMPRESA (use estas regras nas análises)")
    parts.append("═" * 60)
    if rules:
        parts.append("\n[REGRAS DE NEGÓCIO E PROCESSO]")
        parts.append(rules)
    if targets:
        parts.append("\n[METAS E LIMITES OPERACIONAIS]")
        parts.append(targets)
    if glossary:
        parts.append("\n[GLOSSÁRIO DE VARIÁVEIS E TERMOS]")
        parts.append(glossary)
    parts.append("═" * 60)
    return "\n".join(parts)
