"""API para sistemas externos (ERP/PCP): /api/v1.

Autenticação por chave, criada em Configurações > Integrações:
    X-API-Key: mes_...      (ou  Authorization: Bearer mes_...)
Chaves 'ro' só leem; 'rw' também enviam OPs e cadastros.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from app import cadastros as legenda
from app.mes import integracao, ordens, producao, rastreabilidade, tempo
from app.mes.coletor import coletor
from app.mes.db import agora_ms

router = APIRouter(prefix="/api/v1", tags=["Integração"])


def chave(x_api_key: str = Header(default=""), authorization: str = Header(default="")) -> dict:
    valor = x_api_key or (authorization[7:] if authorization.lower().startswith("bearer ") else "")
    info = integracao.validar_chave(valor.strip())
    if not info:
        raise HTTPException(status_code=401, detail="Chave de API ausente ou inválida.")
    return info


def chave_escrita(info: dict = Depends(chave)) -> dict:
    if info["escopo"] != "rw":
        raise HTTPException(status_code=403, detail="Esta chave é só de leitura.")
    return info


def _janela(desde: str, ate: str) -> tuple[int, int]:
    try:
        fim = tempo.parse_local(ate) if ate else agora_ms()
        ini = tempo.parse_local(desde) if desde else fim - 24 * 3600 * 1000
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ini, fim


@router.get("/status", summary="Situação do MES e da coleta")
def status(info: dict = Depends(chave)):
    s = coletor.status()
    return {"ok": True, "chave": info["nome"], "coleta_ativa": s["ativo"], "fase": s["fase"],
            "dados_ate": tempo.ms_para_dt(s["cursor_ms"]).isoformat(timespec="seconds") if s["cursor_ms"] else None}


@router.get("/ordens", summary="Lista ordens de produção")
def listar_ordens(status: str = "", busca: str = "", info: dict = Depends(chave)):
    lista = [s for s in status.split(",") if s] or None
    with ordens.get_conn() as c:
        ids = [o["id"] for o in ordens.listar(status=lista, busca=busca, limite=1000)]
        return [ordens.payload(c, i) for i in ids]


@router.get("/ordens/{numero}", summary="Uma ordem, com a produção contada")
def obter_ordem(numero: str, info: dict = Depends(chave)):
    o = ordens.obter_por_numero(numero)
    if not o:
        raise HTTPException(status_code=404, detail="Ordem não encontrada.")
    with ordens.get_conn() as c:
        return ordens.payload(c, o["id"])


@router.post("/ordens", summary="Cria ou atualiza ordens pelo número")
def enviar_ordens(corpo=Body(...), info: dict = Depends(chave_escrita)):
    """Aceita uma ordem ou uma lista. Campos: numero*, produto_codigo*, quantidade*,
    produto_descricao, maquina, molde_codigo, ciclo_padrao_s, cavidades,
    data_inicio_prev, data_fim_prev, cliente, pedido, observacao, ref_externa, liberar."""
    itens = corpo if isinstance(corpo, list) else [corpo]
    resultado, erros = [], 0
    for item in itens:
        try:
            if not isinstance(item, dict):
                raise ValueError("Cada ordem deve ser um objeto JSON.")
            oid, criada = ordens.upsert_externo(item, "erp", "api:" + info["nome"])
            resultado.append({"numero": item.get("numero"), "ok": True, "criada": criada, "id": oid})
        except ValueError as e:
            erros += 1
            resultado.append({"numero": item.get("numero") if isinstance(item, dict) else None,
                              "ok": False, "erro": str(e)})
    integracao.registrar("entrada", "api", "ordens", f"{len(itens) - erros} ok, {erros} erro(s)",
                         erros == 0, info["nome"])
    if erros and len(itens) == 1:
        raise HTTPException(status_code=400, detail=resultado[0]["erro"])
    return {"recebidas": len(itens), "erros": erros, "ordens": resultado}


@router.get("/apontamentos", summary="Produção por lote no período")
def apontamentos(desde: str = "", ate: str = "", ordem: str = "", info: dict = Depends(chave)):
    """Sem datas, devolve as últimas 24 h. Datas em AAAA-MM-DD ou AAAA-MM-DDTHH:MM."""
    ini, fim = _janela(desde, ate)
    return integracao.apontamentos(ini, fim, ordem)


@router.get("/paradas", summary="Paradas no período")
def paradas(desde: str = "", ate: str = "", info: dict = Depends(chave)):
    ini, fim = _janela(desde, ate)
    d = producao.paradas(ini, fim, limite=2000)
    return [{"maquina": e["maquina"], "ordem": e["ordem"],
             "inicio": tempo.ms_para_dt(e["inicio_ms"]).isoformat(timespec="seconds"),
             "fim": tempo.ms_para_dt(e["fim_ms"]).isoformat(timespec="seconds") if e["fim_ms"] else None,
             "duracao_s": e["duracao_s"], "codigo": e["motivo"]["codigo"], "motivo": e["motivo"]["nome"],
             "tipo": e["motivo"]["classe"], "funcionario": e["funcionario"]} for e in d["eventos"]]


@router.get("/lotes/{codigo}", summary="Genealogia de um lote")
def lote(codigo: str, info: dict = Depends(chave)):
    g = rastreabilidade.genealogia(codigo)
    if not g:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    return g


@router.post("/funcionarios", summary="Sincroniza o cadastro de funcionários")
def enviar_funcionarios(corpo: list = Body(...), info: dict = Depends(chave_escrita)):
    """Lista de {codigo, nome}: cria os novos e atualiza o nome dos existentes."""
    existentes = {c.codigo: c for c in legenda.list_codigos("funcionario")}
    ok, erros = 0, []
    for item in corpo:
        try:
            codigo = legenda._normalize_codigo((item or {}).get("codigo", ""))
            nome = str((item or {}).get("nome", ""))
            if codigo in existentes:
                legenda.update_codigo(existentes[codigo].id, nome=nome, ativo=True)
            else:
                existentes[codigo] = legenda.create_codigo("funcionario", codigo, nome)
            ok += 1
        except (ValueError, AttributeError) as e:
            erros.append(f"{item}: {e}"[:160])
    integracao.registrar("entrada", "api", "funcionarios", f"{ok} ok, {len(erros)} erro(s)", not erros, info["nome"])
    return {"recebidos": len(corpo), "gravados": ok, "erros": erros[:30]}
