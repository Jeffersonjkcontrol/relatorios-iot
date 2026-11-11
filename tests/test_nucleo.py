"""Núcleo do MES com dados sintéticos: ingestão, lotes, ordens e apontamentos.

Os valores imitam o que as injetoras publicam: `ciclo` em ms com o molde no
contexto, `producao` como totalizador, `parada` 0/1.
"""
import pytest

from apoio import MOLDE, MOLDE_B, P, ingerir, lote, t, todas, um
from app.mes import apontamentos, cadastros, ingestao, ordens
from app.mes.ingestao import calcular_delta


def producao_basica(banco, maq):
    """08:00–08:20, molde 2 cavidades: 10 peças a cada 5 min (40 no total)."""
    return ingerir(
        banco, maq,
        cavidade=[P(t(7), 2)],
        ciclo=[P(t(8, m, 10), 30000, MOLDE) for m in range(0, 21, 2)],
        contador=[P(t(8, m), 1000 + 2 * m) for m in range(0, 21, 5)],
    )


def nova_op(banco, maq, quantidade=100, **extra):
    pid = (cadastros.obter_produto_por_codigo("P-1") or {}).get("id") \
        or cadastros.salvar_produto({"codigo": "P-1", "descricao": "Tampa azul"})
    return ordens.criar({"produto_id": pid, "maquina_id": maq["id"], "quantidade": quantidade, **extra}, "ana")


# ------------------------------------------------------------------ contador
def test_delta_do_contador():
    assert calcular_delta(None, 500, 0) == (0, 0)             # primeira leitura é só a base
    assert calcular_delta(100, 130, 300) == (30, 0)
    assert calcular_delta(100, 100, 300) == (0, 0)
    assert calcular_delta(3232, 42, 540) == (42, 1)           # dispositivo religou e recomeçou do zero
    assert calcular_delta(100, 999999, 300) == (0, 2)         # salto impossível
    assert calcular_delta(50000, 30000, 10) == (0, 2)         # "reinício" para um valor impossível


def test_producao_vem_do_contador_e_molde_e_cadastrado_sozinho(banco, maq):
    r = producao_basica(banco, maq)
    assert (r.ciclos, r.contagens, r.pecas, r.moldes_novos) == (11, 5, 40, 1)

    m = cadastros.obter_molde_por_codigo("89-0001")
    assert (m["descricao"], m["cavidades"], m["ciclo_padrao_s"], m["origem"]) == ("Tampa", 2, 30.0, "auto")
    assert m["ciclos_acumulados"] == 20                       # 40 peças / 2 cavidades

    lt = lote(banco, "SEMOP.261103.T1.INJ01.89-0001")
    assert (lt["pecas"], lt["ciclos"], lt["dia"], lt["turno_sigla"]) == (40, 11, "2026-11-03", "T1")
    assert lt["inicio_ms"] == t(8, 0, 10) and lt["fim_ms"] == t(8, 20, 10)


def test_reingerir_os_mesmos_pontos_nao_duplica(banco, maq):
    producao_basica(banco, maq)
    r = producao_basica(banco, maq)
    assert (r.ciclos, r.contagens, r.pecas, r.moldes_novos) == (0, 0, 0, 0)
    assert lote(banco, "SEMOP.261103.T1.INJ01.89-0001")["pecas"] == 40
    assert cadastros.obter_molde_por_codigo("89-0001")["ciclos_acumulados"] == 20
    assert um(banco, "SELECT COUNT(*) FROM lotes") == 1


def test_reinicio_e_salto_do_contador(banco, maq):
    r = ingerir(banco, maq, contador=[
        P(t(8), 3232), P(t(8, 9), 42), P(t(8, 14), 84),       # religou: 42 + 42
        P(t(8, 19), 999999),                                  # salto absurdo: ignorado
        P(t(8, 24), 1000030),                                 # segue contando a partir dele
    ])
    assert r.pecas == 42 + 42 + 31
    assert (r.reinicios_contador, r.saltos_descartados) == (1, 1)


