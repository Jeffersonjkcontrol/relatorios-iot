"""Operações que interagem com o host: status do container, restart, backup, logs.

Requer que `/var/run/docker.sock` esteja montado no container.
Fallback gracioso quando o socket não estiver disponível (modo dev local).
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from pathlib import Path

import httpx

DOCKER_SOCK = "/var/run/docker.sock"
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "relatorios-iot")
BACKUP_SCRIPT = "/usr/local/bin/relatorios-iot-backup"
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/opt/relatorios-iot/backups"))


def _has_docker_socket() -> bool:
    return os.path.exists(DOCKER_SOCK)


def _docker_client() -> httpx.AsyncClient:
    """Cliente httpx falando com Docker via unix socket (suporte nativo do httpx/httpcore)."""
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=DOCKER_SOCK),
        base_url="http://docker",
        timeout=30,
    )


async def _docker_get(path: str) -> dict | list | None:
    if not _has_docker_socket():
        return None
    try:
        async with _docker_client() as c:
            r = await c.get(path)
            if r.status_code >= 400:
                return None
            return r.json()
    except Exception:
        return None


async def _docker_post(path: str) -> bool:
    if not _has_docker_socket():
        return False
    try:
        async with _docker_client() as c:
            r = await c.post(path)
            return r.status_code < 400
    except Exception:
        return False


async def _find_container_id() -> str | None:
    """Acha o ID do container pelo nome (com filtro)."""
    data = await _docker_get(f"/containers/json?all=true&filters=%7B%22name%22%3A%5B%22{CONTAINER_NAME}%22%5D%7D")
    if not data or not isinstance(data, list):
        return None
    for c in data:
        names = c.get("Names") or []
        for n in names:
            if n.lstrip("/").startswith(CONTAINER_NAME):
                return c.get("Id")
    return None


async def container_status() -> dict:
    """Status do próprio container: rodando, uso de CPU/RAM, uptime, image."""
    if not _has_docker_socket():
        return {
            "available": False,
            "reason": "Docker socket não montado (modo dev). Cards de status indisponíveis.",
        }
    cid = await _find_container_id()
    if not cid:
        return {"available": False, "reason": f"Container '{CONTAINER_NAME}' não encontrado."}
    info = await _docker_get(f"/containers/{cid}/json")
    if not info or not isinstance(info, dict):
        return {"available": False, "reason": "Não consegui inspecionar o container."}
    stats = await _docker_get(f"/containers/{cid}/stats?stream=false")
    state = info.get("State") or {}
    image = (info.get("Config") or {}).get("Image", "")
    started_at = state.get("StartedAt", "")
    memory_usage = ((stats or {}).get("memory_stats") or {}).get("usage", 0)
    memory_limit = ((stats or {}).get("memory_stats") or {}).get("limit", 0) or 1
    # CPU%
    cpu_pct = 0.0
    try:
        cpu = (stats or {}).get("cpu_stats", {})
        precpu = (stats or {}).get("precpu_stats", {})
        cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - precpu.get("cpu_usage", {}).get("total_usage", 0)
        sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
        ncpus = cpu.get("online_cpus", 1) or 1
        if sys_delta > 0 and cpu_delta > 0:
            cpu_pct = (cpu_delta / sys_delta) * ncpus * 100
    except Exception:
        pass
    return {
        "available": True,
        "running": state.get("Running", False),
        "status": state.get("Status", ""),
        "image": image,
        "started_at": started_at,
        "restart_count": state.get("RestartCount", 0),
        "memory_mb": round(memory_usage / 1024 / 1024, 1),
        "memory_pct": round(memory_usage / memory_limit * 100, 1),
        "cpu_pct": round(cpu_pct, 1),
        "health": (state.get("Health") or {}).get("Status", "n/a"),
    }


async def restart_app() -> dict:
    """Manda restart no próprio container via Docker API."""
    if not _has_docker_socket():
        return {"ok": False, "error": "Docker socket não montado"}
    cid = await _find_container_id()
    if not cid:
        return {"ok": False, "error": "Container não encontrado"}
    ok = await _docker_post(f"/containers/{cid}/restart?t=5")
    return {"ok": ok, "message": "Reiniciando…" if ok else "Falha ao reiniciar"}


async def get_recent_logs(lines: int = 200) -> str:
    """Últimas N linhas de log do container."""
    if not _has_docker_socket():
        return "(Docker socket não disponível neste ambiente.)"
    cid = await _find_container_id()
    if not cid:
        return "(Container não encontrado.)"
    try:
        async with _docker_client() as c:
            r = await c.get(f"/containers/{cid}/logs?stdout=1&stderr=1&tail={lines}")
            if r.status_code >= 400:
                return f"(Erro {r.status_code} ao ler logs)"
            raw = r.content
    except Exception as e:
        return f"(Erro ao ler logs: {e})"
    # Docker prefixa cada chunk com 8 bytes (stream type + tamanho). Removemos.
    out = []
    i = 0
    while i + 8 <= len(raw):
        size = int.from_bytes(raw[i+4:i+8], "big")
        chunk = raw[i+8:i+8+size]
        out.append(chunk.decode("utf-8", errors="replace"))
        i += 8 + size
    if not out:
        return raw.decode("utf-8", errors="replace")
    return "".join(out)


async def run_backup() -> dict:
    """Dispara o script de backup. Funciona no host via subprocess (se acessível)
    OU manda comando via docker exec (quando socket disponível)."""
    # Se o script existir no path do container (caso o usuário tenha mapeado), roda direto
    if os.path.exists(BACKUP_SCRIPT) and os.access(BACKUP_SCRIPT, os.X_OK):
        try:
            r = subprocess.run([BACKUP_SCRIPT], capture_output=True, text=True, timeout=120)
            return {"ok": r.returncode == 0, "stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    # Caso contrário: tenta criar backup tar diretamente do volume montado
    return {
        "ok": False,
        "error": "Script de backup não acessível. Rode 'sudo /usr/local/bin/relatorios-iot-backup' no host.",
    }


def list_backups() -> list[dict]:
    """Lista arquivos de backup do volume (se montado em /app/backups)."""
    candidates = [Path("/app/backups"), Path("/opt/relatorios-iot/backups"), BACKUP_DIR]
    for d in candidates:
        if d.exists() and d.is_dir():
            files = sorted(d.glob("relatorios-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
            return [
                {"name": f.name, "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                 "mtime": f.stat().st_mtime}
                for f in files[:20]
            ]
    return []
