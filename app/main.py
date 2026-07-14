from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import json

from fastapi import Depends
from fastapi.responses import StreamingResponse, RedirectResponse

from app.config import PLATFORMS, settings
from app.clients import PlatformClient, date_to_ms, make_client
from app.reports.csv_report import build_csv
from app.reports.pdf_report import build_pdf
from app.reports.pdf_oee import build_pdf_oee
from app.reports.pdf_paradas import build_pdf_paradas
from app.oee import compute_oee, to_dict as oee_to_dict
from app.transforms import apply_value, format_value, get_transform
from app.ai import db as ai_db
from app.ai.agent import run_turn, deserialize_messages
from app.ai.providers import list_providers
from app.live import get_live_snapshot
from app.moldes import aggregate_moldes
from app.paradas import analyze_paradas
from app.cadastros import list_codigos, create_codigo, delete_codigo
from app.admin import (
    is_maintenance_mode, set_maintenance_mode, get_maintenance_msg,
    container_status, restart_app, run_backup, get_recent_logs, list_backups,
)
from app.ai.knowledge import (
    get_business_rules, set_business_rules,
    get_glossary, set_glossary,
    get_oee_targets, set_oee_targets,
)
from app.auth import (
    User, current_user, require_user, require_gestor,
    create_session_cookie, COOKIE_NAME,
)
from app.auth.db import (
    list_users, create_user, delete_user as auth_delete_user,
    change_password, get_user_by_username, verify_password,
    reset_password as auth_reset_password, update_profile,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Relatorios Ubidots", version="1.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _client(platform_id: str) -> PlatformClient:
    base_url, token = settings.platform(platform_id)
    if not token:
        raise HTTPException(
            status_code=400,
            detail=f"Token não configurado para '{platform_id}'. Edite o arquivo .env.",
        )
    return make_client(platform_id, base_url, token)


def tctx(request: Request, **extra) -> dict:
    """Contexto-base para todas as templates: inclui current_user e platforms."""
    base = {
        "request": request,
        "current_user": current_user(request),
        "platforms": PLATFORMS,
        "year": datetime.now().year,
    }
    base.update(extra)
    return base


# ============================================================
# Middleware: redireciona pra /login se não autenticado
# ============================================================
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    public_paths = {"/login", "/logout", "/change-password", "/maintenance"}
    is_static = path.startswith("/static/")
    if path in public_paths or is_static:
        return await call_next(request)

    user = current_user(request)
    if not user:
        if path.startswith("/api/") or request.method != "GET":
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Não autenticado"}, status_code=401)
        return RedirectResponse(url=f"/login?next={path}", status_code=303)

    if user.must_change_password and path != "/change-password":
        return RedirectResponse(url="/login?must_change=1", status_code=303)

    # Modo manutenção: bloqueia tudo exceto admin pra operadores
    if is_maintenance_mode() and user.role != "gestor":
        if path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Sistema em manutenção"}, status_code=503)
        if path != "/maintenance":
            return RedirectResponse(url="/maintenance", status_code=303)

    return await call_next(request)


@app.get("/maintenance", response_class=HTMLResponse)
async def page_maintenance(request: Request):
    return templates.TemplateResponse("maintenance.html", {
        "request": request, "message": get_maintenance_msg(),
    })


# ============================================================
# Auth: login / logout / trocar senha
# ============================================================
@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request, must_change: int = 0):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "must_change": bool(must_change),
        "error": request.query_params.get("error"),
        "info": request.query_params.get("info"),
    })