def test_virada_de_turno_separa_os_lotes(banco, maq):
    ingerir(banco, maq, contador=[P(t(13, 50), 100), P(t(13, 55), 110), P(t(14, 5), 120), P(t(14, 10), 130)])
    assert lote(banco, "SEMOP.261103.T1.INJ01")["pecas"] == 10
    assert lote(banco, "SEMOP.261103.T2.INJ01")["pecas"] == 20


def test_madrugada_pertence_ao_dia_fabril_anterior(banco, maq):
    ingerir(banco, maq, contador=[P(t(1, 55, dia=1), 100), P(t(2, 0, dia=1), 115)])
    lt = lote(banco, "SEMOP.261103.T3.INJ01")
    assert lt["pecas"] == 15 and lt["dia"] == "2026-11-03"


def test_maquina_sem_totalizador_conta_pelos_ciclos(banco, maq):
    cadastros.atualizar_maquina(maq["id"], {
        "fonte_contagem": "ciclo", "ciclos_por_leitura": 2, "cavidades_padrao": 4})
    r = ingerir(banco, maq,
                ciclo=[P(t(8, m), 30000, MOLDE) for m in (0, 1, 2)],
                contador=[P(t(8), 10), P(t(8, 5), 99)])       # ignorado neste modo
    assert (r.ciclos, r.contagens, r.pecas) == (3, 3, 3 * 8)
    assert cadastros.obter_molde_por_codigo("89-0001")["ciclos_acumulados"] == 6


def test_ciclo_invalido_e_descartado(banco, maq):
    r = ingerir(banco, maq, ciclo=[P(t(8), 0), P(t(8, 1), -5), P(t(8, 2), None), P(t(8, 3), 25000, MOLDE)])
    assert r.ciclos == 1
    assert um(banco, "SELECT duracao_ms FROM ciclos") == 25000


# ------------------------------------------------------------- ordens: fluxo
def test_op_com_inicio_retroativo_reatribui_a_producao(banco, maq):
    producao_basica(banco, maq)
    molde = cadastros.obter_molde_por_codigo("89-0001")
    oid = nova_op(banco, maq, molde_id=molde["id"])
    assert ordens.obter(oid)["numero"] == "OP-000001"
    ordens.liberar(oid, "ana")

    # "começou às 08:07, só registrei agora"
    ordens.iniciar(oid, "joao", inicio_ms=t(8, 7))
    o = ordens.obter(oid)
    assert (o["status"], o["qtd_produzida"], o["iniciada_ms"], o["em_execucao"]) == \
        ("em_producao", 30, t(8, 7), True)
    assert lote(banco, "OP-000001.261103.T1.INJ01")["pecas"] == 30     # 08:10, 08:15, 08:20
    assert lote(banco, "SEMOP.261103.T1.INJ01.89-0001")["pecas"] == 10  # 08:05
    assert um(banco, "SELECT COUNT(*) FROM ciclos WHERE ordem_id = ?", oid) == 7  # 08:08 em diante

    # pausa retroativa: o que veio depois do fim deixa de ser da OP
    ordens.pausar(oid, "joao", fim_ms=t(8, 17))
    o = ordens.obter(oid)
    assert (o["status"], o["qtd_produzida"], o["em_execucao"]) == ("pausada", 20, False)
    assert lote(banco, "SEMOP.261103.T1.INJ01.89-0001")["pecas"] == 20

    # retoma e conclui. A leitura das 08:20 cobre 08:15–08:20 e é atribuída ao meio
    # do intervalo (08:17:30): volta a ser da OP porque a retomada foi às 08:17:15.
    ordens.iniciar(oid, "joao", inicio_ms=t(8, 17, 15))
    assert ordens.obter(oid)["qtd_produzida"] == 30
    ordens.concluir(oid, "joao", fim_ms=t(9))
    o = ordens.obter(oid)
    assert (o["status"], o["concluida_ms"], o["qtd_produzida"], o["pct"]) == ("concluida", t(9), 30, 30.0)

    tipos = [e["tipo"] for e in ordens.historico(oid)][::-1]
    assert tipos == ["criada", "liberada", "iniciada", "pausada", "retomada", "concluida"]
    assert len(ordens.execucoes(oid)) == 2

    ordens.reabrir(oid, "ana")
    assert ordens.obter(oid)["status"] == "pausada"


