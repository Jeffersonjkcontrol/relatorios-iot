"""Cadastro de códigos RFID: a legenda dos cartões de cada empresa.

Três tipos de cartão, cada um com sua lista de código -> nome:

    motivo       cartão de máquina (maq_rfid): evento/motivo de parada
    funcionario  cartão do operador (fun_rfid)
    cod_fun      cartão cod_fun_rfid (código de funcionário, numeração própria)

Traduz o código gravado no cartão — publicado pelo firmware — em nomes legíveis. É a "legenda" de cada empresa: os códigos e seus
significados mudam de fábrica para fábrica, então nada aqui é fixo no código.

Cada código de máquina tem uma CLASSE, que define como o MES trata o evento:

    parada     parada não planejada — conta contra a disponibilidade (padrão)
    setup      troca de molde / regulagem — perda de setup, mostrada à parte
    planejada  parada planejada (refeição, sem programação) — sai do tempo planejado
    producao   não é parada: o cartão que libera a máquina de volta à produção
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from app.ai.db import get_conn

VALID_TIPOS = {"motivo", "funcionario", "cod_fun"}
ROTULO_TIPO = {
    "motivo": "Código de parada", "funcionario": "Funcionário", "cod_fun": "Cód. funcionário (cod_fun)",
}

CLASSES: dict[str, str] = {
    "parada": "Parada não planejada",
    "setup": "Setup / troca de molde",
    "planejada": "Parada planejada",
    "producao": "Produção (liberação)",
}
# Cor sugerida por classe (o gestor pode trocar por código)
COR_DA_CLASSE: dict[str, str] = {
    "parada": "#ef4444", "setup": "#f59e0b", "planejada": "#a855f7", "producao": "#10b981",
}


@dataclass
class RfidCodigo:
    id: int
    tipo: str        # 'motivo' | 'funcionario' | 'cod_fun'
    codigo: str      # código do cartão, normalizado
    nome: str        # "Troca de molde" / "João Silva"
    categoria: str   # só motivos: mecânica, elétrica, material, setup...
    ativo: bool
    created_at: float
    classe: str = "parada"
    cor: str = ""

    @property
    def cor_efetiva(self) -> str:
        return self.cor or COR_DA_CLASSE.get(self.classe, "#ef4444")


def _normalize_codigo(codigo) -> str:
    """Normaliza o código do cartão para comparação consistente.

    - firmware pode publicar como float (12345.0) -> '12345'
    - cartões gravados por pessoas diferentes trazem '06' e '6' para o MESMO
      código (visto em campo) -> zeros à esquerda saem de códigos numéricos
    """
    s = str(codigo).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s


_DDL = """
    CREATE TABLE rfid_codigos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL CHECK(tipo IN ('motivo','funcionario','cod_fun')),
        codigo TEXT NOT NULL,
        nome TEXT NOT NULL,
        categoria TEXT DEFAULT '',
        ativo INTEGER DEFAULT 1,
        created_at REAL NOT NULL,
        classe TEXT NOT NULL DEFAULT 'parada',
        cor TEXT NOT NULL DEFAULT '',
        UNIQUE(tipo, codigo)
    )
