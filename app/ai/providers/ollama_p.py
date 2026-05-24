from __future__ import annotations

import os
import json
from typing import AsyncIterator

import httpx

from app.ai.providers import (
    Message, Provider, StreamChunk, ToolCall, ToolDef, register,
)


class OllamaProvider:
    """Cliente Ollama local. Usa REST /api/chat. Tool use depende do modelo
    (llama3.1, mistral-nemo, qwen2.5 funcionam bem)."""
    id = "ollama"
    label = "Ollama (local)"
    models = ["llama3.1", "qwen2.5", "mistral-nemo"]
    default_model = "llama3.1"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _msgs_to_ollama(messages: list[Message], system: str) -> list[dict]:
        out = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                msg = {"role": "assistant", "content": m.text or ""}
                if m.tool_calls:
                    msg["tool_calls"] = [{
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    } for tc in m.tool_calls]
                out.append(msg)
            elif m.role == "tool":
                out.append({"role": "tool", "content": m.text})
        return out

    @staticmethod
    def _tools_to_ollama(tools: list[ToolDef]) -> list[dict]:
        return [{
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        } for t in tools]

    async def chat_stream(self, messages, tools, system, model) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": model,
            "messages": self._msgs_to_ollama(messages, system),
            "stream": True,
        }
        if tools:
            payload["tools"] = self._tools_to_ollama(tools)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                    r.raise_for_status()
                    tc_id = 0
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = data.get("message", {})
                        content = msg.get("content")
                        if content:
                            yield StreamChunk(type="text", text=content)
                        for tc in msg.get("tool_calls", []) or []:
                            tc_id += 1
                            fn = tc.get("function", {})
                            args = fn.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {}
                            yield StreamChunk(type="tool_call", tool_call=ToolCall(
                                id=f"oll_{tc_id}", name=fn.get("name", ""), arguments=args,
                            ))
                        if data.get("done"):
                            break
            yield StreamChunk(type="done")
        except Exception as e:
            yield StreamChunk(type="error", error=str(e))


_url = os.environ.get("OLLAMA_BASE_URL")
if _url:
    register(OllamaProvider(_url))