def test_producao_nova_cai_na_op_em_execucao(banco, maq):
    oid = nova_op(banco, maq)
    ordens.iniciar(oid, "joao", inicio_ms=t(8))
    ingerir(banco, maq, contador=[P(t(8, 1), 0), P(t(8, 6), 12), P(t(8, 11), 24)])
    assert ordens.obter(oid)["qtd_produzida"] == 24
    assert ordens.ordem_na_maquina(maq["id"])["id"] == oid
    assert lote(banco, "OP-000001.261103.T1.INJ01")["pecas"] == 24


def test_maquina_ocupada_e_troca_de_op(banco, maq):
    a, b = nova_op(banco, maq), nova_op(banco, maq)
    ordens.iniciar(a, "joao", inicio_ms=t(8))
    with pytest.raises(ValueError, match="executando a OP"):
        ordens.iniciar(b, "joao", inicio_ms=t(9))
    with pytest.raises(ValueError, match="anterior ao início"):
        ordens.iniciar(b, "joao", inicio_ms=t(7), substituir="concluir")

    ordens.iniciar(b, "joao", inicio_ms=t(9), substituir="concluir")
    assert (ordens.obter(a)["status"], ordens.obter(a)["concluida_ms"]) == ("concluida", t(9))
    assert ordens.obter(b)["status"] == "em_producao"

    # não dá para encaixar uma execução antes do fim da anterior
    c_ = nova_op(banco, maq)
    ordens.pausar(b, "joao", fim_ms=t(10))
    with pytest.raises(ValueError, match="ainda executava outra OP"):
        ordens.iniciar(c_, "joao", inicio_ms=t(9, 30))


def test_transicoes_invalidas(banco, maq):
    oid = nova_op(banco, maq)
    with pytest.raises(ValueError):
        ordens.pausar(oid)
    with pytest.raises(ValueError):
        ordens.concluir(oid)
    with pytest.raises(ValueError, match="futuro"):
        ordens.iniciar(oid, inicio_ms=t(8, dia=4000))
    ordens.iniciar(oid, inicio_ms=t(8))
    with pytest.raises(ValueError, match="anterior ao início"):
        ordens.pausar(oid, fim_ms=t(7))
    ordens.cancelar(oid, "ana", "pedido cancelado")
    assert ordens.obter(oid)["status"] == "cancelada"
    assert ordens.ordem_na_maquina(maq["id"]) is None
    with pytest.raises(ValueError):
        ordens.cancelar(oid)

    sem_maquina = ordens.criar({"produto_codigo": "P-1", "quantidade": 10})
    with pytest.raises(ValueError, match="Informe a máquina"):
        ordens.iniciar(sem_maquina)


def test_inicio_automatico_quando_o_molde_da_op_comeca_a_ciclar(banco, maq):
    mid = cadastros.salvar_molde({"codigo": "89-0001", "descricao": "Tampa", "cavidades": 2,
                                  "ciclo_padrao_s": 30})
    oid = nova_op(banco, maq, molde_id=mid)
    ordens.liberar(oid, "ana")
    with banco.get_conn() as c:                    # liberada antes do ciclo chegar
        c.execute("UPDATE ordens SET liberada_ms = ? WHERE id = ?", (t(7), oid))

    # histórico antigo (backfill) NÃO inicia a OP
    r = ingerir(banco, maq, agora=t(23), ciclo=[P(t(7, 30), 30000, MOLDE)])
    assert r.ops_iniciadas == 0 and ordens.obter(oid)["status"] == "liberada"
    # outro molde também não
    r = ingerir(banco, maq, agora=t(8, 1), ciclo=[P(t(8), 30000, MOLDE_B)])
    assert r.ops_iniciadas == 0
    # dado "de agora" com o molde da OP: inicia no instante do ciclo
    r = ingerir(banco, maq, agora=t(8, 5), ciclo=[P(t(8, 3), 30000, MOLDE), P(t(8, 4), 30000, MOLDE)])
    assert r.ops_iniciadas == 1
    o = ordens.obter(oid)
    assert (o["status"], o["exec_inicio_ms"]) == ("em_producao", t(8, 3))
    assert um(banco, "SELECT COUNT(*) FROM ciclos WHERE ordem_id = ?", oid) == 2
    exec_ = ordens.execucoes(oid)[0]
    assert (exec_["origem"], exec_["inicio_ms"]) == ("molde", t(8, 3))
    assert "iniciada_auto" in [e["tipo"] for e in ordens.historico(oid)]


