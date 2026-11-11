"""Fábrica com leitor RFID na máquina (firmware InjetNitron V12.x).

O operador bate o cartão da OP; a partir daí cada ciclo e cada leitura do
totalizador saem com {"op_rfid": "<nº>"} no contexto. Os códigos dos demais
cartões (máquina, operador, qualidade) também vêm no contexto, com valor 1.
"""
import pytest

from apoio import C, ingerir, lote, t, todas, um
from app.cadastros import create_codigo
from app.mes import cadastros, ingestao, ordens
from app.mes.ingestao import Opcoes, calcular_delta, codigo_do_ponto, instante_de_atribuicao

A, B = "112729", "112977"


def producao_da_op(numero, inicio_h=8, pecas=(0, 52, 104)):
    """Séries de uma OP: cartão batido, ciclos de 17,16 s e totalizador a cada 15 min."""
    return dict(
        op=[C(t(inicio_h), 1, op_rfid=numero)],
        ciclo=[C(t(inicio_h, m, 30), 17160, op_rfid=numero) for m in range(0, 31, 2)],
        contador=[C(t(inicio_h, 15 * i, 4 + i), v, op_rfid=numero) for i, v in enumerate(pecas)],
    )


# -------------------------------------------------------------- OP pelo cartão
def test_cartao_de_op_cria_a_ordem_e_atribui_a_producao(banco, maq):
    r = ingerir(banco, maq, **producao_da_op(A))
    assert (r.ops_criadas, r.ops_iniciadas, r.ciclos, r.pecas) == (1, 1, 16, 104)

    o = ordens.obter_por_numero(A)
    assert (o["status"], o["origem"], o["produto_id"], o["quantidade"]) == ("em_producao", "chao", None, 0)
    assert (o["qtd_produzida"], o["pct"], o["restante"], o["cadastro_incompleto"]) == (104, None, None, True)
    assert o["maquina_codigo"] == "inj01" and o["exec_maquinas"] == "Injetora 01"

    (ex,) = ordens.execucoes(o["id"])
    assert (ex["origem"], ex["inicio_ms"], ex["fim_ms"], ex["iniciado_por"]) == ("rfid", t(8), None, "cartão RFID")
    lt = lote(banco, "112729.261103.T1.INJ01")
    assert (lt["pecas"], lt["ciclos"], lt["ordem_id"]) == (104, 16, o["id"])
    assert um(banco, "SELECT COUNT(*) FROM ciclos WHERE op_ctx = ? AND ordem_id = ?", A, o["id"]) == 16
    assert ordens.contagem_por_status()["incompletas"] == 1


def test_reingerir_nao_duplica_op_nem_execucao(banco, maq):
    ingerir(banco, maq, **producao_da_op(A))
    r = ingerir(banco, maq, **producao_da_op(A))
    assert (r.ops_criadas, r.ops_iniciadas, r.ciclos, r.pecas) == (0, 0, 0, 0)
    assert um(banco, "SELECT COUNT(*) FROM ordens") == 1
    assert um(banco, "SELECT COUNT(*) FROM ordem_execucoes") == 1


def test_troca_de_op_fecha_a_anterior_e_o_contador_recomeca(banco, maq):
    ingerir(
        banco, maq,
        op=[C(t(8), 1, op_rfid=A), C(t(8, 20), 1, op_rfid=B)],
        ciclo=[
            C(t(8, 5), 17000, op_rfid=A), C(t(8, 19), 17000, op_rfid=A),
            C(t(8, 20, 1), 17000, op_rfid=A),      # medido antes da troca, publicado 1 s depois
            C(t(8, 21), 30000, op_rfid=B), C(t(8, 40), 30000, op_rfid=B),
        ],
        contador=[
            C(t(8, 0, 5), 100, op_rfid=A), C(t(8, 15), 150, op_rfid=A),
            C(t(8, 20), 160, op_rfid=A),           # leitura FINAL da A, publicada junto com a troca
            C(t(8, 30), 30, op_rfid=B), C(t(8, 45), 80, op_rfid=B),
        ],
    )
    a, b = ordens.obter_por_numero(A), ordens.obter_por_numero(B)
    assert (a["status"], a["qtd_produzida"], a["em_execucao"]) == ("pausada", 60, False)
    assert (b["status"], b["qtd_produzida"], b["em_execucao"]) == ("em_producao", 80, True)

    # o ciclo atrasado não "reabre" a OP anterior: uma execução para cada OP
    (ea,), (eb,) = ordens.execucoes(a["id"]), ordens.execucoes(b["id"])
    assert (ea["inicio_ms"], ea["fim_ms"], ea["motivo_fim"]) == (t(8), t(8, 20), "troca")
    assert (eb["inicio_ms"], eb["fim_ms"]) == (t(8, 20), None)
    # ...mas ele continua pertencendo à OP que o produziu
    assert um(banco, "SELECT ordem_id FROM ciclos WHERE ts_ms = ?", t(8, 20, 1)) == a["id"]
    assert um(banco, "SELECT COUNT(*) FROM contagens WHERE anomalia <> 0") == 0   # zerar na troca é normal
    assert ordens.ordem_na_maquina(maq["id"])["numero"] == B


