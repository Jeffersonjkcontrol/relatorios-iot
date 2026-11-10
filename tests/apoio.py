"""Apoio aos testes do MES: pontos sintéticos no formato que as injetoras publicam."""
from datetime import datetime, timedelta

from app.mes import cadastros, ingestao
from app.mes.ingestao import Opcoes, Ponto
from app.mes.tempo import TZ

BASE = datetime(2026, 11, 3, tzinfo=TZ)        # terça-feira, já no passado
MOLDE = "Tampa|#89-0001|#30seg"
MOLDE_B = "Base|#89-0002|#40seg"


def t(h, m=0, s=0, dia=0) -> int:
    """Instante (ms) no dia-base dos testes."""
    return int((BASE + timedelta(days=dia, hours=h, minutes=m, seconds=s)).timestamp() * 1000)


def P(ts, valor, molde=None) -> Ponto:
    """Ponto no estilo antigo: o molde vem no contexto do ciclo."""
    return Ponto(ts, valor, {"molde": molde} if molde else None)


def C(ts, valor=1, **contexto) -> Ponto:
    """Ponto com contexto livre — ex.: C(ts, 17160, op_rfid="112729")."""
    return Ponto(ts, valor, contexto or None)


def ingerir(banco, maquina, agora=None, opcoes=None, **series):
    maquina = cadastros.obter_maquina(maquina["id"])
    turnos = cadastros.turnos_ativos()
    with banco.get_conn() as c:
        return ingestao.processar_maquina(c, maquina, series, opcoes or Opcoes(), turnos, agora or t(23, 59))


def um(banco, sql, *args):
    with banco.get_conn() as c:
        row = c.execute(sql, args).fetchone()
    return row[0] if row else None


def todas(banco, sql, *args):
    with banco.get_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def lote(banco, codigo):
    r = todas(banco, "SELECT * FROM lotes WHERE codigo = ?", codigo)
    return r[0] if r else None
