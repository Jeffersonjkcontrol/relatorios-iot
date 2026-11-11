"""Integração com ERP: CSV, chaves de API, /api/v1 e webhooks."""
import asyncio
import json

import pytest

from apoio import C, ingerir, t
from app.mes import integracao, ordens

A = "112729"


@pytest.fixture()
def cliente(banco, maq):
    from fastapi.testclient import TestClient
    import app.main as principal
    return TestClient(principal.app)


def test_csv_cria_atualiza_e_completa_op_do_cartao(banco, maq):
    ingerir(banco, maq, op=[C(t(8), 1, op_rfid=A)],
            contador=[C(t(8, 15), 0, op_rfid=A), C(t(8, 30), 50, op_rfid=A)])
    csv_ = ("OP;Produto;Descricao;Qtd;Injetora;Ciclo;Entrega;Cliente;Liberar\n"
            f"{A};TP-01;Tampa 20L;5000;INJ01;17,2;14/11/2026;ACME;\n"
            "200100;TP-02;Balde 10L;300;inj02;;;;sim\n"
            "\n"
            "200101;TP-03;Sem quantidade;0;;;;;\n"
            "200102;TP-04;Maquina errada;10;inj99;;;;\n")
    r = integracao.importar_ordens_csv(csv_, "ana")
    assert (r["criadas"], r["atualizadas"], len(r["erros"])) == (1, 1, 2)
    assert "linha 5" in r["erros"][0] and "linha 6" in r["erros"][1]

    a = ordens.obter_por_numero(A)                    # nasceu do cartão, ganhou cadastro
    assert (a["produto_codigo"], a["quantidade"], a["ciclo_padrao_s"], a["data_fim_prev"], a["qtd_produzida"]) == \
        ("TP-01", 5000, 17.2, "2026-11-14", 50)
    b = ordens.obter_por_numero("200100")
    assert (b["status"], b["origem"], b["maquina_codigo"]) == ("liberada", "csv", "inj02")
    assert integracao.importar_ordens_csv(csv_, "ana")["criadas"] == 0     # idempotente
    with pytest.raises(ValueError, match="Faltam colunas"):
        integracao.importar_ordens_csv("numero;descricao\n1;x\n")
    assert integracao.historico()[0]["canal"] == "csv"


def test_chaves_de_api(banco):
    k = integracao.criar_chave("ERP", "rw", "ana")
    assert k["chave"].startswith("mes_") and len(k["chave"]) > 40
    assert integracao.validar_chave(k["chave"])["nome"] == "ERP"
    assert integracao.validar_chave("mes_errada") is None and integracao.validar_chave("") is None
    (lst,) = integracao.listar_chaves()
    assert "chave" not in lst and "hash" not in lst and lst["ultimo_uso_ms"]   # o valor não fica guardado
    integracao.revogar_chave(k["id"])
    assert integracao.validar_chave(k["chave"]) is None
    with pytest.raises(ValueError):
        integracao.criar_chave("ERP", "admin")


