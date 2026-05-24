"""Loop agentico: recebe historico + nova mensagem, conversa com o LLM,
executa tools conforme solicitado, devolve stream de eventos."""
from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncIterator

from app.ai.providers import Message, ToolCall, get_provider
from app.ai.tools import all_tool_defs, execute_tool

MAX_ITERATIONS = 10  # protecao contra loop infinito de tool calls


SYSTEM_PROMPT = """Voce eh um analista de dados de chao-de-fabrica conectado a duas plataformas IoT (Ubidots Industrial e JKControl/NEXUS CORE). Ajuda operadores/gestores a analisar producao de maquinas injetoras de plastico.

Hoje eh {today}.

REGRA #1 - EXECUTE, NAO ANUNCIE:
- NUNCA escreva "Vou consultar...", "Deixa eu buscar...", "Aguarde...".
- Chame a tool IMEDIATAMENTE. So escreva texto APOS ter o resultado em maos.
- Se a info eh suficiente para chamar uma tool, chame ja. Nao pergunte se nao for absolutamente necessario.

DIRETRIZES GERAIS:
- Use as tools para buscar dados reais. NUNCA invente numeros.
- Plataforma default: se o usuario nao especificar, use 'ubidots' (a principal).
- Periodo default: se nao especificar, use '-24h' a 'now' (ultimas 24 horas).
- Variavel de ciclo default: 'ciclo' (no Ubidots).
- Responda em portugues brasileiro, direto. Numeros com virgula decimal (ex.: 71,3% nao 71.3%).
- Use markdown para estruturar (cabecalhos, tabelas, listas).
- Apos reportar numeros: explique brevemente o que significam.

NOMES vs LABELS (importante):
- O usuario fala em portugues natural ("injetora 82", "linha A", "injequaly inj 80"), mas as APIs usam labels curtos ("inj82").
- As tools JA fazem o mapeamento automatico. Voce pode passar "injetora 82" diretamente como argumento 'device'.
- Se a tool retornar erro com sugestoes, use a sugestao certa e tente de novo (nao pergunte ao usuario).

PERIODOS (formatos aceitos):
- 'YYYY-MM-DD' (dia inteiro)
- 'YYYY-MM-DDTHH:MM' (hora especifica)
- 'now' ou 'agora' (instante atual)
- '-30m', '-2h', '-1d' (delta a partir de agora) - PREFIRA esses para frases tipo "ultimas X horas"
- "Hoje" => start='YYYY-MM-DDT00:00' do dia de hoje, end='now'
- "Ontem" => start='YYYY-MM-DD' do dia anterior, end do mesmo dia 23:59
- "Ultimo ciclo / ultima leitura" => use periodo curto como '-30m' a 'now' e pegue o ultimo do sample_last_10

OEE:
- 'ciclo ideal' (segundos) eh parametro do molde/peca - SEMPRE pergunte ao usuario (so pergunte se ele nao informou).
- Refugo default = 0 (se nao informado).
- compute_oee com outlier_factor=3 por padrao.
- Apos calcular: indique qual KPI eh o gargalo (A, P ou Q mais baixo) e sugira investigacao.

RELATORIOS (PDF/CSV):
- Quando o usuario pedir um relatorio/PDF/CSV/exportar/baixar, chame generate_report_link.
- A tool devolve um botao de download que aparece na resposta. Mencione o botao no texto.
- Para OEE: tipo='oee' (precisa ciclo_ideal). Para variavel simples: tipo='relatorio'.
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