def test_molde_diferente_do_planejado_vira_lote_a_parte(banco, maq):
    mid = cadastros.salvar_molde({"codigo": "89-0001", "descricao": "Tampa"})
    oid = nova_op(banco, maq, molde_id=mid)
    ordens.iniciar(oid, inicio_ms=t(8))
    ingerir(banco, maq, ciclo=[P(t(8, 1), 30000, MOLDE), P(t(8, 2), 40000, MOLDE_B)])
    assert lote(banco, "OP-000001.261103.T1.INJ01")["ciclos"] == 1
    assert lote(banco, "OP-000001.261103.T1.INJ01.89-0002")["ciclos"] == 1


# ------------------------------------------------- ordens: cadastro e regras
def test_numeracao_edicao_e_exclusao(banco, maq):
    a = nova_op(banco, maq)
    b = ordens.criar({"numero": "PED-9/A", "produto_codigo": "P-1", "quantidade": 5})
    assert ordens.obter(a)["numero"] == "OP-000001" and ordens.obter(b)["numero"] == "PED-9/A"
    with pytest.raises(ValueError, match="Já existe"):
        ordens.criar({"numero": "PED-9/A", "produto_codigo": "P-1", "quantidade": 5})
    with pytest.raises(ValueError, match="Quantidade"):
        ordens.criar({"produto_codigo": "P-1", "quantidade": 0})
    with pytest.raises(ValueError, match="produto"):
        ordens.criar({"quantidade": 5})

    ordens.atualizar(a, {"quantidade": 250, "cliente": "ACME", "data_fim_prev": "15/11/2026"}, "ana")
    o = ordens.obter(a)
    assert (o["quantidade"], o["cliente"], o["data_fim_prev"]) == (250, "ACME", "2026-11-15")
    with pytest.raises(ValueError, match="fim previsto"):
        ordens.atualizar(a, {"data_inicio_prev": "2026-11-20", "data_fim_prev": "2026-11-10"})

    ordens.excluir(b)                              # nunca rodou: pode excluir
    assert ordens.obter(b) is None
    ordens.iniciar(a, inicio_ms=t(8))
    with pytest.raises(ValueError, match="histórico"):
        ordens.excluir(a)
    with pytest.raises(ValueError, match="número"):
        ordens.atualizar(a, {"numero": "OUTRO"})
    with pytest.raises(ValueError, match="pause"):
        ordens.atualizar(a, {"maquina": "inj02"})
    ordens.atualizar(a, {"quantidade": 300})       # quantidade pode mudar em produção
    assert ordens.obter(a)["quantidade"] == 300


def test_fila_e_reordenacao(banco, maq):
    a, b, c_ = nova_op(banco, maq), nova_op(banco, maq), nova_op(banco, maq)
    assert [o["id"] for o in ordens.listar(status=list(ordens.ABERTAS))] == [a, b, c_]
    ordens.reordenar([c_, a, b])
    assert [o["id"] for o in ordens.listar(status="planejada")] == [c_, a, b]
    ordens.iniciar(b, inicio_ms=t(8))              # quem está rodando vai para o topo
    assert ordens.listar()[0]["id"] == b
    assert ordens.contagem_por_status()["em_producao"] == 1


def test_integracao_cria_e_atualiza_pela_chave_numero(banco, maq):
    dados = {"numero": "ERP-77", "produto_codigo": "X-9", "produto_descricao": "Peça X",
             "quantidade": 500, "maquina": "INJ01", "molde_codigo": "M-77", "liberar": True,
             "cliente": "ACME", "pedido": "4500012"}
    oid, criada = ordens.upsert_externo(dados, "erp", "api:teste")
    o = ordens.obter(oid)
    assert criada and (o["status"], o["origem"], o["maquina_codigo"], o["molde_codigo"]) == \
        ("liberada", "erp", "inj01", "M-77")
    assert cadastros.obter_produto_por_codigo("X-9")["origem"] == "erp"

    oid2, criada2 = ordens.upsert_externo({**dados, "quantidade": 600}, "erp", "api:teste")
    assert (oid2, criada2) == (oid, False) and ordens.obter(oid)["quantidade"] == 600
    assert um(banco, "SELECT COUNT(*) FROM ordens") == 1

    with pytest.raises(ValueError, match="não cadastrada"):
        ordens.upsert_externo({**dados, "numero": "ERP-78", "maquina": "inj99"}, "erp")
    with pytest.raises(ValueError, match="não cadastrado"):       # manual não cria produto sozinho
        ordens.criar({"produto_codigo": "NAO-EXISTE", "quantidade": 1})


