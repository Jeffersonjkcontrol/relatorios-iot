"""Publicação de eventos de negócio para os webhooks (fila de saída).

`publicar` só ENFILEIRA, dentro da transação de quem gerou o evento: se a
transação desfizer, o evento some junto. A entrega HTTP é feita depois, pelo
despachante em integracao.py, com novas tentativas.
"""
from __future__ import annotations

import json
import sqlite3

from app.mes.db import agora_ms

# Catálogo (aparece na tela de Integrações)
EVENTOS: dict[str, str] = {
    "ordem.criada": "OP criada (manual, CSV ou ERP)",
    "ordem.liberada": "OP liberada para o chão de fábrica",
    "ordem.iniciada": "OP entrou em produção numa máquina",
    "ordem.pausada": "Execução da OP pausada",
    "ordem.concluida": "OP concluída",
    "ordem.cancelada": "OP cancelada",
    "ordem.meta_atingida": "Peças boas alcançaram a quantidade planejada",
    "refugo.apontado": "Refugo apontado",
    "parada.iniciada": "Máquina parou",
    "parada.encerrada": "Máquina voltou a produzir",
}


def _assina(eventos_csv: str, evento: str) -> bool:
    eventos_csv = (eventos_csv or "*").strip()
    if eventos_csv == "*":
        return True
    return evento in {e.strip() for e in eventos_csv.split(",")}


def publicar(c: sqlite3.Connection, evento: str, dados: dict) -> int:
    """Enfileira o evento para cada webhook ativo que o assina. Devolve quantos."""
    agora = agora_ms()
    payload = json.dumps({"evento": evento, "ts_ms": agora, "dados": dados},
                         ensure_ascii=False, default=str)
    n = 0
    for wh in c.execute("SELECT id, eventos FROM webhooks WHERE ativo = 1").fetchall():
        if not _assina(wh["eventos"], evento):
            continue
        c.execute(
            "INSERT INTO webhook_entregas (webhook_id, evento, payload, proxima_ms, criado_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (wh["id"], evento, payload, agora, agora),
        )
        n += 1
    return n