@app.post("/login")
async def do_login(
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    res = get_user_by_username(username)
    if not res:
        return RedirectResponse(url="/login?error=Usuário+ou+senha+inválidos", status_code=303)
    user, pwd_hash = res
    if not verify_password(password, pwd_hash):
        return RedirectResponse(url="/login?error=Usuário+ou+senha+inválidos", status_code=303)

    if user.must_change_password:
        # cria sessão temporária só pra permitir troca de senha
        resp = RedirectResponse(url="/login?must_change=1", status_code=303)
        resp.set_cookie(COOKIE_NAME, create_session_cookie(user.username),
                        httponly=True, samesite="lax", max_age=600)
        return resp

    resp = RedirectResponse(url=next or "/", status_code=303)
    from app.admin import get_session_max_age_days
    max_age = get_session_max_age_days() * 24 * 3600
    resp.set_cookie(COOKIE_NAME, create_session_cookie(user.username),
                    httponly=True, samesite="lax", max_age=max_age)
    return resp


@app.post("/change-password")
async def do_change_password(
    request: Request,
    new_password: str = Form(...),
    confirm: str = Form(...),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?error=Sessão+expirada", status_code=303)
    if new_password != confirm:
        return RedirectResponse(url="/login?must_change=1&error=Senhas+não+conferem", status_code=303)
    try:
        change_password(user.id, new_password)
    except ValueError as e:
        return RedirectResponse(url=f"/login?must_change=1&error={str(e)}", status_code=303)
    resp = RedirectResponse(url="/", status_code=303)
    from app.admin import get_session_max_age_days
    max_age = get_session_max_age_days() * 24 * 3600
    resp.set_cookie(COOKIE_NAME, create_session_cookie(user.username),
                    httponly=True, samesite="lax", max_age=max_age)
    return resp


@app.get("/logout")
@app.post("/logout")
async def do_logout():
    resp = RedirectResponse(url="/login?info=Você+saiu+do+sistema", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ============================================================
# Gestão de usuários (somente gestor)
# ============================================================
# ============================================================
# /perfil - qualquer usuário trocar a própria senha
# ============================================================
@app.get("/perfil", response_class=HTMLResponse)
async def page_perfil(request: Request, user: User = Depends(require_user)):
    created_at_fmt = datetime.fromtimestamp(user.created_at).strftime("%d/%m/%Y %H:%M")
    return templates.TemplateResponse("perfil.html", tctx(
        request, current_page="perfil",
        created_at_fmt=created_at_fmt,
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
    ))


@app.post("/perfil/info")
async def perfil_update_info(
    full_name: str = Form(""),
    email: str = Form(""),
    user: User = Depends(require_user),
):
    try:
        update_profile(user.id, full_name=full_name, email=email)
    except ValueError as e:
        return RedirectResponse(url=f"/perfil?error={e}", status_code=303)
    return RedirectResponse(url="/perfil?msg=Dados+atualizados", status_code=303)


@app.post("/perfil/senha")
async def perfil_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
    user: User = Depends(require_user),
):
    # Confirma senha atual
    res = get_user_by_username(user.username)
    if not res or not verify_password(current_password, res[1]):
        return RedirectResponse(url="/perfil?error=Senha+atual+incorreta", status_code=303)
    if new_password != confirm:
        return RedirectResponse(url="/perfil?error=Senhas+não+conferem", status_code=303)
    if current_password == new_password:
        return RedirectResponse(url="/perfil?error=A+nova+senha+precisa+ser+diferente+da+atual", status_code=303)
    try:
        change_password(user.id, new_password)
    except ValueError as e:
        return RedirectResponse(url=f"/perfil?error={e}", status_code=303)
    return RedirectResponse(url="/perfil?msg=Senha+atualizada+com+sucesso", status_code=303)


@app.get("/users", response_class=HTMLResponse)
async def page_users(request: Request, user: User = Depends(require_gestor)):
    users = list_users()
    users_data = [{
        "id": u.id, "username": u.username, "role": u.role,
        "must_change_password": u.must_change_password,
        "created_at_fmt": datetime.fromtimestamp(u.created_at).strftime("%d/%m/%Y %H:%M"),
    } for u in users]
    return templates.TemplateResponse("users.html", tctx(
        request, current_page="users", users=users_data, current=user,
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
    ))


@app.post("/users")
async def create_user_route(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    user: User = Depends(require_gestor),
):
    try:
        create_user(username, password, role)
    except ValueError as e:
        return RedirectResponse(url=f"/users?error={e}", status_code=303)
    return RedirectResponse(url=f"/users?msg=Usuário+criado", status_code=303)


@app.post("/users/{uid}/delete")
async def delete_user_route(uid: int, user: User = Depends(require_gestor)):
    if uid == user.id:
        return RedirectResponse(url="/users?error=Você+não+pode+excluir+a+si+mesmo", status_code=303)
    auth_delete_user(uid)
    return RedirectResponse(url="/users?msg=Usuário+excluído", status_code=303)


@app.post("/users/{uid}/reset-password")
async def reset_password_route(
    uid: int,
    new_password: str = Form(...),
    user: User = Depends(require_gestor),
):
    """Gestor reseta a senha de outro usuário. Força o usuário a trocar a senha
    no próximo login (must_change_password=True)."""
    if uid == user.id:
        return RedirectResponse(url="/users?error=Use+%2Fperfil+para+trocar+a+propria+senha", status_code=303)
    if not new_password or len(new_password) < 4:
        return RedirectResponse(url="/users?error=Senha+precisa+ter+ao+menos+4+caracteres", status_code=303)
    try:
        auth_reset_password(uid, new_password)
    except ValueError as e:
        return RedirectResponse(url=f"/users?error={e}", status_code=303)
    return RedirectResponse(
        url=f"/users?msg=Senha+resetada.+O+usuário+precisará+trocá-la+no+próximo+login.",
        status_code=303,
    )


# ============================================================
# Painel /admin (somente gestor)
# ============================================================
@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request, user: User = Depends(require_gestor)):
    from app.admin import get_session_max_age_days
    status = await container_status()
    backups = list_backups()
    for b in backups:
        b["mtime_fmt"] = datetime.fromtimestamp(b["mtime"]).strftime("%d/%m/%Y %H:%M")
    return templates.TemplateResponse("admin.html", tctx(
        request, current_page="admin",
        status=status, backups=backups,
        maintenance_on=is_maintenance_mode(),
        maintenance_msg=get_maintenance_msg(),
        session_max_age_days=get_session_max_age_days(),
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
    ))


@app.post("/admin/maintenance")
async def admin_set_maintenance(
    active: str = Form("0"),
    message: str = Form(""),
    user: User = Depends(require_gestor),
):
    set_maintenance_mode(active == "1", message)
    label = "ativada" if active == "1" else "desligada"
    return RedirectResponse(url=f"/admin?msg=Manutenção+{label}", status_code=303)


@app.post("/admin/session-expiry")
async def admin_set_session_expiry(
    days: int = Form(...),
    user: User = Depends(require_gestor),
):
    from app.admin import set_session_max_age_days
    try:
        set_session_max_age_days(days)
    except ValueError as e:
        return RedirectResponse(url=f"/admin?error={e}", status_code=303)
    return RedirectResponse(url=f"/admin?msg=Expiração+de+sessão+definida+como+{days}+dias", status_code=303)


@app.post("/admin/restart")
async def admin_restart(user: User = Depends(require_gestor)):
    result = await restart_app()
    if not result.get("ok"):
        return RedirectResponse(url=f"/admin?error={result.get('error', 'Falha')}", status_code=303)
    return RedirectResponse(url="/admin?msg=Container+reiniciando…", status_code=303)


@app.post("/admin/backup")
async def admin_backup(user: User = Depends(require_gestor)):
    result = await run_backup()
    if not result.get("ok"):
        return RedirectResponse(url=f"/admin?error={result.get('error', 'Falha')}", status_code=303)
    return RedirectResponse(url="/admin?msg=Backup+concluído", status_code=303)


@app.get("/admin/ia", response_class=HTMLResponse)
async def page_admin_ia(request: Request, user: User = Depends(require_gestor)):
    return templates.TemplateResponse("admin_ia.html", tctx(
        request, current_page="admin",
        business_rules=get_business_rules(),
        glossary=get_glossary(),
        oee_targets=get_oee_targets(),
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
    ))


@app.post("/admin/ia")
async def admin_ia_save(
    business_rules: str = Form(""),
    oee_targets: str = Form(""),
    glossary: str = Form(""),
    user: User = Depends(require_gestor),
):
    set_business_rules(business_rules)
    set_oee_targets(oee_targets)
    set_glossary(glossary)
    return RedirectResponse(url="/admin/ia?msg=Conhecimento+atualizado+(novas+conversas+já+usam)", status_code=303)


@app.get("/admin/logs", response_class=HTMLResponse)
async def page_admin_logs(request: Request, lines: int = 200, user: User = Depends(require_gestor)):
    logs = await get_recent_logs(lines)
    return templates.TemplateResponse("admin_logs.html", tctx(
        request, current_page="admin", logs=logs, lines=lines,
    ))


@app.get("/", response_class=HTMLResponse)
async def page_relatorios(request: Request):
    return templates.TemplateResponse(
        "relatorios.html", tctx(request, current_page="relatorios"),
    )


@app.get("/oee", response_class=HTMLResponse)
async def page_oee(request: Request):
    return templates.TemplateResponse(
        "oee.html", tctx(request, current_page="oee"),
    )


@app.get("/config", response_class=HTMLResponse)
async def page_config(request: Request, user: User = Depends(require_gestor)):
    return templates.TemplateResponse(
        "config.html", tctx(request, current_page="config", ai_providers=list_providers()),
    )


@app.get("/ai", response_class=HTMLResponse)
async def page_ai(request: Request):
    return templates.TemplateResponse(
        "ai.html", tctx(request, current_page="ai", ai_providers=list_providers()),
    )


@app.get("/live", response_class=HTMLResponse)
async def page_live(request: Request):
    return templates.TemplateResponse("live.html", tctx(request, current_page="live"))


@app.get("/api/live")
async def api_live(platform: str, variable: str = "ciclo"):
    try:
        return await get_live_snapshot(platform, variable)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# Cache em memória do display name por plataforma (expira em 1h)
_PLATFORM_DISPLAY_CACHE: dict[str, tuple[float, dict]] = {}
_DISPLAY_TTL_S = 3600


@app.get("/api/platform-display")
async def api_platform_display(platform: str):
    """Devolve {label, name, fallback} para o badge da plataforma.
    Para Ubidots: nome da organização (ex.: 'Injequaly').
    Para JKControl: o label padrão configurado em PLATFORMS."""
    import time
    fallback_label = next((p["label"] for p in PLATFORMS if p["id"] == platform), platform)
    now = time.time()
    if platform in _PLATFORM_DISPLAY_CACHE:
        ts, data = _PLATFORM_DISPLAY_CACHE[platform]
        if now - ts < _DISPLAY_TTL_S:
            return data
    try:
        client = _client(platform)
        info = await client.get_organization_display()
    except Exception:
        info = None
    if info and info.get("name"):
        result = {"display": info["name"], "label": info.get("label", ""), "fallback": fallback_label, "source": "organization"}
    else:
        result = {"display": fallback_label, "label": platform, "fallback": fallback_label, "source": "platform"}
    _PLATFORM_DISPLAY_CACHE[platform] = (now, result)
    return result


@app.get("/moldes", response_class=HTMLResponse)
async def page_moldes(request: Request):
    return templates.TemplateResponse("moldes.html", tctx(request, current_page="moldes"))


@app.get("/api/moldes")
async def api_moldes(platform: str, variable: str = "ciclo", days: int = 7):
    try:
        return await aggregate_moldes(platform, variable, days)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ============================================================
# Paradas (análise) + Cadastros RFID
# ============================================================
@app.get("/paradas", response_class=HTMLResponse)
async def page_paradas(request: Request):
    return templates.TemplateResponse("paradas.html", tctx(request, current_page="paradas"))


@app.get("/api/paradas")
async def api_paradas(
    platform: str,
    device: str | None = None,
    days: int = 7,
    var_parada: str = "parada",
    var_motivo: str = "maq_rfid",
    var_func: str = "fun_rfid",
):
    try:
        return await analyze_paradas(
            platform, device or None, days, var_parada, var_motivo, var_func,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/cadastros", response_class=HTMLResponse)
async def page_cadastros(request: Request, user: User = Depends(require_gestor)):
    return templates.TemplateResponse("cadastros.html", tctx(
        request, current_page="cadastros",
        motivos=list_codigos("motivo"),
        funcionarios=list_codigos("funcionario"),
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
    ))


@app.post("/cadastros")
async def cadastros_create(
    tipo: str = Form(...),
    codigo: str = Form(...),
    nome: str = Form(...),
    categoria: str = Form(""),
    user: User = Depends(require_gestor),
):
    try:
        create_codigo(tipo, codigo, nome, categoria)
    except ValueError as e:
        return RedirectResponse(url=f"/cadastros?error={e}", status_code=303)
    return RedirectResponse(url="/cadastros?msg=Código+cadastrado", status_code=303)


@app.post("/cadastros/{cid}/delete")
async def cadastros_delete(cid: int, user: User = Depends(require_gestor)):
    delete_codigo(cid)
    return RedirectResponse(url="/cadastros?msg=Código+excluído", status_code=303)


@app.post("/relatorio_paradas")
async def relatorio_paradas(
    platform: str = Form(...),
    days: int = Form(7),
    device: str = Form(""),
):
    try:
        data = await analyze_paradas(platform, device or None, days)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {e}")

    platform_label = next(
        (p["label"] for p in PLATFORMS if p["id"] == platform), platform
    )
    content = build_pdf_paradas(data, platform_label)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"paradas_{platform}_{device or 'todas'}_{stamp}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
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
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
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
        raise HTTPException(400, "Mensagem vazia")
    if not provider_id or not model:
        raise HTTPException(400, "Provedor e modelo são obrigatórios")

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
        raise HTTPException(status_code=502, detail=f"Erro ao listar dispositivos: {e}")
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
        raise HTTPException(status_code=502, detail=f"Erro ao listar variáveis: {e}")

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
