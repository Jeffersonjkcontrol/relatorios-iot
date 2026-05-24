from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import json

from fastapi.responses import StreamingResponse

from app.config import PLATFORMS, settings
from app.clients import PlatformClient, date_to_ms, make_client
from app.reports.csv_report import build_csv
from app.reports.pdf_report import build_pdf
from app.reports.pdf_oee import build_pdf_oee
from app.oee import compute_oee, to_dict as oee_to_dict
from app.transforms import apply_value, format_value, get_transform
from app.ai import db as ai_db
from app.ai.agent import run_turn, deserialize_messages
from app.ai.providers import list_providers

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Relatorios Ubidots", version="1.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _client(platform_id: str) -> PlatformClient:
    base_url, token = settings.platform(platform_id)
    if not token:
        raise HTTPException(
            status_code=400,
            detail=f"Token nao configurado para '{platform_id}'. Edite o arquivo .env.",
        )
    return make_client(platform_id, base_url, token)


@app.get("/", response_class=HTMLResponse)
async def page_relatorios(request: Request):
    return templates.TemplateResponse(
        "relatorios.html",
        {"request": request, "platforms": PLATFORMS, "year": datetime.now().year, "current_page": "relatorios"},
    )


@app.get("/oee", response_class=HTMLResponse)
async def page_oee(request: Request):
    return templates.TemplateResponse(
        "oee.html",
        {"request": request, "platforms": PLATFORMS, "year": datetime.now().year, "current_page": "oee"},
    )


@app.get("/config", response_class=HTMLResponse)
async def page_config(request: Request):
    return templates.TemplateResponse(
        "config.html",
        {
            "request": request, "platforms": PLATFORMS, "year": datetime.now().year,
            "current_page": "config",
            "ai_providers": list_providers(),
        },
    )


@app.get("/ai", response_class=HTMLResponse)
async def page_ai(request: Request):
    return templates.TemplateResponse(
        "ai.html",
        {
            "request": request, "platforms": PLATFORMS, "year": datetime.now().year,
            "current_page": "ai",
            "ai_providers": list_providers(),
        },
    )


# ---------- AI API ----------
@app.get("/api/ai/providers")
async def ai_providers():
    return list_providers()


@app.get("/api/ai/conversations")
async def ai_conversations():
    return ai_db.list_conversations()


@app.get("/api/ai/conversations/{cid}")
async def ai_conversation_get(cid: str):
    conv = ai_db.get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversa nao encontrada")
    return conv


@app.delete("/api/ai/conversations/{cid}")
async def ai_conversation_delete(cid: str):
    ai_db.delete_conversation(cid)
    return {"ok": True}


