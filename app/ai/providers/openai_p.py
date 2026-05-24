from __future__ import annotations

import os
import json
from typing import AsyncIterator

from app.ai.providers import (
    Message, Provider, StreamChunk, ToolCall, ToolDef, register,
)


class OpenAIProvider:
    id = "openai"
    label = "OpenAI GPT"
    models = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"]
    default_model = "gpt-4o-mini"

    def __init__(self, api_key: str):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key)

    @staticmethod
    def _msgs_to_openai(messages: list[Message], system: str) -> list[dict]:
        out = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                msg = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    } for tc in m.tool_calls]
                out.append(msg)
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text})
        return out

    @staticmethod
    def _tools_to_openai(tools: list[ToolDef]) -> list[dict]:
        return [{
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        } for t in tools]

    async def chat_stream(self, messages, tools, system, model) -> AsyncIterator[StreamChunk]:
        kwargs = {
            "model": model,
            "messages": self._msgs_to_openai(messages, system),
            "stream": True,
        }
        if tools:
            kwargs["tools"] = self._tools_to_openai(tools)

        try:
            stream = await self.client.chat.completions.create(**kwargs)
            tool_calls_acc: dict[int, dict] = {}
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue
                if delta.content:
                    yield StreamChunk(type="text", text=delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": tc.id or "", "name": "", "args": ""}
                        if tc.id:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_acc[idx]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls_acc[idx]["args"] += tc.function.arguments

            for idx in sorted(tool_calls_acc.keys()):
                t = tool_calls_acc[idx]
                try:
                    args = json.loads(t["args"]) if t["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                yield StreamChunk(type="tool_call", tool_call=ToolCall(
                    id=t["id"], name=t["name"], arguments=args,
                ))
            yield StreamChunk(type="done")
        except Exception as e:
            yield StreamChunk(type="error", error=str(e))


_key = os.environ.get("OPENAI_API_KEY")
if _key:
    register(OpenAIProvider(_key))