# ------------------------------------------------------------------- paradas
def test_paradas_sao_reconstruidas_das_transicoes(banco, maq):
    pontos = [P(t(9), 0), P(t(9, 30), 1), P(t(9, 31), 1), P(t(10), 0), P(t(10, 5), 0), P(t(11), 1)]
    r = ingerir(banco, maq, parada=pontos)
    assert (r.paradas_abertas, r.paradas_fechadas) == (2, 1)
    # O dispositivo só avisa depois de 5 min sem injeção: o início REAL é 5 min antes do sinal
    evs = todas(banco, "SELECT inicio_ms, sinal_ms, fim_ms, inicio_estimado FROM paradas ORDER BY inicio_ms")
    assert evs == [
        {"inicio_ms": t(9, 25), "sinal_ms": t(9, 30), "fim_ms": t(10), "inicio_estimado": 0},
        {"inicio_ms": t(10, 55), "sinal_ms": t(11), "fim_ms": None, "inicio_estimado": 0},
    ]
    r = ingerir(banco, maq, parada=pontos)                       # idempotente
    assert (r.paradas_abertas, r.paradas_fechadas) == (0, 0)
    r = ingerir(banco, maq, parada=[P(t(11, 20), 0)])
    assert r.paradas_fechadas == 1
    assert um(banco, "SELECT COUNT(*) FROM paradas WHERE fim_ms IS NULL") == 0


def test_maquina_ja_parada_no_inicio_da_coleta(banco, maq):
    outra = cadastros.obter_maquina_por_codigo("inj02")
    ingerir(banco, outra, parada=[P(t(9), 1)])
    ev = todas(banco, "SELECT * FROM paradas WHERE maquina_id = ?", outra["id"])[0]
    # sem transição observada não há como saber quando parou: fica o instante do sinal
    assert (ev["inicio_ms"], ev["fim_ms"], ev["inicio_estimado"]) == (t(9), None, 1)


def test_parada_herda_a_op_em_execucao(banco, maq):
    oid = nova_op(banco, maq)
    ordens.iniciar(oid, inicio_ms=t(8))
    ingerir(banco, maq, parada=[P(t(8, 30), 1), P(t(8, 50), 0)])
    assert um(banco, "SELECT ordem_id FROM paradas") == oid


def _garantir_motivo(codigo, nome, categoria="", classe="parada"):
    from app.cadastros import create_codigo
    create_codigo("motivo", codigo, nome, categoria, classe)


def test_cartoes_rfid_viram_motivo_e_operador_da_parada(banco, maq):
    ingerir(
        banco, maq,
        funcionario=[P(t(6), 2001.0)],
        parada=[P(t(9, 30), 1), P(t(10), 0), P(t(10, 20), 1), P(t(10, 40), 0)],
        motivo=[
            P(t(9, 26), 1001.0),      # 4 min ANTES do sinal de parada (que chega atrasado)
            P(t(9, 45), 1002.0),      # operador corrigiu: a última batida vale
            P(t(10, 15), 1005.0),     # pertence à 2ª parada, não à 1ª
        ],
    )
    p1, p2 = todas(banco, "SELECT * FROM paradas ORDER BY inicio_ms")
    assert (p1["motivo_codigo"], p1["motivo_origem"], p1["funcionario_codigo"]) == ("1002", "rfid", "2001")
    assert (p2["motivo_codigo"], p2["funcionario_codigo"]) == ("1005", "2001")

    # apontamento manual na tela tem precedência sobre o cartão
    _garantir_motivo("1001", "Falta de material", "Material")
    apontamentos.apontar_motivo_parada(p1["id"], "1001.0", "ana", "conferido com o líder")
    ingerir(banco, maq, motivo=[P(t(9, 50), 1003.0)])
    p1 = todas(banco, "SELECT * FROM paradas ORDER BY inicio_ms")[0]
    assert (p1["motivo_codigo"], p1["motivo_origem"], p1["apontado_por"]) == ("1001", "manual", "ana")

    with pytest.raises(ValueError, match="não cadastrado"):
        apontamentos.apontar_motivo_parada(p1["id"], "9999")
    # desfazer o manual devolve, na hora, a última batida de cartão daquela parada
    apontamentos.limpar_motivo_parada(p1["id"])
    p1, p2 = todas(banco, "SELECT * FROM paradas ORDER BY inicio_ms")
    assert (p1["motivo_codigo"], p1["motivo_origem"]) == ("1003", "rfid")
    assert p2["motivo_codigo"] == "1005"                         # a vizinha não é afetada
    with pytest.raises(ValueError, match="não encontrada"):
        apontamentos.limpar_motivo_parada(99999)


