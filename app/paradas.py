"""Análise de paradas com apontamento RFID.

Cruza três séries por máquina:
- `parada`   (0/1)  — 1 = máquina parada, 0 = rodando (marca início/fim do evento)
- `maq_rfid`        — código RFID do MOTIVO da parada (badge do operador)
- `fun_rfid`        — código RFID do FUNCIONÁRIO logado na máquina

O motor `build_stop_events` é uma função pura (testável sem API). O wrapper
`analyze_paradas` busca as séries em paralelo e agrega por motivo, máquina e
funcionário.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

from app.cadastros import resolve_map, categoria_map, _normalize_codigo
from app.clients import make_client
from app.clients.ubidots import ValuePoint
from app.config import settings

# Tolerância pra associar badge de motivo fora da janela exata da parada
TOLERANCIA_MOTIVO_MS = 2 * 60 * 1000       # 2 minutos
# Idade máxima do badge de funcionário pra considerá-lo "logado"
MAX_IDADE_FUNCIONARIO_MS = 24 * 3600 * 1000  # 24 horas


def _norm_bool(v) -> bool | None:
    """Converte o value da variável `parada` em bool (1/0). None se não numérico."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v >= 0.5
    return None


def build_stop_events(
    parada_points: list[ValuePoint],
    maq_points: list[ValuePoint],
    fun_points: list[ValuePoint],
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict], int]:
    """Reconstrói eventos de parada a partir das transições de `parada`.

    Retorna (eventos, apontamentos_orfaos):
    - eventos: lista de dicts com inicio_ms, fim_ms, duracao_s, motivo_codigo,
      funcionario_codigo, em_andamento, inicio_truncado
    - apontamentos_orfaos: badges de motivo que não casaram com nenhuma parada
    """
    parada_points = sorted(parada_points, key=lambda p: p.timestamp_ms)
    maq_points = sorted(maq_points, key=lambda p: p.timestamp_ms)
    fun_points = sorted(fun_points, key=lambda p: p.timestamp_ms)

    # ---- 1) Reconstrói janelas de parada pelas transições ----
    eventos: list[dict] = []
    estado_atual: bool | None = None
    inicio_atual: int | None = None
    inicio_truncado = False

    for p in parada_points:
        b = _norm_bool(p.value)
        if b is None:
            continue
        if estado_atual is None:
            # primeiro ponto da janela
            estado_atual = b
            if b:  # já começa parada -> início truncado na borda
                inicio_atual = start_ms
                inicio_truncado = True
            continue
        if b == estado_atual:
            continue  # valor repetido, ignora
        # transição
        if b:  # 0 -> 1 : abre evento
            inicio_atual = p.timestamp_ms
            inicio_truncado = False
        else:  # 1 -> 0 : fecha evento
            if inicio_atual is not None:
                eventos.append({
                    "inicio_ms": inicio_atual,
                    "fim_ms": p.timestamp_ms,
                    "em_andamento": False,
                    "inicio_truncado": inicio_truncado,
                })
            inicio_atual = None
        estado_atual = b

    # série terminou em 1 -> evento aberto (fim = borda da janela / agora)
    if estado_atual and inicio_atual is not None:
        eventos.append({
            "inicio_ms": inicio_atual,
            "fim_ms": end_ms,
            "em_andamento": True,
            "inicio_truncado": inicio_truncado,
        })

    # ---- 2) Associa motivo (maq_rfid mais próximo dentro da janela ± tolerância) ----
    badges_usados: set[int] = set()
    for ev in eventos:
        lo = ev["inicio_ms"] - TOLERANCIA_MOTIVO_MS
        hi = ev["fim_ms"] + TOLERANCIA_MOTIVO_MS
        melhor = None
        melhor_dist = None
        for i, mp in enumerate(maq_points):
            if i in badges_usados:
                continue
            if mp.timestamp_ms < lo:
                continue
            if mp.timestamp_ms > hi:
                break  # ordenado; não há mais candidatos
            dist = abs(mp.timestamp_ms - ev["inicio_ms"])
            if melhor is None or dist < melhor_dist:
                melhor = i
                melhor_dist = dist
        if melhor is not None:
            badges_usados.add(melhor)
            ev["motivo_codigo"] = _normalize_codigo(maq_points[melhor].value)
            ev["motivo_ts_ms"] = maq_points[melhor].timestamp_ms
        else:
            ev["motivo_codigo"] = None
            ev["motivo_ts_ms"] = None

    apontamentos_orfaos = len(maq_points) - len(badges_usados)

    # ---- 3) Associa funcionário (último fun_rfid antes do FIM, máx 24h de idade) ----
    for ev in eventos:
        func = None
        for fp in fun_points:
            if fp.timestamp_ms > ev["fim_ms"]:
                break
            if ev["fim_ms"] - fp.timestamp_ms <= MAX_IDADE_FUNCIONARIO_MS:
                func = fp  # continua — queremos o ÚLTIMO antes do fim
        ev["funcionario_codigo"] = _normalize_codigo(func.value) if func else None

    # ---- 4) Duração ----
    for ev in eventos:
        ev["duracao_s"] = max(0.0, (ev["fim_ms"] - ev["inicio_ms"]) / 1000.0)

    return eventos, apontamentos_orfaos


