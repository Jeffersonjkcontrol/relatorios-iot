"""Peças puras da base do MES: banco, parser de molde, turnos."""
from datetime import datetime

import pytest

from app.mes import molde_ctx, tempo
from app.mes.tempo import TZ, Turno, classificar, janela_dia, janela_turno


def ms(ano, mes, dia, h=0, m=0):
    return int(datetime(ano, mes, dia, h, m, tzinfo=TZ).timestamp() * 1000)


TODOS = frozenset(range(7))
T1 = Turno("T1", "1º turno", 6 * 60, 14 * 60, TODOS)
T2 = Turno("T2", "2º turno", 14 * 60, 22 * 60, TODOS)
T3 = Turno("T3", "3º turno", 22 * 60, 6 * 60, TODOS)
TRES = [T1, T2, T3]


# ---------------------------------------------------------------- banco
def test_schema_cria_tabelas_e_sementes(banco):
    with banco.get_conn() as c:
        tabelas = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"maquinas", "ordens", "ordem_execucoes", "ciclos", "contagens",
                "paradas", "lotes", "refugos", "api_keys", "webhooks"} <= tabelas
        assert c.execute("SELECT COUNT(*) FROM turnos").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM motivos_refugo").fetchone()[0] >= 10
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert banco.versao_schema() >= 1


def test_migracao_e_idempotente(banco):
    banco.usar_banco(banco.DB_PATH)      # força nova verificação no mesmo arquivo
    banco.garantir_schema()
    with banco.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM turnos").fetchone()[0] == 3


def test_rollback_em_erro(banco):
    with pytest.raises(RuntimeError):
        with banco.get_conn() as c:
            c.execute("INSERT INTO config (chave, valor, atualizado_ms) VALUES ('x', '1', 0)")
            raise RuntimeError("falha no meio")
    with banco.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM config WHERE chave='x'").fetchone()[0] == 0


def test_snapshot_gera_copia_integra(banco, tmp_path):
    destino = banco.snapshot(tmp_path / "copia" / "mes-snapshot.db")
    import sqlite3
    c = sqlite3.connect(destino)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("SELECT COUNT(*) FROM turnos").fetchone()[0] == 3
    c.close()


# ------------------------------------------------- molde (amostras reais)
@pytest.mark.parametrize("raw, nome, codigo, ciclo", [
    ("Botão|#89-09307|#28seg", "Botão", "89-09307", 28.0),
    ("TAMPA|#89-01933|#33segseg", "TAMPA", "89-01933", 33.0),
    ("Console.OPP|#89-09267|#65seg.", "Console.OPP", "89-09267", 65.0),
    ("REGUA|#89-01946|#40.0", "REGUA", "89-01946", 40.0),
    ("CHANNEL|#89-01909/911|#70segseg", "CHANNEL", "89-01909/911", 70.0),
    ("TOP|#89-03123|#50Segseg", "TOP", "89-03123", 50.0),
    ("maniFornoGlpPrt#89-07003#32seg.", "maniFornoGlpPrt", "89-07003", 32.0),
    ("Pe.Front.Babel|#89-01617|#45seg", "Pe.Front.Babel", "89-01617", 45.0),
    ("Peca|#12,5seg", "Peca", "", 12.5),
    ("Só o nome", "Só o nome", "", None),
    ("Peca|#12345", "Peca", "12345", None),      # número sozinho é código, não ciclo
])
def test_parse_molde(raw, nome, codigo, ciclo):
    m = molde_ctx.parse(raw)
    assert (m.nome, m.codigo, m.ciclo_padrao_s) == (nome, codigo, ciclo)
    assert m.chave == (codigo or nome)


@pytest.mark.parametrize("raw", [None, "", "   ", 123, {}, "|#|"])
def test_parse_molde_invalido(raw):
    assert molde_ctx.parse(raw) is None


def test_molde_do_contexto():
    assert molde_ctx.do_contexto({"molde": "A|#1-2|#10seg"}).codigo == "1-2"
    assert molde_ctx.do_contexto({}) is None
    assert molde_ctx.do_contexto(None) is None


