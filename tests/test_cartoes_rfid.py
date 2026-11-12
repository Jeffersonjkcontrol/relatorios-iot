"""Cartões RFID: regras do conteúdo (app) e o agente com um leitor simulado."""
import json
import pathlib
import sys
import threading
import urllib.request

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "agente_rfid"))
import agente  # noqa: E402

from app.mes import cartoes  # noqa: E402


# ------------------------------------------------------------- regras do app
def test_montar_blocos_compativeis_com_o_firmware():
    r = cartoes.montar("funcionario", " 038 ")
    assert r["blocos"] == {"1": "fun_rfid", "2": "38", "4": "", "5": ""}      # 4 e 5 sempre limpos
    assert cartoes.montar("op", "112729")["blocos"]["1"] == "op_rfid"
    assert cartoes.montar("op", "0112729")["conteudo"] == "0112729"          # OP mantém zeros
    p = cartoes.montar("parada", "010")
    assert (p["conteudo"], p["blocos"]["1"]) == ("10", "maq_rfid") and "BLOQUEIA" in p["avisos"][0]
    assert cartoes.montar("parada", "30")["avisos"] == []
    assert cartoes.montar("qualidade", "Reprovado")["blocos"]["2"] == "reprovado"


@pytest.mark.parametrize("tipo, conteudo, erro", [
    ("op", "11A", "números"), ("parada", "", "números"), ("funcionario", "Maria Souza", "sem espaços"),
    ("funcionario", 'a"b', "sem espaços"), ("funcionario", "", "Informe"), ("qualidade", "ok", "aprovado"),
    ("op", "1" * 17, "máximo"), ("molde", "x", "inválido"), ("dupla", "1", "inválido"),
])
def test_montar_recusa(tipo, conteudo, erro):
    with pytest.raises(ValueError, match=erro):
        cartoes.montar(tipo, conteudo)


def test_interpretar_como_o_firmware():
    # o firmware descarta TODOS os espaços e emenda os blocos 2, 4 e 5
    r = cartoes.interpretar({"1": "fun_rfid        ", "2": "3 8", "4": "", "5": ""})
    assert (r["tipo"], r["conteudo"], r["rotulo"]) == ("funcionario", "38", "Funcionário")
    assert cartoes.interpretar({"1": "", "2": ""})["rotulo"] == "Cartão vazio"
    assert cartoes.interpretar({"1": "molde", "2": "x"})["tipo"] is None


def test_historico_de_gravacao(banco):
    cartoes.registrar("funcionario", "38", "04A1B2C3", "Maria", "ana")
    cartoes.registrar("parada", "10", "04FFEE00", "Troca de molde", "ana")
    assert [h["conteudo"] for h in cartoes.historico()] == ["10", "38"]
    assert cartoes.historico(uid="04a1b2c3")[0]["nome"] == "Maria"
    with pytest.raises(ValueError):
        cartoes.registrar("molde", "1", "x")


# --------------------------------------------------------- leitor simulado
class CartaoFalso:
    """MIFARE Classic 1K que responde às APDUs do ACR122U."""

    def __init__(self, uid=(0x04, 0xA1, 0xB2, 0xC3), falha_gravar=None, nao_persiste=None):
        self.uid = list(uid)
        self.blocos = {n: [0] * 16 for n in range(64)}
        self.chave_ok = False
        self.autenticado = set()
        self.falha_gravar, self.nao_persiste = falha_gravar, nao_persiste
        self.gravados = []

    def transmitir(self, apdu):
        cla, ins, p1, p2 = apdu[:4]
        if ins == 0xCA:
            return self.uid, 0x90, 0x00
        if ins == 0x82:
            self.chave_ok = apdu[5:11] == [0xFF] * 6
            return [], 0x90, 0x00
        if ins == 0x86:
            if not self.chave_ok:
                return [], 0x63, 0x00
            self.autenticado.add(apdu[7])
            return [], 0x90, 0x00
        if ins == 0xB0:
            return (list(self.blocos[p2]), 0x90, 0x00) if p2 in self.autenticado else ([], 0x63, 0x00)
        if ins == 0xD6:
            if p2 not in self.autenticado or p2 == self.falha_gravar:
                return [], 0x63, 0x00
            self.gravados.append(p2)
            if p2 != self.nao_persiste:
                self.blocos[p2] = list(apdu[5:21])
            return [], 0x90, 0x00
        return [], 0x6A, 0x81


