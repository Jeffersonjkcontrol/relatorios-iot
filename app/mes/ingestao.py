"""Ingestão: pontos brutos da plataforma IoT -> fatos do MES.

Recebe, por máquina, as séries já buscadas pelo coletor (por PAPEL, não pelo
label da plataforma) e grava:

    ciclo        -> ciclos          tempo de ciclo; o contexto traz a OP e/ou o molde
    contador     -> contagens       delta do totalizador = peças produzidas
    parada       -> paradas         eventos reconstruídos das transições 0/1
    op           -> execuções       cartão de OP batido no leitor da máquina
    motivo       -> motivo da parada (cartão de máquina)
    funcionario / cod_fun           operador da parada e da genealogia
    qualidade, bloqueio, reinicio,
    cavidade, alerta                guardados em `sinais` para consulta

Três fatos medidos em campo moldam este módulo:

1. O firmware publica `ciclo` a cada 2 ciclos da máquina, e o totalizador conta
   todas as peças. Por isso a PRODUÇÃO vem do contador; o ciclo serve para tempo
   de ciclo e para saber qual OP/molde está na máquina.
2. O totalizador sai a cada 15 min (ou 5), e a leitura das 14:00 cobre o que foi
   feito das 13:45 às 14:00. Cada contagem é atribuída ao MEIO do intervalo que
   cobre (ref_ms) — senão o fim de cada turno cairia no turno seguinte.
3. `parada = 1` só é publicado depois de N minutos sem injeção. O início real da
   parada é o instante do sinal menos esse atraso.

Tudo é idempotente — reprocessar o mesmo lote de pontos não duplica nada: ciclos
têm chave única (máquina, instante) e as séries de estado (contador, parada) só
aceitam pontos posteriores ao último gravado.
"""
from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from dataclasses import asdict, dataclass, field

from app.cadastros import _normalize_codigo, codigos_da_classe
from app.mes import cadastros, config, eventos, molde_ctx, ordens, tempo
from app.mes.db import agora_ms
from app.mes.lotes import Atribuidor
from app.transforms import get_transform

PAPEIS = ("ciclo", "contador", "parada", "cavidade", "alerta", "motivo", "funcionario",
          "cod_fun", "op", "qualidade", "bloqueio", "reinicio")
# Papéis guardados como vieram (os demais viram fatos em tabelas próprias)
PAPEIS_DE_SINAL = ("cavidade", "alerta", "motivo", "funcionario", "cod_fun", "op",
                   "qualidade", "bloqueio", "reinicio")

TAXA_MAX_PECAS_S = 40                    # acima disso o salto do contador é erro, não produção
DUR_MAX_CICLO_MS = 6 * 3600 * 1000
ALERTA_MAX_MS = 3600 * 1000
MEIO_INTERVALO_MAX_MS = 15 * 60 * 1000   # atribuição recua no máximo 15 min (intervalo de 30)
ANTES_MOTIVO_MS = 10 * 60 * 1000         # cartão batido um pouco antes de a máquina parar
DEPOIS_MOTIVO_MS = 2 * 60 * 1000
MAX_IDADE_FUNC_MS = 24 * 3600 * 1000
RETORNO_ESPURIO_MS = 2 * 60 * 1000       # ciclo da OP anterior que chega logo após a troca
JANELA_AUTO_INICIO_MS = 30 * 60 * 1000   # início automático de OP só com dado "de agora"
JANELA_EVENTO_MS = 30 * 60 * 1000        # webhooks só para o que é recente (não inundar no histórico)


@dataclass
class Ponto:
    ts_ms: int
    valor: float | None
    contexto: dict | None = None


def _variaveis_padrao() -> dict[str, str]:
    return {papel: config.PADROES[chave] for papel, chave in config.PAPEL_PARA_CONFIG.items()}


