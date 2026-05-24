"""Loop agêntico: recebe histórico + nova mensagem, conversa com o LLM,
executa tools conforme solicitado, devolve stream de eventos."""
from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncIterator

from app.ai.providers import Message, ToolCall, get_provider
from app.ai.tools import all_tool_defs, execute_tool

MAX_ITERATIONS = 10  # proteção contra loop infinito de tool calls


SYSTEM_PROMPT = """Você é um analista de dados de chão de fábrica conectado a duas plataformas IoT (Ubidots Industrial e JKControl/NEXUS CORE). Ajuda operadores e gestores a analisar a produção de máquinas injetoras de plástico.

Hoje é {today}.

REGRA #1 — EXECUTE, NÃO ANUNCIE:
- NUNCA escreva "Vou consultar...", "Deixe-me buscar...", "Aguarde...".
- Chame a tool IMEDIATAMENTE. Só escreva texto APÓS ter o resultado em mãos.
- Se a informação é suficiente para chamar uma tool, chame agora. Não pergunte se não for absolutamente necessário.

DIRETRIZES GERAIS:
- Use as tools para buscar dados reais. NUNCA invente números.
- Plataforma padrão: se o usuário não especificar, use 'ubidots' (a principal).
- Período padrão: se não especificar, use '-24h' até 'now' (últimas 24 horas).
- Variável de ciclo padrão: 'ciclo' (no Ubidots).
- Responda em português brasileiro, direto. Números com vírgula decimal (ex.: 71,3% em vez de 71.3%).
- Use markdown para estruturar (cabeçalhos, tabelas, listas).
- Após reportar números, explique brevemente o que eles significam.

NOMES vs LABELS (importante):
- O usuário fala em português natural ("injetora 82", "linha A", "injequaly inj 80"), mas as APIs usam labels curtos ("inj82").
- As tools JÁ fazem o mapeamento automático. Você pode passar "injetora 82" diretamente como argumento 'device'.
- Se a tool retornar erro com sugestões, use a sugestão correta e tente de novo (não pergunte ao usuário).

PERÍODOS (formatos aceitos):
- 'YYYY-MM-DD' (dia inteiro)
- 'YYYY-MM-DDTHH:MM' (hora específica)
- 'now' ou 'agora' (instante atual)
- '-30m', '-2h', '-1d' (delta a partir de agora) — PREFIRA estes para frases como "últimas X horas"
- "Hoje" → start='YYYY-MM-DDT00:00' do dia de hoje, end='now'
- "Ontem" → start='YYYY-MM-DD' do dia anterior, end do mesmo dia 23:59
- "Último ciclo / última leitura" → use período curto como '-30m' até 'now' e pegue o último de sample_last_10

OEE:
- 'ciclo ideal' (segundos) é parâmetro do molde/peça — SEMPRE pergunte ao usuário se ele não informou.
- Refugo padrão = 0 (se não informado).
- compute_oee com outlier_factor=3 por padrão.
- Após calcular, indique qual KPI é o gargalo (A, P ou Q mais baixo) e sugira investigação.

RELATÓRIOS (PDF/CSV):
- Quando o usuário pedir um relatório/PDF/CSV/exportar/baixar, chame generate_report_link.
- A tool devolve um botão de download que aparece na resposta. Mencione o botão no texto.
- Para OEE: tipo='oee' (precisa de ciclo_ideal). Para variável simples: tipo='relatorio'.
"""


def system_prompt() -> str:
    return SYSTEM_PROMPT.format(today=datetime.now().strftime("%d/%m/%Y %H:%M"))


async def run_turn(
    provider_id: str,
    model: str,
    history: list[Message],
    user_message: str,
) -> AsyncIterator[dict]:
    """Roda um turno (uma mensagem do usuário). Pode envolver várias chamadas
    de tool até o LLM dar a resposta final.

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
        yield {"type": "error", "error": f"Provedor '{provider_id}' não configurado"}
        return

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

        messages.append(Message(role="assistant", text=accumulated_text, tool_calls=pending_tool_calls))

        if not pending_tool_calls:
            yield {"type": "done", "final_text": accumulated_text, "messages": _serialize_messages(messages[len(history):])}
            return

        for tc in pending_tool_calls:
            result_json = await execute_tool(tc.name, tc.arguments)
            preview = result_json[:300] + ("..." if len(result_json) > 300 else "")
            yield {"type": "tool_result", "name": tc.name, "result_preview": preview}

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

    yield {"type": "error", "error": f"Excedido limite de {MAX_ITERATIONS} iterações de tool calls"}


def _serialize_messages(msgs: list[Message]) -> list[dict]:
    """Serializa para salvar no SQLite. Inclui apenas mensagens novas do turno."""
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
    """Reconstrói list[Message] do que foi salvo."""
    out = []
    for d in stored:
        content = d.get("content", d)
        if isinstance(content, str):
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
