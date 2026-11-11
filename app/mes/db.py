"""Conexão e migrações do banco do MES (SQLite dedicado: DATA_DIR/mes.db).

Fica separado do ai_history.db (usuários, conversas, cadastros RFID) porque
guarda séries temporais que crescem todo dia — ciclos e contagens.

- WAL: leituras da web não bloqueiam as escritas do coletor (e vice-versa)
- Migrações versionadas por PRAGMA user_version (ver schema.py)
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from app.mes.schema import MIGRATIONS

_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent.parent)
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "mes.db"

_init_lock = threading.Lock()
_schema_ok_para: Path | None = None


def agora_ms() -> int:
    return int(time.time() * 1000)


def usar_banco(path: Path | str) -> None:
    """Aponta o módulo para outro arquivo (testes / ferramentas)."""
    global DB_PATH, _schema_ok_para
    DB_PATH = Path(path)
    _schema_ok_para = None


def garantir_schema() -> None:
    """Aplica as migrações pendentes. Idempotente e barato depois da 1ª chamada."""
    global _schema_ok_para
    if _schema_ok_para == DB_PATH:
        return
    with _init_lock:
        if _schema_ok_para == DB_PATH:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
        try:
            c.execute("PRAGMA journal_mode = WAL")
            versao = c.execute("PRAGMA user_version").fetchone()[0]
            for numero, sql in enumerate(MIGRATIONS[versao:], start=versao + 1):
                # Tudo ou nada: se um comando falhar, o close() desfaz a transação
                c.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
        finally:
            c.close()
        _schema_ok_para = DB_PATH


def connect() -> sqlite3.Connection:
    garantir_schema()
    c = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 8000")
    c.execute("PRAGMA synchronous = NORMAL")
    return c


@contextmanager
def get_conn():
    """Conexão com commit no sucesso e rollback em qualquer erro."""
    c = connect()
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def versao_schema() -> int:
    with get_conn() as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def snapshot(destino: Path | str) -> Path:
    """Cópia consistente do banco (API de backup do SQLite), segura com o app rodando.

    O backup do host faz um tar do volume; copiar um banco em WAL no meio de uma
    escrita pode gerar arquivo inconsistente. Este snapshot é sempre íntegro.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    origem = connect()
    alvo = sqlite3.connect(str(tmp))
    try:
        origem.backup(alvo)
    finally:
        alvo.close()
        origem.close()
    os.replace(tmp, destino)
    return destino