def test_primeira_leitura_da_nova_op_vale_inteira_mesmo_sendo_maior(banco, maq):
    # A termina em 5; a B já acumulou 50 quando publica pela 1ª vez. Sem saber da
    # troca, o delta seria 45 — o certo são 50, porque o contador foi zerado.
    ingerir(banco, maq, contador=[
        C(t(8, 0, 5), 0, op_rfid=A), C(t(8, 10), 5, op_rfid=A), C(t(8, 15), 50, op_rfid=B)])
    assert ordens.obter_por_numero(A)["qtd_produzida"] == 5
    assert ordens.obter_por_numero(B)["qtd_produzida"] == 50
    assert calcular_delta(5, 50, 300, zerou=True) == (50, 0)
    assert calcular_delta(5, 999999, 300, zerou=True) == (0, 2)


def test_sobreposicao_com_pontos_da_op_antiga_nao_reabre_nada(banco, maq):
    ingerir(banco, maq,
            op=[C(t(8), 1, op_rfid=A), C(t(9), 1, op_rfid=B)],
            ciclo=[C(t(8, 30), 17000, op_rfid=A), C(t(9, 1), 17000, op_rfid=B)])
    # passada seguinte relê os últimos minutos, incluindo ciclos da OP A
    r = ingerir(banco, maq, ciclo=[C(t(8, 30), 17000, op_rfid=A), C(t(8, 58), 17000, op_rfid=A),
                                   C(t(9, 1), 17000, op_rfid=B), C(t(9, 3), 17000, op_rfid=B)])
    assert r.ops_iniciadas == 0 and r.ciclos == 2
    assert um(banco, "SELECT COUNT(*) FROM ordem_execucoes") == 2
    assert ordens.ordem_na_maquina(maq["id"])["numero"] == B


def test_op_planejada_pelo_erp_e_concluida_ao_sair_com_a_meta_batida(banco, maq):
    with banco.get_conn() as c:
        c.execute("INSERT INTO webhooks (nome, url, eventos, criado_ms) VALUES ('erp', 'http://erp/h', '*', 0)")
    ordens.upsert_externo({"numero": A, "produto_codigo": "TP-01", "produto_descricao": "Tampa 20L",
                           "quantidade": 100, "liberar": True}, "erp", "api:erp")
    ingerir(banco, maq, **producao_da_op(A))                      # 104 peças
    assert ordens.obter_por_numero(A)["status"] == "em_producao"

    ingerir(banco, maq, op=[C(t(9), 1, op_rfid=B)], ciclo=[C(t(9, 1), 30000, op_rfid=B)],
            contador=[C(t(9), 110, op_rfid=A)])                   # leitura final da A
    a = ordens.obter_por_numero(A)
    assert (a["status"], a["qtd_produzida"], a["pct"], a["concluida_ms"]) == ("concluida", 110, 100.0, t(9))
    tipos = [e["tipo"] for e in ordens.historico(a["id"])]
    assert "meta_atingida" in tipos and tipos.count("concluida") == 1
    assert um(banco, "SELECT COUNT(*) FROM webhook_entregas WHERE evento = 'ordem.meta_atingida'") == 1

    # sem a regra de conclusão automática a OP fica pausada
    ordens.upsert_externo({"numero": "300", "produto_codigo": "TP-01", "quantidade": 1}, "erp")
    sem_auto = Opcoes(auto_concluir_na_troca=False)
    ingerir(banco, maq, opcoes=sem_auto, op=[C(t(10), 1, op_rfid="300")],
            contador=[C(t(10, 15), 9, op_rfid="300")])
    ingerir(banco, maq, opcoes=sem_auto, op=[C(t(11), 1, op_rfid=B)])
    assert ordens.obter_por_numero("300")["status"] == "pausada"


