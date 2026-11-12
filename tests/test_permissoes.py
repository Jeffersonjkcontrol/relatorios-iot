"""Papéis e permissões configuráveis pelo gestor."""
import sqlite3

import pytest

from app.auth import permissoes


@pytest.fixture()
def cliente(banco, maq):
    from fastapi.testclient import TestClient
    import app.main as principal
    from app.auth.db import create_user, get_user_by_username
    for nome, papel in (("gest", "gestor"), ("supe", "supervisor"), ("oper", "operador")):
        if not get_user_by_username(nome):
            create_user(nome, "senha-de-teste-123", papel)
    return TestClient(principal.app)


def como(cliente, usuario):
    cliente.cookies.clear()
    r = cliente.post("/login", data={"username": usuario, "password": "senha-de-teste-123", "next": "/"},
                     follow_redirects=False)
    assert r.status_code == 303
    return cliente


def test_padrao_e_gestor_sempre_pode(banco):
    from app.auth.db import User
    g, s, o = (User(1, n, p, False, 0) for n, p in (("g", "gestor"), ("s", "supervisor"), ("o", "operador")))
    assert all(permissoes.pode(g, p) for p in permissoes.PERMISSOES)
    assert permissoes.pode(s, "op_planejar") and not permissoes.pode(s, "auditoria")
    assert permissoes.pode(o, "op_executar") and not permissoes.pode(o, "op_planejar")
    assert permissoes.abas_config(o) == [] and "coleta" not in permissoes.abas_config(s)
    assert permissoes.abas_config(g) == list(permissoes.ABAS_CONFIG)
    permissoes.salvar({"operador": ["legenda", "inexistente"], "supervisor": []})
    assert permissoes.matriz() == {"operador": {"legenda"}, "supervisor": set()}
    assert permissoes.abas_config(o) == ["paradas", "funcionarios"]
    assert not permissoes.pode(None, "ia")


def test_banco_antigo_ganha_o_papel_supervisor(banco, tmp_path):
    import app.ai.db as geral
    from app.auth import db as usuarios
    caminho = tmp_path / "antigo.db"
    c = sqlite3.connect(caminho)
    c.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('gestor','operador')),
        must_change_password INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)""")
    c.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
    c.execute("INSERT INTO users (username, password_hash, role, created_at, full_name) VALUES ('ana','x','gestor',0,'Ana')")
    c.commit(); c.close()
    original = geral.DB_PATH
    geral.DB_PATH = caminho
    try:
        usuarios._init()
        usuarios.create_user("sup", "senha-1234", "supervisor")
        with geral.get_conn() as c2:
            assert [tuple(r) for r in c2.execute("SELECT username, role, full_name FROM users ORDER BY id")] == \
                [("ana", "gestor", "Ana"), ("sup", "supervisor", None)]
        usuarios._init()                                       # segunda vez: não refaz nada
    finally:
        geral.DB_PATH = original


def test_regras_valem_no_servidor(cliente, banco):
    # --- operador (padrão): executa OP e aponta, não planeja nem edita cadastros
    como(cliente, "gest")
    oid = cliente.post("/api/mes/ordens", json={"produto_codigo": "P1", "produto_descricao": "Peça",
                                                "quantidade": 10, "maquina_id": 1}).json()["id"]
    como(cliente, "oper")
    assert cliente.post("/api/mes/ordens", json={"produto_codigo": "P1", "quantidade": 1}).status_code == 403
    assert cliente.post(f"/api/mes/ordens/{oid}/liberar").status_code == 403
    assert cliente.post(f"/api/mes/ordens/{oid}/iniciar").status_code == 200
    assert cliente.post("/api/mes/produtos", json={"codigo": "X", "descricao": "Xis"}).status_code == 403
    assert cliente.get("/config", follow_redirects=False).status_code == 403
    assert cliente.get("/admin/auditoria").status_code == 403
    assert cliente.get("/ai").status_code == 200
    menu = cliente.get("/mes").text
    assert 'href="/users"' not in menu and 'href="/config"' not in menu and 'id="btn-nova-op"' not in menu

    # --- supervisor (padrão): planeja e cadastra, mas nada de Admin/usuários/coleta/integração
    como(cliente, "supe")
    assert cliente.post(f"/api/mes/ordens/{oid}/pausar").status_code == 200
    assert cliente.post("/api/mes/produtos", json={"codigo": "X", "descricao": "Xis"}).status_code == 200
    assert cliente.get("/config").status_code == 200
    for rota in ("/admin", "/users", "/api/mes/config", "/api/mes/integracao"):
        assert cliente.get(rota).status_code == 403, rota
    assert cliente.post("/users/permissoes", data={"supervisor": "auditoria"}).status_code == 403
    pagina = cliente.get("/config").text
    assert 'data-aba="produtos"' in pagina and 'data-aba="coleta"' not in pagina and 'data-aba="geral"' not in pagina

    # --- gestor muda as permissões: operador ganha a legenda e perde a IA
    como(cliente, "gest")
    r = cliente.post("/users/permissoes", data={"operador": ["op_executar", "legenda"], "supervisor": ["auditoria"]},
                     follow_redirects=False)
    assert r.status_code == 303
    como(cliente, "oper")
    assert cliente.post("/api/mes/legenda", json={"tipo": "motivo", "codigo": "7", "nome": "Manutenção"}).status_code == 200
    assert cliente.get("/ai").status_code == 403
    assert cliente.post("/api/mes/refugos", json={"ordem_id": oid, "quantidade": 1, "motivo_id": 1}).status_code == 403
    como(cliente, "supe")
    assert cliente.get("/admin/auditoria").status_code == 200
    assert cliente.post("/api/mes/produtos", json={"codigo": "Y", "descricao": "Ypsilon"}).status_code == 403

    # --- troca de papel pela tela e recusas na auditoria
    como(cliente, "gest")
    from app.auth.db import get_user_by_username
    oid1 = get_user_by_username("oper")[0].id
    assert cliente.post(f"/users/{oid1}/role", data={"role": "supervisor"}, follow_redirects=False).status_code == 303
    assert get_user_by_username("oper")[0].role == "supervisor"
    from app import auditoria
    recusas = [a["acao"] for a in auditoria.listar(so_falhas=True)]
    assert "Criou ordem de produção" in recusas and "Alterou as permissões dos papéis" in recusas
