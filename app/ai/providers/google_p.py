from __future__ import annotations

import os
import json
from typing import AsyncIterator

from app.ai.providers import (
    Message, Provider, StreamChunk, ToolCall, ToolDef, register,
)


class GoogleProvider:
    id = "google"
    label = "Google Gemini"
    models = [
        "gemini-2.5-flash",      # padrao - rapido e barato, free tier alto
        "gemini-2.5-pro",        # mais capaz, ainda no free tier (menos requisicoes/dia)
        "gemini-2.0-flash",      # geracao anterior, ainda otimo
        "gemini-2.0-flash-lite", # mais economico ainda
        "gemini-1.5-pro",        # legado, contexto enorme (2M tokens)
        "gemini-1.5-flash",      # legado, barato
    ]
    default_model = "gemini-2.5-flash"

    def __init__(self, api_key: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)

    @staticmethod
    def _msgs_to_gemini(messages: list[Message]) -> list[dict]:
        contents = []
        for m in messages:
            if m.role == "user":
                contents.append({"role": "user", "parts": [{"text": m.text}]})
            elif m.role == "assistant":
                parts = []
                if m.text:
                    parts.append({"text": m.text})
                for tc in m.tool_calls:
                    parts.append({"function_call": {"name": tc.name, "args": tc.arguments}})
                contents.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{"function_response": {
                        "name": m.tool_name or "tool",
                        "response": {"result": m.text},
                    }}],
                })
        return contents

    @staticmethod
    def _tools_to_gemini(tools: list[ToolDef]):
        return [{
            "function_declarations": [
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools
            ]
        }]

    async def chat_stream(self, messages, tools, system, model) -> AsyncIterator[StreamChunk]:
        from google.genai import types as gtypes
        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            tools=self._tools_to_gemini(tools) if tools else None,
        )
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=model,
                contents=self._msgs_to_gemini(messages),
                config=config,
            )
            tc_id = 0
            async for chunk in stream:
                if not chunk.candidates:
                    continue
                cand = chunk.candidates[0]
                if not cand.content or not cand.content.parts:
                    continue
                for part in cand.content.parts:
                    if hasattr(part, "text") and part.text:
                        yield StreamChunk(type="text", text=part.text)
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        tc_id += 1
                        args = dict(fc.args) if fc.args else {}
                        yield StreamChunk(type="tool_call", tool_call=ToolCall(
                            id=f"gem_{tc_id}", name=fc.name, arguments=args,
                        ))
            yield StreamChunk(type="done")
        except Exception as e:
            yield StreamChunk(type="error", error=str(e))


_key = os.environ.get("GOOGLE_API_KEY")
if _key:
    register(GoogleProvider(_key))