@dataclass
class Opcoes:
    plataforma: str = "ubidots"
    variaveis: dict[str, str] = field(default_factory=_variaveis_padrao)   # papel -> label
    ctx_op: str = "op_rfid"
    ctx_molde: str = "molde"
    alerta_padrao_ms: int = 300_000
    auto_cadastrar_moldes: bool = True
    auto_iniciar_op: bool = True
    auto_concluir_na_troca: bool = True
    codigos_liberacao: frozenset = frozenset()   # cartões de máquina que NÃO são motivo de parada

    @classmethod
    def da_config(cls, cfg: dict[str, str] | None = None) -> "Opcoes":
        cfg = cfg or config.todas()
        return cls(
            plataforma=cfg["plataforma"],
            variaveis=config.variaveis(cfg),
            ctx_op=cfg.get("ctx_op", "").strip(),
            ctx_molde=cfg.get("ctx_molde", "").strip(),
            alerta_padrao_ms=max(0, int(float(cfg.get("alerta_parada_s") or 0))) * 1000,
            auto_cadastrar_moldes=cfg["auto_cadastrar_moldes"] == "1",
            auto_iniciar_op=cfg["auto_iniciar_op"] == "1",
            auto_concluir_na_troca=cfg["auto_concluir_na_troca"] == "1",
            codigos_liberacao=liberacoes_cadastradas(),
        )


def liberacoes_cadastradas() -> frozenset:
    """Códigos de cartão de máquina cadastrados como 'produção (liberação)'."""
    return frozenset(codigos_da_classe("producao"))


@dataclass
class Resumo:
    ciclos: int = 0
    contagens: int = 0
    pecas: int = 0
    paradas_abertas: int = 0
    paradas_fechadas: int = 0
    moldes_novos: int = 0
    ops_criadas: int = 0
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


def _ordenados(pontos: list[Ponto] | None) -> list[Ponto]:
    return sorted(pontos or [], key=lambda p: p.ts_ms)


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


def op_do_contexto(contexto, chave: str) -> str | None:
    """Nº da OP que o dispositivo anexou ao ponto (cartão de OP batido no leitor)."""
    if not chave or not isinstance(contexto, dict):
        return None
    numero = str(contexto.get(chave) or "").strip()[:40]
    return numero or None


def codigo_do_ponto(p: Ponto, papel: str, chave: str) -> str | None:
    """Código textual de um evento de cartão.

    O firmware publica valor 1 e o conteúdo do cartão no contexto
    ({"maq_rfid": "30"}); firmwares mais simples mandam o código como valor.
    """
    ctx = p.contexto if isinstance(p.contexto, dict) else {}
    if papel == "qualidade":
        status = str(ctx.get("status") or "").strip().lower()
        if status:
            return status[:20]
        v = _numero(p.valor)
        return None if v is None else ("aprovado" if v >= 0.5 else "reprovado")
    if papel == "op":
        return op_do_contexto(ctx, chave)
    if papel in ("motivo", "funcionario", "cod_fun"):
        bruto = ctx.get(chave)
        if bruto is None or str(bruto).strip() == "":
            bruto = _numero(p.valor)
            if bruto is None:
                return None
        return _normalize_codigo(bruto)[:40] or None
    return None


class _Serie:
    """Último valor conhecido de um sinal numérico num instante (cavidade, alerta)."""

    def __init__(self, c: sqlite3.Connection, maquina_id: int, papel: str, novos: list[Ponto]):
        pontos = {r["ts_ms"]: r["valor"] for r in c.execute(
            "SELECT ts_ms, valor FROM sinais WHERE maquina_id = ? AND variavel = ?", (maquina_id, papel))}
        for p in novos:
            if _numero(p.valor) is not None:
                pontos[p.ts_ms] = p.valor
        self._ts = sorted(pontos)
        self._val = [pontos[t] for t in self._ts]

    def em(self, ts_ms: int) -> float | None:
        if not self._ts:
            return None
        # antes do 1º registro, o primeiro valor conhecido é o melhor palpite
        return self._val[max(0, bisect_right(self._ts, ts_ms) - 1)]


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
        if ts_ms not in self._cache_banco:   # anterior aos ciclos deste lote de trabalho
            row = self._c.execute(
                "SELECT molde_id FROM ciclos WHERE maquina_id = ? AND ts_ms <= ? AND molde_id IS NOT NULL "
                "ORDER BY ts_ms DESC LIMIT 1", (self._mid, ts_ms)).fetchone()
            self._cache_banco[ts_ms] = row["molde_id"] if row else None
        return self._cache_banco[ts_ms]


