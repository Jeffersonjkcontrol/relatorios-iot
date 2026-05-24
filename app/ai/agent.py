"""Loop agentico: recebe historico + nova mensagem, conversa com o LLM,
executa tools conforme solicitado, devolve stream de eventos."""
from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncIterator

from app.ai.providers import Message, ToolCall, get_provider
from app.ai.tools import all_tool_defs, execute_tool

MAX_ITERATIONS = 10  # protecao contra loop infinito de tool calls


SYSTEM_PROMPT = """Voce eh um analista de dados de chao-de-fabrica conectado a duas plataformas IoT (Ubidots Industrial e JKControl/NEXUS CORE). Sua funcao eh ajudar o operador/gestor a analisar producao de maquinas injetoras de plastico.

Hoje eh {today}.

DIRETRIZES:
- Use as tools para buscar dados reais. NUNCA invente numeros.
- Se faltar informacao (plataforma, ciclo ideal, periodo), pergunte ao usuario antes.
- Para OEE: o ciclo ideal eh um parametro do molde/peca, deve ser fornecido pelo usuario.
- Quando o usuario pedir um PDF/CSV, use generate_report_link e mencione o botao na resposta.
- Responda em portugues brasileiro, conciso e direto. Numeros sempre com virgula decimal brasileira (ex.: 71,3% em vez de 71.3%).
- Para analises: nao apenas reporte numeros, mas explique o que significam e sugira acoes.
- Use markdown para estruturar respostas (cabecalhos, tabelas, listas).
- Formatos de data aceitos pelas tools: 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM', 'now', '-2h', '-1d', '-30m'.
"""


def system_prompt() -> str:
    return SYSTEM_PROMPT.format(today=datetime.now().strftime("%d/%m/%Y %H:%M"))


async def run_turn(
    provider_id: str,
    model: str,
    history: list[Message],
    user_message: str,
) -> AsyncIterator[dict]:
    """Roda um turno (uma mensagem do usuario). Pode envolver varias chamadas
    de tool ate o LLM dar resposta final.

    Yield eventos:
      {"type": "text", "text": "..."}        - chunk de texto da resposta
      {"type": "tool_call", "name": "...", "arguments": {...}}
      {"type": "tool_result", "name": "...", "result_preview": "..."}
      {"type": "report_link", "tool_name": "...", "data": {...}}
      {"type": "done"}
      {"type": "error", "error": "..."}
    """
    provider = get_provider(provider_id)
    if not provider:
        yield {"type": "error", "error": f"Provider '{provider_id}' nao configurado"}
        return

    # Adiciona a mensagem do usuario ao historico em memoria
    messages = list(history) + [Message(role="user", text=user_message)]
    tools = all_tool_defs()
    system = system_prompt()

    for iteration in range(MAX_ITERATIONS):
        accumulated_text = ""
        pending_tool_calls: list[ToolCall] = []
        error = None

        async for chunk in provider.chat_stream(messages, tools, system, model):
            if chunk.type == "text":
                accumulated_text += chunk.text
                yield {"type": "text", "text": chunk.text}
            elif chunk.type == "tool_call" and chunk.tool_call:
                pending_tool_calls.append(chunk.tool_call)
                yield {"type": "tool_call", "name": chunk.tool_call.name, "arguments": chunk.tool_call.arguments}
            elif chunk.type == "error":
                error = chunk.error
                yield {"type": "error", "error": chunk.error}
                break

        if error:
            return

        # Adiciona a resposta do assistant ao historico (mesmo se vazio mas com tool_calls)
        messages.append(Message(role="assistant", text=accumulated_text, tool_calls=pending_tool_calls))

        # Se nao houve tool calls, terminou
        if not pending_tool_calls:
            yield {"type": "done", "final_text": accumulated_text, "messages": _serialize_messages(messages[len(history):])}
            return

        # Executa cada tool e adiciona resultado ao historico
        for tc in pending_tool_calls:
            result_json = await execute_tool(tc.name, tc.arguments)
            preview = result_json[:300] + ("..." if len(result_json) > 300 else "")
            yield {"type": "tool_result", "name": tc.name, "result_preview": preview}

            # Se a tool gerou um link de relatorio, propaga pro frontend
            if tc.name == "generate_report_link":
                try:
                    data = json.loads(result_json)
                    if "error" not in data:
                        yield {"type": "report_link", "data": data}
                except json.JSONDecodeError:
                    pass

            messages.append(Message(
                role="tool",
                text=result_json,
                tool_call_id=tc.id,
                tool_name=tc.name,
            ))

    # Excedeu iteracoes
    yield {"type": "error", "error": f"Excedeu {MAX_ITERATIONS} iteracoes de tool calls"}


def _serialize_messages(msgs: list[Message]) -> list[dict]:
    """Serializa pra salvar no SQLite. Inclui apenas mensagens novas do turno."""
    out = []
    for m in msgs:
        d = {"role": m.role, "text": m.text}
        if m.tool_calls:
            d["tool_calls"] = [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls]
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.tool_name:
            d["tool_name"] = m.tool_name
        out.append(d)
    return out


def deserialize_messages(stored: list[dict]) -> list[Message]:
    """Reconstroi list[Message] do que foi salvo."""
    out = []
    for d in stored:
        content = d.get("content", d)
        if isinstance(content, str):
            # mensagem antiga (user puro)
            out.append(Message(role=d["role"], text=content))
            continue
        if isinstance(content, dict):
            content = [content]
        if isinstance(content, list):
            for c in content:
                role = c.get("role", d.get("role"))
                msg = Message(role=role, text=c.get("text", ""))
                if c.get("tool_calls"):
                    msg.tool_calls = [ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                                       for tc in c["tool_calls"]]
                if c.get("tool_call_id"):
                    msg.tool_call_id = c["tool_call_id"]
                if c.get("tool_name"):
                    msg.tool_name = c["tool_name"]
                out.append(msg)
    return out