# ------------------------------------------------------- refugo, meta, MP
def test_refugo_meta_e_fila_de_webhooks(banco, maq):
    with banco.get_conn() as c:
        c.execute("INSERT INTO webhooks (nome, url, eventos, criado_ms) VALUES ('erp', 'http://erp.local/h', '*', 0)")
        c.execute("INSERT INTO webhooks (nome, url, eventos, criado_ms) "
                  "VALUES ('so-refugo', 'http://q.local/h', 'refugo.apontado', 0)")
    oid = nova_op(banco, maq, quantidade=30)
    ordens.iniciar(oid, inicio_ms=t(8))
    ingerir(banco, maq, contador=[P(t(8, 1), 0), P(t(8, 6), 20), P(t(8, 11), 40)])
    ingerir(banco, maq, contador=[P(t(8, 16), 50)])
    assert ordens.obter(oid)["qtd_produzida"] == 50

    def entregas(evento):
        return um(banco, "SELECT COUNT(*) FROM webhook_entregas WHERE evento = ?", evento)
    assert entregas("ordem.meta_atingida") == 1                  # uma vez só, e só para quem assina
    assert entregas("ordem.iniciada") == 1

    rid = apontamentos.apontar_refugo({"ordem_id": oid, "quantidade": 5, "motivo": "rebarba"}, "ana")
    o = ordens.obter(oid)
    assert (o["qtd_refugo"], o["qtd_boas"], o["restante"]) == (5, 45, 0)
    assert lote(banco, "OP-000001.261103.T1.INJ01")["refugo"] == 5
    assert entregas("refugo.apontado") == 2
    ref = apontamentos.listar_refugos(ordem_id=oid)[0]
    assert (ref["motivo_codigo"], ref["lote_codigo"], ref["usuario"]) == \
        ("REBARBA", "OP-000001.261103.T1.INJ01", "ana")

    # só com a máquina: descobre a OP que estava rodando naquele instante
    rid2 = apontamentos.apontar_refugo({"maquina": "INJ01", "quantidade": 2, "ts": t(8, 30)}, "ana")
    assert ordens.obter(oid)["qtd_refugo"] == 7
    apontamentos.excluir_refugo(rid)
    apontamentos.excluir_refugo(rid2)
    assert ordens.obter(oid)["qtd_refugo"] == 0

    with pytest.raises(ValueError, match="não cadastrado"):
        apontamentos.apontar_refugo({"ordem_id": oid, "quantidade": 1, "motivo": "inexistente"})
    with pytest.raises(ValueError):
        apontamentos.apontar_refugo({"ordem_id": oid, "quantidade": 0})


