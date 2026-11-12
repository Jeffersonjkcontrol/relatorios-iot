"""Saúde da contagem: totalizador x leituras de ciclo."""
from apoio import C, ingerir, t
from app.mes import cadastros, producao
from app.mes.tempo import janela_dia


def janela():
    return janela_dia("2026-11-03", cadastros.turnos_ativos())


def test_saude_da_contagem(banco, maq):
    m2 = cadastros.obter_maquina_por_codigo("inj02")
    # inj01: 30 leituras de ciclo (= 60 ciclos) e 60 peças contadas -> íntegra
    ingerir(banco, maq, cavidade=[C(t(7), 1)],
            ciclo=[C(t(8, m), 17000) for m in range(0, 60, 2)],
            contador=[C(t(8, 0, 5), 0), C(t(8, 30), 30), C(t(9), 60)],
            reinicio=[C(t(8, 10), 9)])
    # inj02: as mesmas 60 leituras de ciclo, mas só 50 peças -> subcontando
    ingerir(banco, m2, cavidade=[C(t(7), 1)],
            ciclo=[C(t(8, m // 2, (m % 2) * 30), 17000) for m in range(60)],
            contador=[C(t(8, 0, 5), 0), C(t(9), 50)])
    d = producao.saude_contagem(*janela())
    por = {m["nome"]: m for m in d["maquinas"]}
    a, b = por["Injetora 01"], por["Injetora 02"]
    assert (a["esperado"], a["pecas"], a["situacao"], a["reinicios"], a["causas"]) == \
        (60, 60, "ok", 1, {"queda de tensão": 1})
    assert (b["esperado"], b["pecas"], b["situacao"], b["faltando"]) == (120, 50, "subcontando", 70)
    assert d["maquinas"][0]["nome"] == "Injetora 02"           # pior primeiro
    assert (d["geral"]["subcontando"], d["geral"]["ok"], d["geral"]["faltando"]) == (1, 1, 70)


def test_ciclos_com_contagem_bloqueada_saem_do_esperado(banco, maq):
    ingerir(banco, maq, cavidade=[C(t(7), 1)],
            ciclo=[C(t(8, m), 17000) for m in range(60)],
            contador=[C(t(8, 0, 5), 0), C(t(9), 60)],
            bloqueio=[C(t(8, 30), 1), C(t(9, 0, 30), 0)])
    m = producao.saude_contagem(*janela())["maquinas"][0]
    # 30 das 60 leituras caíram no bloqueio: esperado = 30 × 2 = 60, igual ao contado
    assert (m["esperado"], m["pecas"], m["situacao"], m["ciclos_bloqueados"]) == (60, 60, "ok", 60)
    assert m["bloqueado_s"] == 1830


def test_pouca_producao_nao_e_julgada(banco, maq):
    ingerir(banco, maq, ciclo=[C(t(8), 17000)], contador=[C(t(8, 0, 5), 0), C(t(8, 30), 1)])
    assert producao.saude_contagem(*janela())["maquinas"][0]["situacao"] == "sem_base"