async def analyze_paradas(
    platform: str,
    device: str | None = None,
    days: int = 7,
    var_parada: str = "parada",
    var_motivo: str = "maq_rfid",
    var_func: str = "fun_rfid",
) -> dict:
    """Busca as séries de cada máquina em paralelo e agrega os eventos."""
    base_url, token = settings.platform(platform)
    client = make_client(platform, base_url, token)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    devices = await client.list_devices()
    if device:
        devices = [d for d in devices if d.get("label") == device]

    async def fetch_series(dev_label: str, var: str) -> list[ValuePoint]:
        try:
            return await client.get_values(dev_label, var, start_ms, end_ms, page_size=5000)
        except Exception:
            return []  # variável inexistente nesta máquina -> vazio, sem erro

    async def process_device(d: dict) -> dict | None:
        label = d.get("label")
        name = d.get("name") or label
        if not label:
            return None
        parada_pts, maq_pts, fun_pts = await asyncio.gather(
            fetch_series(label, var_parada),
            fetch_series(label, var_motivo),
            fetch_series(label, var_func),
        )
        if not parada_pts:
            return None  # máquina sem dados de parada na janela
        eventos, orfaos = build_stop_events(parada_pts, maq_pts, fun_pts, start_ms, end_ms)
        return {"label": label, "name": name, "eventos": eventos, "orfaos": orfaos}

    results = [r for r in await asyncio.gather(*[process_device(d) for d in devices]) if r]

    # ---- Resolve códigos -> nomes ----
    nomes_motivo = resolve_map("motivo")
    nomes_func = resolve_map("funcionario")
    cat_motivo = categoria_map()

    def nome_motivo(codigo: str | None) -> str:
        if codigo is None:
            return "Sem motivo apontado"
        return nomes_motivo.get(codigo, f"Código {codigo} (não cadastrado)")

    def nome_func(codigo: str | None) -> str:
        if codigo is None:
            return "Desconhecido"
        return nomes_func.get(codigo, f"Código {codigo} (não cadastrado)")

    # ---- Agregações ----
    todos_eventos: list[dict] = []
    por_motivo: dict[str, dict] = defaultdict(lambda: {"count": 0, "tempo_s": 0.0, "categoria": ""})
    por_maquina: dict[str, dict] = defaultdict(lambda: {"name": "", "count": 0, "tempo_s": 0.0, "maior_s": 0.0})
    por_funcionario: dict[str, dict] = defaultdict(lambda: {"count": 0, "tempo_s": 0.0})
    total_orfaos = 0
    com_motivo = 0

    for r in results:
        total_orfaos += r["orfaos"]
        for ev in r["eventos"]:
            m_nome = nome_motivo(ev["motivo_codigo"])
            f_nome = nome_func(ev["funcionario_codigo"])
            todos_eventos.append({
                "device": r["label"],
                "device_name": r["name"],
                "inicio_ms": ev["inicio_ms"],
                "fim_ms": ev["fim_ms"],
                "duracao_s": ev["duracao_s"],
                "motivo": m_nome,
                "motivo_codigo": ev["motivo_codigo"],
                "funcionario": f_nome,
                "funcionario_codigo": ev["funcionario_codigo"],
                "em_andamento": ev["em_andamento"],
                "inicio_truncado": ev["inicio_truncado"],
            })
            if ev["motivo_codigo"] is not None:
                com_motivo += 1
            pm = por_motivo[m_nome]
            pm["count"] += 1
            pm["tempo_s"] += ev["duracao_s"]
            if ev["motivo_codigo"]:
                pm["categoria"] = cat_motivo.get(ev["motivo_codigo"], "")
            pq = por_maquina[r["label"]]
            pq["name"] = r["name"]
            pq["count"] += 1
            pq["tempo_s"] += ev["duracao_s"]
            pq["maior_s"] = max(pq["maior_s"], ev["duracao_s"])
            pf = por_funcionario[f_nome]
            pf["count"] += 1
            pf["tempo_s"] += ev["duracao_s"]

    total_paradas = len(todos_eventos)
    tempo_total_s = sum(e["duracao_s"] for e in todos_eventos)

    def _pct(x: float, total: float) -> float:
        return (x / total * 100) if total > 0 else 0.0

    motivos_out = sorted(
        [
            {
                "motivo": nome, "categoria": v["categoria"], "count": v["count"],
                "tempo_s": v["tempo_s"],
                "tempo_medio_s": v["tempo_s"] / v["count"] if v["count"] else 0,
                "pct_tempo": _pct(v["tempo_s"], tempo_total_s),
            }
            for nome, v in por_motivo.items()
        ],
        key=lambda m: -m["tempo_s"],
    )
    maquinas_out = sorted(
        [
            {"device": lbl, "name": v["name"], "count": v["count"],
             "tempo_s": v["tempo_s"], "maior_s": v["maior_s"],
             "pct_tempo": _pct(v["tempo_s"], tempo_total_s)}
            for lbl, v in por_maquina.items()
        ],
        key=lambda m: -m["tempo_s"],
    )
    funcionarios_out = sorted(
        [
            {"funcionario": nome, "count": v["count"], "tempo_s": v["tempo_s"],
             "tempo_medio_s": v["tempo_s"] / v["count"] if v["count"] else 0}
            for nome, v in por_funcionario.items()
        ],
        key=lambda f: -f["count"],
    )
    todos_eventos.sort(key=lambda e: -e["inicio_ms"])  # mais recentes primeiro

    return {
        "platform": platform,
        "device_filtro": device,
        "days": days,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "maquinas_consultadas": len(devices),
        "maquinas_com_paradas": len(results),
        "summary": {
            "total_paradas": total_paradas,
            "tempo_total_s": tempo_total_s,
            "tempo_medio_s": tempo_total_s / total_paradas if total_paradas else 0,
            "pct_com_motivo": _pct(com_motivo, total_paradas),
            "apontamentos_orfaos": total_orfaos,
            "em_andamento": sum(1 for e in todos_eventos if e["em_andamento"]),
            "motivo_campeao": motivos_out[0]["motivo"] if motivos_out else None,
        },
        "por_motivo": motivos_out,
        "por_maquina": maquinas_out,
        "por_funcionario": funcionarios_out,
        "eventos": todos_eventos,
    }
