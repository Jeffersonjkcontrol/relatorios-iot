"""Auditoria: quem fez o quê no sistema.

Toda requisição que ALTERA algo (POST/PUT/PATCH/DELETE) é registrada por um
middleware — não depende de cada rota lembrar de registrar. Consultas (GET) não
entram: encheriam o log sem dizer quem mudou o quê.

Grava: quando, quem (usuário e papel, ou a chave de API), IP, a ação em
português, o item afetado (ids do caminho + campos do formulário) e o resultado.
Senhas, chaves e segredos NUNCA são gravados.
"""
from __future__ import annotations

import csv
import io
import json
import re
from urllib.parse import parse_qs

from app.mes.db import agora_ms, get_conn

RETENCAO_DIAS = 365
METODOS = {"POST", "PUT", "PATCH", "DELETE"}

# POSTs que só leem ou validam (não mudam nada)
IGNORAR = {"/api/mes/cartoes/preparar", "/api/mes/cartoes/interpretar", "/login", "/logout"}

# Campos que podem aparecer no detalhe. Qualquer outro fica de fora — inclusive
# senhas, tokens e chaves, que nunca estão nesta lista.
CAMPOS = (
    "numero", "codigo", "nome", "tipo", "conteudo", "quantidade", "produto_codigo", "produto_descricao",
    "maquina_id", "maquina", "motivo_id", "motivo", "ordem_id", "ordem", "sigla", "inicio", "fim", "descricao",
    "ciclo_padrao_s", "cavidades", "username", "role", "active", "days", "plataforma", "escopo", "url",
    "eventos", "uid", "observacao", "substituir", "platform", "device", "variable", "formato", "start", "end",
    "classe", "categoria", "liberar", "status", "cliente", "pedido", "provider", "model",
)

# (método, padrão do caminho, descrição). A primeira que casar vale.
ACOES: list[tuple[str, str, str]] = [
    ("POST", r"/change-password", "Trocou a própria senha (1º acesso)"),
    ("POST", r"/perfil/info", "Editou o próprio perfil"),
    ("POST", r"/perfil/senha", "Trocou a própria senha"),
    ("POST", r"/users", "Criou usuário"),
    ("POST", r"/users/(?P<id>\d+)/delete", "Excluiu usuário"),
    ("POST", r"/users/(?P<id>\d+)/reset-password", "Redefiniu a senha de outro usuário"),
    ("POST", r"/admin/maintenance", "Alterou o modo manutenção"),
    ("POST", r"/admin/session-expiry", "Alterou a duração da sessão"),
    ("POST", r"/admin/restart", "Reiniciou o sistema"),
    ("POST", r"/admin/backup", "Executou backup"),
    ("POST", r"/admin/ia", "Editou o conhecimento da IA"),
    ("POST", r"/cadastros", "Cadastrou código RFID"),
    ("POST", r"/cadastros/(?P<id>\d+)/delete", "Excluiu código RFID"),
    ("POST", r"/relatorio", "Gerou relatório de variável"),
    ("POST", r"/relatorio_oee", "Gerou relatório de OEE"),
    ("POST", r"/relatorio_paradas", "Gerou relatório de paradas"),
    ("POST", r"/api/ai/chat", "Perguntou à IA"),
    ("DELETE", r"/api/ai/conversations/(?P<id>\w+)", "Excluiu conversa da IA"),
    ("POST", r"/api/mes/paradas/(?P<id>\d+)/motivo", "Apontou motivo de parada"),
    ("POST", r"/api/mes/ordens", "Criou ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/editar", "Editou ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/completar", "Completou cadastro de ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/liberar", "Liberou ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/recolher", "Recolheu ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/iniciar", "Iniciou ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/pausar", "Pausou ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/concluir", "Concluiu ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/cancelar", "Cancelou ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/reabrir", "Reabriu ordem de produção"),
    ("POST", r"/api/mes/ordens/(?P<id>\d+)/excluir", "Excluiu ordem de produção"),
    ("POST", r"/api/mes/refugos", "Apontou refugo"),
    ("DELETE", r"/api/mes/refugos/(?P<id>\d+)", "Excluiu apontamento de refugo"),
    ("POST", r"/api/mes/turnos", "Salvou turno"),
    ("DELETE", r"/api/mes/turnos/(?P<id>\d+)", "Excluiu turno"),
    ("POST", r"/api/mes/produtos", "Salvou produto"),
    ("DELETE", r"/api/mes/produtos/(?P<id>\d+)", "Excluiu produto"),
    ("POST", r"/api/mes/legenda", "Salvou código na legenda"),
    ("DELETE", r"/api/mes/legenda/(?P<id>\d+)", "Excluiu código da legenda"),
    ("POST", r"/api/mes/legenda/importar", "Importou lista na legenda"),
    ("POST", r"/api/mes/cartoes/historico", "Gravou cartão RFID"),
    ("POST", r"/api/mes/chaves", "Criou chave de API"),
    ("DELETE", r"/api/mes/chaves/(?P<id>\d+)", "Revogou chave de API"),
    ("POST", r"/api/mes/webhooks", "Criou webhook"),
    ("DELETE", r"/api/mes/webhooks/(?P<id>\d+)", "Excluiu webhook"),
    ("POST", r"/api/mes/ordens-csv", "Importou ordens por CSV"),
    ("POST", r"/api/mes/config", "Alterou a configuração da coleta"),
    ("POST", r"/api/mes/ativar", "Ativou a coleta do MES"),
    ("POST", r"/api/mes/desativar", "Pausou a coleta do MES"),
    ("POST", r"/api/mes/coletar-agora", "Forçou uma coleta"),
    ("POST", r"/api/v1/ordens", "ERP enviou ordens de produção"),
    ("POST", r"/api/v1/funcionarios", "ERP enviou funcionários"),
]
_ACOES = [(m, re.compile(p + r"$"), d) for m, p, d in ACOES]


