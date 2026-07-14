"""Cadastro de códigos RFID: motivos de parada e funcionários.

Traduz o valor bruto do cartão RFID (publicado pelo firmware nas variáveis
maq_rfid / fun_rfid) em nomes legíveis para as análises.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.ai.db import get_conn

VALID_TIPOS = {"motivo", "funcionario"}


@dataclass
class RfidCodigo:
    id: int
    tipo: str        # 'motivo' | 'funcionario'
    codigo: str      # valor RFID como string
    nome: str        # "Falta de material" / "João Silva"
    categoria: str   # só motivos: mecânica, elétrica, material, setup...
    ativo: bool
    created_at: float


def _init():
    with get_conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS rfid_codigos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL CHECK(tipo IN ('motivo','funcionario')),
            codigo TEXT NOT NULL,
            nome TEXT NOT NULL,
            categoria TEXT DEFAULT '',
            ativo INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            UNIQUE(tipo, codigo)
        )
        """)


_init()


def _row_to_codigo(row) -> RfidCodigo:
    return RfidCodigo(
        id=row["id"],
        tipo=row["tipo"],
        codigo=row["codigo"],
        nome=row["nome"],
        categoria=row["categoria"] or "",
        ativo=bool(row["ativo"]),
        created_at=row["created_at"],
    )


def _normalize_codigo(codigo: str) -> str:
    """Normaliza o código RFID pra comparação consistente.

    Firmware pode publicar como float (12345.0) e o cadastro como '12345' —
    remove '.0' final e espaços."""
    s = str(codigo).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def list_codigos(tipo: str | None = None) -> list[RfidCodigo]:
    with get_conn() as c:
        if tipo:
            rows = c.execute(
                "SELECT * FROM rfid_codigos WHERE tipo = ? ORDER BY nome", (tipo,)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM rfid_codigos ORDER BY tipo, nome").fetchall()
    return [_row_to_codigo(r) for r in rows]


def create_codigo(tipo: str, codigo: str, nome: str, categoria: str = "") -> RfidCodigo:
    if tipo not in VALID_TIPOS:
        raise ValueError(f"Tipo inválido: {tipo}. Use 'motivo' ou 'funcionario'.")
    codigo = _normalize_codigo(codigo)
    nome = (nome or "").strip()
    if not codigo:
        raise ValueError("Código não pode ser vazio")
    if not nome or len(nome) < 2:
        raise ValueError("Nome deve ter pelo menos 2 caracteres")
    if len(nome) > 100:
        raise ValueError("Nome muito longo (máx 100)")
    categoria = (categoria or "").strip()[:50]
    with get_conn() as c:
        existing = c.execute(
            "SELECT id FROM rfid_codigos WHERE tipo = ? AND codigo = ?", (tipo, codigo)
        ).fetchone()
        if existing:
            raise ValueError(f"Código '{codigo}' já cadastrado para esse tipo")
        cur = c.execute(
            "INSERT INTO rfid_codigos (tipo, codigo, nome, categoria, ativo, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (tipo, codigo, nome, categoria, time.time()),
        )
        new_id = cur.lastrowid
        row = c.execute("SELECT * FROM rfid_codigos WHERE id = ?", (new_id,)).fetchone()
    return _row_to_codigo(row)


def update_codigo(cid: int, nome: str | None = None, categoria: str | None = None,
                   ativo: bool | None = None):
    sets, vals = [], []
    if nome is not None:
        nome = nome.strip()
        if not nome or len(nome) < 2:
            raise ValueError("Nome deve ter pelo menos 2 caracteres")
        sets.append("nome = ?")
        vals.append(nome[:100])
    if categoria is not None:
        sets.append("categoria = ?")
        vals.append(categoria.strip()[:50])
    if ativo is not None:
        sets.append("ativo = ?")
        vals.append(1 if ativo else 0)
    if not sets:
        return
    vals.append(cid)
    with get_conn() as c:
        c.execute(f"UPDATE rfid_codigos SET {', '.join(sets)} WHERE id = ?", vals)


def delete_codigo(cid: int):
    with get_conn() as c:
        c.execute("DELETE FROM rfid_codigos WHERE id = ?", (cid,))


def resolve_map(tipo: str) -> dict[str, str]:
    """Mapa codigo->nome (só ativos) pra lookup em massa nas análises."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT codigo, nome FROM rfid_codigos WHERE tipo = ? AND ativo = 1", (tipo,)
        ).fetchall()
    return {r["codigo"]: r["nome"] for r in rows}


def categoria_map() -> dict[str, str]:
    """Mapa codigo->categoria (só motivos ativos)."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT codigo, categoria FROM rfid_codigos WHERE tipo = 'motivo' AND ativo = 1"
        ).fetchall()
    return {r["codigo"]: (r["categoria"] or "") for r in rows}
