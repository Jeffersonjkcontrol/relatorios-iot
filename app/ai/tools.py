"""Tools que a IA pode invocar. Cada tool tem schema JSON e funcao Python.

A IA recebe a lista de schemas, decide qual chamar com quais args, e o agent
executa a funcao correspondente e devolve o resultado serializado em JSON.

REGRA: nunca retorne grandes volumes de pontos brutos (custo de tokens).
Devolva sumarios + amostras pequenas.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any
from urllib.parse import urlencode

from app.ai.providers import ToolDef
from app.clients import date_to_ms, make_client
from app.config import PLATFORMS, settings
from app.oee import compute_oee, to_dict as oee_to_dict
from app.transforms import apply_value, format_br, get_transform


# ---------- Helpers ----------
PLATFORM_IDS = [p["id"] for p in PLATFORMS]


def _client(platform: str):
    base_url, token = settings.platform(platform)
    if not token:
        raise ValueError(f"Token não configurado para a plataforma '{platform}'")
    return make_client(platform, base_url, token)


def _parse_when(when: str) -> int:
    """Aceita formatos: 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM', 'now', '-2h', '-1d', '-30m'."""
    when = when.strip().lower()
    now = datetime.now()
    if when in ("now", "agora"):
        return int(now.timestamp() * 1000)
    if when.startswith("-") and when[-1] in "mhd":
        n = int(when[1:-1])
        unit = when[-1]
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return int((now - delta).timestamp() * 1000)
    return date_to_ms(when)


# Cache curto de devices por plataforma (resolucao name->label)
_DEVICES_CACHE: dict[str, tuple[float, list[dict]]] = {}
_DEVICES_TTL_S = 60  # 1 minuto


async def _get_devices_cached(platform: str) -> list[dict]:
    import time
    now = time.time()
    if platform in _DEVICES_CACHE:
        ts, devs = _DEVICES_CACHE[platform]
        if now - ts < _DEVICES_TTL_S:
            return devs
    c = _client(platform)
    devs = await c.list_devices()
    _DEVICES_CACHE[platform] = (now, devs)
    return devs


async def _resolve_device(platform: str, device_input: str) -> str:
    """Recebe nome amigável ou label e retorna o label real.
    Faz match exato no label primeiro, depois match no name (case-insensitive,
    com normalização de espaços). Levanta erro com sugestões se não achar."""
    if not device_input:
        raise ValueError("Dispositivo não informado")

    devices = await _get_devices_cached(platform)
    if not devices:
        raise ValueError(f"Nenhum dispositivo encontrado na plataforma '{platform}'")

    raw = device_input.strip()
    norm = raw.lower().replace(" ", "").replace("-", "").replace("_", "")

    # 1) match exato no label
    for d in devices:
        if d.get("label") == raw:
            return raw

    # 2) match case-insensitive no label
    for d in devices:
        if (d.get("label") or "").lower() == raw.lower():
            return d["label"]

    # 3) match normalizado (ignora espacos/hifen/underscore) - label OU name
    for d in devices:
        for field in (d.get("label"), d.get("name")):
            if not field:
                continue
            fnorm = field.lower().replace(" ", "").replace("-", "").replace("_", "")
            if fnorm == norm:
                return d["label"]

    # 4) match por substring no name
    matches = [d for d in devices if (d.get("name") or "").lower().find(raw.lower()) >= 0]
    if len(matches) == 1:
        return matches[0]["label"]
    if len(matches) > 1:
        sug = ", ".join(f"{m['label']} ({m.get('name', '')})" for m in matches[:5])
        raise ValueError(f"'{raw}' é ambíguo. Encontrei vários: {sug}. Use o label exato.")

    # não achou — retornar sugestões
    sample = ", ".join(f"{d['label']} ({d.get('name', '')})" for d in devices[:8])
    raise ValueError(
        f"Dispositivo '{raw}' não encontrado na plataforma '{platform}'. "
        f"Dispositivos disponíveis (amostra): {sample}. Total: {len(devices)}."
    )


# ---------- Tools ----------
async def t_list_platforms(**_) -> dict:
    return {"platforms": [{"id": p["id"], "label": p["label"]} for p in PLATFORMS]}