def test_agente_grava_le_e_confere():
    c = CartaoFalso()
    leitor = agente.Leitor(c.transmitir)
    r = leitor.gravar({"1": "fun_rfid", "2": "38", "4": "", "5": ""})
    assert (r["ok"], r["uid"], r["conferido"]) == (True, "04A1B2C3", True)
    assert bytes(c.blocos[1]) == b"fun_rfid        "            # completado com espaços
    assert bytes(c.blocos[4]) == b" " * 16
    assert leitor.ler([1, 2]) == {"1": "fun_rfid", "2": "38"}


def test_agente_nunca_grava_blocos_de_chave():
    c = CartaoFalso()
    leitor = agente.Leitor(c.transmitir)
    for proibido in (0, 3, 7, 6, "x"):
        with pytest.raises(agente.ErroCartao):
            leitor.gravar({"1": "fun_rfid", proibido: "x"})
    assert c.gravados == []                                     # recusou antes de gravar qualquer coisa
    with pytest.raises(agente.ErroCartao, match="16"):
        leitor.gravar({"2": "1" * 17})
    with pytest.raises(agente.ErroCartao):
        leitor.gravar({"2": "ção"})                             # só ASCII
    assert c.gravados == []


def test_agente_acusa_falha_e_gravacao_nao_confirmada():
    with pytest.raises(agente.ErroCartao, match="gravar o bloco 2"):
        agente.Leitor(CartaoFalso(falha_gravar=2).transmitir).gravar({"1": "fun_rfid", "2": "38"})
    with pytest.raises(agente.ErroCartao, match="não confirmou"):
        agente.Leitor(CartaoFalso(nao_persiste=2).transmitir).gravar({"1": "fun_rfid", "2": "38"})


@pytest.mark.parametrize("origem, ok", [
    ("http://relatorios.local", True), ("http://192.168.0.251", True), ("http://10.1.2.3:8000", True),
    ("http://172.20.0.5", True), ("http://localhost:8011", True), ("http://127.0.0.1:8000", True),
    ("https://exemplo.com", False), ("http://8.8.8.8", False), ("http://relatorios.local.evil.com", False),
    (None, False), ("", False),
])
def test_origens_permitidas(origem, ok):
    assert agente.origem_permitida(origem) is ok


def test_servidor_http_do_agente(monkeypatch):
    c = CartaoFalso()

    class Conexao:
        def disconnect(self):
            pass
    monkeypatch.setattr(agente, "abrir_leitor", lambda: (agente.Leitor(c.transmitir), Conexao(), "ACR122U falso"))
    srv = agente.ThreadingHTTPServer(("127.0.0.1", 0), agente.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def chamar(caminho, corpo=None, origem="http://relatorios.local", metodo=None):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(base + caminho, data=dados, method=metodo,
                                     headers={"Content-Type": "application/json", "Origin": origem})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), (json.loads(r.read() or b"{}") if r.status != 204 else {})
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read() or b"{}")

    try:
        st, cab, d = chamar("/status")
        assert st == 200 and d["cartao"] and d["uid"] == "04A1B2C3"
        assert cab["Access-Control-Allow-Origin"] == "http://relatorios.local"
        st, cab, _ = chamar("/gravar", metodo="OPTIONS")
        assert st == 204 and cab["Access-Control-Allow-Private-Network"] == "true"
        st, _, d = chamar("/gravar", {"blocos": {"1": "maq_rfid", "2": "10", "4": "", "5": ""}})
        assert st == 200 and d["conferido"]
        st, _, d = chamar("/ler", {"blocos": [1, 2]})
        assert d["blocos"] == {"1": "maq_rfid", "2": "10"}
        assert chamar("/gravar", {"blocos": {"3": "x"}})[0] == 409
        assert chamar("/gravar", {"blocos": {"1": "x"}}, origem="https://site-qualquer.com")[0] == 403
        assert chamar("/status", origem="http://8.8.8.8")[0] == 403
        assert chamar("/gravar", metodo="OPTIONS", origem="https://site-qualquer.com")[0] == 403
    finally:
        srv.shutdown()