def test_api_v1(cliente, banco, maq):
    rw = {"X-API-Key": integracao.criar_chave("ERP", "rw")["chave"]}
    ro = {"Authorization": "Bearer " + integracao.criar_chave("BI", "ro")["chave"]}
    assert cliente.get("/api/v1/status").status_code == 401
    assert cliente.get("/api/v1/status", headers={"X-API-Key": "mes_nada"}).status_code == 401
    assert cliente.get("/api/v1/status", headers=ro).json()["ok"] is True

    op = {"numero": A, "produto_codigo": "TP-01", "produto_descricao": "Tampa 20L", "quantidade": 100,
          "maquina": "inj01", "liberar": True}
    assert cliente.post("/api/v1/ordens", json=op, headers=ro).status_code == 403
    r = cliente.post("/api/v1/ordens", json=op, headers=rw)
    assert r.status_code == 200 and r.json()["ordens"][0]["criada"] is True
    r = cliente.post("/api/v1/ordens", json=[{**op, "quantidade": 150}, {"numero": "X", "quantidade": 1}], headers=rw)
    assert (r.json()["erros"], r.json()["ordens"][0]["criada"]) == (1, False)
    assert cliente.post("/api/v1/ordens", json={"numero": "Y"}, headers=rw).status_code == 400

    ingerir(banco, maq, op=[C(t(8), 1, op_rfid=A)],
            contador=[C(t(8, 15), 0, op_rfid=A), C(t(8, 30), 60, op_rfid=A)],
            parada=[C(t(8, 31), 0), C(t(8, 50), 1), C(t(8, 55), 0)])
    o = cliente.get(f"/api/v1/ordens/{A}", headers=ro).json()
    assert (o["status"], o["quantidade"], o["qtd_produzida"], o["pct"]) == ("em_producao", 150, 60, 40.0)
    assert cliente.get("/api/v1/ordens/NAO", headers=ro).status_code == 404
    assert [x["numero"] for x in cliente.get("/api/v1/ordens?status=em_producao", headers=ro).json()] == [A]

    ap = cliente.get("/api/v1/apontamentos?desde=2026-11-03&ate=2026-11-04", headers=ro).json()
    assert [(x["ordem"], x["maquina"], x["turno"], x["pecas"], x["boas"]) for x in ap] == [(A, "inj01", "T1", 60, 60)]
    assert ap[0]["inicio"].startswith("2026-11-03T08:")
    par = cliente.get("/api/v1/paradas?desde=2026-11-03&ate=2026-11-04", headers=ro).json()
    assert len(par) == 1 and par[0]["duracao_s"] == 600 and par[0]["ordem"] == A
    assert cliente.get("/api/v1/lotes/" + ap[0]["lote"], headers=ro).json()["lote"]["pecas"] == 60
    assert cliente.get("/api/v1/apontamentos?desde=ontem", headers=ro).status_code == 400

    r = cliente.post("/api/v1/funcionarios", headers=rw,
                     json=[{"codigo": "038", "nome": "Maria Souza"}, {"codigo": "38", "nome": "Maria S. Lima"}, {"codigo": "", "nome": "x"}])
    assert (r.json()["gravados"], len(r.json()["erros"])) == (2, 1)
    from app.cadastros import resolve_map
    assert resolve_map("funcionario") == {"38": "Maria S. Lima"}
    # as rotas das telas continuam exigindo login
    assert cliente.get("/api/mes/resumo").status_code == 401


def test_webhook_e_entregue_assinado_e_tenta_de_novo(banco, maq, monkeypatch):
    wh = integracao.salvar_webhook("ERP", "http://erp.local/mes", "ordem.criada,ordem.liberada")
    with pytest.raises(ValueError, match="desconhecido"):
        integracao.salvar_webhook("x1", "http://erp.local", "ordem.inventada")
    with pytest.raises(ValueError, match="http"):
        integracao.salvar_webhook("x2", "ftp://erp.local")
    ordens.upsert_externo({"numero": A, "produto_codigo": "TP-01", "produto_descricao": "Tampa",
                           "quantidade": 10, "liberar": True}, "erp")

    recebidos, respostas = [], [500, 200, 200]

    class Falso:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, content, headers):
            recebidos.append((url, content, headers))
            return type("R", (), {"status_code": respostas.pop(0)})()
    monkeypatch.setattr(integracao.httpx, "AsyncClient", Falso)

    assert asyncio.run(integracao.despachar_pendentes()) == 1          # 1º falhou (500), 2º entregue
    url, corpo, cab = recebidos[1]
    assert url == "http://erp.local/mes" and cab["X-MES-Evento"] == "ordem.liberada"
    assert cab["X-MES-Assinatura"] == integracao.assinatura(wh["segredo"], corpo)
    assert json.loads(corpo)["dados"]["numero"] == A
    (w,) = integracao.listar_webhooks()
    assert (w["entregues"], w["pendentes"], w["ultimo_erro"]) == (1, 1, "HTTP 500")
    assert asyncio.run(integracao.despachar_pendentes()) == 0          # o que falhou espera 1 min
    with banco.get_conn() as c:
        c.execute("UPDATE webhook_entregas SET proxima_ms = 0 WHERE status = 'pendente'")
    assert asyncio.run(integracao.despachar_pendentes()) == 1
    assert integracao.listar_webhooks()[0]["pendentes"] == 0


