"""Cartões RFID das máquinas: o que gravar em cada bloco.

O firmware das injetoras (JKControl V12.x, RFID.ino) lê um cartão MIFARE
Classic 1K assim:

    bloco 1          tipo do cartão       "op_rfid", "fun_rfid", "maq_rfid", "quali_rfid"
    blocos 2, 4, 5   conteúdo, emendados  "112729", "38", "10", "aprovado"

Cada bloco tem 16 bytes; o firmware descarta TODOS os espaços e zeros (não só os
das pontas) e monta o contexto "\\"tipo\\":\\"conteúdo\\"". Daí as regras:
- conteúdo sem espaço, aspas ou barra invertida (quebrariam o contexto);
- os blocos 4 e 5 são SEMPRE regravados em branco — se o cartão já foi de outro
  tipo, sobra de conteúdo antigo seria emendada ao novo.

Os blocos 3, 7, 11... guardam as chaves do setor: gravar neles inutiliza o
cartão. O agente recusa qualquer bloco fora da lista abaixo.
"""
from __future__ import annotations

import re

from app.cadastros import _normalize_codigo

BLOCOS_GRAVAVEIS = (1, 2, 4, 5)
TAMANHO_BLOCO = 16

TIPOS: dict[str, dict] = {
    "op": {"rotulo": "Ordem de produção", "bloco1": "op_rfid"},
    "funcionario": {"rotulo": "Funcionário", "bloco1": "fun_rfid"},
    "parada": {"rotulo": "Código de parada", "bloco1": "maq_rfid"},
    "qualidade": {"rotulo": "Qualidade", "bloco1": "quali_rfid"},
}
QUALIDADE = ("aprovado", "reprovado")
POR_BLOCO1 = {t["bloco1"]: k for k, t in TIPOS.items()}

# Códigos de cartão de máquina com efeito especial no firmware V12.23
AVISOS_PARADA = {
    "10": "O código 10 BLOQUEIA a contagem de peças na máquina até ser batido outro cartão de liberação.",
    "24": "O código 24 BLOQUEIA a contagem de peças na máquina até ser batido outro cartão de liberação.",
    "13": "O código 13 não libera o relé da máquina.",
}

_PERMITIDOS = re.compile(r"^[A-Za-z0-9._/-]+$")


def montar(tipo: str, conteudo: str) -> dict:
    """Valida e devolve {blocos: {n: texto}, conteudo, avisos}."""
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de cartão inválido: '{tipo}'.")
    bruto = str(conteudo or "").strip()
    if tipo == "qualidade":
        conteudo = bruto.lower()
        if conteudo not in QUALIDADE:
            raise ValueError("Qualidade: escolha 'aprovado' ou 'reprovado'.")
    elif tipo in ("op", "parada"):
        if not bruto.isdigit():
            raise ValueError(f"{TIPOS[tipo]['rotulo']}: use só números.")
        conteudo = _normalize_codigo(bruto) if tipo == "parada" else bruto
    else:
        if not bruto:
            raise ValueError("Informe o código do funcionário.")
        if not _PERMITIDOS.match(bruto):
            raise ValueError("Código do funcionário: use letras, números, ponto, hífen ou barra — sem espaços.")
        conteudo = _normalize_codigo(bruto)
    if len(conteudo) > TAMANHO_BLOCO:
        raise ValueError(f"Conteúdo com {len(conteudo)} caracteres; o máximo é {TAMANHO_BLOCO}.")
    avisos = [AVISOS_PARADA[conteudo]] if tipo == "parada" and conteudo in AVISOS_PARADA else []
    return {
        "tipo": tipo, "conteudo": conteudo, "avisos": avisos,
        "blocos": {"1": TIPOS[tipo]["bloco1"], "2": conteudo, "4": "", "5": ""},
    }


def interpretar(blocos: dict) -> dict:
    """Lê o que veio do cartão do mesmo jeito que o firmware: sem espaços, b2+b4+b5."""
    def limpo(n):
        return re.sub(r"[\s\x00]", "", str(blocos.get(str(n)) or blocos.get(n) or ""))
    b1 = limpo(1)
    conteudo = limpo(2) + limpo(4) + limpo(5)
    tipo = POR_BLOCO1.get(b1)
    return {"bloco1": b1, "conteudo": conteudo, "tipo": tipo,
            "rotulo": TIPOS[tipo]["rotulo"] if tipo else ("Cartão vazio" if not b1 else f"Tipo desconhecido ({b1})")}


# ------------------------------------------------------------------ histórico
def registrar(tipo: str, conteudo: str, uid: str, nome: str = "", usuario: str = "") -> int:
    from app.mes.db import agora_ms, get_conn
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de cartão inválido: '{tipo}'.")
    with get_conn() as c:
        return c.execute(
            "INSERT INTO cartoes_gravados (ts_ms, uid, tipo, conteudo, nome, usuario) VALUES (?, ?, ?, ?, ?, ?)",
            (agora_ms(), str(uid or "")[:32], tipo, str(conteudo)[:40], str(nome or "")[:100], usuario or ""),
        ).lastrowid


def historico(uid: str = "", limite: int = 100) -> list[dict]:
    from app.mes.db import get_conn
    sql, args = "SELECT * FROM cartoes_gravados", []
    if uid:
        sql += " WHERE uid = ?"
        args.append(uid.strip().upper())
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limite])]
