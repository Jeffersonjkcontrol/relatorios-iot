"""Atribuição de produção a ordens e lotes — o mecanismo da rastreabilidade.

Todo ciclo e toda contagem recebem:
- ordem_id: a OP cuja execução (ordem_execucoes) cobre aquele instante na máquina
- lote_id:  o lote de OP × máquina × dia fabril × turno (× molde, quando difere)

O lote é identificado pelo próprio código, que é determinístico:

    OP000123.261110.T1.INJ82            produção com OP
    SEMOP.261110.T1.INJ82.89-09307      produção sem OP (agrupa pelo molde)

Como a atribuição é por instante, mudar uma execução (iniciar a OP com horário
retroativo, corrigir o fim) exige `reatribuir` a janela afetada — os totais são
sempre recalculados a partir dos fatos, nunca incrementados.
"""
from __future__ import annotations

import re
import sqlite3

from app.mes import tempo
from app.mes.db import agora_ms

FIM_DOS_TEMPOS = 9_000_000_000_000   # sentinela para "sem fim" (ano 2255)

_NAO_SEGURO = re.compile(r"[^A-Za-z0-9_-]+")


def _seguro(texto: str) -> str:
    """Normaliza um trecho do código do lote (sem espaços, barras ou pontos)."""
    return _NAO_SEGURO.sub("-", str(texto or "").strip()).strip("-").upper() or "X"


def montar_codigo(numero_op: str | None, dia: str, turno_sigla: str, maquina_codigo: str,
                  molde_codigo: str | None) -> str:
    partes = [
        _seguro(numero_op) if numero_op else "SEMOP",
        dia[2:4] + dia[5:7] + dia[8:10],
        turno_sigla or "FT",                 # FT = fora de turno
        _seguro(maquina_codigo),
    ]
    if molde_codigo:
        partes.append(_seguro(molde_codigo))
    return ".".join(partes)


class Atribuidor:
    """Resolve OP e lote para instantes de UMA máquina, com cache por lote de trabalho."""

    def __init__(self, c: sqlite3.Connection, maquina: dict, turnos: list[tempo.Turno],
                 desde_ms: int = 0):
        self.c = c
        self.maquina = maquina
        self.turnos = turnos
        self._ordens: dict[int, sqlite3.Row] = {}
        self._moldes: dict[int, str] = {}
        self._lotes: dict[str, int] = {}
        self.lotes_tocados: set[int] = set()
        self.ordens_tocadas: set[int] = set()
        self._desde_ms = desde_ms
        self.recarregar_execucoes()

    # --- execuções -----------------------------------------------------
    def recarregar_execucoes(self) -> None:
        self._execs = self.c.execute(
            "SELECT ordem_id, inicio_ms, COALESCE(fim_ms, ?) AS fim_ms FROM ordem_execucoes "
            "WHERE maquina_id = ? AND COALESCE(fim_ms, ?) > ? ORDER BY inicio_ms",
            (FIM_DOS_TEMPOS, self.maquina["id"], FIM_DOS_TEMPOS, self._desde_ms),
        ).fetchall()

    def ordem_em(self, ts_ms: int) -> int | None:
        for e in self._execs:
            if e["inicio_ms"] <= ts_ms < e["fim_ms"]:
                return e["ordem_id"]
        return None

    # --- lote ----------------------------------------------------------
    def _ordem(self, ordem_id: int) -> sqlite3.Row | None:
        if ordem_id not in self._ordens:
            self._ordens[ordem_id] = self.c.execute(
                "SELECT id, numero, molde_id FROM ordens WHERE id = ?", (ordem_id,)
            ).fetchone()
        return self._ordens[ordem_id]

    def _molde_codigo(self, molde_id: int) -> str:
        if molde_id not in self._moldes:
            row = self.c.execute("SELECT codigo FROM moldes WHERE id = ?", (molde_id,)).fetchone()
            self._moldes[molde_id] = row["codigo"] if row else ""
        return self._moldes[molde_id]

    def lote_para(self, ts_ms: int, ordem_id: int | None, molde_id: int | None) -> int:
        dia, turno = tempo.classificar(ts_ms, self.turnos)
        ordem = self._ordem(ordem_id) if ordem_id else None
        numero = ordem["numero"] if ordem else None
        # Com OP, o molde só entra no código quando DIVERGE do planejado — é um
        # lote à parte, porque foi feito com outra ferramenta.
        if ordem:
            diverge = ordem["molde_id"] is not None and molde_id is not None \
                and molde_id != ordem["molde_id"]
            molde_no_codigo = self._molde_codigo(molde_id) if diverge else None
        else:
            molde_no_codigo = self._molde_codigo(molde_id) if molde_id else None

        codigo = montar_codigo(numero, dia, turno.sigla if turno else "",
                               self.maquina["codigo"], molde_no_codigo)
        lote_id = self._lotes.get(codigo)
        if lote_id is None:
            row = self.c.execute("SELECT id FROM lotes WHERE codigo = ?", (codigo,)).fetchone()
            if row:
                lote_id = row["id"]
            else:
                lote_id = self.c.execute(
                    "INSERT INTO lotes (codigo, ordem_id, maquina_id, molde_id, dia, turno_sigla, "
                    "turno_nome, inicio_ms, fim_ms, criado_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (codigo, ordem["id"] if ordem else None, self.maquina["id"], molde_id, dia,
                     turno.sigla if turno else "", turno.nome if turno else "Fora de turno",
                     ts_ms, ts_ms, agora_ms()),
                ).lastrowid
            self._lotes[codigo] = lote_id
        self.lotes_tocados.add(lote_id)
        if ordem_id:
            self.ordens_tocadas.add(ordem_id)
        return lote_id

    def finalizar(self) -> None:
        """Recalcula os totais de tudo que foi tocado neste lote de trabalho."""
        recalcular_lotes(self.c, self.lotes_tocados)
        recalcular_ordens(self.c, self.ordens_tocadas)


