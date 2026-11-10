"""Ingestão: pontos brutos da plataforma IoT -> fatos do MES.

Recebe, por máquina, as séries já buscadas pelo coletor e grava:

    ciclo        -> ciclos          (tempo de ciclo + molde do contexto)
    contador     -> contagens       (delta do totalizador = peças produzidas)
    parada       -> paradas         (eventos reconstruídos das transições 0/1)
    cavidade     -> sinais          (cavidades configuradas no dispositivo)
    motivo       -> sinais + motivo da parada (cartão RFID)
    funcionario  -> sinais + operador da parada

Por que a produção vem do CONTADOR e não da contagem de ciclos: medido em campo,
o firmware publica `ciclo` a cada 2 ciclos da máquina, enquanto o totalizador
`producao` conta todas as peças (ciclos × cavidades). Contar leituras de ciclo
subestimaria a produção pela metade.

Tudo aqui é idempotente — reprocessar o mesmo lote de pontos não duplica nada:
ciclos têm chave única (máquina, instante) e as séries de estado (contador,
parada) só aceitam pontos posteriores ao último gravado.
"""
from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from dataclasses import asdict, dataclass, field

from app.cadastros import _normalize_codigo
from app.mes import cadastros, eventos, molde_ctx, ordens, tempo
from app.mes.db import agora_ms
from app.mes.lotes import Atribuidor
from app.transforms import get_transform

PAPEIS = ("ciclo", "contador", "parada", "cavidade", "motivo", "funcionario")

TAXA_MAX_PECAS_S = 40                    # acima disso o salto do contador é erro, não produção
DUR_MAX_CICLO_MS = 6 * 3600 * 1000
ANTES_MOTIVO_MS = 10 * 60 * 1000         # o sinal de parada chega ~5 min depois da parada real;
                                         # o cartão do motivo costuma ser batido ANTES dele
DEPOIS_MOTIVO_MS = 2 * 60 * 1000
MAX_IDADE_FUNC_MS = 24 * 3600 * 1000
JANELA_AUTO_INICIO_MS = 30 * 60 * 1000   # início automático de OP só com dado "de agora"
JANELA_EVENTO_MS = 30 * 60 * 1000        # idem para webhooks de parada (não inundar no backfill)


@dataclass
class Ponto:
    ts_ms: int
    valor: float | None
    contexto: dict | None = None


@dataclass
class Opcoes:
    plataforma: str = "ubidots"
    var_ciclo: str = "ciclo"
    auto_cadastrar_moldes: bool = True
    auto_iniciar_op: bool = True