# ---------------------------------------------------------------- turnos
def test_classificar_turnos_do_dia():
    assert classificar(ms(2026, 11, 10, 6, 0), TRES) == ("2026-11-10", T1)
    assert classificar(ms(2026, 11, 10, 13, 59), TRES) == ("2026-11-10", T1)
    assert classificar(ms(2026, 11, 10, 14, 0), TRES) == ("2026-11-10", T2)
    assert classificar(ms(2026, 11, 10, 22, 0), TRES) == ("2026-11-10", T3)


def test_turno_noturno_pertence_ao_dia_em_que_comecou():
    # 03:00 de quarta ainda é o T3 que começou na terça
    assert classificar(ms(2026, 11, 11, 3, 0), TRES) == ("2026-11-10", T3)
    assert classificar(ms(2026, 11, 11, 5, 59), TRES) == ("2026-11-10", T3)
    assert classificar(ms(2026, 11, 11, 6, 0), TRES) == ("2026-11-11", T1)


def test_fora_de_turno_usa_o_corte_do_dia():
    dois = [T1, T2]                      # sem turno das 22 às 06
    assert classificar(ms(2026, 11, 10, 23, 0), dois) == ("2026-11-10", None)
    assert classificar(ms(2026, 11, 11, 3, 0), dois) == ("2026-11-10", None)


def test_sem_turnos_dia_e_o_calendario():
    assert classificar(ms(2026, 11, 10, 3, 0), []) == ("2026-11-10", None)
    ini, fim = janela_dia("2026-11-10", [])
    assert ini == ms(2026, 11, 10, 0, 0) and fim == ms(2026, 11, 11, 0, 0)


def test_turno_respeita_dias_da_semana():
    uteis = Turno("T1", "1º", 6 * 60, 14 * 60, frozenset({0, 1, 2, 3, 4}))
    assert classificar(ms(2026, 11, 13, 8, 0), [uteis])[1] == uteis      # sexta
    assert classificar(ms(2026, 11, 14, 8, 0), [uteis])[1] is None       # sábado
    # noturno de sexta invade o sábado de madrugada, mas não existe o de sábado
    noite = Turno("T3", "3º", 22 * 60, 6 * 60, frozenset({0, 1, 2, 3, 4}))
    assert classificar(ms(2026, 11, 14, 2, 0), [noite]) == ("2026-11-13", noite)
    assert classificar(ms(2026, 11, 15, 2, 0), [noite])[1] is None


def test_janelas():
    ini, fim = janela_dia("2026-11-10", TRES)
    assert ini == ms(2026, 11, 10, 6, 0) and fim == ms(2026, 11, 11, 6, 0)
    ini, fim = janela_turno("2026-11-10", T3)
    assert ini == ms(2026, 11, 10, 22, 0) and fim == ms(2026, 11, 11, 6, 0)
    assert T3.duracao_min == 480 and T3.cruza_meia_noite


def test_todo_instante_cai_na_janela_do_proprio_dia_fabril():
    # consistência entre classificar() e janela_dia()
    for h in range(0, 24):
        t = ms(2026, 11, 10, h, 30)
        dia, _ = classificar(t, TRES)
        ini, fim = janela_dia(dia, TRES)
        assert ini <= t < fim


def test_parse_local_formatos():
    esperado = ms(2026, 11, 10, 8, 30)
    assert tempo.parse_local("2026-11-10T08:30") == esperado
    assert tempo.parse_local("2026-11-10 08:30") == esperado
    assert tempo.parse_local("10/11/2026 08:30") == esperado
    assert tempo.parse_local("2026-11-10") == ms(2026, 11, 10)
    assert tempo.parse_local("2026-11-10T11:30:00Z") == esperado      # UTC-3
    assert tempo.parse_data("10/11/2026") == "2026-11-10"
    with pytest.raises(ValueError):
        tempo.parse_local("amanhã")


def test_hhmm():
    assert tempo.hhmm_para_min("06:30") == 390
    assert tempo.min_para_hhmm(390) == "06:30"
    for ruim in ("25:00", "aa:bb", "0630", ""):
        with pytest.raises(ValueError):
            tempo.hhmm_para_min(ruim)
