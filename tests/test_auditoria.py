"""Auditoria: toda ação que altera algo fica registrada, sem senhas."""
import pytest

from app import auditoria
from app.mes import integracao


@pytest.fixture()
def cliente(banco, maq):
    from fastapi.testclient import TestClient
    import app.main as principal
    from app.auth.db import create_user, get_user_by_username
    for nome, papel in (("gestor_a", "gestor"), ("oper_a", "operador")):
        if not get_user_by_username(nome):
            create_user(nome, "senha-de-teste-123", papel)
    return TestClient(principal.app)


def entrar(cliente, usuario, senha="senha-de-teste-123"):
    return cliente.post("/login", data={"username": usuario, "password": senha, "next": "/"}, follow_redirects=False)


def test_descrever_e_resumir_sem_senhas():
    assert auditoria.descrever("POST", "/api/mes/ordens/12/concluir") == ("Concluiu ordem de produção", "12")
    assert auditoria.descrever("DELETE", "/api/mes/refugos/7") == ("Excluiu apontamento de refugo", "7")
    assert auditoria.descrever("POST", "/rota/nova") == ("POST /rota/nova", "")
    d = auditoria.resumir(b'{"numero": "112729", "quantidade": 500, "password": "x", "chave": "mes_abc"}',
                          "application/json")
    assert d == "numero=112729; quantidade=500"
    d = auditoria.resumir(b"username=maria&password=segredo&role=operador", "application/x-www-form-urlencoded")
    assert "segredo" not in d and d == "username=maria; role=operador"
    assert auditoria.resumir(b'[{"numero": 1}, {"numero": 2}]', "application/json") == "2 item(ns)"
    assert auditoria.resumir(b'{"tipo": "funcionario", "texto": "1;A\\n2;B"}', "application/json") == "tipo=funcionario; 2 linha(s)"
    assert auditoria.resumir(b"nao e json", "application/json") == ""


def test_acoes_pela_tela_ficam_registradas(cliente, banco):
    assert entrar(cliente, "oper_a", "errada").status_code == 303
    assert entrar(cliente, "gestor_a").status_code == 303

    r = cliente.post("/api/mes/produtos", json={"codigo": "TP-01", "descricao": "Tampa", "ciclo_padrao_s": 17})
    assert r.status_code == 200                                 # a rota leu o corpo depois do middleware
    oid = cliente.post("/api/mes/ordens", json={"produto_codigo": "TP-01", "quantidade": 100}).json()["id"]
    assert cliente.post(f"/api/mes/ordens/{oid}/concluir").status_code == 400     # recusada: OP não iniciada
    cliente.post("/users", data={"username": "novo_u", "password": "outra-senha-1", "role": "operador"},
                 follow_redirects=False)
    cliente.post("/users", data={"username": "novo_u", "password": "outra-senha-1", "role": "operador"},
                 follow_redirects=False)                       # repetido: recusado via ?error=
    cliente.post("/api/mes/cartoes/preparar", json={"tipo": "op", "conteudo": "1"})   # só valida: não entra
    cliente.get("/api/mes/ordens")                                                      # consulta: não entra
    cliente.post("/logout", follow_redirects=False)

    linhas = auditoria.listar()[::-1]
    resumo = [(l["usuario"], l["acao"], l["status"] >= 400) for l in linhas]
    assert resumo == [
        ("oper_a", "Tentativa de login com usuário ou senha errados", True),
        ("gestor_a", "Entrou no sistema", False),
        ("gestor_a", "Salvou produto", False),
        ("gestor_a", "Criou ordem de produção", False),
        ("gestor_a", "Concluiu ordem de produção", True),
        ("gestor_a", "Criou usuário", False),
        ("gestor_a", "Criou usuário", True),
        ("gestor_a", "Saiu do sistema", False),
    ]
    assert linhas[3]["detalhe"] == "quantidade=100; produto_codigo=TP-01"
    assert linhas[4]["item"] == str(oid)
    assert "recusado: " in linhas[6]["detalhe"]
    assert not any("outra-senha" in l["detalhe"] or "senha-de-teste" in l["detalhe"] for l in linhas)
    assert linhas[1]["papel"] == "gestor" and linhas[1]["ip"]


def test_api_do_erp_registra_o_nome_da_chave(cliente, banco):
    k = integracao.criar_chave("ERP Protheus", "rw")
    cliente.post("/api/v1/ordens", headers={"X-API-Key": k["chave"]},
                 json={"numero": "9", "produto_codigo": "X", "produto_descricao": "Peça", "quantidade": 1})
    cliente.post("/api/v1/ordens", headers={"X-API-Key": "mes_falsa"}, json={"numero": "9"})
    a, b = auditoria.listar()[::-1]
    assert (a["usuario"], a["papel"], a["acao"], a["status"]) == ("api:ERP Protheus", "api", "ERP enviou ordens de produção", 200)
    assert (b["usuario"], b["status"]) == ("api:chave inválida", 401)


def test_tela_filtros_e_csv(cliente, banco):
    entrar(cliente, "gestor_a")
    cliente.post("/api/mes/produtos", json={"codigo": "TP-01", "descricao": "Tampa"})
    assert auditoria.usuarios() == ["gestor_a"]
    assert len(auditoria.listar(busca="produto")) == 1 and auditoria.listar(so_falhas=True) == []
    r = cliente.get("/admin/auditoria?busca=produto")
    assert r.status_code == 200 and "Salvou produto" in r.text
    csv_ = cliente.get("/admin/auditoria.csv").text
    assert csv_.startswith("﻿data_hora;usuario") and "Salvou produto" in csv_
    assert cliente.get("/admin/auditoria?inicio=amanha").status_code == 200        # data inválida: avisa
    entrar(cliente, "oper_a")
    assert cliente.get("/admin/auditoria").status_code == 403                      # só gestor
    with banco.get_conn() as c:
        c.execute("UPDATE auditoria SET ts_ms = 0")
    assert auditoria.limpar_antigos() >= 3
