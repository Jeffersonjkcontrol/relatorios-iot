"""Configuração do MES, editável pelo gestor e persistida em mes.db (tabela config).

Fica no banco (e não no .env) porque muda em operação — ativar a coleta, trocar
o nome de uma variável — sem reiniciar o container.
"""
from __future__ import annotations

import os

from app.mes.db import agora_ms, get_conn

PADROES: dict[str, str] = {
    # --- coleta ---
    "ativo": "0",                    # MES ligado? (o gestor ativa explicitamente)
    "plataforma": "ubidots",
    "intervalo_s": "60",             # período do coletor
    "backfill_dias": "7",            # histórico trazido na ativação
    # --- nomes das variáveis na plataforma IoT ---
    "var_ciclo": "ciclo",            # tempo de ciclo; contexto traz o molde
    "var_contador": "producao",      # totalizador de peças
    "var_parada": "parada",          # 0/1
    "var_cavidade": "cavidade",      # cavidades configuradas no dispositivo
    "var_motivo": "maq_rfid",        # cartão do motivo de parada
    "var_funcionario": "fun_rfid",   # cartão do operador
    # --- regras ---
    "auto_cadastrar_moldes": "1",    # molde novo no contexto vira cadastro
    "auto_iniciar_op": "1",          # OP liberada inicia quando o molde dela começa a ciclar
    "ocioso_min": "3",               # sem ciclo há N minutos (e sem parada) = ociosa
}


def get(chave: str, default: str | None = None) -> str:
    with get_conn() as c:
        row = c.execute("SELECT valor FROM config WHERE chave = ?", (chave,)).fetchone()
    if row is not None:
        return row["valor"]
    if default is not None:
        return default
    return PADROES.get(chave, "")


def get_int(chave: str, default: int = 0) -> int:
    try:
        return int(float(get(chave)))
    except (TypeError, ValueError):
        return default


def get_bool(chave: str) -> bool:
    return get(chave).strip().lower() in ("1", "true", "sim", "on")


def set_(chave: str, valor) -> None:
    if isinstance(valor, bool):
        valor = "1" if valor else "0"
    with get_conn() as c:
        c.execute(
            "INSERT INTO config (chave, valor, atualizado_ms) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_ms = excluded.atualizado_ms",
            (chave, str(valor), agora_ms()),
        )


def todas() -> dict[str, str]:
    """Padrões sobrepostos pelo que está gravado."""
    out = dict(PADROES)
    with get_conn() as c:
        for row in c.execute("SELECT chave, valor FROM config"):
            out[row["chave"]] = row["valor"]
    return out


def coletor_permitido() -> bool:
    """Trava de emergência por variável de ambiente: MES_COLETOR=0 desliga a coleta
    mesmo com o MES ativo (útil em bancada / segunda instância do app)."""
    return os.environ.get("MES_COLETOR", "1").strip() not in ("0", "false", "off")


def mes_ativo() -> bool:
    return get_bool("ativo")