# ------------------------------------------------------------------ recálculos
def _em_blocos(ids, tamanho: int = 400):
    ids = [i for i in ids if i is not None]
    for i in range(0, len(ids), tamanho):
        yield ids[i:i + tamanho]


def recalcular_lotes(c: sqlite3.Connection, lote_ids) -> None:
    for bloco in _em_blocos(set(lote_ids)):
        marcas = ",".join("?" * len(bloco))
        c.execute(f"""
            UPDATE lotes SET
                pecas = COALESCE((SELECT SUM(k.pecas) FROM contagens k WHERE k.lote_id = lotes.id), 0),
                ciclos = (SELECT COUNT(*) FROM ciclos x WHERE x.lote_id = lotes.id),
                refugo = COALESCE((SELECT SUM(r.quantidade) FROM refugos r WHERE r.lote_id = lotes.id), 0),
                inicio_ms = COALESCE((
                    SELECT MIN(t) FROM (
                        SELECT MIN(ref_ms) AS t FROM contagens WHERE lote_id = lotes.id
                        UNION ALL SELECT MIN(ts_ms) FROM ciclos WHERE lote_id = lotes.id)), inicio_ms),
                fim_ms = COALESCE((
                    SELECT MAX(t) FROM (
                        SELECT MAX(ts_ms) AS t FROM contagens WHERE lote_id = lotes.id
                        UNION ALL SELECT MAX(ts_ms) FROM ciclos WHERE lote_id = lotes.id)), fim_ms)
            WHERE id IN ({marcas})""", bloco)


def recalcular_ordens(c: sqlite3.Connection, ordem_ids) -> None:
    agora = agora_ms()
    for bloco in _em_blocos(set(ordem_ids)):
        marcas = ",".join("?" * len(bloco))
        c.execute(f"""
            UPDATE ordens SET
                qtd_produzida = COALESCE((SELECT SUM(k.pecas) FROM contagens k WHERE k.ordem_id = ordens.id), 0),
                qtd_refugo = COALESCE((SELECT SUM(r.quantidade) FROM refugos r WHERE r.ordem_id = ordens.id), 0),
                atualizado_ms = ?
            WHERE id IN ({marcas})""", [agora, *bloco])


def lote_para_refugo(c: sqlite3.Connection, maquina_id: int, ordem_id: int | None,
                     ts_ms: int) -> int | None:
    """Lote ao qual um refugo pertence: o mais recente da mesma OP (ou, sem OP, da
    mesma máquina) iniciado até aquele instante."""
    if ordem_id:
        row = c.execute(
            "SELECT id FROM lotes WHERE ordem_id = ? AND maquina_id = ? AND inicio_ms <= ? "
            "ORDER BY inicio_ms DESC LIMIT 1", (ordem_id, maquina_id, ts_ms)).fetchone()
    else:
        row = c.execute(
            "SELECT id FROM lotes WHERE ordem_id IS NULL AND maquina_id = ? AND inicio_ms <= ? "
            "ORDER BY inicio_ms DESC LIMIT 1", (maquina_id, ts_ms)).fetchone()
    return row["id"] if row else None


# ----------------------------------------------------------------- reatribuição
_TEMPO = {"ciclos": "ts_ms", "contagens": "ref_ms", "paradas": "inicio_ms"}