def test_abastecimento_de_materia_prima(banco, maq):
    mp = cadastros.salvar_mp({"codigo": "PP-H301", "descricao": "Polipropileno H301"})
    l1 = cadastros.salvar_lote_mp({"mp_id": mp, "codigo": "L-001", "fornecedor": "Braskem", "quantidade": 1000})
    l2 = cadastros.salvar_lote_mp({"mp_id": mp, "codigo": "L-002", "fornecedor": "Braskem"})
    oid = nova_op(banco, maq)
    ordens.iniciar(oid, inicio_ms=t(8))

    k1 = apontamentos.abastecer_mp({"lote_mp_id": l1, "ordem_id": oid, "inicio": t(8)}, "joao")
    apontamentos.abastecer_mp({"lote_mp_id": l2, "maquina_id": maq["id"], "inicio": t(12)}, "joao")
    consumos = {c["lote_codigo"]: c for c in apontamentos.listar_consumos(maquina_id=maq["id"])}
    assert consumos["L-001"]["fim_ms"] == t(12)                  # lote novo encerra o anterior
    assert consumos["L-002"]["fim_ms"] is None and consumos["L-002"]["ordem_numero"] == "OP-000001"
    with banco.get_conn() as c:
        em_uso = apontamentos.consumos_no_intervalo(c, maq["id"], t(9), t(10))
    assert [x["lote_codigo"] for x in em_uso] == ["L-001"]

    with pytest.raises(ValueError, match="rastreabilidade"):
        cadastros.excluir_lote_mp(l1)
    with pytest.raises(ValueError, match="já está cadastrado"):
        cadastros.salvar_lote_mp({"mp_id": mp, "codigo": "L-001"})
    apontamentos.encerrar_consumo(k1, t(11))
    assert apontamentos.listar_consumos(maquina_id=maq["id"], so_abertos=True)[0]["lote_codigo"] == "L-002"


# ----------------------------------------------------------------- cadastros
def test_validacoes_de_cadastro(banco, maq):
    with pytest.raises(ValueError, match="sobrepõe"):
        cadastros.salvar_turno({"sigla": "X1", "nome": "Extra", "inicio": "13:00", "fim": "15:00"})
    with pytest.raises(ValueError, match="sobrepõe"):             # cruza a meia-noite
        cadastros.salvar_turno({"sigla": "X2", "nome": "Extra", "inicio": "05:00", "fim": "05:30"})
    t3 = next(x for x in cadastros.listar_turnos() if x["sigla"] == "T3")
    cadastros.salvar_turno({**t3, "ativo": 0}, t3["id"])
    novo = cadastros.salvar_turno({"sigla": "n1", "nome": "Noite curta", "inicio": "22:00", "fim": "02:00"})
    assert [x.sigla for x in cadastros.turnos_ativos()] == ["T1", "T2", "N1"]
    cadastros.excluir_turno(novo)

    pid = cadastros.salvar_produto({"codigo": "P-1", "descricao": "Tampa", "peso_g": "12,5"})
    assert cadastros.obter_produto(pid)["peso_g"] == 12.5
    with pytest.raises(ValueError, match="Já existe"):
        cadastros.salvar_produto({"codigo": "p-1", "descricao": "Outra"})
    ordens.criar({"produto_id": pid, "quantidade": 1})
    with pytest.raises(ValueError, match="desative"):
        cadastros.excluir_produto(pid)

    mid = cadastros.salvar_molde({"codigo": "M1", "descricao": "Molde 1", "cavidades": 4,
                                  "ciclo_padrao_s": "22,5", "produto_id": pid, "manut_intervalo_ciclos": 50000})
    m = cadastros.obter_molde(mid)
    assert (m["cavidades"], m["ciclo_padrao_s"], m["produto_codigo"]) == (4, 22.5, "P-1")
    with pytest.raises(ValueError, match="mínimo"):
        cadastros.salvar_molde({"codigo": "M2", "descricao": "Molde 2", "cavidades": 0})
    with pytest.raises(ValueError, match="não existe"):
        cadastros.salvar_molde({"codigo": "M3", "descricao": "Molde 3", "produto_id": 999})

    assert cadastros.sincronizar_maquinas("ubidots", [{"label": "inj01"}, {"label": "inj03", "name": "Nova"}]) \
        == {"novas": 1, "total": 3}
    cadastros.atualizar_maquina(maq["id"], {"nome": "Injetora 01 — Haitian", "ativo": 0})
    assert [m["codigo"] for m in cadastros.listar_maquinas(so_ativas=True)] == ["inj02", "inj03"]
    with pytest.raises(ValueError, match="desativada"):
        ordens.iniciar(nova_op(banco, maq), inicio_ms=t(8))
