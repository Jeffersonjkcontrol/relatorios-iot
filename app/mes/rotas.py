"""Rotas do MES: página do painel e a API que as telas consomem (/api/mes/*).

As funções são `def` comuns de propósito: o FastAPI as roda em thread, então
uma consulta mais pesada ao banco não trava o laço de eventos (nem o coletor).
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app import cadastros as legenda
from app.auth import User, pode, require_gestor, require_perm, require_user
from app.mes import apontamentos, cadastros, cartoes, config, eventos, ingestao, integracao, ordens, producao, rastreabilidade
from app.mes.coletor import ativar_mes, coletor, desativar_mes, reconhecer_conta

router = APIRouter()


def _erro(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _periodo(periodo: str, inicio: str = "", fim: str = "") -> dict:
    try:
        return producao.resolver_periodo(periodo, inicio, fim)
    except ValueError as e:
        raise _erro(e)


# ---------------------------------------------------------------------- página
def registrar_paginas(app, templates, tctx) -> None:
    @app.get("/mes", response_class=HTMLResponse)
    async def pagina_mes(request: Request):
        return templates.TemplateResponse("mes.html", tctx(request, current_page="mes"))

    @app.get("/mes/tv", response_class=HTMLResponse)
    async def pagina_tv(request: Request):
        return templates.TemplateResponse("mes_tv.html", tctx(request, current_page="mes"))

    @app.get("/mes/etiqueta.pdf")
    def etiqueta_do_lote(request: Request, lote: str):
        # o QR leva direto à genealogia do lote neste mesmo servidor
        url = str(request.base_url).rstrip("/") + "/mes?lote=" + lote + "#rastreabilidade"
        pdf = rastreabilidade.etiqueta_pdf(lote, url)
        if pdf is None:
            raise HTTPException(status_code=404, detail="Lote não encontrado.")
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="lote-{lote}.pdf"'})


# ---------------------------------------------------------------------- painel
@router.get("/api/mes/status")
def api_status(user: User = Depends(require_user)):
    return coletor.status()


@router.get("/api/mes/resumo")
def api_resumo(periodo: str = "hoje", inicio: str = "", fim: str = "", user: User = Depends(require_user)):
    p = _periodo(periodo, inicio, fim)
    return {"periodo": p, **producao.resumo(p["ini_ms"], p["fim_ms"])}


@router.get("/api/mes/maquinas")
def api_maquinas(user: User = Depends(require_user)):
    return producao.estado_maquinas()


@router.get("/api/mes/producao")
def api_producao(periodo: str = "hoje", inicio: str = "", fim: str = "", maquina_id: int | None = None,
                 user: User = Depends(require_user)):
    p = _periodo(periodo, inicio, fim)
    return {"periodo": p,
            "por_hora": producao.producao_por_hora(p["ini_ms"], p["fim_ms"], maquina_id),
            "por_maquina": producao.producao_por_maquina(p["ini_ms"], p["fim_ms"])}


@router.get("/api/mes/paradas")
def api_paradas(periodo: str = "hoje", inicio: str = "", fim: str = "", maquina_id: int | None = None,
                user: User = Depends(require_user)):
    p = _periodo(periodo, inicio, fim)
    return {"periodo": p, **producao.paradas(p["ini_ms"], p["fim_ms"], maquina_id),
            "motivos": apontamentos.motivos_parada()}


@router.get("/api/mes/oee")
def api_oee(periodo: str = "hoje", inicio: str = "", fim: str = "", user: User = Depends(require_user)):
    p = _periodo(periodo, inicio, fim)
    return {"periodo": p, **producao.oee(p["ini_ms"], p["fim_ms"])}


@router.get("/api/mes/saude")
def api_saude(periodo: str = "hoje", inicio: str = "", fim: str = "", user: User = Depends(require_user)):
    p = _periodo(periodo, inicio, fim)
    return {"periodo": p, **producao.saude_contagem(p["ini_ms"], p["fim_ms"])}


@router.post("/api/mes/paradas/{parada_id}/motivo")
def api_apontar_motivo(parada_id: int, dados: dict = Body(...), user: User = Depends(require_perm("apontar"))):
    try:
        if dados.get("codigo"):
            apontamentos.apontar_motivo_parada(parada_id, dados["codigo"], user.username,
                                               dados.get("observacao", ""))
        else:
            apontamentos.limpar_motivo_parada(parada_id)
    except ValueError as e:
        raise _erro(e)
    return {"ok": True}


# ------------------------------------------------------------- rastreabilidade
@router.get("/api/mes/lotes")
def api_lotes(busca: str = "", dia: str = "", maquina_id: int | None = None,
              user: User = Depends(require_user)):
    try:
        return rastreabilidade.buscar(busca, dia, maquina_id)
    except ValueError as e:
        raise _erro(e)


@router.get("/api/mes/lote")
def api_lote(codigo: str, user: User = Depends(require_user)):
    g = rastreabilidade.genealogia(codigo)
    if not g:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    return g


# ---------------------------------------------------------------------- ordens
@router.get("/api/mes/ordens")
def api_ordens(status: str = "", busca: str = "", incompletas: bool = False,
               user: User = Depends(require_user)):
    lista = [s for s in status.split(",") if s] or None
    return {"contagem": ordens.contagem_por_status(),
            "ordens": ordens.listar(status=lista, busca=busca, incompletas=incompletas)}


@router.post("/api/mes/ordens/{ordem_id}/completar")
def api_completar_ordem(ordem_id: int, dados: dict = Body(...), user: User = Depends(require_perm("op_planejar"))):
    """Preenche produto/quantidade/ciclo de uma OP que nasceu do cartão."""
    try:
        campos = {k: v for k, v in dados.items()
                  if k in ("produto_codigo", "produto_descricao", "quantidade", "ciclo_padrao_s",
                           "cavidades", "cliente", "pedido", "observacao") and str(v).strip() != ""}
        ordens.atualizar(ordem_id, campos, user.username, origem="csv")
    except ValueError as e:
        raise _erro(e)
    return ordens.obter(ordem_id)


@router.get("/api/mes/base")
def api_base(user: User = Depends(require_user)):
    """Listas para os formulários: máquinas, produtos e motivos de refugo."""
    return {"maquinas": [{"id": m["id"], "nome": m["nome"]} for m in cadastros.listar_maquinas(so_ativas=True)],
            "produtos": cadastros.listar_produtos(so_ativos=True, limite=2000),
            "motivos_refugo": cadastros.listar_motivos_refugo(so_ativos=True)}


def _dados_da_op(dados: dict) -> dict:
    campos = ("numero", "produto_codigo", "produto_descricao", "quantidade", "maquina_id", "ciclo_padrao_s",
              "cavidades", "data_inicio_prev", "data_fim_prev", "cliente", "pedido", "observacao")
    out = {k: dados[k] for k in campos if k in dados and str(dados[k]).strip() != ""}
    # produto digitado que ainda não existe é cadastrado junto com a OP
    codigo = str(out.get("produto_codigo") or "").strip()
    if codigo and not cadastros.obter_produto_por_codigo(codigo):
        cadastros.salvar_produto({"codigo": codigo, "descricao": out.get("produto_descricao") or codigo})
    return out


@router.post("/api/mes/ordens")
def api_criar_ordem(dados: dict = Body(...), user: User = Depends(require_perm("op_planejar"))):
    try:
        oid = ordens.criar(_dados_da_op(dados), user.username)
        if dados.get("liberar"):
            ordens.liberar(oid, user.username)
    except ValueError as e:
        raise _erro(e)
    return ordens.obter(oid)


@router.post("/api/mes/ordens/{ordem_id}/editar")
def api_editar_ordem(ordem_id: int, dados: dict = Body(...), user: User = Depends(require_perm("op_planejar"))):
    try:
        ordens.atualizar(ordem_id, _dados_da_op(dados), user.username)
    except ValueError as e:
        raise _erro(e)
    return ordens.obter(ordem_id)


@router.post("/api/mes/ordens/{ordem_id}/{acao}")
def api_acao_ordem(ordem_id: int, acao: str, dados: dict = Body(default={}), user: User = Depends(require_user)):
    """Iniciar/pausar/concluir exigem 'Executar OP'; liberar, recolher, cancelar,
    reabrir e excluir exigem 'Planejar OP'."""
    perm = "op_executar" if acao in ("iniciar", "pausar", "concluir") else "op_planejar"
    if not pode(user, perm):
        raise HTTPException(status_code=403, detail="Sem permissão para esta ação. Fale com o gestor.")
    try:
        if acao == "iniciar":
            ordens.iniciar(ordem_id, user.username, maquina_id=int(dados["maquina_id"]) if dados.get("maquina_id") else None,
                           substituir=dados.get("substituir") or None)
        elif acao == "excluir":
            ordens.excluir(ordem_id)
            return {"ok": True}
        elif acao in ("pausar", "concluir", "cancelar", "reabrir", "liberar", "recolher"):
            getattr(ordens, acao)(ordem_id, user.username)
        else:
            raise HTTPException(status_code=404, detail="Ação desconhecida.")
    except ValueError as e:
        raise _erro(e)
    return ordens.obter(ordem_id)


# --------------------------------------------------------------------- refugo
@router.get("/api/mes/refugos")
def api_refugos(ordem_id: int, user: User = Depends(require_user)):
    return apontamentos.listar_refugos(ordem_id=ordem_id, limite=100)


@router.post("/api/mes/refugos")
def api_apontar_refugo(dados: dict = Body(...), user: User = Depends(require_perm("apontar"))):
    try:
        rid = apontamentos.apontar_refugo(dados, user.username)
    except ValueError as e:
        raise _erro(e)
    return {"id": rid}


@router.delete("/api/mes/refugos/{refugo_id}")
def api_excluir_refugo(refugo_id: int, user: User = Depends(require_perm("refugo_excluir"))):
    try:
        apontamentos.excluir_refugo(refugo_id)
    except ValueError as e:
        raise _erro(e)
    return {"ok": True}


# ------------------------------------------------ cadastros: turnos e produtos
@router.get("/api/mes/turnos")
def api_turnos(user: User = Depends(require_perm("cadastros"))):
    return cadastros.listar_turnos()


@router.post("/api/mes/turnos")
def api_salvar_turno(dados: dict = Body(...), user: User = Depends(require_perm("cadastros"))):
    try:
        return {"id": cadastros.salvar_turno(dados, int(dados["id"]) if dados.get("id") else None)}
    except ValueError as e:
        raise _erro(e)


@router.delete("/api/mes/turnos/{turno_id}")
def api_excluir_turno(turno_id: int, user: User = Depends(require_perm("cadastros"))):
    cadastros.excluir_turno(turno_id)
    return {"ok": True}


@router.get("/api/mes/produtos")
def api_produtos(busca: str = "", user: User = Depends(require_perm("cadastros"))):
    return cadastros.listar_produtos(busca)


@router.post("/api/mes/produtos")
def api_salvar_produto(dados: dict = Body(...), user: User = Depends(require_perm("cadastros"))):
    try:
        existente = cadastros.obter_produto_por_codigo(str(dados.get("codigo") or ""))
        return {"id": cadastros.salvar_produto(dados, existente["id"] if existente else None)}
    except ValueError as e:
        raise _erro(e)


@router.delete("/api/mes/produtos/{produto_id}")
def api_excluir_produto(produto_id: int, user: User = Depends(require_perm("cadastros"))):
    try:
        cadastros.excluir_produto(produto_id)
    except ValueError as e:
        raise _erro(e)
    return {"ok": True}


# --------------------------------------------------- legenda dos cartões RFID
def _tipo_valido(tipo: str) -> str:
    if tipo not in ("motivo", "funcionario"):
        raise HTTPException(status_code=400, detail="Tipo inválido: use 'motivo' ou 'funcionario'.")
    return tipo


@router.get("/api/mes/legenda")
def api_legenda(tipo: str = "motivo", user: User = Depends(require_user)):
    """Códigos cadastrados + os que as máquinas publicaram e ainda estão sem nome."""
    _tipo_valido(tipo)
    cadastrados = legenda.list_codigos(tipo)
    conhecidos = {c.codigo for c in cadastrados}
    vistos = producao.codigos_vistos(tipo)
    uso = {v["codigo"]: v for v in vistos}
    return {
        "classes": legenda.CLASSES,
        "cadastrados": [
            {"id": c.id, "codigo": c.codigo, "nome": c.nome, "categoria": c.categoria, "classe": c.classe,
             "cor": c.cor_efetiva, "ativo": c.ativo, "vezes": (uso.get(c.codigo) or {}).get("vezes", 0)}
            for c in sorted(cadastrados, key=lambda c: (not c.codigo.isdigit(), c.codigo.zfill(12)))],
        "sem_nome": [v for v in vistos if v["codigo"] not in conhecidos],
    }


def _salvar_codigo(tipo: str, dados: dict) -> None:
    codigo = legenda._normalize_codigo(dados.get("codigo", ""))
    existente = next((c for c in legenda.list_codigos(tipo) if c.codigo == codigo), None)
    if existente:
        legenda.update_codigo(existente.id, nome=dados.get("nome"), categoria=dados.get("categoria"),
                              classe=dados.get("classe") if tipo == "motivo" else None,
                              cor=dados.get("cor"), ativo=True)
    else:
        legenda.create_codigo(tipo, codigo, dados.get("nome", ""), dados.get("categoria", "") or "",
                              dados.get("classe") or "parada", dados.get("cor") or "")


@router.post("/api/mes/legenda")
def api_salvar_legenda(dados: dict = Body(...), user: User = Depends(require_perm("legenda"))):
    """Cria ou atualiza um código. Mudar a legenda de paradas corrige o histórico."""
    tipo = _tipo_valido(dados.get("tipo", "motivo"))
    try:
        _salvar_codigo(tipo, dados)
    except ValueError as e:
        raise _erro(e)
    corrigidas = ingestao.reassociar_tudo() if tipo == "motivo" else 0
    return {"ok": True, "paradas_corrigidas": corrigidas}


@router.delete("/api/mes/legenda/{codigo_id}")
def api_excluir_legenda(codigo_id: int, user: User = Depends(require_perm("legenda"))):
    item = legenda.get_codigo(codigo_id)
    legenda.delete_codigo(codigo_id)
    if item and item.tipo == "motivo":
        ingestao.reassociar_tudo()
    return {"ok": True}


@router.post("/api/mes/legenda/importar")
def api_importar_legenda(dados: dict = Body(...), user: User = Depends(require_perm("legenda"))):
    """Importa uma lista 'código;nome' (uma por linha; aceita ; , ou tab). Para
    paradas, uma 3ª coluna opcional traz o tipo (parada, setup, planejada, producao)."""
    tipo = _tipo_valido(dados.get("tipo", "funcionario"))
    texto = str(dados.get("texto") or "")
    amostra = texto[:2000]
    sep = max(";\t,", key=amostra.count)
    ok, erros = 0, []
    for n, linha in enumerate(csv.reader(io.StringIO(texto), delimiter=sep), start=1):
        campos = [x.strip() for x in linha]
        if not any(campos):
            continue
        if n == 1 and campos[0].lower() in ("codigo", "código", "cod"):
            continue                                   # cabeçalho
        if len(campos) < 2 or not campos[0] or not campos[1]:
            erros.append(f"linha {n}: esperado 'código{sep}nome'")
            continue
        try:
            _salvar_codigo(tipo, {"codigo": campos[0], "nome": campos[1],
                                  "classe": campos[2].lower() if len(campos) > 2 and campos[2] else None})
            ok += 1
        except ValueError as e:
            erros.append(f"linha {n}: {e}")
    if tipo == "motivo" and ok:
        ingestao.reassociar_tudo()
    return {"importados": ok, "erros": erros[:30]}


# ------------------------------------------------------- cartões RFID (gestor)
URL_AGENTE = "https://github.com/Jeffersonjkcontrol/relatorios-iot/releases/download/agente-rfid/AgenteRFID.exe"


@router.get("/api/mes/cartoes/opcoes")
def api_cartoes_opcoes(user: User = Depends(require_perm("cartoes"))):
    """O que dá para gravar: OPs abertas e a legenda de funcionários e paradas."""
    def legenda_de(tipo):
        return [{"codigo": c.codigo, "nome": c.nome, "classe": c.classe}
                for c in legenda.list_codigos(tipo) if c.ativo]
    ops = ordens.listar(status=list(ordens.ABERTAS), limite=500)
    return {
        "tipos": {k: v["rotulo"] for k, v in cartoes.TIPOS.items()},
        "ops": [{"codigo": o["numero"], "nome": o["produto_descricao"] or ""} for o in ops],
        "funcionario": legenda_de("funcionario"), "parada": legenda_de("motivo"),
        "url_agente": URL_AGENTE,
    }


@router.post("/api/mes/cartoes/preparar")
def api_cartao_preparar(dados: dict = Body(...), user: User = Depends(require_perm("cartoes"))):
    """Valida e devolve os blocos a gravar. Nome novo de funcionário/parada já
    entra na legenda."""
    try:
        r = cartoes.montar(dados.get("tipo", ""), dados.get("conteudo", ""))
        nome = str(dados.get("nome") or "").strip()
        if nome and r["tipo"] in ("funcionario", "parada"):
            _salvar_codigo("funcionario" if r["tipo"] == "funcionario" else "motivo",
                           {"codigo": r["conteudo"], "nome": nome})
    except ValueError as e:
        raise _erro(e)
    return r


@router.post("/api/mes/cartoes/interpretar")
def api_cartao_interpretar(dados: dict = Body(...), user: User = Depends(require_perm("cartoes"))):
    """Traduz o que foi lido do cartão: tipo, conteúdo, nome na legenda e
    quando/por quem foi gravado aqui."""
    r = cartoes.interpretar(dados.get("blocos") or {})
    mapa = {"funcionario": "funcionario", "parada": "motivo"}.get(r["tipo"])
    r["nome"] = legenda.resolve_map(mapa).get(r["conteudo"]) if mapa else None
    if r["tipo"] == "op":
        o = ordens.obter_por_numero(r["conteudo"])
        r["nome"] = (o["produto_descricao"] or f"sem produto cadastrado · {o['status_rotulo'].lower()}") if o else "OP ainda não vista no MES"
    r["gravacoes"] = cartoes.historico(uid=str(dados.get("uid") or ""), limite=5) if dados.get("uid") else []
    return r


@router.post("/api/mes/cartoes/historico")
def api_cartao_registrar(dados: dict = Body(...), user: User = Depends(require_perm("cartoes"))):
    try:
        cartoes.registrar(dados.get("tipo", ""), dados.get("conteudo", ""), dados.get("uid", ""),
                          dados.get("nome", ""), user.username)
    except ValueError as e:
        raise _erro(e)
    return {"ok": True}


@router.get("/api/mes/cartoes/historico")
def api_cartao_historico(uid: str = "", user: User = Depends(require_perm("cartoes"))):
    return cartoes.historico(uid)


# ------------------------------------------------------- integrações (gestor)
@router.get("/api/mes/integracao")
def api_integracao(user: User = Depends(require_gestor)):
    return {"chaves": integracao.listar_chaves(), "webhooks": integracao.listar_webhooks(),
            "eventos": eventos.EVENTOS, "historico": integracao.historico(30),
            "colunas_csv": integracao.COLUNAS_CSV, "modelo_csv": integracao.modelo_csv()}


@router.post("/api/mes/chaves")
def api_criar_chave(dados: dict = Body(...), user: User = Depends(require_gestor)):
    try:
        return integracao.criar_chave(dados.get("nome", ""), dados.get("escopo", "rw"), user.username)
    except ValueError as e:
        raise _erro(e)


@router.delete("/api/mes/chaves/{chave_id}")
def api_revogar_chave(chave_id: int, user: User = Depends(require_gestor)):
    integracao.revogar_chave(chave_id)
    return {"ok": True}


@router.post("/api/mes/webhooks")
def api_criar_webhook(dados: dict = Body(...), user: User = Depends(require_gestor)):
    try:
        return integracao.salvar_webhook(dados.get("nome", ""), dados.get("url", ""), dados.get("eventos", "*"))
    except ValueError as e:
        raise _erro(e)


@router.delete("/api/mes/webhooks/{webhook_id}")
def api_excluir_webhook(webhook_id: int, user: User = Depends(require_gestor)):
    integracao.excluir_webhook(webhook_id)
    return {"ok": True}


@router.post("/api/mes/ordens-csv")
def api_importar_ordens(dados: dict = Body(...), user: User = Depends(require_perm("op_planejar"))):
    try:
        return integracao.importar_ordens_csv(dados.get("texto", ""), user.username)
    except ValueError as e:
        raise _erro(e)


# ---------------------------------------------------------- coleta (gestor)
@router.get("/api/mes/config")
def api_config(user: User = Depends(require_gestor)):
    cfg = config.todas()
    return {"status": coletor.status(), "config": cfg, "descricoes": config.DESCRICOES}


@router.post("/api/mes/config")
def api_salvar_config(dados: dict = Body(...), user: User = Depends(require_gestor)):
    permitidas = set(config.PADROES) - {"ativo"}
    for chave, valor in dados.items():
        if chave in permitidas:
            config.set_(chave, str(valor).strip())
    return {"ok": True}


@router.get("/api/mes/conta")
async def api_conta(plataforma: str = "ubidots", user: User = Depends(require_gestor)):
    try:
        return await reconhecer_conta(plataforma)
    except ValueError as e:
        raise _erro(e)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao consultar a plataforma: {e}")


@router.post("/api/mes/ativar")
async def api_ativar(dados: dict = Body(default={}), user: User = Depends(require_gestor)):
    try:
        return await ativar_mes(dados.get("plataforma") or config.get("plataforma"))
    except ValueError as e:
        raise _erro(e)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao consultar a plataforma: {e}")


@router.post("/api/mes/desativar")
def api_desativar(user: User = Depends(require_gestor)):
    desativar_mes()
    return {"ok": True}


@router.post("/api/mes/coletar-agora")
def api_coletar_agora(user: User = Depends(require_gestor)):
    coletor.iniciar()
    coletor.acordar()
    return {"ok": True}