class _Ops:
    """OPs apontadas pelo cartão: cria as desconhecidas e mantém as execuções."""

    def __init__(self, c, maquina: dict, opcoes: Opcoes, agora: int, resumo: Resumo):
        self.c, self.maquina, self.opcoes, self.agora, self.resumo = c, maquina, opcoes, agora, resumo
        self._ids: dict[str, int] = {}
        self._moldes: dict[int, int | None] = {}
        self.sairam: dict[int, int] = {}          # ordem_id -> instante em que saiu da máquina

    def id_de(self, numero: str, ts_ms: int) -> int:
        chave = numero.lower()
        if chave not in self._ids:
            recente = self.agora - ts_ms <= JANELA_EVENTO_MS
            ordem_id, criada = ordens.garantir_do_chao(self.c, numero, ts_ms, self.maquina, publicar=recente)
            self._ids[chave] = ordem_id
            self.resumo.ops_criadas += criada
        return self._ids[chave]

    def molde_de(self, ordem_id: int) -> int | None:
        if ordem_id not in self._moldes:
            row = self.c.execute("SELECT molde_id FROM ordens WHERE id = ?", (ordem_id,)).fetchone()
            self._moldes[ordem_id] = row["molde_id"] if row else None
        return self._moldes[ordem_id]

    def sincronizar(self, eventos_op: list[Ponto], ciclos: list[Ponto]) -> None:
        """Abre/fecha execuções conforme a OP que o dispositivo declara.

        A fonte são os eventos do cartão de OP e o contexto dos CICLOS. As leituras
        do contador ficam de fora de propósito: na troca, o firmware publica a
        contagem final da OP ANTIGA no mesmo instante em que anuncia a nova.
        """
        chave = self.opcoes.ctx_op
        linha = [(p.ts_ms, n) for p in eventos_op if (n := op_do_contexto(p.contexto, chave))]
        linha += [(p.ts_ms, n) for p in ciclos if (n := op_do_contexto(p.contexto, chave))]
        if not linha:
            return
        linha.sort()
        mid = self.maquina["id"]
        aberta = self.c.execute(
            "SELECT e.ordem_id, e.inicio_ms, o.numero FROM ordem_execucoes e JOIN ordens o ON o.id = e.ordem_id "
            "WHERE e.maquina_id = ? AND e.fim_ms IS NULL", (mid,)).fetchone()
        atual = (aberta["numero"].lower(), aberta["inicio_ms"]) if aberta else None
        ultimo_fim = self.c.execute(
            "SELECT MAX(fim_ms) FROM ordem_execucoes WHERE maquina_id = ?", (mid,)).fetchone()[0] or 0
        anterior: tuple[str, int] | None = None     # (OP que acabou de sair, instante da troca)

        for ts, numero in linha:
            n = numero.lower()
            if atual and atual[0] == n:
                continue
            if (atual and ts <= atual[1]) or ts < ultimo_fim:
                continue                             # ponto antigo, relido pela sobreposição
            if anterior and anterior[0] == n and ts - anterior[1] <= RETORNO_ESPURIO_MS:
                continue                             # ciclo medido antes da troca, publicado depois
            recente = self.agora - ts <= JANELA_EVENTO_MS
            if n not in self._ids:
                self.id_de(numero, ts)
            ordem_id, saiu, _ = ordens.trocar_op_por_cartao(self.c, self.maquina, numero, ts, recente)
            self._ids[n] = ordem_id
            if saiu:
                self.sairam[saiu] = ts
            self.sairam.pop(ordem_id, None)          # voltou à máquina neste mesmo lote
            if atual:
                anterior = (atual[0], ts)
                ultimo_fim = ts
            atual = (n, ts)
            self.resumo.ops_iniciadas += 1


# ------------------------------------------------------------------- por série
def _gravar_sinais(c: sqlite3.Connection, maquina_id: int, papel: str, pontos: list[Ponto],
                   chave: str) -> int:
    n = 0
    for p in pontos:
        v = _numero(p.valor)
        codigo = codigo_do_ponto(p, papel, chave)
        if v is None and codigo is None:
            continue
        ctx = json.dumps(p.contexto, ensure_ascii=False) if p.contexto else None
        n += c.execute(
            "INSERT OR IGNORE INTO sinais (maquina_id, variavel, ts_ms, valor, codigo, contexto) "
            "VALUES (?, ?, ?, ?, ?, ?)", (maquina_id, papel, p.ts_ms, v, codigo, ctx)).rowcount
    return n