@app.post("/api/ai/chat")
async def ai_chat(request: Request):
    body = await request.json()
    provider_id = body.get("provider")
    model = body.get("model")
    message = (body.get("message") or "").strip()
    conv_id = body.get("conversation_id")

    if not message:
        raise HTTPException(400, "mensagem vazia")
    if not provider_id or not model:
        raise HTTPException(400, "provider e model sao obrigatorios")

    # Cria conversa se necessario
    if not conv_id:
        title = message[:60]
        conv_id = ai_db.create_conversation(title, provider_id, model)
        is_new = True
    else:
        is_new = False

    # Carrega historico
    conv = ai_db.get_conversation(conv_id) if not is_new else None
    history_msgs = deserialize_messages(conv["messages"]) if conv else []

    # Salva mensagem do user
    ai_db.add_message(conv_id, "user", message)

    async def event_stream():
        # Manda primeiro o conversation_id (frontend precisa pra salvar referencia)
        yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conv_id, 'is_new': is_new})}\n\n"

        assistant_buffer = []

        try:
            async for event in run_turn(provider_id, model, history_msgs, message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                # Acumula pra salvar no banco
                if event["type"] in ("text", "tool_call", "tool_result", "done", "report_link"):
                    assistant_buffer.append(event)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        # Salva resposta do assistant no banco (todos os eventos do turno)
        ai_db.add_message(conv_id, "assistant", assistant_buffer)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/groups")
async def api_groups(platform: str):
    client = _client(platform)
    try:
        groups = await client.list_device_groups()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao listar grupos: {e}")
    return [
        {"label": g.get("label"), "name": g.get("name") or g.get("label")}
        for g in groups
        if g.get("label")
    ]


@app.get("/api/devices")
async def api_devices(platform: str, group: str | None = None):
    client = _client(platform)
    try:
        devices = await client.list_devices(group_label=group or None)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao listar devices: {e}")
    return [
        {"label": d.get("label"), "name": d.get("name") or d.get("label")}
        for d in devices
        if d.get("label")
    ]


@app.get("/api/variables")
async def api_variables(platform: str, device: str | None = None, group: str | None = None):
    client = _client(platform)
    try:
        variables = await client.list_variables(
            device_label=device or None,
            group_label=group or None,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao listar variaveis: {e}")

    result = []
    for v in variables:
        label = v.get("label")
        if not label:
            continue
        dev = v.get("device") or {}
        result.append({
            "label": label,
            "name": v.get("name") or label,
            "unit": v.get("unit"),
            "device_label": dev.get("label") if isinstance(dev, dict) else None,
            "device_name": dev.get("name") if isinstance(dev, dict) else None,
        })
    return result


@app.get("/api/preview")
async def api_preview(
    platform: str,
    device: str,
    variable: str,
    start: str = "",
    end: str = "",
    limit: int = 200,
):
    from statistics import mean
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Sao_Paulo")

    client = _client(platform)
    start_ms = date_to_ms(start) if start else None
    end_ms = date_to_ms(end) if end else None

    try:
        points = await client.get_values(device, variable, start_ms, end_ms)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {e}")

    points.sort(key=lambda p: p.timestamp_ms)
    total = len(points)
    transform = get_transform(platform, variable)
    converted = [apply_value(p.value, transform) for p in points if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)]
    has_ctx = any(isinstance(p.context, dict) and p.context for p in points)

    sample = points[-limit:][::-1] if total > limit else points[::-1]

    return {
        "device": device,
        "variable": variable,
        "platform": platform,
        "total": total,
        "showing": len(sample),
        "has_context": has_ctx,
        "transform": {
            "applied": transform is not None,
            "factor": transform.factor if transform else 1.0,
            "unit": transform.unit if transform else "",
            "description": transform.description if transform else "",
        },
        "stats": {
            "min": format_value(min(converted), transform) if converted else None,
            "max": format_value(max(converted), transform) if converted else None,
            "mean": format_value(mean(converted), transform) if converted else None,
            "min_raw": min(converted) if converted else None,
            "max_raw": max(converted) if converted else None,
            "mean_raw": mean(converted) if converted else None,
        },
        "first_ts": (
            points[0].datetime_utc.astimezone(tz).strftime("%d/%m/%Y %H:%M:%S")
            if points else None
        ),
        "last_ts": (
            points[-1].datetime_utc.astimezone(tz).strftime("%d/%m/%Y %H:%M:%S")
            if points else None
        ),
        "points": [
            {
                "ts": p.datetime_utc.astimezone(tz).strftime("%d/%m/%Y %H:%M:%S"),
                "value_raw": p.value,
                "value": format_value(p.value, transform),
                "context": p.context if isinstance(p.context, dict) else None,
            }
            for p in sample
        ],
    }


@app.post("/relatorio")
async def gerar_relatorio(
    platform: str = Form(...),
    device: str = Form(...),
    variable: str = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    formato: str = Form("pdf"),
):
    client = _client(platform)
    start_ms = date_to_ms(start) if start else None
    end_ms = date_to_ms(end) if end else None

    try:
        points = await client.get_values(device, variable, start_ms, end_ms)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {e}")

    points.sort(key=lambda p: p.timestamp_ms)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname_base = f"{platform}_{device}_{variable}_{stamp}"

    transform = get_transform(platform, variable)

    if formato == "csv":
        content = build_csv(points, device, variable, transform)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.csv"'},
        )

    platform_label = next(
        (p["label"] for p in PLATFORMS if p["id"] == platform), platform
    )
    content = build_pdf(points, device, variable, platform_label, start, end, transform)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname_base}.pdf"'},
    )


@app.get("/api/oee")
async def api_oee(
    platform: str,
    device: str,
    variable: str,
    start: str,
    end: str,
    ciclo_ideal: float,
    refugo: int = 0,
    outlier_factor: float = 3.0,
):
    client = _client(platform)
    start_ms = date_to_ms(start) if start else None
    end_ms = date_to_ms(end) if end else None

    try:
        points = await client.get_values(device, variable, start_ms, end_ms)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {e}")

    points.sort(key=lambda p: p.timestamp_ms)
    transform = get_transform(platform, variable)
    result = compute_oee(points, ciclo_ideal, refugo, start_ms, end_ms, transform, outlier_factor)
    return oee_to_dict(result)


@app.post("/relatorio_oee")
async def relatorio_oee(
    platform: str = Form(...),
    device: str = Form(...),
    variable: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    ciclo_ideal: float = Form(...),
    refugo: int = Form(0),
    outlier_factor: float = Form(3.0),
):
    client = _client(platform)
    start_ms = date_to_ms(start)
    end_ms = date_to_ms(end)

    try:
        points = await client.get_values(device, variable, start_ms, end_ms)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {e}")

    points.sort(key=lambda p: p.timestamp_ms)
    transform = get_transform(platform, variable)
    result = compute_oee(points, ciclo_ideal, refugo, start_ms, end_ms, transform, outlier_factor)

    platform_label = next((p["label"] for p in PLATFORMS if p["id"] == platform), platform)
    pdf = build_pdf_oee(result, device, variable, platform_label)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"oee_{platform}_{device}_{stamp}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