def test_erp_completa_o_cadastro_de_uma_op_que_nasceu_no_cartao(banco, maq):
    ingerir(banco, maq, **producao_da_op(A))
    oid, criada = ordens.upsert_externo(
        {"numero": A, "produto_codigo": "TP-01", "produto_descricao": "Tampa 20L", "quantidade": 5000,
         "cliente": "ACME", "ciclo_padrao_s": 17, "cavidades": 1}, "erp", "api:erp")
    o = ordens.obter(oid)
    assert not criada
    assert (o["produto_codigo"], o["quantidade"], o["cliente"], o["cadastro_incompleto"]) == \
        ("TP-01", 5000, "ACME", False)
    assert (o["status"], o["qtd_produzida"], o["pct"], o["origem"]) == ("em_producao", 104, 2.1, "chao")
    # completar o produto é permitido; TROCAR o produto de uma OP em produção, não
    with pytest.raises(ValueError, match="produto não pode mudar"):
        ordens.upsert_externo({"numero": A, "produto_codigo": "OUTRO", "produto_descricao": "Outra peça"}, "erp")


def test_mesma_op_em_duas_maquinas(banco, maq):
    m2 = cadastros.obter_maquina_por_codigo("inj02")
    ingerir(banco, maq, op=[C(t(8), 1, op_rfid=A)], contador=[C(t(8, 15), 0, op_rfid=A), C(t(8, 30), 40, op_rfid=A)])
    ingerir(banco, m2, op=[C(t(8, 5), 1, op_rfid=A)], contador=[C(t(8, 15), 0, op_rfid=A), C(t(8, 30), 25, op_rfid=A)])
    o = ordens.obter_por_numero(A)
    assert (o["exec_abertas"], o["exec_maquinas"], o["qtd_produzida"]) == (2, "Injetora 01, Injetora 02", 65)
    assert lote(banco, "112729.261103.T1.INJ01")["pecas"] == 40
    assert lote(banco, "112729.261103.T1.INJ02")["pecas"] == 25

    ingerir(banco, maq, op=[C(t(9), 1, op_rfid=B)])               # sai de uma: segue em produção na outra
    o = ordens.obter_por_numero(A)
    assert (o["status"], o["exec_abertas"], o["exec_maquinas"]) == ("em_producao", 1, "Injetora 02")
    ingerir(banco, m2, op=[C(t(9, 30), 1, op_rfid=B)])            # sai da última: pausada
    assert ordens.obter_por_numero(A)["status"] == "pausada"
    assert ordens.obter_por_numero(B)["exec_abertas"] == 2

    # pela tela dá para pausar só uma das máquinas
    ordens.pausar(ordens.obter_por_numero(B)["id"], "ana", maquina_id=maq["id"])
    b = ordens.obter_por_numero(B)
    assert (b["status"], b["exec_maquinas"]) == ("em_producao", "Injetora 02")


def test_cartao_reabre_op_concluida(banco, maq):
    ingerir(banco, maq, **producao_da_op(A))
    oid = ordens.obter_por_numero(A)["id"]
    ordens.concluir(oid, "ana", fim_ms=t(9))
    ingerir(banco, maq, op=[C(t(10), 1, op_rfid=A)], ciclo=[C(t(10, 1), 17160, op_rfid=A)])
    o = ordens.obter(oid)
    assert (o["status"], o["concluida_ms"], len(ordens.execucoes(oid))) == ("em_producao", None, 2)
    assert "reaberta" in [e["tipo"] for e in ordens.historico(oid)]


def test_ops_que_nao_voltam_a_maquina_sao_encerradas(banco, maq):
    ingerir(banco, maq, op=[C(t(8), 1, op_rfid=A), C(t(9), 1, op_rfid=B)])
    manual = ordens.criar({"produto_codigo": cadastros.obter_produto(
        cadastros.salvar_produto({"codigo": "P-1", "descricao": "Peça"}))["codigo"],
        "quantidade": 10, "maquina_id": cadastros.obter_maquina_por_codigo("inj02")["id"]})
    ordens.iniciar(manual, inicio_ms=t(8))
    ordens.pausar(manual, fim_ms=t(9))

    assert ordens.encerrar_inativas(0) == 0                        # 0 = desligado
    assert ordens.encerrar_inativas(36500) == 0                    # ainda dentro do prazo
    assert ordens.encerrar_inativas(1) == 1
    a = ordens.obter_por_numero(A)
    assert (a["status"], a["concluida_ms"]) == ("concluida", t(9))
    assert ordens.obter(manual)["status"] == "pausada"             # pausa feita por gente não é mexida
    assert ordens.obter_por_numero(B)["status"] == "em_producao"


