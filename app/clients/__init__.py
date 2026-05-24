from __future__ import annotations

from typing import Protocol

from app.clients.ubidots import UbidotsClient, ValuePoint, date_to_ms
from app.clients.nexus import NexusCoreClient

__all__ = ["UbidotsClient", "NexusCoreClient", "ValuePoint", "date_to_ms", "make_client", "PlatformClient"]


class PlatformClient(Protocol):
    async def list_device_groups(self) -> list[dict]: ...
    async def list_devices(self, group_label: str | None = None) -> list[dict]: ...
    async def list_variables(
        self,
        device_label: str | None = None,
        group_label: str | None = None,
        variable_label: str | None = None,
    ) -> list[dict]: ...
    async def get_values(
        self,
        device_label: str,
        variable_label: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[ValuePoint]: ...


def make_client(platform_id: str, base_url: str, token: str) -> PlatformClient:
    if platform_id == "jkcontrol":
        return NexusCoreClient(base_url=base_url, token=token)
    if platform_id == "ubidots":
        return UbidotsClient(base_url=base_url, token=token)
    raise ValueError(f"Plataforma desconhecida: {platform_id}")