def test_telas_op_refugo_produtos_e_turnos(cliente, banco, maq):
    """Rotas usadas pelas telas: criar/operar OP, refugo, cadastros."""
    from app.auth import create_session_cookie, COOKIE_NAME
    from app.auth.db import create_user, get_user_by_username
    for nome, papel in (("gestor_t", "gestor"), ("oper_t", "operador")):
        if not get_user_by_username(nome):
            create_user(nome, "senha-de-teste", papel)
    g = {COOKIE_NAME: create_session_cookie("gestor_t")}
    o = {COOKIE_NAME: create_session_cookie("oper_t")}
    post = lambda url, corpo=None, quem=g: cliente.post(url, json=corpo or {}, cookies=quem)

    assert post("/api/mes/produtos", {"codigo": "TP-01", "descricao": "Tampa", "ciclo_padrao_s": "17,5", "cavidades": 2}).status_code == 200
    assert post("/api/mes/produtos", {"codigo": "tp-01", "descricao": "Tampa 20L", "ciclo_padrao_s": 18, "cavidades": 2}).status_code == 200
    (p,) = cliente.get("/api/mes/produtos", cookies=g).json()
    assert (p["descricao"], p["ciclo_padrao_s"], p["cavidades"]) == ("Tampa 20L", 18.0, 2)
    assert post("/api/mes/produtos", {"codigo": "X"}, o).status_code == 403

    r = post("/api/mes/ordens", {"produto_codigo": "TP-01", "quantidade": 100, "maquina_id": maq["id"], "liberar": True})
    op = r.json()
    assert r.status_code == 200 and (op["status"], op["ciclo_efetivo_s"], op["cavidades_efetivas"]) == ("liberada", 18.0, 2)
    nova = post("/api/mes/ordens", {"produto_codigo": "NOVO-9", "produto_descricao": "Produto novo", "quantidade": 5}).json()
    assert nova["produto_descricao"] == "Produto novo" and nova["status"] == "planejada"
    assert post("/api/mes/ordens", {"produto_codigo": "TP-01"}).status_code == 400
    assert post("/api/mes/ordens", {"produto_codigo": "TP-01", "quantidade": 1}, o).status_code == 403

    oid = op["id"]
    assert post(f"/api/mes/ordens/{oid}/iniciar", quem=o).json()["status"] == "em_producao"   # operador inicia
    assert post(f"/api/mes/ordens/{oid}/cancelar", quem=o).status_code == 403                 # mas não cancela
    assert post(f"/api/mes/ordens/{nova['id']}/iniciar", {"maquina_id": maq["id"]}).status_code == 400   # máquina ocupada
    assert post(f"/api/mes/ordens/{nova['id']}/iniciar", {"maquina_id": maq["id"], "substituir": "pausar"}).json()["status"] == "em_producao"
    assert cliente.get("/api/mes/ordens?status=pausada", cookies=g).json()["ordens"][0]["id"] == oid
    assert post(f"/api/mes/ordens/{oid}/editar", {"quantidade": 250, "cliente": "ACME"}).json()["quantidade"] == 250

    motivo = cliente.get("/api/mes/base", cookies=o).json()["motivos_refugo"][0]["id"]
    assert post("/api/mes/refugos", {"ordem_id": oid, "quantidade": 3, "motivo_id": motivo}, o).status_code == 200
    assert post("/api/mes/refugos", {"ordem_id": oid, "quantidade": 0, "motivo_id": motivo}, o).status_code == 400
    (ref,) = cliente.get(f"/api/mes/refugos?ordem_id={oid}", cookies=o).json()
    assert (ref["quantidade"], ref["usuario"]) == (3, "oper_t")
    assert ordens.obter(oid)["qtd_refugo"] == 3
    assert post(f"/api/mes/ordens/{oid}/concluir", quem=o).json()["status"] == "concluida"
    assert post(f"/api/mes/ordens/{oid}/reabrir").json()["status"] == "pausada"

    turnos = cliente.get("/api/mes/turnos", cookies=g).json()
    assert [x["sigla"] for x in turnos] == ["T1", "T2", "T3"]
    assert post("/api/mes/turnos", {"sigla": "X", "nome": "Extra", "inicio": "13:00", "fim": "15:00"}).status_code == 400
    t1 = turnos[0]
    assert post("/api/mes/turnos", {"id": t1["id"], "sigla": "T1", "nome": "Manhã", "inicio": "06:00", "fim": "14:00"}).status_code == 200
    assert cliente.get("/api/mes/turnos", cookies=g).json()[0]["nome"] == "Manhã"
