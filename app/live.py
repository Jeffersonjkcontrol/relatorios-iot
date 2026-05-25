"""Snapshot ao vivo de todas as máquinas: status, último ciclo, média recente.

Estratégia: pega só os últimos N pontos de cada máquina pra cada variável de interesse.
Cache curto pra evitar bombardear a API quando vários browsers abertos no /live.
"""
from __future__ import annotations

import asyncio
import time
from statistics import mean
from typing import Any

from app.clients import PlatformClient, make_client
from app.config import PLATFORMS, settings
from app.transforms import apply_value, get_transform

_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 8  # segundos (menor que o polling de 10s, mas evita burst)


def _client(platform: str) -> PlatformClient:
    base_url, token = settings.platform(platform)
    return make_client(platform, base_url, token)


async def _snapshot_for_device(
    client: PlatformClient,
    platform: str,
    device_label: str,
    device_name: str | None,
    variable: str,
    horizon_ms: int = 30 * 60 * 1000,
) -> dict:
    """Pega últimos pontos das últimas 30min de uma variável; calcula stats."""
    now_ms = int(time.time() * 1000)
    transform = get_transform(platform, variable)
    try:
        points = await client.get_values(device_label, variable, now_ms - horizon_ms, now_ms, page_size=50)
    except Exception as e:
        return {
            "device": device_label, "name": device_name or device_label,
            "variable": variable, "error": str(e)[:80],
            "status": "erro", "last_value": None, "last_ts": None,
            "mean_recent": None, "count_recent": 0,
        }
    points.sort(key=lambda p: p.timestamp_ms)
    if not points:
        return {
            "device": device_label, "name": device_name or device_label,
            "variable": variable, "status": "offline",
            "last_value": None, "last_ts": None,
            "mean_recent": None, "count_recent": 0,
            "minutes_since_last": None,
        }
    last = points[-1]
    valores = [apply_value(p.value, transform) for p in points
               if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)]
    last_val = apply_value(last.value, transform)
    minutes_since = (now_ms - last.timestamp_ms) / 60000
    if minutes_since < 2:
        status = "ativo"
    elif minutes_since < 10:
        status = "ocioso"
    else:
        status = "parado"

    # Tendência: compara média da 2ª metade vs 1ª metade (só se tem >=6 pontos)
    trend = "estavel"
    if len(valores) >= 6:
        half = len(valores) // 2
        m1 = mean(valores[:half])
        m2 = mean(valores[half:])
        if m2 > m1 * 1.05:
            trend = "subindo"
        elif m2 < m1 * 0.95:
            trend = "descendo"

    last_ctx = last.context if isinstance(last.context, dict) else None
    molde = (last_ctx or {}).get("molde") if last_ctx else None

    return {
        "device": device_label,
        "name": device_name or device_label,
        "variable": variable,
        "status": status,
        "last_value": last_val,
        "last_ts": last.datetime_utc.isoformat(),
        "mean_recent": mean(valores) if valores else None,
        "count_recent": len(points),
        "minutes_since_last": round(minutes_since, 1),
        "unit": (transform.unit if transform else ""),
        "trend": trend,
        "molde": molde,
    }


async def get_live_snapshot(platform: str, variable: str = "ciclo") -> list[dict]:
    """Snapshot de todas as máquinas para a variável dada. Cache de 8s."""
    key = f"{platform}:{variable}"
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < _CACHE_TTL:
            return data

    client = _client(platform)
    devices = await client.list_devices()
    # Roda em paralelo
    tasks = [
        _snapshot_for_device(client, platform, d.get("label"), d.get("name"), variable)
        for d in devices if d.get("label")
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    # Ordena: ativos primeiro, depois ociosos, depois parados, depois erros
    order = {"ativo": 0, "ocioso": 1, "parado": 2, "offline": 3, "erro": 4}
    results.sort(key=lambda r: (order.get(r.get("status"), 99), r.get("device", "")))
    _CACHE[key] = (now, results)
    return results
