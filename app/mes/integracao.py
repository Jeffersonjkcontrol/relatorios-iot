"""Integração com ERP: chaves de API, importação de OPs por CSV e webhooks.

Três caminhos, do mais simples ao mais automático:
- CSV: o PCP exporta as OPs do ERP e cola/importa aqui.
- API (/api/v1): o ERP envia OPs e lê os apontamentos, autenticado por chave.
- Webhooks: o MES avisa o ERP quando algo acontece (OP iniciada, concluída...).

A OP é sempre identificada pelo NÚMERO. Como o cartão da máquina usa o mesmo
número, importar uma OP que já nasceu do cartão só completa o cadastro dela.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import logging
import secrets

import httpx

from app.mes import cadastros, eventos, ordens, tempo
from app.mes.db import agora_ms, get_conn

log = logging.getLogger("mes.integracao")

PREFIXO_CHAVE = "mes_"
ESPERAS_S = (60, 300, 1800, 7200, 21600)       # novas tentativas de webhook: 1 min ... 6 h
COLUNAS_CSV = ("numero", "produto_codigo", "produto_descricao", "quantidade", "maquina", "molde_codigo",
               "ciclo_padrao_s", "cavidades", "data_inicio_prev", "data_fim_prev", "cliente", "pedido",
               "observacao", "liberar")
APELIDOS = {"op": "numero", "ordem": "numero", "produto": "produto_codigo", "codigo": "produto_codigo",
            "descricao": "produto_descricao", "qtd": "quantidade", "qtde": "quantidade",
            "injetora": "maquina", "molde": "molde_codigo", "ciclo": "ciclo_padrao_s",
            "data_inicio": "data_inicio_prev", "inicio": "data_inicio_prev",
            "data_fim": "data_fim_prev", "fim": "data_fim_prev", "entrega": "data_fim_prev",
            "obs": "observacao"}


def registrar(direcao: str, canal: str, recurso: str, resumo: str, ok: bool = True, autor: str = "") -> None:
    with get_conn() as c:
        c.execute("INSERT INTO integracao_log (ts_ms, direcao, canal, recurso, resumo, ok, autor) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (agora_ms(), direcao, canal, recurso[:80], resumo[:300], 1 if ok else 0, autor[:60]))


def historico(limite: int = 50) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM integracao_log ORDER BY id DESC LIMIT ?", (limite,))]


# ------------------------------------------------------------- chaves de API
def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def criar_chave(nome: str, escopo: str = "rw", usuario: str = "") -> dict:
    """Gera uma chave. O valor completo só existe aqui — o banco guarda o hash."""
    nome = cadastros._texto(nome, "Nome", 2, 60)
    if escopo not in ("ro", "rw"):
        raise ValueError("Permissão: use 'ro' (só leitura) ou 'rw' (leitura e escrita).")
    chave = PREFIXO_CHAVE + secrets.token_urlsafe(32)
    with get_conn() as c:
        cid = c.execute(
            "INSERT INTO api_keys (nome, prefixo, hash, escopo, criado_por, criado_ms) VALUES (?, ?, ?, ?, ?, ?)",
            (nome, chave[:10], _hash(chave), escopo, usuario or "", agora_ms())).lastrowid
    return {"id": cid, "nome": nome, "escopo": escopo, "chave": chave}


def listar_chaves() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, nome, prefixo, escopo, ativo, criado_por, criado_ms, ultimo_uso_ms FROM api_keys "
            "ORDER BY ativo DESC, id DESC")]


def revogar_chave(chave_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE api_keys SET ativo = 0 WHERE id = ?", (chave_id,))


def validar_chave(chave: str) -> dict | None:
    if not chave or not chave.startswith(PREFIXO_CHAVE):
        return None
    with get_conn() as c:
        row = c.execute("SELECT id, nome, escopo FROM api_keys WHERE hash = ? AND ativo = 1",
                        (_hash(chave),)).fetchone()
        if not row:
            return None
        c.execute("UPDATE api_keys SET ultimo_uso_ms = ? WHERE id = ?", (agora_ms(), row["id"]))
    return dict(row)


# ------------------------------------------------------------- OPs por CSV
def importar_ordens_csv(texto: str, autor: str = "") -> dict:
    """Importa OPs de um CSV com cabeçalho (separador ; , ou tab).

    Colunas obrigatórias: numero, produto_codigo, quantidade. As demais são
    opcionais. Cada linha cria a OP ou atualiza a que já tem esse número.
    """
    texto = (texto or "").lstrip("﻿").strip()
    if not texto:
        raise ValueError("Arquivo vazio.")
    cabecalho = texto.splitlines()[0]
    sep = max(";\t,", key=cabecalho.count)
    linhas = list(csv.reader(io.StringIO(texto), delimiter=sep))
    nomes = [APELIDOS.get(n.strip().lower(), n.strip().lower()) for n in linhas[0]]
    faltam = [c for c in ("numero", "produto_codigo", "quantidade") if c not in nomes]
    if faltam:
        raise ValueError(f"Faltam colunas no cabeçalho: {', '.join(faltam)}. "
                         f"Colunas aceitas: {', '.join(COLUNAS_CSV)}.")
    criadas = atualizadas = 0
    erros: list[str] = []
    for n, valores in enumerate(linhas[1:], start=2):
        if not any(v.strip() for v in valores):
            continue
        dados = {k: v.strip() for k, v in zip(nomes, valores) if k in COLUNAS_CSV and v.strip() != ""}
        try:
            _, criada = ordens.upsert_externo(dados, "csv", autor)
            criadas += criada
            atualizadas += not criada
        except ValueError as e:
            erros.append(f"linha {n} (OP {dados.get('numero', '?')}): {e}")
    registrar("entrada", "csv", "ordens", f"{criadas} criada(s), {atualizadas} atualizada(s), {len(erros)} erro(s)",
              not erros, autor)
    return {"criadas": criadas, "atualizadas": atualizadas, "erros": erros[:50]}


def modelo_csv() -> str:
    return (";".join(COLUNAS_CSV) + "\n"
            "112729;TP-01;Tampa 20L azul;5000;nitron_inj13;;17,2;1;10/11/2026;14/11/2026;ACME;4500012;;sim\n")


# ------------------------------------------------- apontamentos para o ERP
def apontamentos(desde_ms: int, ate_ms: int, ordem: str = "") -> list[dict]:
    """Produção por lote (OP × máquina × turno) com atividade no período — é o
    que o ERP lê para reportar a produção."""
    sql = """
        SELECT l.codigo AS lote, o.numero AS ordem, o.ref_externa, p.codigo AS produto_codigo,
               q.codigo AS maquina, l.dia, l.turno_sigla AS turno, l.inicio_ms, l.fim_ms,
               l.pecas, l.refugo
        FROM lotes l JOIN maquinas q ON q.id = l.maquina_id
        LEFT JOIN ordens o ON o.id = l.ordem_id LEFT JOIN produtos p ON p.id = o.produto_id
        WHERE l.pecas > 0 AND l.fim_ms >= ? AND l.inicio_ms < ?"""
    args: list = [desde_ms, ate_ms]
    if ordem:
        sql += " AND o.numero = ?"
        args.append(ordem.strip())
    with get_conn() as c:
        linhas = [dict(r) for r in c.execute(sql + " ORDER BY l.inicio_ms", args)]
    for ln in linhas:
        ln["boas"] = max(0, ln["pecas"] - ln["refugo"])
        ln["inicio"] = tempo.ms_para_dt(ln.pop("inicio_ms")).isoformat(timespec="seconds")
        ln["fim"] = tempo.ms_para_dt(ln.pop("fim_ms")).isoformat(timespec="seconds")
    return linhas


# ------------------------------------------------------------------ webhooks
def salvar_webhook(nome: str, url: str, eventos_csv: str = "*") -> dict:
    nome = cadastros._texto(nome, "Nome", 2, 60)
    url = cadastros._texto(url, "URL", 8, 300)
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("URL: precisa começar com http:// ou https://")
    lista = [e.strip() for e in (eventos_csv or "*").split(",") if e.strip()] or ["*"]
    invalidos = [e for e in lista if e != "*" and e not in eventos.EVENTOS]
    if invalidos:
        raise ValueError(f"Evento desconhecido: {', '.join(invalidos)}.")
    segredo = secrets.token_urlsafe(24)
    with get_conn() as c:
        wid = c.execute("INSERT INTO webhooks (nome, url, segredo, eventos, criado_ms) VALUES (?, ?, ?, ?, ?)",
                        (nome, url, segredo, ",".join(lista), agora_ms())).lastrowid
    return {"id": wid, "nome": nome, "url": url, "eventos": ",".join(lista), "segredo": segredo}


def listar_webhooks() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("""
            SELECT w.id, w.nome, w.url, w.eventos, w.ativo, w.criado_ms,
                   (SELECT COUNT(*) FROM webhook_entregas e WHERE e.webhook_id = w.id AND e.status = 'ok') AS entregues,
                   (SELECT COUNT(*) FROM webhook_entregas e WHERE e.webhook_id = w.id AND e.status = 'pendente') AS pendentes,
                   (SELECT COUNT(*) FROM webhook_entregas e WHERE e.webhook_id = w.id AND e.status = 'falha') AS falhas,
                   (SELECT e.ultimo_erro FROM webhook_entregas e WHERE e.webhook_id = w.id AND e.ultimo_erro <> ''
                     ORDER BY e.id DESC LIMIT 1) AS ultimo_erro
            FROM webhooks w ORDER BY w.id DESC""")]


def excluir_webhook(webhook_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))


def assinatura(segredo: str, corpo: bytes) -> str:
    return "sha256=" + hmac.new(segredo.encode("utf-8"), corpo, hashlib.sha256).hexdigest()


async def despachar_pendentes(limite: int = 20) -> int:
    """Entrega os eventos enfileirados. Falhou? Tenta de novo com espera crescente."""
    with get_conn() as c:
        pendentes = [dict(r) for r in c.execute("""
            SELECT e.id, e.evento, e.payload, e.tentativas, w.url, w.segredo
            FROM webhook_entregas e JOIN webhooks w ON w.id = e.webhook_id
            WHERE e.status = 'pendente' AND e.proxima_ms <= ? AND w.ativo = 1
            ORDER BY e.id LIMIT ?""", (agora_ms(), limite))]
    if not pendentes:
        return 0
    entregues = 0
    async with httpx.AsyncClient(timeout=10) as cliente:
        for e in pendentes:
            corpo = e["payload"].encode("utf-8")
            status, erro = None, ""
            try:
                r = await cliente.post(e["url"], content=corpo, headers={
                    "Content-Type": "application/json", "X-MES-Evento": e["evento"],
                    "X-MES-Assinatura": assinatura(e["segredo"], corpo)})
                status = r.status_code
                if status >= 300:
                    erro = f"HTTP {status}"
            except httpx.HTTPError as ex:
                erro = f"{type(ex).__name__}: {ex}"[:200]
            tentativas = e["tentativas"] + 1
            with get_conn() as c:
                if not erro:
                    c.execute("UPDATE webhook_entregas SET status = 'ok', tentativas = ?, http_status = ?, "
                              "ultimo_erro = '', entregue_ms = ? WHERE id = ?",
                              (tentativas, status, agora_ms(), e["id"]))
                    entregues += 1
                elif tentativas > len(ESPERAS_S):
                    c.execute("UPDATE webhook_entregas SET status = 'falha', tentativas = ?, http_status = ?, "
                              "ultimo_erro = ? WHERE id = ?", (tentativas, status, erro, e["id"]))
                else:
                    c.execute("UPDATE webhook_entregas SET tentativas = ?, http_status = ?, ultimo_erro = ?, "
                              "proxima_ms = ? WHERE id = ?",
                              (tentativas, status, erro, agora_ms() + ESPERAS_S[tentativas - 1] * 1000, e["id"]))
    return entregues


async def laco_de_webhooks(intervalo_s: int = 20) -> None:
    while True:
        try:
            await despachar_pendentes()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Falha ao despachar webhooks")
        await asyncio.sleep(intervalo_s)