"""


def _init():
    """Cria a tabela ou a atualiza em bancos já existentes (tudo numa transação)."""
    with get_conn() as c:
        c.execute("BEGIN")
        atual = c.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'rfid_codigos'").fetchone()
        if atual is None:
            c.execute(_DDL)
        elif "cod_fun" not in atual["sql"]:
            # Versão anterior: sem o tipo 'cod_fun' (o CHECK não pode ser alterado no
            # SQLite) e talvez sem classe/cor. Recria preservando os registros.
            colunas = {r["name"] for r in c.execute("PRAGMA table_info(rfid_codigos)")}
            classe = "classe" if "classe" in colunas else "'parada'"
            cor = "cor" if "cor" in colunas else "''"
            c.execute("ALTER TABLE rfid_codigos RENAME TO rfid_codigos_antiga")
            c.execute(_DDL)
            c.execute(
                "INSERT INTO rfid_codigos (id, tipo, codigo, nome, categoria, ativo, created_at, classe, cor) "
                f"SELECT id, tipo, codigo, nome, COALESCE(categoria, ''), ativo, created_at, {classe}, {cor} "
                "FROM rfid_codigos_antiga")
            c.execute("DROP TABLE rfid_codigos_antiga")
        # Códigos cadastrados antes da normalização de zeros à esquerda
        for row in c.execute("SELECT id, tipo, codigo FROM rfid_codigos").fetchall():
            normal = _normalize_codigo(row["codigo"])
            if normal == row["codigo"]:
                continue
            try:
                c.execute("UPDATE rfid_codigos SET codigo = ? WHERE id = ?", (normal, row["id"]))
            except sqlite3.IntegrityError:
                c.execute("DELETE FROM rfid_codigos WHERE id = ?", (row["id"],))   # duplicata de '6' x '06'


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
        classe=row["classe"] or "parada",
        cor=row["cor"] or "",
    )


def _validar_classe(classe: str | None) -> str:
    classe = (classe or "parada").strip().lower()
    if classe not in CLASSES:
        raise ValueError(f"Tipo de evento inválido: '{classe}'. Use: {', '.join(CLASSES)}.")
    return classe


def _validar_cor(cor: str | None) -> str:
    cor = (cor or "").strip()
    if not cor:
        return ""
    if len(cor) != 7 or cor[0] != "#" or any(ch not in "0123456789abcdefABCDEF" for ch in cor[1:]):
        raise ValueError("Cor inválida: use o formato #RRGGBB.")
    return cor.lower()


def list_codigos(tipo: str | None = None) -> list[RfidCodigo]:
    with get_conn() as c:
        if tipo:
            rows = c.execute(
                "SELECT * FROM rfid_codigos WHERE tipo = ? ORDER BY nome", (tipo,)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM rfid_codigos ORDER BY tipo, nome").fetchall()
    return [_row_to_codigo(r) for r in rows]


def create_codigo(tipo: str, codigo: str, nome: str, categoria: str = "",
                  classe: str = "parada", cor: str = "") -> RfidCodigo:
    if tipo not in VALID_TIPOS:
        raise ValueError(f"Tipo inválido: {tipo}. Use 'motivo', 'funcionario' ou 'cod_fun'.")
    codigo = _normalize_codigo(codigo)
    nome = (nome or "").strip()
    if not codigo:
        raise ValueError("Código não pode ser vazio")
    if not nome or len(nome) < 2:
        raise ValueError("Nome deve ter pelo menos 2 caracteres")
    if len(nome) > 100:
        raise ValueError("Nome muito longo (máx 100)")
    categoria = (categoria or "").strip()[:50]
    classe = _validar_classe(classe) if tipo == "motivo" else "parada"
    cor = _validar_cor(cor)
    with get_conn() as c:
        existing = c.execute(
            "SELECT id FROM rfid_codigos WHERE tipo = ? AND codigo = ?", (tipo, codigo)
        ).fetchone()
        if existing:
            raise ValueError(f"Código '{codigo}' já cadastrado para esse tipo")
        cur = c.execute(
            "INSERT INTO rfid_codigos (tipo, codigo, nome, categoria, ativo, created_at, classe, cor) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (tipo, codigo, nome, categoria, time.time(), classe, cor),
        )
        new_id = cur.lastrowid
        row = c.execute("SELECT * FROM rfid_codigos WHERE id = ?", (new_id,)).fetchone()
    return _row_to_codigo(row)


def update_codigo(cid: int, nome: str | None = None, categoria: str | None = None,
                  ativo: bool | None = None, classe: str | None = None, cor: str | None = None):
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
    if classe is not None:
        sets.append("classe = ?")
        vals.append(_validar_classe(classe))
    if cor is not None:
        sets.append("cor = ?")
        vals.append(_validar_cor(cor))
    if not sets:
        return
    vals.append(cid)
    with get_conn() as c:
        c.execute(f"UPDATE rfid_codigos SET {', '.join(sets)} WHERE id = ?", vals)


def delete_codigo(cid: int):
    with get_conn() as c:
        c.execute("DELETE FROM rfid_codigos WHERE id = ?", (cid,))


def get_codigo(cid: int) -> RfidCodigo | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM rfid_codigos WHERE id = ?", (cid,)).fetchone()
    return _row_to_codigo(row) if row else None


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


def motivos_info() -> dict[str, dict]:
    """Legenda completa dos eventos de máquina: codigo -> {nome, categoria, classe, cor}."""
    return {
        m.codigo: {"nome": m.nome, "categoria": m.categoria, "classe": m.classe, "cor": m.cor_efetiva}
        for m in list_codigos("motivo") if m.ativo
    }


def codigos_da_classe(classe: str) -> set[str]:
    """Códigos (ativos) de uma classe — ex.: os cartões de 'producao' (liberação)."""
    return {codigo for codigo, info in motivos_info().items() if info["classe"] == classe}
