from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


@dataclass
class ValuePoint:
    timestamp_ms: int
    value: float
    context: dict[str, Any] | None = None

    @property
    def datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=timezone.utc)


class UbidotsClient:
    """Cliente compativel com Ubidots (jkcontrol.online ou industrial.api.ubidots.com).

    base_url e o host puro: https://industrial.api.ubidots.com
    O cliente monta /api/v1.6/ ou /api/v2.0/ conforme a operacao.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 25.0):
        if not token:
            raise ValueError("Token vazio. Configure o .env antes de usar.")
        self.host = base_url.rstrip("/")
        self.v1 = f"{self.host}/api/v1.6"
        self.v2 = f"{self.host}/api/v2.0"
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Auth-Token": self.token, "Content-Type": "application/json"}

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, headers=self._headers(), params=params)
            r.raise_for_status()
            return r.json()

    async def _get_all(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Pagina via 'next' ate esgotar."""
        results: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params: dict[str, Any] | None = params
        while next_url:
            data = await self._get(next_url, next_params)
            results.extend(data.get("results", []))
            next_url = data.get("next")
            next_params = None  # next ja vem com query string completa
        return results

    # ----- v2.0 -----
    async def get_organization_display(self) -> dict[str, str] | None:
        """Retorna {label, name} da organização principal (1ª da lista).
        Usado pra mostrar 'Injequaly' em vez de 'Ubidots Industrial' na UI."""
        try:
            data = await self._get(f"{self.v2}/organizations/", {"page_size": 1})
            results = data.get("results") or []
            if results:
                o = results[0]
                return {"label": o.get("label", ""), "name": o.get("name") or o.get("label", "")}
        except httpx.HTTPStatusError:
            pass
        # Fallback: pega do primeiro device
        try:
            data = await self._get(
                f"{self.v2}/devices/",
                {"fields": "organization", "page_size": 1},
            )
            results = data.get("results") or []
            if results and isinstance(results[0].get("organization"), dict):
                o = results[0]["organization"]
                return {"label": o.get("label", ""), "name": o.get("name") or o.get("label", "")}
        except httpx.HTTPStatusError:
            pass
        return None

    async def list_device_groups(self) -> list[dict[str, Any]]:
        url = f"{self.v2}/device_groups/"
        params = {"fields": "id,label,name", "page_size": 100}
        try:
            return await self._get_all(url, params)
        except httpx.HTTPStatusError as e:
            # Plataforma pode nao ter device groups habilitados
            if e.response.status_code in (403, 404):
                return []
            raise

    async def list_devices(self, group_label: str | None = None) -> list[dict[str, Any]]:
        url = f"{self.v2}/devices/"
        params: dict[str, Any] = {"fields": "id,label,name", "page_size": 100}
        if group_label:
            params["deviceGroup__label"] = group_label
        try:
            return await self._get_all(url, params)
        except httpx.HTTPStatusError as e:
            # Plataforma sem v2.0 ou sem permissao: deixa o usuario digitar manualmente
            if e.response.status_code in (403, 404):
                return []
            raise

    async def list_variables(
        self,
        device_label: str | None = None,
        group_label: str | None = None,
        variable_label: str | None = None,
    ) -> list[dict[str, Any]]:
        url = f"{self.v2}/variables/"
        params: dict[str, Any] = {
            "fields": "id,label,name,unit,device,lastValue",
            "page_size": 100,
        }
        if device_label:
            params["device__label"] = device_label
        if group_label:
            params["device__deviceGroup__label"] = group_label
        if variable_label:
            params["label"] = variable_label
        try:
            return await self._get_all(url, params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                return []
            raise

    # ----- v1.6 (historico de valores) -----
    async def get_values(
        self,
        device_label: str,
        variable_label: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        page_size: int = 1000,
    ) -> list[ValuePoint]:
        url = f"{self.v1}/devices/{device_label}/{variable_label}/values"
        params: dict[str, Any] = {"page_size": page_size}
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms

        data = await self._get(url, params)
        results = data.get("results", [])
        return [
            ValuePoint(
                timestamp_ms=item.get("timestamp"),
                value=item.get("value"),
                context=item.get("context"),
            )
            for item in results
            if item.get("timestamp") is not None
        ]


def date_to_ms(dt_iso: str) -> int:
    """Converte 'YYYY-MM-DD' ou 'YYYY-MM-DDTHH:MM' em epoch ms (hora local)."""
    if not dt_iso:
        raise ValueError("Data vazia")
    if "T" not in dt_iso:
        dt_iso = f"{dt_iso}T00:00"
    dt = datetime.fromisoformat(dt_iso)
    return int(dt.timestamp() * 1000)