# ------------------------------------------------ atribuição pelo meio do intervalo
def test_leitura_das_14h_pertence_ao_turno_que_produziu(banco, maq):
    # Totalizador a cada 15 min: a leitura das 14:00:05 cobre 13:45–14:00 (1º turno).
    ingerir(banco, maq, contador=[
        C(t(13, 30, 4), 1000, op_rfid=A), C(t(13, 45, 5), 1052, op_rfid=A),
        C(t(14, 0, 5), 1104, op_rfid=A), C(t(14, 15, 3), 1156, op_rfid=A)])
    assert lote(banco, "112729.261103.T1.INJ01")["pecas"] == 104
    assert lote(banco, "112729.261103.T2.INJ01")["pecas"] == 52
    assert instante_de_atribuicao(t(13, 45), t(14)) == t(13, 52, 30)
    assert instante_de_atribuicao(None, t(14)) == t(14)
    # depois de horas sem publicar, não recua mais que 15 min
    assert instante_de_atribuicao(t(2), t(6)) == t(5, 45)


# ------------------------------------------------------------ cartões e sinais
def test_codigo_do_cartao_vem_do_contexto():
    assert codigo_do_ponto(C(0, 1, maq_rfid="010"), "motivo", "maq_rfid") == "10"
    assert codigo_do_ponto(C(0, 1, fun_rfid=" 38 "), "funcionario", "fun_rfid") == "38"
    assert codigo_do_ponto(C(0, 1001.0), "motivo", "maq_rfid") == "1001"      # firmware simples: código no valor
    assert codigo_do_ponto(C(0, 0, status="Reprovado"), "qualidade", "quali_rfid") == "reprovado"
    assert codigo_do_ponto(C(0, 1), "qualidade", "quali_rfid") == "aprovado"
    assert codigo_do_ponto(C(0, 1, op_rfid="112729"), "op", "op_rfid") == "112729"
    assert codigo_do_ponto(C(0, 1), "op", "op_rfid") is None                  # OP só vale se vier explícita
    assert codigo_do_ponto(C(0, 1), "bloqueio", "cont_bloq") is None


def test_cartoes_de_maquina_operador_e_qualidade(banco, maq):
    ingerir(
        banco, maq, opcoes=Opcoes(codigos_liberacao=frozenset({"30"}),
                                  variaveis={"cod_fun": "cod_fun_rfid", "motivo": "maq_rfid",
                                             "funcionario": "fun_rfid", "ciclo": "ciclo"}),
        funcionario=[C(t(6), 1, fun_rfid="0038")],
        cod_fun=[C(t(6, 1), 1, cod_fun_rfid="4512")],
        qualidade=[C(t(8, 5), 1, status="aprovado"), C(t(9), 0, status="reprovado")],
        bloqueio=[C(t(9, 24), 1), C(t(9, 52), 0)],
        parada=[C(t(9), 0), C(t(9, 30), 1), C(t(9, 50), 0)],
        motivo=[C(t(9, 24), 1, maq_rfid="010"),       # troca de molde
                C(t(9, 51), 1, maq_rfid="30")],       # liberou a máquina: não é motivo
    )
    (p,) = todas(banco, "SELECT * FROM paradas")
    assert (p["inicio_ms"], p["sinal_ms"], p["fim_ms"]) == (t(9, 25), t(9, 30), t(9, 50))
    assert (p["motivo_codigo"], p["motivo_origem"], p["funcionario_codigo"]) == ("10", "rfid", "38")

    def sinais(papel):
        return [(s["codigo"], s["valor"]) for s in todas(
            banco, "SELECT codigo, valor FROM sinais WHERE variavel = ? ORDER BY ts_ms", papel)]
    assert sinais("qualidade") == [("aprovado", 1.0), ("reprovado", 0.0)]
    assert sinais("cod_fun") == [("4512", 1.0)]
    assert sinais("motivo") == [("10", 1.0), ("30", 1.0)]
    assert sinais("bloqueio") == [(None, 1.0), (None, 0.0)]


