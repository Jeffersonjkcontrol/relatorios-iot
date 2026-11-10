"""Coletor: busca os pontos novos na plataforma IoT e entrega à ingestão.

Roda como tarefa em segundo plano dentro do próprio app (um único processo
uvicorn — não usar --workers > 1, senão haveria dois coletores).

Estratégia:
- Ubidots: UMA requisição em lote por passada (POST /data/raw/series) para
  todas as máquinas e variáveis — medido: 123 variáveis em ~2,5 s.
- Demais plataformas: uma consulta por (máquina, variável), com paralelismo
  limitado.

Cada passada refaz os últimos minutos (sobreposição) para pegar pontos que
chegaram atrasados; a ingestão é idempotente, então isso não duplica nada.
Na ativação (ou depois de o app ficar desligado), a lacuna é percorrida em
fatias de 2 h — é o "histórico inicial".
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.clients import make_client
from app.config import settings
from app.mes import cadastros, config, ingestao
from app.mes.db import agora_ms, get_conn
from app.mes.ingestao import Opcoes, Ponto, Resumo

log = logging.getLogger("mes.coletor")

SOBREPOSICAO_MS = 10 * 60 * 1000
FATIA_MS = 2 * 3600 * 1000
LIMITE_POR_SERIE = 1000
IDS_POR_REQUISICAO = 150
SEMENTE_JANELA_MS = 400 * 86400 * 1000   # até onde olhar para achar o estado anterior
PAUSA_ENTRE_FATIAS_S = 0.3
TTL_VARIAVEIS_S = 600

PAPEL_PARA_CONFIG = {
    "ciclo": "var_ciclo", "contador": "var_contador", "parada": "var_parada",
    "cavidade": "var_cavidade", "motivo": "var_motivo", "funcionario": "var_funcionario",
}


class Coletor:
    def __init__(self) -> None:
        self._tarefa: asyncio.Task | None = None
        self._trava = asyncio.Lock()
        self._acordar = asyncio.Event()
        self._variaveis: tuple[float, str, dict[str, tuple[int, str]]] | None = None
        self.fase = "parado"             # parado | historico | ao_vivo | erro
        self.progresso = 0.0             # 0..1 durante o histórico
        self.ultimo_ok_ms: int | None = None
        self.ultimo_erro = ""
        self.ultimo_erro_ms: int | None = None
        self.ultima_passada: dict = {}
        self.acumulado = Resumo()

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        if self._tarefa is None or self._tarefa.done():
            self._tarefa = asyncio.create_task(self._laco(), name="mes-coletor")

    async def parar(self) -> None:
        if self._tarefa and not self._tarefa.done():
            self._tarefa.cancel()
            try:
                await self._tarefa
            except (asyncio.CancelledError, Exception):
                pass
        self._tarefa = None

    def acordar(self) -> None:
        """Antecipa a próxima passada (ex.: logo após ativar o MES)."""
        self._acordar.set()

    async def _laco(self) -> None:
        await asyncio.sleep(2)           # deixa o app terminar de subir
        while True:
            intervalo = max(15, config.get_int("intervalo_s", 60))
            try:
                await self.passada()
            except asyncio.CancelledError:
                raise
            except Exception as e:       # nunca derruba o laço
                self._registrar_erro(e)
            self._acordar.clear()
            try:
                await asyncio.wait_for(self._acordar.wait(), timeout=intervalo)
            except asyncio.TimeoutError:
                pass

    def _registrar_erro(self, e: Exception) -> None:
        self.fase = "erro"
        self.ultimo_erro = f"{type(e).__name__}: {e}"[:300]
        self.ultimo_erro_ms = agora_ms()
        log.warning("Falha na coleta: %s", self.ultimo_erro)

    def status(self) -> dict:
        return {
            "ativo": config.mes_ativo(),
            "permitido": config.coletor_permitido(),
            "rodando": bool(self._tarefa and not self._tarefa.done()),
            "fase": self.fase,
            "progresso": round(self.progresso, 3),
            "ultimo_ok_ms": self.ultimo_ok_ms,
            "ultimo_erro": self.ultimo_erro,
            "ultimo_erro_ms": self.ultimo_erro_ms,
            "cursor_ms": int(config.get("coletor_cursor_ms", "0") or 0) or None,
            "ultima_passada": self.ultima_passada,
            "acumulado": self.acumulado.as_dict(),
        }

    # ------------------------------------------------------------------ passada
    async def passada(self) -> Resumo | None:
        """Uma rodada completa de coleta. Devolve None se o MES não estiver ativo."""
        if not config.mes_ativo() or not config.coletor_permitido():
            self.fase = "parado"
            return None
        async with self._trava:
            return await self._passada()

    async def _passada(self) -> Resumo:
        cfg = config.todas()
        plataforma = cfg["plataforma"]
        maquinas = [m for m in cadastros.listar_maquinas(so_ativas=True) if m["plataforma"] == plataforma]
        total = Resumo()
        if not maquinas:
            self.fase = "ao_vivo"
            self.ultima_passada = {"aviso": "Nenhuma máquina ativa para coletar."}
            return total

        base_url, token = settings.platform(plataforma)
        if not token:
            raise RuntimeError(f"Token da plataforma '{plataforma}' não configurado no .env.")
        cliente = make_client(plataforma, base_url, token)
        opcoes = Opcoes(
            plataforma=plataforma, var_ciclo=cfg["var_ciclo"],
            auto_cadastrar_moldes=cfg["auto_cadastrar_moldes"] == "1",
            auto_iniciar_op=cfg["auto_iniciar_op"] == "1",
        )
        variaveis = {papel: cfg[chave] for papel, chave in PAPEL_PARA_CONFIG.items() if cfg.get(chave)}
        turnos = cadastros.turnos_ativos()

        agora = agora_ms()
        cursor = int(cfg.get("coletor_cursor_ms") or 0)
        t0 = time.monotonic()

        if cursor <= 0:
            # Primeira coleta: semeia o estado anterior e traz o histórico
            dias = max(0, int(float(cfg.get("backfill_dias") or 0)))
            inicio = agora - dias * 86400 * 1000
            self.fase, self.progresso = "historico", 0.0
            series = await self._buscar(cliente, maquinas, variaveis, inicio - SEMENTE_JANELA_MS, inicio, limite=1)
            total.somar(await asyncio.to_thread(self._gravar, maquinas, series, opcoes, turnos))
        else:
            inicio = cursor - SOBREPOSICAO_MS
            self.fase = "historico" if agora - cursor > FATIA_MS else "ao_vivo"

        duracao_total = max(1, agora - inicio)
        ini = inicio
        while ini < agora:
            fim = min(ini + FATIA_MS, agora)
            series = await self._buscar(cliente, maquinas, variaveis, ini, fim)
            total.somar(await asyncio.to_thread(self._gravar, maquinas, series, opcoes, turnos))
            config.set_("coletor_cursor_ms", fim)
            self.progresso = (fim - inicio) / duracao_total
            ini = fim
            if ini < agora:
                await asyncio.sleep(PAUSA_ENTRE_FATIAS_S)

        self.fase, self.progresso = "ao_vivo", 1.0
        self.ultimo_ok_ms = agora_ms()
        self.ultimo_erro = "; ".join(total.erros[:3])
        self.ultima_passada = {**total.as_dict(), "duracao_s": round(time.monotonic() - t0, 1)}
        self.acumulado.somar(total)
        self.acumulado.erros = self.acumulado.erros[-20:]
        return total

    # -------------------------------------------------------------------- busca
    async def _buscar(self, cliente, maquinas: list[dict], variaveis: dict[str, str],
                      ini_ms: int, fim_ms: int, limite: int = LIMITE_POR_SERIE
                      ) -> dict[int, dict[str, list[Ponto]]]:
        """{maquina_id: {papel: [pontos]}} para a janela."""
        if hasattr(cliente, "get_series_bulk"):
            return await self._buscar_em_lote(cliente, maquinas, variaveis, ini_ms, fim_ms, limite)
        if limite <= 1:
            # Semente de estado: sem consulta em lote nao ha como pedir "o ultimo
            # ponto antes de X" de forma confiavel - a coleta parte sem ela.
            return {m["id"]: {} for m in maquinas}
        return await self._buscar_uma_a_uma(cliente, maquinas, variaveis, ini_ms, fim_ms)

    async def _mapa_variaveis(self, cliente, maquinas, variaveis) -> dict[str, tuple[int, str]]:
        """{id da variável na plataforma: (maquina_id, papel)}, com cache de 10 min."""
        assinatura = "|".join(sorted(m["codigo"] for m in maquinas)) + "#" + "|".join(sorted(variaveis.values()))
        if self._variaveis and time.time() - self._variaveis[0] < TTL_VARIAVEIS_S \
                and self._variaveis[1] == assinatura:
            return self._variaveis[2]
        por_codigo = {m["codigo"]: m["id"] for m in maquinas}
        papel_do_label = {label: papel for papel, label in variaveis.items()}
        mapa: dict[str, tuple[int, str]] = {}
        for v in await cliente.listar_variaveis_por_labels(sorted(set(variaveis.values()))):
            dispositivo = (v.get("device") or {}).get("label") if isinstance(v.get("device"), dict) else None
            if v.get("id") and dispositivo in por_codigo and v.get("label") in papel_do_label:
                mapa[v["id"]] = (por_codigo[dispositivo], papel_do_label[v["label"]])
        self._variaveis = (time.time(), assinatura, mapa)
        return mapa

    async def _buscar_em_lote(self, cliente, maquinas, variaveis, ini_ms, fim_ms, limite):
        mapa = await self._mapa_variaveis(cliente, maquinas, variaveis)
        ids = list(mapa)
        bruto: dict[str, list] = {}
        for i in range(0, len(ids), IDS_POR_REQUISICAO):
            bruto.update(await self._lote_sem_truncar(
                cliente, ids[i:i + IDS_POR_REQUISICAO], ini_ms, fim_ms, limite))
        out: dict[int, dict[str, list[Ponto]]] = {m["id"]: {} for m in maquinas}
        for vid, pontos in bruto.items():
            maquina_id, papel = mapa[vid]
            out[maquina_id][papel] = [Ponto(p.timestamp_ms, p.value, p.context) for p in pontos]
        return out

    async def _lote_sem_truncar(self, cliente, ids, ini_ms, fim_ms, limite) -> dict[str, list]:
        """Consulta em lote; se alguma série bater no limite (pode ter perdido o
        começo da janela), refaz só ela em duas metades."""
        res = await cliente.get_series_bulk(ids, ini_ms, fim_ms, limite)
        if limite <= 1:                  # consulta de semente: 1 ponto é o que se quer
            return res
        truncadas = [vid for vid, pontos in res.items() if len(pontos) >= limite]
        if truncadas and fim_ms - ini_ms > 60_000:
            meio = (ini_ms + fim_ms) // 2
            a = await self._lote_sem_truncar(cliente, truncadas, ini_ms, meio, limite)
            b = await self._lote_sem_truncar(cliente, truncadas, meio, fim_ms, limite)
            for vid in truncadas:
                unicos = {p.timestamp_ms: p for p in a.get(vid, []) + b.get(vid, [])}
                res[vid] = [unicos[t] for t in sorted(unicos)]
        return res

    async def _buscar_uma_a_uma(self, cliente, maquinas, variaveis, ini_ms, fim_ms):
        semaforo = asyncio.Semaphore(4)
        out: dict[int, dict[str, list[Ponto]]] = {m["id"]: {} for m in maquinas}

        async def uma(maquina: dict, papel: str, label: str) -> None:
            async with semaforo:
                try:
                    pontos = await cliente.get_values(maquina["codigo"], label, ini_ms, fim_ms)
                except Exception:
                    return               # variável inexistente nesta máquina
            out[maquina["id"]][papel] = [Ponto(p.timestamp_ms, p.value, p.context) for p in pontos]

        await asyncio.gather(*[uma(m, papel, label) for m in maquinas for papel, label in variaveis.items()])
        return out

    # ------------------------------------------------------------------ gravação
    @staticmethod
    def _gravar(maquinas: list[dict], series: dict[int, dict[str, list[Ponto]]],
                opcoes: Opcoes, turnos: list) -> Resumo:
        """Roda em thread: uma transação por máquina, e a falha de uma não barra as outras."""
        total = Resumo()
        for maquina in maquinas:
            dados = series.get(maquina["id"]) or {}
            if not any(dados.values()):
                continue
            try:
                with get_conn() as c:
                    total.somar(ingestao.processar_maquina(c, maquina, dados, opcoes, turnos))
            except Exception as e:
                log.exception("Falha ao ingerir %s", maquina["codigo"])
                total.erros.append(f"{maquina['codigo']}: {type(e).__name__}: {e}"[:200])
        return total


coletor = Coletor()


async def ativar_mes(plataforma: str) -> dict:
    """Liga o MES: cadastra as máquinas da plataforma e dispara o histórico inicial."""
    base_url, token = settings.platform(plataforma)
    if not token:
        raise ValueError(f"Token da plataforma '{plataforma}' não configurado no .env.")
    cliente = make_client(plataforma, base_url, token)
    dispositivos = await cliente.list_devices()
    if not dispositivos:
        raise ValueError("A plataforma não devolveu nenhum dispositivo — confira o token.")
    resultado = cadastros.sincronizar_maquinas(plataforma, dispositivos)
    config.set_("plataforma", plataforma)
    config.set_("ativo", True)
    coletor.iniciar()
    coletor.acordar()
    return resultado


async def sincronizar_maquinas() -> dict:
    plataforma = config.get("plataforma")
    base_url, token = settings.platform(plataforma)
    cliente = make_client(plataforma, base_url, token)
    return cadastros.sincronizar_maquinas(plataforma, await cliente.list_devices())


def desativar_mes() -> None:
    """Pausa a coleta. Os dados já coletados ficam; ao reativar, a lacuna é preenchida."""
    config.set_("ativo", False)
