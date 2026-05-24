from __future__ import annotations

import csv
import io
import json
from zoneinfo import ZoneInfo

from app.clients.ubidots import ValuePoint
from app.transforms import Transform, apply_value, format_br

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def _context_keys(points: list[ValuePoint]) -> list[str]:
    seen: dict[str, None] = {}
    for p in points:
        if isinstance(p.context, dict):
            for k in p.context.keys():
                seen.setdefault(k, None)
    return list(seen.keys())


def build_csv(
    points: list[ValuePoint],
    device_label: str,
    variable_label: str,
    transform: Transform | None = None,
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")

    ctx_keys = _context_keys(points)
    header = ["device", "variable", "timestamp_utc", "timestamp_local", "valor_bruto"]
    if transform:
        header += [f"valor_convertido ({transform.unit})" if transform.unit else "valor_convertido"]
    header += ["contexto_json"]
    header += [f"ctx.{k}" for k in ctx_keys]
    writer.writerow(header)

    for p in points:
        utc = p.datetime_utc
        local = utc.astimezone(LOCAL_TZ)
        ctx = p.context if isinstance(p.context, dict) else None
        ctx_json = json.dumps(ctx, ensure_ascii=False, sort_keys=True) if ctx else ""
        row = [
            device_label,
            variable_label,
            utc.strftime("%Y-%m-%d %H:%M:%S"),
            local.strftime("%Y-%m-%d %H:%M:%S"),
            p.value,
        ]
        if transform:
            converted = apply_value(p.value, transform)
            row.append(format_br(converted, transform.decimals) if isinstance(converted, (int, float)) else "")
        row.append(ctx_json)
        row += [ctx.get(k, "") if ctx else "" for k in ctx_keys]
        writer.writerow(row)

    return buf.getvalue().encode("utf-8-sig")
