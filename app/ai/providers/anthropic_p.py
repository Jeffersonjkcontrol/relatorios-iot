from __future__ import annotations

import os
from typing import AsyncIterator

from app.ai.providers import (
    Message, Provider, StreamChunk, ToolCall, ToolDef, register,
)


class AnthropicProvider:
    id = "anthropic"
    label = "Anthropic Claude"
    models = ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5"]
    default_model = "claude-haiku-4-5"

    def __init__(self, api_key: str):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=api_key)

    @staticmethod
    def _msgs_to_anthropic(messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                blocks = []
                if m.text:
                    blocks.append({"type": "text", "text": m.text})
                for tc in m.tool_calls:
                    blocks.append({
                        "type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments,
                    })
                out.append({"role": "assistant", "content": blocks})
            elif m.role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.text,
                    }],
                })
        return out

    @staticmethod
    def _tools_to_anthropic(tools: list[ToolDef]) -> list[dict]:
        return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]

    async def chat_stream(self, messages, tools, system, model) -> AsyncIterator[StreamChunk]:
        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": self._msgs_to_anthropic(messages),
            "system": system,
        }
        if tools:
            kwargs["tools"] = self._tools_to_anthropic(tools)

        try:
            async with self.client.messages.stream(**kwargs) as stream:
                # Stream incremental de texto (poupa latencia percebida)
                async for text_chunk in stream.text_stream:
                    if text_chunk:
                        yield StreamChunk(type="text", text=text_chunk)

                # Apos terminar o stream, extrai blocos de tool_use da mensagem final
                final_msg = await stream.get_final_message()
                for block in final_msg.content:
                    if getattr(block, "type", None) == "tool_use":
                        yield StreamChunk(type="tool_call", tool_call=ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=block.input if isinstance(block.input, dict) else {},
                        ))
            yield StreamChunk(type="done")
        except Exception as e:
            yield StreamChunk(type="error", error=str(e))


_key = os.environ.get("ANTHROPIC_API_KEY")
if _key:
    register(AnthropicProvider(_key))