def test_mudar_a_legenda_corrige_o_historico(banco, maq):
    series = dict(parada=[C(t(9), 0), C(t(9, 30), 1), C(t(9, 50), 0)],
                  motivo=[C(t(9, 24), 1, maq_rfid="10"), C(t(9, 51), 1, maq_rfid="30")])
    ingerir(banco, maq, **series)
    assert um(banco, "SELECT motivo_codigo FROM paradas") == "30"     # ainda ninguém disse que 30 é liberação

    create_codigo("motivo", "30", "Produção", classe="producao")
    create_codigo("motivo", "10", "Troca de molde", "Setup", classe="setup")
    assert ingestao.liberacoes_cadastradas() == frozenset({"30"})
    assert ingestao.reassociar_tudo(dias=36500) == 1
    assert um(banco, "SELECT motivo_codigo FROM paradas") == "10"
    assert Opcoes.da_config().codigos_liberacao == frozenset({"30"})

    # parada cujo único cartão era o de liberação fica sem motivo
    ingerir(banco, maq, opcoes=Opcoes.da_config(),
            parada=[C(t(12, 30), 1), C(t(12, 40), 0)], motivo=[C(t(12, 39), 1, maq_rfid="30")])
    assert todas(banco, "SELECT motivo_codigo FROM paradas ORDER BY inicio_ms")[1]["motivo_codigo"] is None


def test_atraso_do_alerta_de_parada(banco, maq):
    # a variável de alerta da própria máquina (ms) vale mais que o padrão configurado
    ingerir(banco, maq, alerta=[C(t(7), 120000)], parada=[C(t(9), 0), C(t(9, 30), 1), C(t(9, 40), 0)])
    assert um(banco, "SELECT inicio_ms FROM paradas") == t(9, 28)
    # sem atraso configurado, vale o instante do sinal
    m2 = cadastros.obter_maquina_por_codigo("inj02")
    ingerir(banco, m2, opcoes=Opcoes(alerta_padrao_ms=0), parada=[C(t(9), 0), C(t(9, 30), 1)])
    assert um(banco, "SELECT inicio_ms FROM paradas WHERE maquina_id = ?", m2["id"]) == t(9, 30)
    # paradas coladas: o início corrigido nunca invade a parada anterior
    ingerir(banco, maq, parada=[C(t(9, 41), 1), C(t(9, 45), 0)])
    assert todas(banco, "SELECT inicio_ms FROM paradas WHERE maquina_id = ? ORDER BY inicio_ms",
                 maq["id"])[1]["inicio_ms"] == t(9, 40) + 1


def test_parada_herda_a_op_do_cartao(banco, maq):
    ingerir(banco, maq, op=[C(t(8), 1, op_rfid=A)], parada=[C(t(8, 10), 0), C(t(8, 40), 1), C(t(8, 50), 0)])
    assert um(banco, "SELECT ordem_id FROM paradas") == ordens.obter_por_numero(A)["id"]


def test_opcoes_vem_da_configuracao(banco, maq):
    from app.mes import config
    config.set_("var_contador", "producao")
    config.set_("var_cod_fun", "")
    config.set_("alerta_parada_s", "180")
    o = Opcoes.da_config()
    assert o.variaveis["contador"] == "producao" and "cod_fun" not in o.variaveis
    assert (o.ctx_op, o.alerta_padrao_ms, o.auto_concluir_na_troca) == ("op_rfid", 180000, True)


def test_dispositivo_sem_ciclo_e_desativado_na_ativacao(banco, maq):
    cadastros.sincronizar_maquinas("ubidots", [{"label": "esteira02", "name": "Esteira 02"}])
    ingerir(banco, maq, ciclo=[C(t(8), 17000)])                       # inj01 já tem ciclos: não mexe
    cadastros.atualizar_maquina(cadastros.obter_maquina_por_codigo("inj02")["id"], {"setor": "Galpão 2"})
    assert cadastros.desativar_recem_criadas("ubidots", ["esteira02", "inj01", "inj02"]) == 1
    assert [m["codigo"] for m in cadastros.listar_maquinas(so_ativas=True)] == ["inj01", "inj02"]