def reatribuir(c: sqlite3.Connection, maquina: dict, ini_ms: int, fim_ms: int | None,
               turnos: list[tempo.Turno]) -> dict:
    """Refaz OP e lote de tudo que a máquina produziu em [ini_ms, fim_ms).

    Chamado sempre que os intervalos de execução mudam (início retroativo,
    correção de fim). Pontos em que o PRÓPRIO dispositivo informou a OP (op_ctx,
    cartão RFID) não mudam de OP: o cartão é a fonte da verdade.
    """
    fim = fim_ms if fim_ms is not None else FIM_DOS_TEMPOS
    mid = maquina["id"]
    janela = (mid, ini_ms, fim)

    ordens_antes = {r[0] for r in c.execute(
        "SELECT DISTINCT ordem_id FROM contagens WHERE maquina_id = ? AND ref_ms >= ? AND ref_ms < ? "
        "AND ordem_id IS NOT NULL", janela)}
    lotes_antes = {r[0] for r in c.execute(
        "SELECT DISTINCT lote_id FROM contagens WHERE maquina_id = ? AND ref_ms >= ? AND ref_ms < ? "
        "AND lote_id IS NOT NULL "
        "UNION SELECT DISTINCT lote_id FROM ciclos WHERE maquina_id = ? AND ts_ms >= ? AND ts_ms < ? "
        "AND lote_id IS NOT NULL", janela + janela)}

    # 1) OP pelo intervalo de execução que cobre o instante
    for tabela, coluna in _TEMPO.items():
        so_sem_cartao = "" if tabela == "paradas" else " AND op_ctx IS NULL"
        c.execute(f"""
            UPDATE {tabela} SET ordem_id = (
                SELECT e.ordem_id FROM ordem_execucoes e
                WHERE e.maquina_id = {tabela}.maquina_id AND e.inicio_ms <= {tabela}.{coluna}
                  AND (e.fim_ms IS NULL OR {tabela}.{coluna} < e.fim_ms)
                ORDER BY e.inicio_ms DESC LIMIT 1)
            WHERE maquina_id = ? AND {coluna} >= ? AND {coluna} < ?{so_sem_cartao}""", janela)

    # 2) Lote de cada ponto (depende da OP, do dia fabril, do turno e do molde)
    atrib = Atribuidor(c, maquina, turnos, desde_ms=ini_ms)
    movidos = 0
    for tabela in ("contagens", "ciclos"):
        coluna = _TEMPO[tabela]
        filtro = " AND pecas > 0" if tabela == "contagens" else ""
        linhas = c.execute(
            f"SELECT id, {coluna} AS t, ordem_id, molde_id, lote_id FROM {tabela} "
            f"WHERE maquina_id = ? AND {coluna} >= ? AND {coluna} < ?{filtro}", janela).fetchall()
        mudancas = []
        for ln in linhas:
            novo = atrib.lote_para(ln["t"], ln["ordem_id"], ln["molde_id"])
            if novo != ln["lote_id"]:
                mudancas.append((novo, ln["id"]))
        if mudancas:
            c.executemany(f"UPDATE {tabela} SET lote_id = ? WHERE id = ?", mudancas)
            movidos += len(mudancas)

    # 3) Totais: lotes e ordens de antes e de depois
    todos_lotes = lotes_antes | atrib.lotes_tocados
    recalcular_lotes(c, todos_lotes)

    # Lotes que ficaram vazios deixam de existir (o refugo ligado a eles é religado)
    vazios = [r["id"] for bloco in _em_blocos(todos_lotes) for r in c.execute(
        f"SELECT id FROM lotes WHERE pecas = 0 AND ciclos = 0 AND id IN ({','.join('?' * len(bloco))})",
        bloco)]
    orfaos = []
    for bloco in _em_blocos(vazios):
        marcas = ",".join("?" * len(bloco))
        orfaos += c.execute(
            f"SELECT id, maquina_id, ordem_id, ts_ms FROM refugos WHERE lote_id IN ({marcas})", bloco
        ).fetchall()
        c.execute(f"DELETE FROM lotes WHERE id IN ({marcas})", bloco)
    religados = set()
    for r in orfaos:
        novo = lote_para_refugo(c, r["maquina_id"], r["ordem_id"], r["ts_ms"])
        c.execute("UPDATE refugos SET lote_id = ? WHERE id = ?", (novo, r["id"]))
        if novo:
            religados.add(novo)
    recalcular_lotes(c, religados)

    recalcular_ordens(c, ordens_antes | atrib.ordens_tocadas)
    return {"pontos_movidos": movidos, "lotes_removidos": len(vazios)}
