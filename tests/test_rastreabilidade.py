"""Rastreabilidade: genealogia do lote e etiqueta."""
from apoio import C, ingerir, t
from app.cadastros import create_codigo
from app.mes import apontamentos, cadastros, ordens, rastreabilidade
from app.mes.ingestao import Opcoes

A = "112729"


def producao(banco, maq):
    create_codigo("funcionario", "38", "Maria Souza")
    create_codigo("motivo", "10", "Troca de molde", classe="setup")
    ordens.upsert_externo({"numero": A, "produto_codigo": "TP-01", "produto_descricao": "Tampa 20L",
                           "quantidade": 5000, "cliente": "ACME", "ciclo_padrao_s": 17}, "erp")
    ingerir(
        banco, maq, opcoes=Opcoes(codigos_liberacao=frozenset({"30"})),
        op=[C(t(8), 1, op_rfid=A)],
        ciclo=[C(t(8, m), 17000 + 100 * (m % 3), op_rfid=A) for m in range(1, 60, 2)],
        contador=[C(t(8, 0, 5), 0, op_rfid=A), C(t(8, 30), 90, op_rfid=A), C(t(9), 180, op_rfid=A)],
        funcionario=[C(t(6), 1, fun_rfid="38"), C(t(8, 40), 1, fun_rfid="77")],
        qualidade=[C(t(8, 5), 1, status="aprovado")],
        parada=[C(t(8, 2), 0), C(t(8, 25), 1), C(t(8, 28), 0)],
        motivo=[C(t(8, 21), 1, maq_rfid="10"), C(t(8, 29), 1, maq_rfid="30")],
    )
    return "112729.261103.T1.INJ01"


def test_genealogia_do_lote(banco, maq):
    codigo = producao(banco, maq)
    oid = ordens.obter_por_numero(A)["id"]
    apontamentos.apontar_refugo({"ordem_id": oid, "quantidade": 4, "motivo": "REBARBA", "ts": t(8, 50)}, "ana")
    mp = cadastros.salvar_mp({"codigo": "PP-H301", "descricao": "Polipropileno H301"})
    lote_mp = cadastros.salvar_lote_mp({"mp_id": mp, "codigo": "L-001", "fornecedor": "Braskem"})
    apontamentos.abastecer_mp({"lote_mp_id": lote_mp, "ordem_id": oid, "inicio": t(7, 50)}, "joao")

    g = rastreabilidade.genealogia(codigo)
    lt = g["lote"]
    assert (lt["ordem_numero"], lt["produto_descricao"], lt["cliente"], lt["maquina_nome"]) == \
        (A, "Tampa 20L", "ACME", "Injetora 01")
    assert (lt["pecas"], lt["refugo"], lt["boas"], lt["turno_sigla"]) == (180, 4, 176, "T1")
    assert (g["ciclo"]["leituras"], g["ciclo"]["min_s"], g["ciclo"]["max_s"], g["ciclo"]["padrao_s"]) == \
        (30, 17.0, 17.2, 17.0)
    # quem já estava na máquina + quem bateu cartão durante o lote
    assert [(p["nome"], p["codigo"], p["ja_estava"]) for p in g["operadores"]] == \
        [("Maria Souza", "38", True), (None, "77", False)]
    assert [q["codigo"] for q in g["qualidade"]] == ["aprovado"]
    assert g["paradas"]["total"] == 1 and g["paradas"]["eventos"][0]["motivo"]["nome"] == "Troca de molde"
    assert [r["motivo_codigo"] for r in g["refugos"]] == ["REBARBA"]
    assert [m["lote_codigo"] for m in g["materia_prima"]] == ["L-001"]
    assert rastreabilidade.genealogia("NAO-EXISTE") is None


def test_busca_de_lotes(banco, maq):
    codigo = producao(banco, maq)
    assert [l["codigo"] for l in rastreabilidade.buscar("1127")] == [codigo]
    assert [l["codigo"] for l in rastreabilidade.buscar("acme")] == [codigo]
    assert [l["codigo"] for l in rastreabilidade.buscar(dia="03/11/2026")] == [codigo]
    assert rastreabilidade.buscar("tampa", dia="2026-11-04") == []
    assert rastreabilidade.buscar(maquina_id=cadastros.obter_maquina_por_codigo("inj02")["id"]) == []


def test_etiqueta_em_pdf(banco, maq):
    codigo = producao(banco, maq)
    pdf = rastreabilidade.etiqueta_pdf(codigo, "http://relatorios.local/mes?lote=" + codigo)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1500
    assert rastreabilidade.etiqueta_pdf("NAO-EXISTE", "http://x") is None