async def t_list_devices(platform: str, group: str | None = None, **_) -> dict:
    c = _client(platform)
    devices = await c.list_devices(group_label=group or None)
    return {"count": len(devices), "devices": [{"label": d.get("label"), "name": d.get("name")} for d in devices]}


async def t_list_groups(platform: str, **_) -> dict:
    c = _client(platform)
    groups = await c.list_device_groups()
    return {"count": len(groups), "groups": [{"label": g.get("label"), "name": g.get("name")} for g in groups]}


async def t_list_variables(platform: str, device: str | None = None, group: str | None = None, **_) -> dict:
    c = _client(platform)
    if device:
        device = await _resolve_device(platform, device)
    vars_ = await c.list_variables(device_label=device or None, group_label=group or None)
    seen = set()
    uniq = []
    for v in vars_:
        lbl = v.get("label")
        if not lbl or lbl in seen:
            continue
        seen.add(lbl)
        uniq.append({"label": lbl, "name": v.get("name"), "unit": v.get("unit")})
    return {"count": len(uniq), "variables": uniq}


async def t_summarize_variable(platform: str, device: str, variable: str,
                                 start: str, end: str, **_) -> dict:
    device = await _resolve_device(platform, device)
    c = _client(platform)
    sms, ems = _parse_when(start), _parse_when(end)
    points = await c.get_values(device, variable, sms, ems)
    points.sort(key=lambda p: p.timestamp_ms)
    transform = get_transform(platform, variable)
    valores = [apply_value(p.value, transform) for p in points
               if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)]
    unit = transform.unit if transform else ""

    sample = points[-10:][::-1]
    sample_out = [{
        "ts": datetime.fromtimestamp(p.timestamp_ms / 1000).isoformat(),
        "value": apply_value(p.value, transform) if transform else p.value,
        "context": p.context if isinstance(p.context, dict) else None,
    } for p in sample]

    return {
        "device": device,
        "variable": variable,
        "unit": unit,
        "start": start, "end": end,
        "count": len(points),
        "stats": {
            "min": min(valores) if valores else None,
            "max": max(valores) if valores else None,
            "mean": mean(valores) if valores else None,
        },
        "first_ts": datetime.fromtimestamp(points[0].timestamp_ms / 1000).isoformat() if points else None,
        "last_ts": datetime.fromtimestamp(points[-1].timestamp_ms / 1000).isoformat() if points else None,
        "sample_last_10": sample_out,
    }


async def t_compute_oee(platform: str, device: str, variable: str,
                         start: str, end: str, ciclo_ideal: float,
                         refugo: int = 0, outlier_factor: float = 3.0, **_) -> dict:
    device = await _resolve_device(platform, device)
    c = _client(platform)
    sms, ems = _parse_when(start), _parse_when(end)
    points = await c.get_values(device, variable, sms, ems)
    points.sort(key=lambda p: p.timestamp_ms)
    transform = get_transform(platform, variable)
    result = compute_oee(points, ciclo_ideal, refugo, sms, ems, transform, outlier_factor)
    d = oee_to_dict(result)
    # Adicionar versoes formatadas pra IA conseguir narrar facil
    d["formatted"] = {
        "oee_pct": f"{d['oee']*100:.1f}%",
        "disponibilidade_pct": f"{d['disponibilidade']*100:.1f}%",
        "performance_pct": f"{d['performance']*100:.1f}%",
        "qualidade_pct": f"{d['qualidade']*100:.1f}%",
    }
    return d


async def t_compare_devices(platform: str, devices: list[str], variable: str,
                             start: str, end: str, **_) -> dict:
    c = _client(platform)
    sms, ems = _parse_when(start), _parse_when(end)
    transform = get_transform(platform, variable)
    results = []
    for dev_input in devices:
        try:
            dev = await _resolve_device(platform, dev_input)
            points = await c.get_values(dev, variable, sms, ems)
        except Exception as e:
            results.append({"device": dev_input, "error": str(e)})
            continue
        valores = [apply_value(p.value, transform) for p in points
                   if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)]
        results.append({
            "device": dev,
            "input_received": dev_input,
            "count": len(points),
            "min": min(valores) if valores else None,
            "max": max(valores) if valores else None,
            "mean": mean(valores) if valores else None,
        })
    return {"variable": variable, "start": start, "end": end, "comparison": results}