def descrever(metodo: str, caminho: str) -> tuple[str, str]:
    """(descrição da ação, id do item) para o método e caminho."""
    for m, padrao, desc in _ACOES:
        achou = padrao.match(caminho)
        if m == metodo and achou:
            return desc, (achou.groupdict().get("id") or "")
    return f"{metodo} {caminho}", ""


def resumir(corpo: bytes, tipo_conteudo: str) -> str:
    """Campos permitidos do corpo da requisição, em texto curto."""
    if not corpo:
        return ""
    dados = None
    try:
        if "json" in tipo_conteudo:
            dados = json.loads(corpo)
        elif "x-www-form-urlencoded" in tipo_conteudo:
            dados = {k: v[0] for k, v in parse_qs(corpo.decode("utf-8", "replace")).items()}
    except (ValueError, UnicodeDecodeError):
        return ""
    if isinstance(dados, list):
        return f"{len(dados)} item(ns)"
    if not isinstance(dados, dict):
        return ""
    partes = [f"{k}={str(dados[k])[:60]}" for k in CAMPOS if k in dados and str(dados[k]).strip() != ""]
    if "texto" in dados:                                   # importações: só o tamanho
        partes.append(f"{len(str(dados['texto']).strip().splitlines())} linha(s)")
    return "; ".join(partes)[:500]


def registrar(usuario: str, papel: str, ip: str, metodo: str, caminho: str, acao: str,
              item: str = "", detalhe: str = "", status: int = 0) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO auditoria (ts_ms, usuario, papel, ip, metodo, caminho, acao, item, detalhe, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agora_ms(), usuario[:60], papel[:20], ip[:45], metodo, caminho[:200], acao[:120],
             str(item)[:40], detalhe[:500], int(status)))


def listar(usuario: str = "", busca: str = "", ini_ms: int | None = None, fim_ms: int | None = None,
           so_falhas: bool = False, limite: int = 500) -> list[dict]:
    sql, args = "SELECT * FROM auditoria WHERE 1 = 1", []
    if usuario:
        sql += " AND usuario = ?"; args.append(usuario)
    if busca:
        like = f"%{busca.strip()}%"
        sql += " AND (acao LIKE ? OR detalhe LIKE ? OR item LIKE ? OR caminho LIKE ?)"; args += [like] * 4
    if ini_ms is not None:
        sql += " AND ts_ms >= ?"; args.append(ini_ms)
    if fim_ms is not None:
        sql += " AND ts_ms < ?"; args.append(fim_ms)
    if so_falhas:
        sql += " AND status >= 400"
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limite])]


def usuarios() -> list[str]:
    with get_conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT usuario FROM auditoria ORDER BY usuario COLLATE NOCASE")]


def para_csv(linhas: list[dict]) -> str:
    from app.mes import tempo
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["data_hora", "usuario", "papel", "ip", "acao", "item", "detalhe", "resultado"])
    for r in linhas:
        w.writerow([tempo.fmt(r["ts_ms"], "%d/%m/%Y %H:%M:%S"), r["usuario"], r["papel"], r["ip"], r["acao"],
                    r["item"], r["detalhe"], "ok" if r["status"] < 400 else f"erro {r['status']}"])
    return "﻿" + buf.getvalue()


def limpar_antigos(dias: int = RETENCAO_DIAS) -> int:
    with get_conn() as c:
        return c.execute("DELETE FROM auditoria WHERE ts_ms < ?", (agora_ms() - dias * 86400 * 1000,)).rowcount
