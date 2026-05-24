"""Registry de providers LLM. Cada provider implementa Provider e se auto-registra
se a dependencia estiver instalada E houver API key configurada."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    """Mensagem unificada entre providers."""
    role: str  # 'user' | 'assistant' | 'tool'
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # so assistant
    tool_call_id: str | None = None  # so tool
    tool_name: str | None = None    # so tool


@dataclass
class StreamChunk:
    type: str  # 'text' | 'tool_call' | 'done' | 'error'
    text: str = ""
    tool_call: ToolCall | None = None
    error: str = ""


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema


class Provider(Protocol):
    id: str
    label: str
    models: list[str]
    default_model: str

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDef],
        system: str,
        model: str,
    ) -> AsyncIterator[StreamChunk]: ...


# Registro dinamico
_PROVIDERS: dict[str, Provider] = {}


def register(provider: Provider):
    _PROVIDERS[provider.id] = provider


def get_provider(pid: str) -> Provider | None:
    return _PROVIDERS.get(pid)


def list_providers() -> list[dict]:
    return [
        {"id": p.id, "label": p.label, "models": p.models, "default_model": p.default_model}
        for p in _PROVIDERS.values()
    ]


# Tenta importar cada provider - cada modulo se auto-registra se as condicoes baterem
def _try_import(modname: str):
    try:
        __import__(f"app.ai.providers.{modname}")
    except Exception as e:
        # Falha silenciosa: provider nao configurado ou dependencia faltando
        import logging
        logging.getLogger("app.ai").debug(f"Provider {modname} nao disponivel: {e}")


for _m in ["anthropic_p", "openai_p", "google_p", "ollama_p"]:
    _try_import(_m)