@dataclass
class Resumo:
    ciclos: int = 0
    contagens: int = 0
    pecas: int = 0
    paradas_abertas: int = 0
    paradas_fechadas: int = 0
    moldes_novos: int = 0
    ops_iniciadas: int = 0
    reinicios_contador: int = 0
    saltos_descartados: int = 0
    maquinas: int = 0
    erros: list[str] = field(default_factory=list)

    def somar(self, outro: "Resumo") -> None:
        for campo, valor in asdict(outro).items():
            if campo == "erros":
                self.erros.extend(valor)
            else:
                setattr(self, campo, getattr(self, campo) + valor)

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ utilidades
def _numero(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def duracao_ms(valor, plataforma: str, var_ciclo: str = "ciclo") -> int | None:
    """Tempo de ciclo em ms, a partir do valor bruto publicado.

    Usa a transformação cadastrada em app/transforms.py; sem ela, deduz a
    unidade (ciclo de injeção nunca passa de 1000 s, então >= 1000 é ms).
    """
    v = _numero(valor)
    if v is None or v <= 0:
        return None
    tr = get_transform(plataforma, var_ciclo)
    segundos = v * tr.factor if tr else (v / 1000 if v >= 1000 else v)
    ms = int(round(segundos * 1000))
    return ms if 0 < ms <= DUR_MAX_CICLO_MS else None


class _Cavidades:
    """Cavidades configuradas no dispositivo ao longo do tempo."""

    def __init__(self, c: sqlite3.Connection, maquina: dict, novos: list[Ponto]):
        pontos = {r["ts_ms"]: r["valor"] for r in c.execute(
            "SELECT ts_ms, valor FROM sinais WHERE maquina_id = ? AND variavel = 'cavidade'",
            (maquina["id"],))}
        for p in novos:
            if _numero(p.valor) is not None:
                pontos[p.ts_ms] = p.valor
        self._ts = sorted(pontos)
        self._val = [pontos[t] for t in self._ts]
        self._padrao = int(maquina.get("cavidades_padrao") or 1)

    def em(self, ts_ms: int) -> int:
        if not self._ts:
            return self._padrao
        # antes do 1º registro, o primeiro valor conhecido é o melhor palpite
        i = max(0, bisect_right(self._ts, ts_ms) - 1)
        v = int(round(self._val[i] or 0))
        return v if 1 <= v <= 128 else self._padrao


class _MoldeAtual:
    """Molde em uso num instante: o do ciclo mais recente até ali."""

    def __init__(self, c: sqlite3.Connection, maquina_id: int):
        self._c, self._mid = c, maquina_id
        self._ts: list[int] = []
        self._ids: list[int] = []
        self._cache_banco: dict[int, int | None] = {}

    def registrar(self, ts_ms: int, molde_id: int) -> None:
        self._ts.append(ts_ms)
        self._ids.append(molde_id)

    def em(self, ts_ms: int) -> int | None:
        i = bisect_right(self._ts, ts_ms) - 1
        if i >= 0:
            return self._ids[i]
        # anterior a todos os ciclos deste lote de trabalho: consulta o banco
        limite = self._ts[0] if self._ts else ts_ms
        chave = min(ts_ms, limite)
        if chave not in self._cache_banco:
            row = self._c.execute(
                "SELECT molde_id FROM ciclos WHERE maquina_id = ? AND ts_ms <= ? AND molde_id IS NOT NULL "
                "ORDER BY ts_ms DESC LIMIT 1", (self._mid, ts_ms)).fetchone()
            self._cache_banco[chave] = row["molde_id"] if row else None
        return self._cache_banco[chave]


def _ordenados(pontos: list[Ponto] | None) -> list[Ponto]:
    return sorted(pontos or [], key=lambda p: p.ts_ms)


# ------------------------------------------------------------------- por série
def _gravar_sinais(c: sqlite3.Connection, maquina_id: int, papel: str, pontos: list[Ponto]) -> int:
    n = 0
    for p in pontos:
        v = _numero(p.valor)
        if v is None:
            continue
        ctx = json.dumps(p.contexto, ensure_ascii=False) if p.contexto else None
        n += c.execute(
            "INSERT OR IGNORE INTO sinais (maquina_id, variavel, ts_ms, valor, contexto) VALUES (?, ?, ?, ?, ?)",
            (maquina_id, papel, p.ts_ms, v, ctx)).rowcount
    return n


def _ingerir_ciclos(c, maquina, pontos, opcoes, atrib, molde_atual, cav, resumo, agora,
                    batidas: dict[int, float], turnos) -> None:
    if not pontos:
        return
    mid = maquina["id"]
    ja_gravados = {r[0] for r in c.execute(
        "SELECT ts_ms FROM ciclos WHERE maquina_id = ? AND ts_ms BETWEEN ? AND ?",
        (mid, pontos[0].ts_ms, pontos[-1].ts_ms))}
    cache_moldes: dict[str, int] = {}
    modo_ciclo = maquina["fonte_contagem"] == "ciclo"
    fator = float(maquina.get("ciclos_por_leitura") or 1)

    for p in pontos:
        dur = duracao_ms(p.valor, opcoes.plataforma, opcoes.var_ciclo)
        if dur is None:
            continue
        molde_id = None
        ctx = molde_ctx.do_contexto(p.contexto)
        if ctx:
            if opcoes.auto_cadastrar_moldes:
                molde_id = cadastros.garantir_molde_do_contexto(c, ctx, cav.em(p.ts_ms), cache_moldes)
            else:
                row = c.execute("SELECT id FROM moldes WHERE codigo = ? COLLATE NOCASE",
                                (ctx.chave[:40],)).fetchone()
                molde_id = row["id"] if row else None
        if molde_id is not None:
            molde_atual.registrar(p.ts_ms, molde_id)
        if p.ts_ms in ja_gravados:
            continue

        ordem_id = atrib.ordem_em(p.ts_ms)
        if (ordem_id is None and opcoes.auto_iniciar_op and molde_id is not None
                and agora - p.ts_ms <= JANELA_AUTO_INICIO_MS):
            candidata = ordens.candidata_auto_inicio(c, mid, molde_id, p.ts_ms)
            if candidata:
                ordens.iniciar_na_conexao(c, candidata, mid, p.ts_ms, "", turnos,
                                          automatico=True, reatribuir=False)
                atrib.recarregar_execucoes()
                ordem_id = candidata
                resumo.ops_iniciadas += 1

        lote_id = atrib.lote_para(p.ts_ms, ordem_id, molde_id)
        c.execute(
            "INSERT INTO ciclos (maquina_id, ts_ms, duracao_ms, molde_id, ordem_id, lote_id) "
            "VALUES (?, ?, ?, ?, ?, ?)", (mid, p.ts_ms, dur, molde_id, ordem_id, lote_id))
        resumo.ciclos += 1

        if modo_ciclo:
            # máquina sem totalizador: cada leitura de ciclo vale cavidades × fator peças
            pecas = max(1, int(round(cav.em(p.ts_ms) * fator)))
            c.execute(
                "INSERT OR IGNORE INTO contagens (maquina_id, ts_ms, contador, pecas, molde_id, ordem_id, lote_id) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?)", (mid, p.ts_ms, pecas, molde_id, ordem_id, lote_id))
            resumo.contagens += 1
            resumo.pecas += pecas
            if molde_id is not None:
                batidas[molde_id] = batidas.get(molde_id, 0) + fator


def calcular_delta(anterior: float | None, atual: float, intervalo_s: float) -> tuple[int, int]:
    """(peças, anomalia) entre duas leituras do totalizador.

    anomalia: 0 = normal, 1 = contador reiniciou (dispositivo religou / operador
    zerou), 2 = salto impossível descartado.
    """
    if anterior is None:
        return 0, 0                       # primeira leitura: só estabelece a base
    teto = max(1.0, intervalo_s) * TAXA_MAX_PECAS_S
    delta = atual - anterior
    if delta < 0:
        # recomeçou do zero: o valor atual já é o que foi produzido desde então
        return (int(round(atual)), 1) if atual <= teto else (0, 2)
    if delta > teto:
        return 0, 2
    return int(round(delta)), 0


def _ingerir_contador(c, maquina, pontos, atrib, molde_atual, resumo, batidas) -> None:
    if not pontos or maquina["fonte_contagem"] != "contador":
        return
    mid = maquina["id"]
    ult = c.execute(
        "SELECT ts_ms, contador FROM contagens WHERE maquina_id = ? AND contador IS NOT NULL "
        "ORDER BY ts_ms DESC LIMIT 1", (mid,)).fetchone()
    ts_ant, val_ant = (ult["ts_ms"], ult["contador"]) if ult else (None, None)
    cav_molde: dict[int, int] = {}

    for p in pontos:
        valor = _numero(p.valor)
        if valor is None or valor < 0:
            continue
        if ts_ant is not None and p.ts_ms <= ts_ant:
            continue                       # já ingerido (ou fora de ordem): a série é só-acréscimo
        intervalo_s = (p.ts_ms - ts_ant) / 1000 if ts_ant is not None else 0
        pecas, anomalia = calcular_delta(val_ant, valor, intervalo_s)
        molde_id = molde_atual.em(p.ts_ms)
        ordem_id = atrib.ordem_em(p.ts_ms)
        lote_id = atrib.lote_para(p.ts_ms, ordem_id, molde_id) if pecas > 0 else None
        c.execute(
            "INSERT INTO contagens (maquina_id, ts_ms, contador, pecas, anomalia, molde_id, ordem_id, lote_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, p.ts_ms, valor, pecas, anomalia, molde_id, ordem_id, lote_id))
        resumo.contagens += 1
        resumo.pecas += pecas
        resumo.reinicios_contador += anomalia == 1
        resumo.saltos_descartados += anomalia == 2
        if pecas and molde_id is not None:
            if molde_id not in cav_molde:
                row = c.execute("SELECT cavidades FROM moldes WHERE id = ?", (molde_id,)).fetchone()
                cav_molde[molde_id] = max(1, row["cavidades"] if row else 1)
            batidas[molde_id] = batidas.get(molde_id, 0) + pecas / cav_molde[molde_id]
        ts_ant, val_ant = p.ts_ms, valor


def _ingerir_paradas(c, maquina, pontos, atrib, resumo, agora) -> None:
    if not pontos:
        return
    mid = maquina["id"]
    ult = c.execute(
        "SELECT ts_ms, valor FROM sinais WHERE maquina_id = ? AND variavel = 'parada' "
        "ORDER BY ts_ms DESC LIMIT 1", (mid,)).fetchone()
    estado: bool | None = (ult["valor"] >= 0.5) if ult else None
    ts_ant = ult["ts_ms"] if ult else None

    for p in pontos:
        v = _numero(p.valor)
        if v is None:
            continue
        if ts_ant is not None and p.ts_ms <= ts_ant:
            continue
        parada = v >= 0.5
        c.execute("INSERT OR IGNORE INTO sinais (maquina_id, variavel, ts_ms, valor) VALUES (?, 'parada', ?, ?)",
                  (mid, p.ts_ms, v))
        recente = agora - p.ts_ms <= JANELA_EVENTO_MS
        if parada and estado is not True:
            aberta = c.execute("SELECT 1 FROM paradas WHERE maquina_id = ? AND fim_ms IS NULL", (mid,)).fetchone()
            if not aberta:
                c.execute(
                    "INSERT OR IGNORE INTO paradas (maquina_id, inicio_ms, inicio_estimado, ordem_id) "
                    "VALUES (?, ?, ?, ?)", (mid, p.ts_ms, 1 if estado is None else 0, atrib.ordem_em(p.ts_ms)))
                resumo.paradas_abertas += 1
                if recente:
                    eventos.publicar(c, "parada.iniciada", {
                        "maquina": maquina["codigo"], "maquina_nome": maquina["nome"],
                        "inicio": tempo.ms_para_dt(p.ts_ms).isoformat(timespec="seconds")})
        elif not parada and estado is True:
            ev = c.execute("SELECT id, inicio_ms FROM paradas WHERE maquina_id = ? AND fim_ms IS NULL",
                           (mid,)).fetchone()
            if ev:
                c.execute("UPDATE paradas SET fim_ms = ? WHERE id = ?", (p.ts_ms, ev["id"]))
                resumo.paradas_fechadas += 1
                if recente:
                    eventos.publicar(c, "parada.encerrada", {
                        "maquina": maquina["codigo"], "maquina_nome": maquina["nome"],
                        "inicio": tempo.ms_para_dt(ev["inicio_ms"]).isoformat(timespec="seconds"),
                        "fim": tempo.ms_para_dt(p.ts_ms).isoformat(timespec="seconds"),
                        "duracao_s": round((p.ts_ms - ev["inicio_ms"]) / 1000)})
        estado, ts_ant = parada, p.ts_ms


def associar_apontamentos(c: sqlite3.Connection, maquina_id: int, desde_ms: int, agora: int) -> int:
    """Liga cartões RFID (motivo e funcionário) às paradas da máquina.

    Regras:
    - Motivo: vale a ÚLTIMA batida entre 10 min antes do sinal de parada e 2 min
      depois do fim. Uma batida pertence a uma parada só (a mais antiga que a cobre).
    - Motivo apontado manualmente na tela nunca é sobrescrito pelo cartão.
    - Funcionário: último cartão batido até o fim da parada, com no máximo 24 h.
    """
    paradas = c.execute(
        "SELECT * FROM paradas WHERE maquina_id = ? AND COALESCE(fim_ms, ?) >= ? ORDER BY inicio_ms",
        (maquina_id, agora, desde_ms - ANTES_MOTIVO_MS)).fetchall()
    if not paradas:
        return 0
    primeira = paradas[0]["inicio_ms"]
    batidas = c.execute(
        "SELECT ts_ms, valor FROM sinais WHERE maquina_id = ? AND variavel = 'motivo' AND ts_ms >= ? ORDER BY ts_ms",
        (maquina_id, primeira - ANTES_MOTIVO_MS)).fetchall()
    cartoes = c.execute(
        "SELECT ts_ms, valor FROM sinais WHERE maquina_id = ? AND variavel = 'funcionario' AND ts_ms >= ? "
        "ORDER BY ts_ms", (maquina_id, primeira - MAX_IDADE_FUNC_MS)).fetchall()
    anterior = c.execute(
        "SELECT fim_ms FROM paradas WHERE maquina_id = ? AND inicio_ms < ? ORDER BY inicio_ms DESC LIMIT 1",
        (maquina_id, primeira)).fetchone()
    fim_anterior = anterior["fim_ms"] if anterior else None

    alteradas = 0
    for ev in paradas:
        fim = ev["fim_ms"] if ev["fim_ms"] is not None else agora
        inicio_janela = ev["inicio_ms"] - ANTES_MOTIVO_MS
        if fim_anterior is not None:
            inicio_janela = max(inicio_janela, fim_anterior + DEPOIS_MOTIVO_MS + 1)
        candidatas = [b for b in batidas if inicio_janela <= b["ts_ms"] <= fim + DEPOIS_MOTIVO_MS]
        if candidatas and ev["motivo_origem"] != "manual":
            b = candidatas[-1]
            codigo = _normalize_codigo(b["valor"])
            if codigo != ev["motivo_codigo"] or b["ts_ms"] != ev["motivo_ts_ms"]:
                c.execute("UPDATE paradas SET motivo_codigo = ?, motivo_origem = 'rfid', motivo_ts_ms = ? "
                          "WHERE id = ?", (codigo, b["ts_ms"], ev["id"]))
                alteradas += 1
        operador = None
        for k in cartoes:
            if k["ts_ms"] > fim:
                break
            if fim - k["ts_ms"] <= MAX_IDADE_FUNC_MS:
                operador = k
        if operador is not None:
            codigo = _normalize_codigo(operador["valor"])
            if codigo != ev["funcionario_codigo"]:
                c.execute("UPDATE paradas SET funcionario_codigo = ? WHERE id = ?", (codigo, ev["id"]))
                alteradas += 1
        fim_anterior = fim
    return alteradas


# ------------------------------------------------------------------ orquestração
def processar_maquina(c: sqlite3.Connection, maquina: dict, series: dict[str, list[Ponto]],
                      opcoes: Opcoes, turnos: list[tempo.Turno], agora: int | None = None) -> Resumo:
    """Ingere as séries de UMA máquina dentro da transação de `c`."""
    agora = agora or agora_ms()
    resumo = Resumo(maquinas=1)
    mid = maquina["id"]
    ser = {papel: _ordenados(series.get(papel)) for papel in PAPEIS}
    todos = [p.ts_ms for lista in ser.values() for p in lista]
    if not todos:
        return resumo
    mais_antigo = min(todos)
    moldes_antes = c.execute("SELECT COUNT(*) FROM moldes").fetchone()[0]

    _gravar_sinais(c, mid, "cavidade", ser["cavidade"])
    cav = _Cavidades(c, maquina, ser["cavidade"])
    atrib = Atribuidor(c, maquina, turnos, desde_ms=mais_antigo)
    molde_atual = _MoldeAtual(c, mid)
    batidas: dict[int, float] = {}

    _ingerir_ciclos(c, maquina, ser["ciclo"], opcoes, atrib, molde_atual, cav, resumo, agora, batidas, turnos)
    _ingerir_contador(c, maquina, ser["contador"], atrib, molde_atual, resumo, batidas)
    _ingerir_paradas(c, maquina, ser["parada"], atrib, resumo, agora)
    _gravar_sinais(c, mid, "motivo", ser["motivo"])
    _gravar_sinais(c, mid, "funcionario", ser["funcionario"])
    if ser["parada"] or ser["motivo"] or ser["funcionario"]:
        associar_apontamentos(c, mid, mais_antigo, agora)

    for molde_id, n in batidas.items():
        if round(n):
            c.execute("UPDATE moldes SET ciclos_acumulados = ciclos_acumulados + ? WHERE id = ?",
                      (int(round(n)), molde_id))
    atrib.finalizar()
    ordens.notificar_meta(c, atrib.ordens_tocadas)
    resumo.moldes_novos = c.execute("SELECT COUNT(*) FROM moldes").fetchone()[0] - moldes_antes
    return resumo
