"""Tempo no MES: fuso da fábrica, dia fabril e turnos.

Tudo aqui é função pura (sem banco), para ser testável.

Dia fabril: começa no início do primeiro turno do dia e dura 24 h. Com turnos
06–14 / 14–22 / 22–06, a produção das 03:00 de terça pertence ao dia fabril de
segunda (turno T3, que começou na segunda às 22:00).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _fuso() -> ZoneInfo:
    for nome in (os.environ.get("MES_TZ"), os.environ.get("TZ"), "America/Sao_Paulo"):
        if not nome:
            continue
        try:
            return ZoneInfo(nome)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("America/Sao_Paulo")


TZ = _fuso()


# ------------------------------------------------------------------ conversões
def ms_para_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, TZ)


def dt_para_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def fmt(ms: int | None, formato: str = "%d/%m/%Y %H:%M") -> str:
    if ms is None:
        return ""
    return ms_para_dt(ms).strftime(formato)


_FORMATOS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
)


def parse_local(texto: str) -> int:
    """Converte data/hora local (ISO ou formato brasileiro) em epoch ms.

    Aceita também ISO com fuso explícito ('...Z' ou '+00:00'), comum em ERPs.
    """
    s = (texto or "").strip()
    if not s:
        raise ValueError("Data vazia")
    for f in _FORMATOS:
        try:
            return dt_para_ms(datetime.strptime(s, f))
        except ValueError:
            continue
    try:
        return dt_para_ms(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        raise ValueError(f"Data inválida: '{texto}'. Use AAAA-MM-DD, AAAA-MM-DDTHH:MM ou DD/MM/AAAA.")


def parse_data(texto: str) -> str:
    """Normaliza uma data (ISO ou DD/MM/AAAA) para 'YYYY-MM-DD'."""
    return ms_para_dt(parse_local(texto)).date().isoformat()


def hhmm_para_min(texto: str) -> int:
    try:
        h, m = (texto or "").strip().split(":")
        h, m = int(h), int(m)
    except ValueError:
        raise ValueError(f"Horário inválido: '{texto}'. Use HH:MM.")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Horário inválido: '{texto}'. Use HH:MM.")
    return h * 60 + m


def min_para_hhmm(minutos: int) -> str:
    return f"{(minutos // 60) % 24:02d}:{minutos % 60:02d}"


# ---------------------------------------------------------------------- turnos
@dataclass(frozen=True)
class Turno:
    sigla: str
    nome: str
    inicio_min: int            # minutos desde 00:00
    fim_min: int               # idem; <= inicio_min significa que cruza a meia-noite
    dias: frozenset[int]       # dias da semana em que o turno COMEÇA (0 = segunda)

    @property
    def cruza_meia_noite(self) -> bool:
        return self.fim_min <= self.inicio_min

    @property
    def duracao_min(self) -> int:
        return (self.fim_min - self.inicio_min) % 1440 or 1440


def corte_do_dia_min(turnos: list[Turno]) -> int:
    """Minuto em que o dia fabril começa (início do primeiro turno; 00:00 sem turnos)."""
    return min((t.inicio_min for t in turnos), default=0)


def classificar(ts_ms: int, turnos: list[Turno]) -> tuple[str, Turno | None]:
    """(dia_fabril, turno) de um instante. turno = None quando cai fora de todos."""
    dt = ms_para_dt(ts_ms)
    minuto = dt.hour * 60 + dt.minute
    dia_semana = dt.weekday()
    for t in turnos:
        if not t.cruza_meia_noite:
            if t.inicio_min <= minuto < t.fim_min and dia_semana in t.dias:
                return dt.date().isoformat(), t
        else:
            if minuto >= t.inicio_min and dia_semana in t.dias:
                return dt.date().isoformat(), t
            if minuto < t.fim_min and (dia_semana - 1) % 7 in t.dias:
                return (dt.date() - timedelta(days=1)).isoformat(), t
    # Fora de turno: o dia fabril ainda é definido pelo corte
    d = dt.date()
    if minuto < corte_do_dia_min(turnos):
        d -= timedelta(days=1)
    return d.isoformat(), None


def janela_dia(dia: str, turnos: list[Turno]) -> tuple[int, int]:
    """[início, fim) em ms do dia fabril."""
    corte = corte_do_dia_min(turnos)
    ini = datetime.combine(date.fromisoformat(dia), dtime(corte // 60, corte % 60), TZ)
    return dt_para_ms(ini), dt_para_ms(ini + timedelta(days=1))


def janela_turno(dia: str, turno: Turno) -> tuple[int, int]:
    """[início, fim) em ms de um turno no dia fabril informado."""
    ini = datetime.combine(
        date.fromisoformat(dia), dtime(turno.inicio_min // 60, turno.inicio_min % 60), TZ
    )
    return dt_para_ms(ini), dt_para_ms(ini + timedelta(minutes=turno.duracao_min))


def dia_fabril(ts_ms: int, turnos: list[Turno]) -> str:
    return classificar(ts_ms, turnos)[0]


def somar_dias(dia: str, n: int) -> str:
    return (date.fromisoformat(dia) + timedelta(days=n)).isoformat()


# --------------------------------------------------------------- formatadores
def fmt_duracao(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    s = int(round(segundos))
    if s < 60:
        return f"{s}s"
    h, resto = divmod(s, 3600)
    m = resto // 60
    if h:
        return f"{h}h{m:02d}"
    return f"{m}min"