def _to_iso_for_form(when: str) -> str:
    """Converte qualquer formato aceito (incluindo '-1h', 'now') para ISO
    'YYYY-MM-DDTHH:MM' que o endpoint /relatorio aceita."""
    if not when:
        return ""
    ms = _parse_when(when)
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M")


async def t_calculate(expression: str, **_) -> dict:
    """Avalia expressão matemática usando AST seguro."""
    from app.ai.calculator import safe_calc, CalcError
    try:
        result = safe_calc(expression)
        return {"expression": expression, "result": result}
    except CalcError as e:
        return {"error": f"Erro de cálculo: {e}", "expression": expression}
    except Exception as e:
        return {"error": str(e), "expression": expression}


async def t_generate_report_link(tipo: str, platform: str, device: str, variable: str,
                                  start: str = "", end: str = "",
                                  formato: str = "pdf",
                                  ciclo_ideal: float | None = None,
                                  refugo: int = 0,
                                  outlier_factor: float = 3.0,
                                  **_) -> dict:
    """Gera URL absoluta que o frontend renderiza como botão de download.
    tipo: 'relatorio' (PDF/CSV de variável) ou 'oee' (PDF OEE).
    Converte start/end de qualquer formato (-1h, now, etc.) para ISO."""
    # Resolve device (aceita "injetora 80" -> "inj80")
    try:
        device_resolved = await _resolve_device(platform, device)
    except Exception:
        device_resolved = device  # se não conseguir, manda original

    start_iso = _to_iso_for_form(start)
    end_iso = _to_iso_for_form(end)

    if tipo == "oee":
        params = {
            "platform": platform, "device": device_resolved, "variable": variable,
            "start": start_iso, "end": end_iso,
            "ciclo_ideal": str(ciclo_ideal or 0),
            "refugo": str(refugo),
            "outlier_factor": str(outlier_factor),
        }
        return {
            "tipo": "oee",
            "method": "POST",
            "url": "/relatorio_oee",
            "params": params,
            "label": f"Baixar PDF de OEE — {device_resolved}",
        }
    else:
        params = {
            "platform": platform, "device": device_resolved, "variable": variable,
            "start": start_iso, "end": end_iso, "formato": formato,
        }
        return {
            "tipo": "relatorio",
            "method": "POST",
            "url": "/relatorio",
            "params": params,
            "label": f"Baixar {formato.upper()} — {device_resolved}/{variable}",
        }