def _cavidades_em(cav: _Serie, ts_ms: int, padrao: int) -> int:
    v = cav.em(ts_ms)
    n = int(round(v)) if v else 0
    return n if 1 <= n <= 128 else max(1, int(padrao or 1))


def _ingerir_ciclos(c, maquina, pontos, opcoes, atrib, ops, molde_atual, cav, resumo, agora,
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
    var_ciclo = opcoes.variaveis.get("ciclo", "ciclo")

    for p in pontos:
        dur = duracao_ms(p.valor, opcoes.plataforma, var_ciclo)
        if dur is None:
            continue
        ctx = p.contexto if isinstance(p.contexto, dict) else {}
        molde_id = None
        desc = molde_ctx.parse(ctx.get(opcoes.ctx_molde)) if opcoes.ctx_molde else None
        if desc:
            if opcoes.auto_cadastrar_moldes:
                molde_id = cadastros.garantir_molde_do_contexto(
                    c, desc, _cavidades_em(cav, p.ts_ms, maquina["cavidades_padrao"]), cache_moldes)
            else:
                row = c.execute("SELECT id FROM moldes WHERE codigo = ?", (desc.chave[:40],)).fetchone()
                molde_id = row["id"] if row else None
        if molde_id is not None:
            molde_atual.registrar(p.ts_ms, molde_id)
        if p.ts_ms in ja_gravados:
            continue

        numero = op_do_contexto(ctx, opcoes.ctx_op)
        if numero:
            ordem_id = ops.id_de(numero, p.ts_ms)          # o cartão manda
        else:
            ordem_id = atrib.ordem_em(p.ts_ms)             # execução aberta pela tela
            if (ordem_id is None and opcoes.auto_iniciar_op and molde_id is not None
                    and agora - p.ts_ms <= JANELA_AUTO_INICIO_MS):
                candidata = ordens.candidata_auto_inicio(c, mid, molde_id, p.ts_ms)
                if candidata:
                    ordens.abrir_execucao(c, candidata, maquina, p.ts_ms, origem="molde",
                                          turnos=turnos, reatribuir=False)
                    atrib.recarregar_execucoes()
                    ordem_id = candidata
                    resumo.ops_iniciadas += 1
        if molde_id is None and ordem_id:
            molde_id = ops.molde_de(ordem_id)              # sem molde no contexto: o da OP

        lote_id = atrib.lote_para(p.ts_ms, ordem_id, molde_id)
        c.execute(
            "INSERT INTO ciclos (maquina_id, ts_ms, duracao_ms, op_ctx, molde_id, ordem_id, lote_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (mid, p.ts_ms, dur, numero, molde_id, ordem_id, lote_id))
        resumo.ciclos += 1

        if modo_ciclo:
            # máquina sem totalizador: cada leitura de ciclo vale cavidades × fator peças
            pecas = max(1, int(round(_cavidades_em(cav, p.ts_ms, maquina["cavidades_padrao"]) * fator)))
            c.execute(
                "INSERT OR IGNORE INTO contagens (maquina_id, ts_ms, ref_ms, contador, pecas, op_ctx, "
                "molde_id, ordem_id, lote_id) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (mid, p.ts_ms, p.ts_ms, pecas, numero, molde_id, ordem_id, lote_id))
            resumo.contagens += 1
            resumo.pecas += pecas
            if molde_id is not None:
                batidas[molde_id] = batidas.get(molde_id, 0) + fator


def calcular_delta(anterior: float | None, atual: float, intervalo_s: float,
                   zerou: bool = False) -> tuple[int, int]:
    """(peças, anomalia) entre duas leituras do totalizador.

    zerou=True: o dispositivo zerou o contador de propósito (troca de OP) — o
    valor atual já é o produzido desde então, e isso não é anomalia.
    anomalia: 0 = normal, 1 = contador reiniciou sozinho (dispositivo religou /
    operador zerou), 2 = salto impossível descartado.
    """
    if anterior is None:
        return 0, 0                       # primeira leitura: só estabelece a base
    teto = max(1.0, intervalo_s) * TAXA_MAX_PECAS_S
    if zerou:
        return (int(round(atual)), 0) if atual <= teto else (0, 2)
    delta = atual - anterior
    if delta < 0:
        return (int(round(atual)), 1) if atual <= teto else (0, 2)
    if delta > teto:
        return 0, 2
    return int(round(delta)), 0


def instante_de_atribuicao(ts_anterior: int | None, ts_ms: int) -> int:
    """Meio do intervalo que a leitura cobre (recuando no máximo 15 min)."""
    if ts_anterior is None:
        return ts_ms
    return ts_ms - min((ts_ms - ts_anterior) // 2, MEIO_INTERVALO_MAX_MS)


def _ingerir_contador(c, maquina, pontos, opcoes, atrib, ops, molde_atual, resumo, batidas) -> None:
    if not pontos or maquina["fonte_contagem"] != "contador":
        return
    mid = maquina["id"]
    ult = c.execute(
        "SELECT ts_ms, contador, op_ctx FROM contagens WHERE maquina_id = ? AND contador IS NOT NULL "
        "ORDER BY ts_ms DESC LIMIT 1", (mid,)).fetchone()
    ts_ant, val_ant, op_ant = (ult["ts_ms"], ult["contador"], ult["op_ctx"]) if ult else (None, None, None)
    cav_molde: dict[int, int] = {}

    for p in pontos:
        valor = _numero(p.valor)
        if valor is None or valor < 0:
            continue
        if ts_ant is not None and p.ts_ms <= ts_ant:
            continue                       # já ingerido (ou fora de ordem): a série é só-acréscimo
        numero = op_do_contexto(p.contexto, opcoes.ctx_op)
        # O firmware zera o totalizador quando entra outra OP
        trocou_op = ts_ant is not None and (numero or "").lower() != (op_ant or "").lower()
        intervalo_s = (p.ts_ms - ts_ant) / 1000 if ts_ant is not None else 0
        pecas, anomalia = calcular_delta(val_ant, valor, intervalo_s, zerou=trocou_op)
        ref_ms = instante_de_atribuicao(ts_ant, p.ts_ms)

        ordem_id = ops.id_de(numero, p.ts_ms) if numero else atrib.ordem_em(ref_ms)
        molde_id = molde_atual.em(p.ts_ms)
        if molde_id is None and ordem_id:
            molde_id = ops.molde_de(ordem_id)
        lote_id = atrib.lote_para(ref_ms, ordem_id, molde_id) if pecas > 0 else None
        c.execute(
            "INSERT INTO contagens (maquina_id, ts_ms, ref_ms, contador, pecas, anomalia, op_ctx, "
            "molde_id, ordem_id, lote_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, p.ts_ms, ref_ms, valor, pecas, anomalia, numero, molde_id, ordem_id, lote_id))
        resumo.contagens += 1
        resumo.pecas += pecas
        resumo.reinicios_contador += anomalia == 1
        resumo.saltos_descartados += anomalia == 2
        if pecas and molde_id is not None:
            if molde_id not in cav_molde:
                row = c.execute("SELECT cavidades FROM moldes WHERE id = ?", (molde_id,)).fetchone()
                cav_molde[molde_id] = max(1, row["cavidades"] if row else 1)
            batidas[molde_id] = batidas.get(molde_id, 0) + pecas / cav_molde[molde_id]
        ts_ant, val_ant, op_ant = p.ts_ms, valor, numero


def _ingerir_paradas(c, maquina, pontos, opcoes, atrib, alerta: _Serie, resumo, agora) -> None:
    if not pontos:
        return
    mid = maquina["id"]
    ult = c.execute(
        "SELECT ts_ms, valor FROM sinais WHERE maquina_id = ? AND variavel = 'parada' "
        "ORDER BY ts_ms DESC LIMIT 1", (mid,)).fetchone()
    estado: bool | None = (ult["valor"] >= 0.5) if ult else None
    ts_ant = ult["ts_ms"] if ult else None
    fim_anterior = c.execute(
        "SELECT MAX(fim_ms) FROM paradas WHERE maquina_id = ?", (mid,)).fetchone()[0] or 0

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
                if estado is None:
                    inicio, estimado = p.ts_ms, 1      # já estava parada: início real desconhecido
                else:
                    atraso = alerta.em(p.ts_ms)
                    atraso = int(atraso) if atraso and 0 < atraso <= ALERTA_MAX_MS else opcoes.alerta_padrao_ms
                    # a máquina parou `atraso` antes de o dispositivo avisar
                    inicio, estimado = max(p.ts_ms - atraso, fim_anterior + 1), 0
                c.execute(
                    "INSERT OR IGNORE INTO paradas (maquina_id, inicio_ms, sinal_ms, inicio_estimado, ordem_id) "
                    "VALUES (?, ?, ?, ?, ?)", (mid, inicio, p.ts_ms, estimado, atrib.ordem_em(inicio)))
                resumo.paradas_abertas += 1
                if recente:
                    eventos.publicar(c, "parada.iniciada", {
                        "maquina": maquina["codigo"], "maquina_nome": maquina["nome"],
                        "inicio": tempo.ms_para_dt(inicio).isoformat(timespec="seconds")})
        elif not parada and estado is True:
            ev = c.execute("SELECT id, inicio_ms FROM paradas WHERE maquina_id = ? AND fim_ms IS NULL",
                           (mid,)).fetchone()
            if ev:
                fim = max(p.ts_ms, ev["inicio_ms"])
                c.execute("UPDATE paradas SET fim_ms = ? WHERE id = ?", (fim, ev["id"]))
                fim_anterior = fim
                resumo.paradas_fechadas += 1
                if recente:
                    eventos.publicar(c, "parada.encerrada", {
                        "maquina": maquina["codigo"], "maquina_nome": maquina["nome"],
                        "inicio": tempo.ms_para_dt(ev["inicio_ms"]).isoformat(timespec="seconds"),
                        "fim": tempo.ms_para_dt(fim).isoformat(timespec="seconds"),
                        "duracao_s": round((fim - ev["inicio_ms"]) / 1000)})
        estado, ts_ant = parada, p.ts_ms


def associar_apontamentos(c: sqlite3.Connection, maquina_id: int, desde_ms: int, agora: int,
                          liberacao: frozenset = frozenset()) -> int:
    """Liga cartões RFID (motivo e funcionário) às paradas da máquina.

    Regras:
    - Motivo: vale a ÚLTIMA batida entre 10 min antes do início e 2 min depois do
      fim. Uma batida pertence a uma parada só (a mais antiga que a cobre).
    - Cartões de 'produção (liberação)' — o que o operador bate para voltar a
      produzir — não são motivo e são ignorados.
    - Motivo apontado manualmente na tela nunca é sobrescrito pelo cartão.
    - Funcionário: último cartão batido até o fim da parada, com no máximo 24 h.

    É recalculável: rodar de novo depois de mudar a legenda corrige o histórico.
    """
    paradas = c.execute(
        "SELECT * FROM paradas WHERE maquina_id = ? AND COALESCE(fim_ms, ?) >= ? ORDER BY inicio_ms",
        (maquina_id, agora, desde_ms - ANTES_MOTIVO_MS)).fetchall()
    if not paradas:
        return 0
    primeira = paradas[0]["inicio_ms"]
    batidas = [b for b in c.execute(
        "SELECT ts_ms, codigo, valor FROM sinais WHERE maquina_id = ? AND variavel = 'motivo' AND ts_ms >= ? "
        "ORDER BY ts_ms", (maquina_id, primeira - ANTES_MOTIVO_MS)).fetchall()
        if (b["codigo"] or _normalize_codigo(b["valor"])) not in liberacao]
    cartoes = c.execute(
        "SELECT ts_ms, codigo, valor FROM sinais WHERE maquina_id = ? AND variavel = 'funcionario' "
        "AND ts_ms >= ? ORDER BY ts_ms", (maquina_id, primeira - MAX_IDADE_FUNC_MS)).fetchall()
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
        if ev["motivo_origem"] != "manual":
            candidatas = [b for b in batidas if inicio_janela <= b["ts_ms"] <= fim + DEPOIS_MOTIVO_MS]
            if candidatas:
                b = candidatas[-1]
                codigo = b["codigo"] or _normalize_codigo(b["valor"])
                if codigo != ev["motivo_codigo"] or b["ts_ms"] != ev["motivo_ts_ms"]:
                    c.execute("UPDATE paradas SET motivo_codigo = ?, motivo_origem = 'rfid', motivo_ts_ms = ? "
                              "WHERE id = ?", (codigo, b["ts_ms"], ev["id"]))
                    alteradas += 1
            elif ev["motivo_codigo"] is not None:
                # o cartão que estava aqui deixou de valer (virou "liberação" na legenda)
                c.execute("UPDATE paradas SET motivo_codigo = NULL, motivo_origem = NULL, motivo_ts_ms = NULL "
                          "WHERE id = ?", (ev["id"],))
                alteradas += 1
        operador = None
        for k in cartoes:
            if k["ts_ms"] > fim:
                break
            if fim - k["ts_ms"] <= MAX_IDADE_FUNC_MS:
                operador = k
        if operador is not None:
            codigo = operador["codigo"] or _normalize_codigo(operador["valor"])
            if codigo != ev["funcionario_codigo"]:
                c.execute("UPDATE paradas SET funcionario_codigo = ? WHERE id = ?", (codigo, ev["id"]))
                alteradas += 1
        fim_anterior = fim
    return alteradas


def reassociar_tudo(dias: int = 90) -> int:
    """Refaz motivo/funcionário das paradas recentes de todas as máquinas — para
    quando a legenda dos cartões muda (ex.: um código virou 'liberação')."""
    from app.mes.db import get_conn
    agora = agora_ms()
    liberacao = liberacoes_cadastradas()
    total = 0
    with get_conn() as c:
        for row in c.execute("SELECT id FROM maquinas").fetchall():
            total += associar_apontamentos(c, row["id"], agora - dias * 86400 * 1000, agora, liberacao)
    return total


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

    for papel in PAPEIS_DE_SINAL:
        chave = opcoes.ctx_op if papel == "op" else opcoes.variaveis.get(papel, papel)
        _gravar_sinais(c, mid, papel, ser[papel], chave)
    cav = _Serie(c, mid, "cavidade", ser["cavidade"])
    alerta = _Serie(c, mid, "alerta", ser["alerta"])

    # 1º as execuções (quem está na máquina), depois os fatos que dependem delas
    ops = _Ops(c, maquina, opcoes, agora, resumo)
    ops.sincronizar(ser["op"], ser["ciclo"])
    atrib = Atribuidor(c, maquina, turnos, desde_ms=mais_antigo)
    molde_atual = _MoldeAtual(c, mid)
    batidas: dict[int, float] = {}

    _ingerir_ciclos(c, maquina, ser["ciclo"], opcoes, atrib, ops, molde_atual, cav, resumo, agora,
                    batidas, turnos)
    _ingerir_contador(c, maquina, ser["contador"], opcoes, atrib, ops, molde_atual, resumo, batidas)
    _ingerir_paradas(c, maquina, ser["parada"], opcoes, atrib, alerta, resumo, agora)
    if ser["parada"] or ser["motivo"] or ser["funcionario"]:
        associar_apontamentos(c, mid, mais_antigo, agora, opcoes.codigos_liberacao)

    for molde_id, n in batidas.items():
        if round(n):
            c.execute("UPDATE moldes SET ciclos_acumulados = ciclos_acumulados + ? WHERE id = ?",
                      (int(round(n)), molde_id))
    atrib.finalizar()
    # Só agora, com os totais em dia: avisa a meta e decide o destino de quem saiu
    # da máquina (nessa ordem — senão a OP seria concluída sem o aviso de meta).
    ordens.notificar_meta(c, atrib.ordens_tocadas | set(ops.sairam))
    for ordem_id, ts in ops.sairam.items():
        ordens.definir_status_apos_saida(c, ordem_id, opcoes.auto_concluir_na_troca,
                                         publicar=agora - ts <= JANELA_EVENTO_MS)
    resumo.moldes_novos = c.execute("SELECT COUNT(*) FROM moldes").fetchone()[0] - moldes_antes
    return resumo
