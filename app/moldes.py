"""Análise de moldes a partir do context.molde dos ciclos.

Formato observado: "Pe.Front.Babel|#89-01617|#45seg"
                    nome           código       ciclo esperado
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from statistics import mean
from typing import Any

from app.clients import PlatformClient, make_client
from app.config import settings
from app.transforms import apply_value, get_transform


_MOLDE_RE = re.compile(r"#(\d+)\s*seg", re.IGNORECASE)


def parse_molde(s: str | None) -> dict | None:
    """Devolve {nome, codigo, ciclo_esperado_s, raw} ou None."""
    if not s or not isinstance(s, str):
        return None
    parts = [p.strip() for p in s.split("|")]
    nome = parts[0] if parts else ""
    codigo = next((p.lstrip("#") for p in parts[1:] if "seg" not in p.lower()), "")
    ciclo = None
    m = _MOLDE_RE.search(s)
    if m:
        try:
            ciclo = float(m.group(1))
        except ValueError:
            ciclo = None
    return {
        "nome": nome,
        "codigo": codigo,
        "ciclo_esperado_s": ciclo,
        "raw": s,
    }


async def _devices_with_history(
    client: PlatformClient,
    platform: str,
    variable: str,
    devices: list[dict],
    start_ms: int,
    end_ms: int,
) -> list[tuple[str, str, list]]:
    """Para cada device, baixa os pontos. Roda em paralelo."""
    transform = get_transform(platform, variable)

    async def get_one(d):
        label = d.get("label")
        name = d.get("name") or label
        if not label:
            return None
        try:
            points = await client.get_values(label, variable, start_ms, end_ms, page_size=5000)
        except Exception:
            return None
        # converte valor se houver transform
        for p in points:
            if isinstance(p.value, (int, float)) and not isinstance(p.value, bool):
                p._converted = apply_value(p.value, transform)
            else:
                p._converted = None
        return (label, name, points)

    results = await asyncio.gather(*[get_one(d) for d in devices])
    return [r for r in results if r]


async def aggregate_moldes(
    platform: str,
    variable: str = "ciclo",
    days: int = 7,
) -> dict:
    """Agrega por molde: contagem de peças, ciclo médio real vs esperado, máquinas usadas."""
    base_url, token = settings.platform(platform)
    client = make_client(platform, base_url, token)

    devices = await client.list_devices()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    series = await _devices_with_history(client, platform, variable, devices, start_ms, end_ms)

    # molde_key (codigo ou nome) -> agregado
    moldes_agg: dict[str, dict] = defaultdict(lambda: {
        "nome": "",
        "codigo": "",
        "ciclo_esperado_s": None,
        "raw_examples": set(),
        "pecas_total": 0,
        "ciclos_validos": [],
        "devices": defaultdict(int),  # label -> contagem
        "first_ts_ms": None,
        "last_ts_ms": None,
    })

    sem_molde = 0
    total_pecas = 0

    for label, name, points in series:
        for p in points:
            ctx = p.context if isinstance(p.context, dict) else None
            molde_raw = (ctx or {}).get("molde") if ctx else None
            total_pecas += 1
            if not molde_raw:
                sem_molde += 1
                continue
            info = parse_molde(molde_raw)
            if not info:
                sem_molde += 1
                continue
            key = info["codigo"] or info["nome"] or molde_raw
            agg = moldes_agg[key]
            agg["nome"] = info["nome"] or agg["nome"]
            agg["codigo"] = info["codigo"] or agg["codigo"]
            if info["ciclo_esperado_s"] is not None:
                agg["ciclo_esperado_s"] = info["ciclo_esperado_s"]
            agg["raw_examples"].add(molde_raw)
            agg["pecas_total"] += 1
            if isinstance(p._converted, (int, float)):
                agg["ciclos_validos"].append(p._converted)
            agg["devices"][label] += 1
            if agg["first_ts_ms"] is None or p.timestamp_ms < agg["first_ts_ms"]:
                agg["first_ts_ms"] = p.timestamp_ms
            if agg["last_ts_ms"] is None or p.timestamp_ms > agg["last_ts_ms"]:
                agg["last_ts_ms"] = p.timestamp_ms

    # Serializa
    out = []
    for key, a in moldes_agg.items():
        ciclos = a["ciclos_validos"]
        ciclo_medio = mean(ciclos) if ciclos else None
        ideal = a["ciclo_esperado_s"]
        desvio_pct = None
        if ciclo_medio and ideal and ideal > 0:
            desvio_pct = (ciclo_medio - ideal) / ideal * 100
        out.append({
            "key": key,
            "nome": a["nome"],
            "codigo": a["codigo"],
            "ciclo_esperado_s": ideal,
            "ciclo_medio_s": ciclo_medio,
            "ciclo_min_s": min(ciclos) if ciclos else None,
            "ciclo_max_s": max(ciclos) if ciclos else None,
            "desvio_pct": desvio_pct,
            "pecas_total": a["pecas_total"],
            "devices": [{"label": k, "pecas": v} for k, v in sorted(a["devices"].items(), key=lambda kv: -kv[1])],
            "n_devices": len(a["devices"]),
            "first_ts_ms": a["first_ts_ms"],
            "last_ts_ms": a["last_ts_ms"],
            "raw_examples": list(a["raw_examples"])[:3],
        })

    # Ordena por peças totais desc
    out.sort(key=lambda m: -m["pecas_total"])

    return {
        "platform": platform,
        "variable": variable,
        "days": days,
        "total_devices_consultados": len(series),
        "total_pecas": total_pecas,
        "pecas_sem_molde": sem_molde,
        "n_moldes_distintos": len(out),
        "moldes": out,
    }