# ---------- Registry ----------
TOOLS: dict[str, dict] = {
    "list_platforms": {
        "fn": t_list_platforms,
        "def": ToolDef(
            name="list_platforms",
            description="Lista as plataformas IoT configuradas. Use quando o usuário não especificou de qual plataforma quer os dados.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
    },
    "list_groups": {
        "fn": t_list_groups,
        "def": ToolDef(
            name="list_groups",
            description="Lista os grupos de dispositivos disponíveis em uma plataforma.",
            parameters={
                "type": "object",
                "properties": {"platform": {"type": "string", "enum": PLATFORM_IDS}},
                "required": ["platform"],
            },
        ),
    },
    "list_devices": {
        "fn": t_list_devices,
        "def": ToolDef(
            name="list_devices",
            description="Lista dispositivos (máquinas) de uma plataforma, opcionalmente filtrados por grupo.",
            parameters={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": PLATFORM_IDS},
                    "group": {"type": "string", "description": "Label do grupo (opcional)"},
                },
                "required": ["platform"],
            },
        ),
    },
    "list_variables": {
        "fn": t_list_variables,
        "def": ToolDef(
            name="list_variables",
            description="Lista variáveis disponíveis em um dispositivo (ou grupo). Use para descobrir o que pode ser consultado.",
            parameters={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": PLATFORM_IDS},
                    "device": {"type": "string", "description": "Label do dispositivo (ex.: inj82)"},
                    "group": {"type": "string", "description": "Label do grupo (alternativo a device)"},
                },
                "required": ["platform"],
            },
        ),
    },
    "summarize_variable": {
        "fn": t_summarize_variable,
        "def": ToolDef(
            name="summarize_variable",
            description="Retorna estatísticas (mín/máx/média/count) e amostra das 10 últimas leituras de uma variável num período. Datas aceitam 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM', 'now', '-2h', '-1d', '-30m'.",
            parameters={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": PLATFORM_IDS},
                    "device": {"type": "string"},
                    "variable": {"type": "string"},
                    "start": {"type": "string", "description": "Início (ex.: '2026-05-23', '-24h')"},
                    "end": {"type": "string", "description": "Fim (ex.: '2026-05-23T18:00', 'now')"},
                },
                "required": ["platform", "device", "variable", "start", "end"],
            },
        ),
    },
    "compute_oee": {
        "fn": t_compute_oee,
        "def": ToolDef(
            name="compute_oee",
            description="Calcula OEE (Disponibilidade × Performance × Qualidade) de uma máquina injetora. Precisa do ciclo ideal em segundos e do número de peças não conformes (refugo).",
            parameters={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": PLATFORM_IDS},
                    "device": {"type": "string"},
                    "variable": {"type": "string", "description": "Variável de ciclo (geralmente 'ciclo')"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "ciclo_ideal": {"type": "number", "description": "Ciclo ideal em segundos"},
                    "refugo": {"type": "integer", "description": "Peças não conformes", "default": 0},
                    "outlier_factor": {"type": "number", "description": "Ciclos maiores que N × ideal viram paradas (padrão 3, 0 desativa)", "default": 3.0},
                },
                "required": ["platform", "device", "variable", "start", "end", "ciclo_ideal"],
            },
        ),
    },
    "compare_devices": {
        "fn": t_compare_devices,
        "def": ToolDef(
            name="compare_devices",
            description="Compara a mesma variável entre vários dispositivos no mesmo período. Retorna estatísticas por dispositivo.",
            parameters={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": PLATFORM_IDS},
                    "devices": {"type": "array", "items": {"type": "string"}, "description": "Lista de labels"},
                    "variable": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["platform", "devices", "variable", "start", "end"],
            },
        ),
    },
    "calculate": {
        "fn": t_calculate,
        "def": ToolDef(
            name="calculate",
            description=(
                "Calcula uma expressão matemática Python. Use para cálculos "
                "estatísticos, conversões, fórmulas complexas. Aceita operadores "
                "(+,-,*,/,%,**,//), funções math (sqrt, log, sin, cos, exp, floor, ceil, "
                "abs, round, min, max, sum, factorial), estatística (mean, median, "
                "stdev, variance) e constantes (pi, e, tau). Pode passar listas: "
                "ex.: 'mean([45.1, 45.8, 44.9])' ou 'sqrt(sum([x**2 for x in [1,2,3]]))'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Expressão Python segura. Ex.: '(2+3)*4', 'mean([45,46,47])', 'stdev([1,2,3,4,5])'"},
                },
                "required": ["expression"],
            },
        ),
    },
    "generate_report_link": {
        "fn": t_generate_report_link,
        "def": ToolDef(
            name="generate_report_link",
            description="Gera um link/botão de download de relatório. Use quando o usuário pedir um PDF/CSV. tipo='oee' para relatório OEE (requer ciclo_ideal); tipo='relatorio' para PDF/CSV padrão de variável.",
            parameters={
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["relatorio", "oee"]},
                    "platform": {"type": "string", "enum": PLATFORM_IDS},
                    "device": {"type": "string"},
                    "variable": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "formato": {"type": "string", "enum": ["pdf", "csv"], "default": "pdf"},
                    "ciclo_ideal": {"type": "number", "description": "Obrigatório se tipo=oee"},
                    "refugo": {"type": "integer", "default": 0},
                    "outlier_factor": {"type": "number", "default": 3.0},
                },
                "required": ["tipo", "platform", "device", "variable", "start", "end"],
            },
        ),
    },
}


def all_tool_defs() -> list[ToolDef]:
    return [t["def"] for t in TOOLS.values()]


async def execute_tool(name: str, arguments: dict) -> str:
    """Executa a tool e retorna JSON serializado (o que vai pro LLM)."""
    if name not in TOOLS:
        return json.dumps({"error": f"Tool '{name}' não existe"})
    try:
        result = await TOOLS[name]["fn"](**arguments)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
