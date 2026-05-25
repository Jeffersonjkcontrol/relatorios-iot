from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.clients.ubidots import ValuePoint


class NexusCoreClient:
    """Cliente para o backend NEXUS CORE (jkcontrol.online / projeto Antigravity).

    Caracteristicas (diferente do Ubidots):
    - Auth: `Authorization: Bearer ag_...`
    - Base: `/api`
    - Listar dispositivos retorna array direto, com variaveis embutidas
    - Historico em `/api/devices/:label/variables/:var/data` com timestamp ISO 8601
    - Filtro de grupo: campo `group` (string) no proprio dispositivo - filtramos localmente
    """

    def __init__(self, base_url: str, token: str, timeout: float = 25.0):
        if not token:
            raise ValueError("Token vazio. Configure o .env antes de usar.")
        self.host = base_url.rstrip("/")
        self.api = f"{self.host}/api"
        self.token = token
        self.timeout = timeout
        self._devices_cache: list[dict[str, Any]] | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def get_organization_display(self) -> dict[str, str] | None:
        """NEXUS CORE: o nome da organização não vem na API; retorna None
        para manter o label padrão da plataforma."""
        return None

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    async def _all_devices(self) -> list[dict[str, Any]]:
        if self._devices_cache is None:
            data = await self._get(f"{self.api}/devices")
            self._devices_cache = data if isinstance(data, list) else []
        return self._devices_cache

    async def list_device_groups(self) -> list[dict[str, Any]]:
        devices = await self._all_devices()
        groups = sorted({d.get("group") for d in devices if d.get("group")})
        return [{"label": g, "name": g} for g in groups]

    async def list_devices(self, group_label: str | None = None) -> list[dict[str, Any]]:
        devices = await self._all_devices()
        if group_label:
            devices = [d for d in devices if d.get("group") == group_label]
        return [
            {
                "id": d.get("_id"),
                "label": d.get("label"),
                "name": d.get("name") or d.get("label"),
                "group": d.get("group"),
            }
            for d in devices
            if d.get("label")
        ]

    async def list_variables(
        self,
        device_label: str | None = None,
        group_label: str | None = None,
        variable_label: str | None = None,
    ) -> list[dict[str, Any]]:
        devices = await self._all_devices()
        out: list[dict[str, Any]] = []
        for d in devices:
            if device_label and d.get("label") != device_label:
                continue
            if group_label and d.get("group") != group_label:
                continue
            for v in d.get("variables") or []:
                vlabel = v.get("label")
                if not vlabel:
                    continue
                if variable_label and vlabel != variable_label:
                    continue
                out.append({
                    "label": vlabel,
                    "name": v.get("name") or vlabel,
                    "unit": v.get("unit"),
                    "device": {"label": d.get("label"), "name": d.get("name")},
                })
        return out

    async def get_values(
        self,
        device_label: str,
        variable_label: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        page_size: int = 5000,
    ) -> list[ValuePoint]:
        url = f"{self.api}/devices/{device_label}/variables/{variable_label}/data"
        params: dict[str, Any] = {"limit": page_size}
        if start_ms is not None:
            params["startDate"] = _ms_to_iso(start_ms)
        if end_ms is not None:
            params["endDate"] = _ms_to_iso(end_ms)

        data = await self._get(url, params)
        # NexusCore pode retornar array direto OU {data: [...], total, page}
        items = data if isinstance(data, list) else data.get("data", [])

        points: list[ValuePoint] = []
        for item in items:
            ts = item.get("timestamp")
            ts_ms = _parse_ts(ts)
            if ts_ms is None:
                continue
            points.append(ValuePoint(
                timestamp_ms=ts_ms,
                value=item.get("value"),
                context=item.get("context"),
            ))
        return points


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: Any) -> int | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # epoch (s ou ms)
        return int(ts if ts > 10**11 else ts * 1000)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None
