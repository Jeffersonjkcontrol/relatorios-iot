"""Ordens de Produção: cadastro, fila, execução e auditoria.

Ciclo de vida:

    planejada ──liberar──▶ liberada ──iniciar──▶ em_producao ◀──iniciar── pausada
        │                     │                      │  └────pausar────────▶ │
        │                     │                      └──concluir──▶ concluida ◀┘
        └─────────────────────┴──────────cancelar──▶ cancelada

Cada período em produção numa máquina é uma linha de ordem_execucoes. Há três
jeitos de uma execução nascer (coluna `origem`):

    rfid    o operador bateu o cartão da OP no leitor da máquina — o firmware
            passa a enviar o nº da OP no contexto de cada ciclo. É o modo normal
            em fábricas com o leitor: ninguém aponta nada no computador.
    manual  alguém clicou em "Iniciar" na tela (aceita horário retroativo).
    molde   OP liberada cujo molde começou a ciclar (firmware sem cartão de OP).

OP cujo número aparece no cartão e ainda não existe aqui é criada na hora
(origem 'chao'), sem produto nem quantidade; o ERP/PCP completa depois pelo
mesmo número.
"""
from __future__ import annotations

import sqlite3

from app.mes import cadastros, eventos, lotes, tempo
from app.mes.db import agora_ms, get_conn

ESTADOS = ("planejada", "liberada", "em_producao", "pausada", "concluida", "cancelada")
ABERTAS = ("planejada", "liberada", "em_producao", "pausada")
ROTULOS = {
    "planejada": "Planejada", "liberada": "Liberada", "em_producao": "Em produção",
    "pausada": "Pausada", "concluida": "Concluída", "cancelada": "Cancelada",
}
ORIGENS = {"manual": "Manual", "erp": "ERP", "csv": "CSV", "chao": "Cartão na máquina"}
TOLERANCIA_FUTURO_MS = 60_000

_SQL = """
    SELECT o.*,
           p.codigo AS produto_codigo, p.descricao AS produto_descricao,
           p.ciclo_padrao_s AS produto_ciclo_s, p.cavidades AS produto_cavidades,
           m.codigo AS molde_codigo, m.descricao AS molde_descricao,
           m.cavidades AS molde_cavidades, m.ciclo_padrao_s AS molde_ciclo_s,
           q.codigo AS maquina_codigo, q.nome AS maquina_nome,
           (SELECT COUNT(*) FROM ordem_execucoes e
             WHERE e.ordem_id = o.id AND e.fim_ms IS NULL) AS exec_abertas,
           (SELECT MIN(e.inicio_ms) FROM ordem_execucoes e
             WHERE e.ordem_id = o.id AND e.fim_ms IS NULL) AS exec_inicio_ms,
           (SELECT GROUP_CONCAT(nome, ', ') FROM (
                SELECT q2.nome FROM ordem_execucoes e JOIN maquinas q2 ON q2.id = e.maquina_id
                WHERE e.ordem_id = o.id AND e.fim_ms IS NULL ORDER BY e.inicio_ms)) AS exec_maquinas
    FROM ordens o
    LEFT JOIN produtos p ON p.id = o.produto_id
    LEFT JOIN moldes m ON m.id = o.molde_id
    LEFT JOIN maquinas q ON q.id = o.maquina_id
"""


class OrdemNaoEncontrada(ValueError):
    pass


# ------------------------------------------------------------------- leitura
def _enriquecer(row: sqlite3.Row) -> dict:
    o = dict(row)
    boas = max(0, o["qtd_produzida"] - o["qtd_refugo"])
    o["qtd_boas"] = boas
    if o["quantidade"] > 0:
        o["restante"] = max(0, o["quantidade"] - boas)
        o["pct"] = round(min(100.0, boas / o["quantidade"] * 100), 1)
    else:                                   # quantidade ainda não informada
        o["restante"] = None
        o["pct"] = None
    o["status_rotulo"] = ROTULOS.get(o["status"], o["status"])
    o["origem_rotulo"] = ORIGENS.get(o["origem"], o["origem"])
    o["cavidades_efetivas"] = o["cavidades"] or o["molde_cavidades"] or o["produto_cavidades"] or 1
    o["ciclo_efetivo_s"] = o["ciclo_padrao_s"] or o["molde_ciclo_s"] or o["produto_ciclo_s"]
    o["em_execucao"] = o["exec_abertas"] > 0
    o["cadastro_incompleto"] = o["produto_id"] is None or o["quantidade"] == 0
    return o


def _obter(c: sqlite3.Connection, ordem_id: int) -> dict:
    row = c.execute(_SQL + " WHERE o.id = ?", (ordem_id,)).fetchone()
    if not row:
        raise OrdemNaoEncontrada("Ordem de produção não encontrada.")
    return _enriquecer(row)


def obter(ordem_id: int) -> dict | None:
    with get_conn() as c:
        try:
            return _obter(c, ordem_id)
        except OrdemNaoEncontrada:
            return None


def obter_por_numero(numero: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(_SQL + " WHERE o.numero = ?", (str(numero or "").strip(),)).fetchone()
    return _enriquecer(row) if row else None


def listar(status: str | list[str] | None = None, maquina_id: int | None = None,
           busca: str = "", desde_ms: int | None = None, incompletas: bool = False,
           limite: int = 300) -> list[dict]:
    sql = _SQL + " WHERE 1 = 1"
    args: list = []
    if status:
        lista = [status] if isinstance(status, str) else list(status)
        sql += f" AND o.status IN ({','.join('?' * len(lista))})"
        args += lista
    if maquina_id:
        sql += " AND o.maquina_id = ?"
        args.append(maquina_id)
    if busca:
        like = f"%{busca.strip()}%"
        sql += (" AND (o.numero LIKE ? OR p.codigo LIKE ? OR p.descricao LIKE ? "
                "OR o.cliente LIKE ? OR o.pedido LIKE ?)")
        args += [like] * 5
    if desde_ms:
        sql += " AND o.atualizado_ms >= ?"
        args.append(desde_ms)
    if incompletas:
        sql += " AND (o.produto_id IS NULL OR o.quantidade = 0)"
    # Fila: o que está rodando primeiro, depois a ordem de prioridade
    sql += """ ORDER BY CASE o.status WHEN 'em_producao' THEN 0 WHEN 'pausada' THEN 1
                 WHEN 'liberada' THEN 2 WHEN 'planejada' THEN 3 WHEN 'concluida' THEN 4 ELSE 5 END,
               CASE WHEN o.status IN ('concluida', 'cancelada') THEN -o.atualizado_ms ELSE o.prioridade END,
               o.id
               LIMIT ?"""
    args.append(limite)
    with get_conn() as c:
        return [_enriquecer(r) for r in c.execute(sql, args)]


def contagem_por_status() -> dict[str, int]:
    with get_conn() as c:
        out = {e: 0 for e in ESTADOS}
        for r in c.execute("SELECT status, COUNT(*) AS n FROM ordens GROUP BY status"):
            out[r["status"]] = r["n"]
        out["incompletas"] = c.execute(
            "SELECT COUNT(*) FROM ordens WHERE (produto_id IS NULL OR quantidade = 0) "
            "AND status IN ('liberada', 'em_producao', 'pausada')").fetchone()[0]
    return out


def execucoes(ordem_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT e.*, q.codigo AS maquina_codigo, q.nome AS maquina_nome "
            "FROM ordem_execucoes e JOIN maquinas q ON q.id = e.maquina_id "
            "WHERE e.ordem_id = ? ORDER BY e.inicio_ms", (ordem_id,))]


def historico(ordem_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM ordem_eventos WHERE ordem_id = ? ORDER BY ts_ms DESC, id DESC", (ordem_id,))]


def ordem_na_maquina(maquina_id: int) -> dict | None:
    """OP em execução agora numa máquina."""
    with get_conn() as c:
        row = c.execute(
            _SQL + " WHERE o.id = (SELECT ordem_id FROM ordem_execucoes "
                   "WHERE maquina_id = ? AND fim_ms IS NULL)", (maquina_id,)).fetchone()
    return _enriquecer(row) if row else None


# ------------------------------------------------------------------ auxiliares
def _evento(c: sqlite3.Connection, ordem_id: int, tipo: str, detalhe: str = "",
            usuario: str = "", ts_ms: int | None = None) -> None:
    c.execute(
        "INSERT INTO ordem_eventos (ordem_id, ts_ms, tipo, detalhe, usuario) VALUES (?, ?, ?, ?, ?)",
        (ordem_id, ts_ms or agora_ms(), tipo, detalhe[:500], usuario or ""),
    )


def _iso(ms: int | None) -> str | None:
    return tempo.ms_para_dt(ms).isoformat(timespec="seconds") if ms else None


def payload(c: sqlite3.Connection, ordem_id: int) -> dict:
    """Retrato da OP para webhooks e API."""
    o = _obter(c, ordem_id)
    return {
        "numero": o["numero"], "status": o["status"], "origem": o["origem"],
        "ref_externa": o["ref_externa"],
        "produto_codigo": o["produto_codigo"], "produto_descricao": o["produto_descricao"],
        "molde_codigo": o["molde_codigo"], "maquina": o["maquina_codigo"],
        "quantidade": o["quantidade"], "qtd_produzida": o["qtd_produzida"],
        "qtd_refugo": o["qtd_refugo"], "qtd_boas": o["qtd_boas"], "pct": o["pct"],
        "cliente": o["cliente"], "pedido": o["pedido"],
        "data_inicio_prev": o["data_inicio_prev"], "data_fim_prev": o["data_fim_prev"],
        "liberada_em": _iso(o["liberada_ms"]), "iniciada_em": _iso(o["iniciada_ms"]),
        "concluida_em": _iso(o["concluida_ms"]), "atualizada_em": _iso(o["atualizado_ms"]),
    }


def _proximo_numero(c: sqlite3.Connection) -> str:
    row = c.execute("SELECT valor FROM config WHERE chave = 'seq_op'").fetchone()
    n = (int(row["valor"]) if row else 0) + 1
    while c.execute("SELECT 1 FROM ordens WHERE numero = ?", (f"OP-{n:06d}",)).fetchone():
        n += 1
    c.execute(
        "INSERT INTO config (chave, valor, atualizado_ms) VALUES ('seq_op', ?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_ms = excluded.atualizado_ms",
        (str(n), agora_ms()),
    )
    return f"OP-{n:06d}"


def _fim_da_fila(c: sqlite3.Connection) -> int:
    return c.execute(
        "SELECT COALESCE(MAX(prioridade), 0) + 10 FROM ordens WHERE status IN "
        "('planejada', 'liberada', 'em_producao', 'pausada')").fetchone()[0]


def _data(valor, campo: str) -> str | None:
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return tempo.parse_data(str(valor))
    except ValueError:
        raise ValueError(f"{campo}: data inválida ('{valor}').")


def _resolver_referencias(dados: dict, origem: str) -> dict:
    """Converte códigos (produto, molde, máquina) em ids. Em integrações, cria o
    produto/molde que o ERP mandou e o MES ainda não conhece."""
    externo = origem != "manual"
    out: dict = {}

    # --- molde ---
    molde = None
    if dados.get("molde_id"):
        molde = cadastros.obter_molde(int(dados["molde_id"]))
        if not molde:
            raise ValueError("Molde informado não existe.")
    elif str(dados.get("molde_codigo") or "").strip():
        codigo = str(dados["molde_codigo"]).strip()
        molde = cadastros.obter_molde_por_codigo(codigo)
        if not molde:
            if not externo:
                raise ValueError(f"Molde '{codigo}' não cadastrado.")
            mid = cadastros.salvar_molde({"codigo": codigo, "descricao": codigo}, origem=origem)
            molde = cadastros.obter_molde(mid)
    out["molde_id"] = molde["id"] if molde else None

    # --- produto ---
    produto = None
    if dados.get("produto_id"):
        produto = cadastros.obter_produto(int(dados["produto_id"]))
        if not produto:
            raise ValueError("Produto informado não existe.")
    elif str(dados.get("produto_codigo") or "").strip():
        codigo = str(dados["produto_codigo"]).strip()
        produto = cadastros.obter_produto_por_codigo(codigo)
        if not produto:
            if not externo:
                raise ValueError(f"Produto '{codigo}' não cadastrado.")
            pid = cadastros.garantir_produto(
                codigo, str(dados.get("produto_descricao") or ""), origem,
                str(dados.get("cliente") or ""))
            produto = cadastros.obter_produto(pid)
    elif molde and molde.get("produto_id"):
        produto = cadastros.obter_produto(molde["produto_id"])
    out["produto_id"] = produto["id"] if produto else None

    # --- máquina ---
    maquina = None
    if dados.get("maquina_id"):
        maquina = cadastros.obter_maquina(int(dados["maquina_id"]))
        if not maquina:
            raise ValueError("Máquina informada não existe.")
    elif str(dados.get("maquina") or "").strip():
        codigo = str(dados["maquina"]).strip()
        maquina = cadastros.obter_maquina_por_codigo(codigo)
        if not maquina:
            raise ValueError(f"Máquina '{codigo}' não cadastrada no MES.")
    out["maquina_id"] = maquina["id"] if maquina else None
    return out


def _campos_comuns(dados: dict) -> dict:
    out: dict = {}
    if "quantidade" in dados:
        out["quantidade"] = cadastros._inteiro(dados["quantidade"], "Quantidade", 1)
    if "cavidades" in dados:
        out["cavidades"] = cadastros._inteiro(dados["cavidades"], "Cavidades", 1, 128, obrigatorio=False)
    if "ciclo_padrao_s" in dados:
        out["ciclo_padrao_s"] = cadastros._decimal(dados["ciclo_padrao_s"], "Ciclo padrão (s)", 0.1)
    if "prioridade" in dados and str(dados["prioridade"]).strip() != "":
        out["prioridade"] = cadastros._inteiro(dados["prioridade"], "Prioridade")
    if "data_inicio_prev" in dados:
        out["data_inicio_prev"] = _data(dados["data_inicio_prev"], "Início previsto")
    if "data_fim_prev" in dados:
        out["data_fim_prev"] = _data(dados["data_fim_prev"], "Fim previsto")
    for campo, maximo in (("cliente", 80), ("pedido", 60), ("observacao", 500), ("ref_externa", 80)):
        if campo in dados:
            out[campo] = cadastros._opcional(dados[campo], maximo)
    ini, fim = out.get("data_inicio_prev"), out.get("data_fim_prev")
    if ini and fim and fim < ini:
        raise ValueError("O fim previsto não pode ser anterior ao início previsto.")
    return out


# ------------------------------------------------------------------ escrita
def criar(dados: dict, usuario: str = "", origem: str = "manual") -> int:
    refs = _resolver_referencias(dados, origem)
    if not refs["produto_id"]:
        raise ValueError("Informe o produto da ordem.")
    campos = _campos_comuns(dados)
    if "quantidade" not in campos:
        raise ValueError("Quantidade: valor obrigatório.")
    numero = cadastros._opcional(dados.get("numero"), 40)
    agora = agora_ms()
    with get_conn() as c:
        if not numero:
            numero = _proximo_numero(c)
        elif c.execute("SELECT 1 FROM ordens WHERE numero = ?", (numero,)).fetchone():
            raise ValueError(f"Já existe uma ordem com o número '{numero}'.")
        if "prioridade" not in campos:
            campos["prioridade"] = _fim_da_fila(c)
        colunas = {
            "numero": numero, **refs, **campos, "origem": origem,
            "criado_por": usuario or "", "criado_ms": agora, "atualizado_ms": agora,
        }
        nomes = ", ".join(colunas)
        ordem_id = c.execute(
            f"INSERT INTO ordens ({nomes}) VALUES ({', '.join('?' * len(colunas))})",
            list(colunas.values()),
        ).lastrowid
        _evento(c, ordem_id, "criada", f"origem: {ORIGENS.get(origem, origem)}", usuario, agora)
        eventos.publicar(c, "ordem.criada", payload(c, ordem_id))
    return ordem_id


def garantir_do_chao(c: sqlite3.Connection, numero: str, ts_ms: int, maquina: dict,
                     publicar: bool = True) -> tuple[int, bool]:
    """Id da OP com esse número; se não existir, cria como "descoberta no chão de
    fábrica" (o operador bateu um cartão de OP que o MES ainda não conhecia)."""
    numero = str(numero).strip()[:40]
    row = c.execute("SELECT id FROM ordens WHERE numero = ?", (numero,)).fetchone()
    if row:
        return row["id"], False
    agora = agora_ms()
    ordem_id = c.execute(
        "INSERT INTO ordens (numero, quantidade, status, prioridade, origem, maquina_id, liberada_ms, "
        "criado_por, criado_ms, atualizado_ms) VALUES (?, 0, 'liberada', ?, 'chao', ?, ?, 'cartão RFID', ?, ?)",
        (numero, _fim_da_fila(c), maquina["id"], ts_ms, agora, agora),
    ).lastrowid
    _evento(c, ordem_id, "criada",
            f"vista pela primeira vez no cartão de OP da {maquina['nome']} — falta completar o cadastro",
            "", ts_ms)
    if publicar:
        eventos.publicar(c, "ordem.criada", payload(c, ordem_id))
    return ordem_id, True


def atualizar(ordem_id: int, dados: dict, usuario: str = "", origem: str = "manual") -> None:
    refs_enviadas = any(k in dados for k in (
        "produto_id", "produto_codigo", "molde_id", "molde_codigo", "maquina_id", "maquina"))
    refs = _resolver_referencias(dados, origem) if refs_enviadas else {}
    campos = _campos_comuns(dados)
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] in ("concluida", "cancelada"):
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — reabra para editar.")
        tem_producao = o["qtd_produzida"] > 0 or c.execute(
            "SELECT 1 FROM ordem_execucoes WHERE ordem_id = ? LIMIT 1", (ordem_id,)).fetchone()

        mudancas: dict = dict(campos)
        if "numero" in dados:
            numero = cadastros._texto(dados["numero"], "Número", 1, 40)
            if numero != o["numero"]:
                if tem_producao:
                    raise ValueError("O número não pode mudar depois que a OP entrou em produção "
                                     "(os lotes carregam esse número).")
                if c.execute("SELECT 1 FROM ordens WHERE numero = ? AND id <> ?",
                             (numero, ordem_id)).fetchone():
                    raise ValueError(f"Já existe uma ordem com o número '{numero}'.")
                mudancas["numero"] = numero
        if any(k in dados for k in ("produto_id", "produto_codigo")) and refs.get("produto_id"):
            if refs["produto_id"] != o["produto_id"]:
                # completar o produto de uma OP vinda do cartão é sempre permitido
                if tem_producao and o["produto_id"] is not None:
                    raise ValueError("O produto não pode mudar depois que a OP entrou em produção.")
                mudancas["produto_id"] = refs["produto_id"]
        if any(k in dados for k in ("molde_id", "molde_codigo")):
            mudancas["molde_id"] = refs.get("molde_id")
        if any(k in dados for k in ("maquina_id", "maquina")):
            if refs.get("maquina_id") != o["maquina_id"]:
                if o["status"] == "em_producao":
                    raise ValueError("A OP está em produção — pause antes de trocar a máquina.")
                mudancas["maquina_id"] = refs.get("maquina_id")

        alteradas = [k for k, v in mudancas.items() if o.get(k) != v]
        if not alteradas:
            return
        sets = ", ".join(f"{k} = ?" for k in alteradas)
        c.execute(f"UPDATE ordens SET {sets}, atualizado_ms = ? WHERE id = ?",
                  [mudancas[k] for k in alteradas] + [agora_ms(), ordem_id])
        if "quantidade" in alteradas:
            c.execute("UPDATE ordens SET meta_notificada = 0 WHERE id = ?", (ordem_id,))
        _evento(c, ordem_id, "editada", "campos: " + ", ".join(alteradas), usuario)


def upsert_externo(dados: dict, origem: str, autor: str = "") -> tuple[int, bool]:
    """Cria ou atualiza pela chave `numero` — é o que ERP e CSV usam (idempotente).

    Se a OP já tinha nascido do cartão na máquina, é aqui que ela ganha produto,
    quantidade e demais dados do PCP, sem perder a produção já contada.
    """
    numero = cadastros._texto(dados.get("numero"), "Número da OP", 1, 40)
    existente = obter_por_numero(numero)
    if existente:
        dados = {k: v for k, v in dados.items() if k != "numero"}
        if existente["status"] not in ("concluida", "cancelada"):
            atualizar(existente["id"], dados, autor, origem)
        ordem_id, criada = existente["id"], False
    else:
        ordem_id, criada = criar(dados, autor, origem), True
    if cadastros._bool(dados.get("liberar")) and obter(ordem_id)["status"] == "planejada":
        liberar(ordem_id, autor)
    return ordem_id, criada


def excluir(ordem_id: int) -> None:
    with get_conn() as c:
        o = _obter(c, ordem_id)
        teve_execucao = c.execute(
            "SELECT 1 FROM ordem_execucoes WHERE ordem_id = ? LIMIT 1", (ordem_id,)).fetchone()
        if teve_execucao or o["qtd_produzida"] or o["qtd_refugo"]:
            raise ValueError(f"OP {o['numero']} tem histórico de produção — cancele em vez de excluir.")
        c.execute("DELETE FROM ordens WHERE id = ?", (ordem_id,))


def reordenar(ids_em_ordem: list[int]) -> None:
    """Regrava a prioridade seguindo a ordem recebida (fila do PCP)."""
    with get_conn() as c:
        for posicao, ordem_id in enumerate(ids_em_ordem, start=1):
            c.execute("UPDATE ordens SET prioridade = ?, atualizado_ms = ? WHERE id = ?",
                      (posicao * 10, agora_ms(), int(ordem_id)))


# ------------------------------------------------- execuções (núcleo comum)
def abrir_execucao(c: sqlite3.Connection, ordem_id: int, maquina: dict, inicio_ms: int,
                   usuario: str = "", origem: str = "manual", turnos: list | None = None,
                   reatribuir: bool = True, publicar: bool = True) -> None:
    """Registra que a OP começou a rodar na máquina. Recebe a conexão de quem
    chama porque é usada tanto pela tela quanto pela ingestão (cartão / molde)."""
    o = _obter(c, ordem_id)
    ja_rodou = c.execute("SELECT 1 FROM ordem_execucoes WHERE ordem_id = ? LIMIT 1", (ordem_id,)).fetchone()
    quem = usuario or {"rfid": "cartão RFID", "molde": "auto"}.get(origem, "")
    c.execute(
        "INSERT INTO ordem_execucoes (ordem_id, maquina_id, inicio_ms, iniciado_por, origem) "
        "VALUES (?, ?, ?, ?, ?)", (ordem_id, maquina["id"], inicio_ms, quem, origem))
    c.execute(
        "UPDATE ordens SET status = 'em_producao', maquina_id = ?, concluida_ms = NULL, "
        "iniciada_ms = MIN(COALESCE(iniciada_ms, ?), ?), liberada_ms = COALESCE(liberada_ms, ?), "
        "atualizado_ms = ? WHERE id = ?",
        (maquina["id"], inicio_ms, inicio_ms, inicio_ms, agora_ms(), ordem_id))

    detalhe = f"máquina {maquina['nome']} às {tempo.fmt(inicio_ms)}"
    if origem == "rfid":
        detalhe += " — cartão da OP batido no leitor"
    elif origem == "molde":
        detalhe += " — início automático: o molde da OP começou a ciclar"
    if o["status"] in ("concluida", "cancelada"):
        tipo = "reaberta"
        detalhe += f" (estava {o['status_rotulo'].lower()})"
    elif origem == "molde":
        tipo = "iniciada_auto"
    else:
        tipo = "retomada" if ja_rodou else "iniciada"
    # Pela tela, o histórico registra QUANDO foi apontado (auditoria; o horário
    # retroativo está no detalhe). Pelo cartão/molde, o instante real do evento.
    _evento(c, ordem_id, tipo, detalhe, usuario, None if origem == "manual" else inicio_ms)
    if reatribuir:
        lotes.reatribuir(c, dict(maquina), inicio_ms, None, turnos or [])
    if publicar:
        eventos.publicar(c, "ordem.iniciada", payload(c, ordem_id))


def fechar_execucao_maquina(c: sqlite3.Connection, maquina_id: int, fim_ms: int,
                            usuario: str = "", motivo: str = "troca") -> int | None:
    """Encerra a execução aberta NA MÁQUINA. Devolve o id da OP que saiu."""
    ex = c.execute("SELECT id, ordem_id, inicio_ms FROM ordem_execucoes "
                   "WHERE maquina_id = ? AND fim_ms IS NULL", (maquina_id,)).fetchone()
    if not ex:
        return None
    c.execute("UPDATE ordem_execucoes SET fim_ms = ?, encerrado_por = ?, motivo_fim = ? WHERE id = ?",
              (max(fim_ms, ex["inicio_ms"]), usuario or "", motivo, ex["id"]))
    return ex["ordem_id"]


def definir_status_apos_saida(c: sqlite3.Connection, ordem_id: int, auto_concluir: bool,
                              publicar: bool = True) -> str | None:
    """A OP saiu de uma máquina (outra OP entrou no lugar). Se ela não roda em
    mais nenhuma, vira 'concluída' (meta batida) ou 'pausada'."""
    o = _obter(c, ordem_id)
    if o["exec_abertas"] > 0 or o["status"] != "em_producao":
        return None
    bateu_meta = o["quantidade"] > 0 and o["qtd_boas"] >= o["quantidade"]
    novo = "concluida" if (auto_concluir and bateu_meta) else "pausada"
    fim = c.execute("SELECT MAX(fim_ms) FROM ordem_execucoes WHERE ordem_id = ?", (ordem_id,)).fetchone()[0]
    c.execute("UPDATE ordens SET status = ?, concluida_ms = ?, atualizado_ms = ? WHERE id = ?",
              (novo, fim if novo == "concluida" else None, agora_ms(), ordem_id))
    detalhe = "saiu da máquina: outra OP foi apontada no leitor"
    if novo == "concluida":
        detalhe += " — quantidade planejada atingida"
    _evento(c, ordem_id, novo, detalhe, "", fim)
    if publicar:
        eventos.publicar(c, f"ordem.{novo}", payload(c, ordem_id))
    return novo


def trocar_op_por_cartao(c: sqlite3.Connection, maquina: dict, numero: str, ts_ms: int,
                         recente: bool = True) -> tuple[int, int | None, bool]:
    """A máquina passou a apontar a OP `numero` (cartão RFID).

    Devolve (id da OP que entrou, id da OP que saiu ou None, se a OP foi criada agora).
    """
    ordem_id, criada = garantir_do_chao(c, numero, ts_ms, maquina, publicar=recente)
    saiu = fechar_execucao_maquina(c, maquina["id"], ts_ms, "", "troca")
    abrir_execucao(c, ordem_id, maquina, ts_ms, origem="rfid", reatribuir=False, publicar=recente)
    return ordem_id, (saiu if saiu != ordem_id else None), criada


# ------------------------------------------------------- transições de estado
def liberar(ordem_id: int, usuario: str = "") -> None:
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] != "planejada":
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — só uma OP planejada pode ser liberada.")
        agora = agora_ms()
        c.execute("UPDATE ordens SET status = 'liberada', liberada_ms = ?, atualizado_ms = ? WHERE id = ?",
                  (agora, agora, ordem_id))
        _evento(c, ordem_id, "liberada", "", usuario, agora)
        eventos.publicar(c, "ordem.liberada", payload(c, ordem_id))


def recolher(ordem_id: int, usuario: str = "") -> None:
    """Desfaz a liberação (volta para planejada)."""
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] != "liberada":
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — só uma OP liberada pode ser recolhida.")
        c.execute("UPDATE ordens SET status = 'planejada', liberada_ms = NULL, atualizado_ms = ? WHERE id = ?",
                  (agora_ms(), ordem_id))
        _evento(c, ordem_id, "recolhida", "", usuario)


def _validar_instante(ms: int | None, rotulo: str) -> int:
    agora = agora_ms()
    if ms is None:
        return agora
    if ms > agora + TOLERANCIA_FUTURO_MS:
        raise ValueError(f"{rotulo} não pode estar no futuro.")
    return ms


def _fechar_execucoes_da_ordem(c: sqlite3.Connection, ordem_id: int, fim_ms: int, usuario: str,
                               motivo: str, turnos: list, reatribuir: bool = True,
                               maquina_id: int | None = None) -> list[dict]:
    """Encerra as execuções abertas da OP (todas, ou só a da máquina indicada)."""
    sql = ("SELECT e.id, e.maquina_id, e.inicio_ms FROM ordem_execucoes e "
           "WHERE e.ordem_id = ? AND e.fim_ms IS NULL")
    args: list = [ordem_id]
    if maquina_id:
        sql += " AND e.maquina_id = ?"
        args.append(maquina_id)
    fechadas = []
    for ex in c.execute(sql, args).fetchall():
        if fim_ms < ex["inicio_ms"]:
            raise ValueError(
                f"O fim informado ({tempo.fmt(fim_ms)}) é anterior ao início da execução "
                f"({tempo.fmt(ex['inicio_ms'])}).")
        c.execute("UPDATE ordem_execucoes SET fim_ms = ?, encerrado_por = ?, motivo_fim = ? WHERE id = ?",
                  (fim_ms, usuario or "", motivo, ex["id"]))
        maquina = dict(c.execute("SELECT * FROM maquinas WHERE id = ?", (ex["maquina_id"],)).fetchone())
        if reatribuir:
            # o que a máquina produziu depois do fim deixa de pertencer à OP
            lotes.reatribuir(c, maquina, fim_ms, None, turnos)
        fechadas.append(maquina)
    return fechadas


def _encerrar(c: sqlite3.Connection, ordem_id: int, novo_status: str, fim_ms: int, usuario: str,
              turnos: list, detalhe: str = "", reatribuir: bool = True,
              maquina_id: int | None = None) -> None:
    motivo = {"pausada": "pausa", "concluida": "conclusao", "cancelada": "cancelamento"}[novo_status]
    maquinas = _fechar_execucoes_da_ordem(c, ordem_id, fim_ms, usuario, motivo, turnos, reatribuir, maquina_id)
    ainda_roda = c.execute(
        "SELECT 1 FROM ordem_execucoes WHERE ordem_id = ? AND fim_ms IS NULL", (ordem_id,)).fetchone()
    onde = ", ".join(m["nome"] for m in maquinas)
    onde = f"{onde} às {tempo.fmt(fim_ms)}" if onde else ""
    if ainda_roda:
        # pausou só uma das máquinas: a OP continua em produção nas outras
        _evento(c, ordem_id, "pausada", " — ".join(x for x in (onde, "segue em outra máquina", detalhe) if x), usuario)
        return
    agora = agora_ms()
    extra = ", concluida_ms = ?" if novo_status == "concluida" else ""
    args = [novo_status, agora] + ([fim_ms] if novo_status == "concluida" else []) + [ordem_id]
    c.execute(f"UPDATE ordens SET status = ?, atualizado_ms = ?{extra} WHERE id = ?", args)
    lotes.recalcular_ordens(c, [ordem_id])
    _evento(c, ordem_id, novo_status, " — ".join(x for x in (onde, detalhe) if x), usuario)
    eventos.publicar(c, f"ordem.{novo_status}", payload(c, ordem_id))


def iniciar(ordem_id: int, usuario: str = "", maquina_id: int | None = None,
            inicio_ms: int | None = None, substituir: str | None = None) -> None:
    """Põe a OP em produção numa máquina, pela tela.

    inicio_ms permite apontamento retroativo ("começou às 6h, só registrei agora").
    substituir = 'pausar' | 'concluir' encerra a OP que estiver na máquina (troca de OP).
    """
    turnos = cadastros.turnos_ativos()
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] not in ("planejada", "liberada", "pausada", "em_producao"):
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — não pode ser iniciada.")
        mid = maquina_id or o["maquina_id"]
        if not mid:
            raise ValueError("Informe a máquina em que a OP vai rodar.")
        maquina = c.execute("SELECT * FROM maquinas WHERE id = ?", (mid,)).fetchone()
        if not maquina:
            raise ValueError("Máquina informada não existe.")
        if not maquina["ativo"]:
            raise ValueError(f"A máquina {maquina['nome']} está desativada para coleta — ative-a em Cadastros.")
        inicio = _validar_instante(inicio_ms, "O início")

        ocupante = c.execute(
            "SELECT e.ordem_id, e.inicio_ms, x.numero FROM ordem_execucoes e "
            "JOIN ordens x ON x.id = e.ordem_id WHERE e.maquina_id = ? AND e.fim_ms IS NULL", (mid,)
        ).fetchone()
        if ocupante and ocupante["ordem_id"] == ordem_id:
            raise ValueError(f"OP {o['numero']} já está em execução na máquina {maquina['nome']}.")
        if ocupante:
            if substituir not in ("pausar", "concluir"):
                raise ValueError(
                    f"A máquina {maquina['nome']} está executando a OP {ocupante['numero']}. "
                    "Pause ou conclua essa OP antes — ou use a troca de OP.")
            if inicio <= ocupante["inicio_ms"]:
                raise ValueError(
                    f"O início informado é anterior ao início da OP {ocupante['numero']} que está na máquina.")
            _encerrar(c, ocupante["ordem_id"], "concluida" if substituir == "concluir" else "pausada",
                      inicio, usuario, turnos, reatribuir=False,
                      maquina_id=None if substituir == "concluir" else mid)

        ultimo_fim = c.execute(
            "SELECT MAX(fim_ms) FROM ordem_execucoes WHERE maquina_id = ?", (mid,)).fetchone()[0]
        if ultimo_fim and inicio < ultimo_fim:
            raise ValueError(
                f"Nesse horário a máquina {maquina['nome']} ainda executava outra OP (até {tempo.fmt(ultimo_fim)}). "
                "Informe um início a partir daí.")
        abrir_execucao(c, ordem_id, dict(maquina), inicio, usuario, "manual", turnos)


def pausar(ordem_id: int, usuario: str = "", fim_ms: int | None = None, observacao: str = "",
           maquina_id: int | None = None) -> None:
    turnos = cadastros.turnos_ativos()
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] != "em_producao":
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — só uma OP em produção pode ser pausada.")
        _encerrar(c, ordem_id, "pausada", _validar_instante(fim_ms, "O fim"), usuario, turnos,
                  observacao, maquina_id=maquina_id)


def concluir(ordem_id: int, usuario: str = "", fim_ms: int | None = None, observacao: str = "") -> None:
    turnos = cadastros.turnos_ativos()
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] not in ("em_producao", "pausada"):
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — só uma OP iniciada pode ser concluída.")
        _encerrar(c, ordem_id, "concluida", _validar_instante(fim_ms, "O fim"), usuario, turnos, observacao)


def cancelar(ordem_id: int, usuario: str = "", motivo: str = "") -> None:
    turnos = cadastros.turnos_ativos()
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] not in ABERTAS:
            raise ValueError(f"OP {o['numero']} já está {o['status_rotulo'].lower()}.")
        _encerrar(c, ordem_id, "cancelada", agora_ms(), usuario, turnos, motivo)


def reabrir(ordem_id: int, usuario: str = "") -> None:
    """Desfaz uma conclusão/cancelamento feito por engano."""
    with get_conn() as c:
        o = _obter(c, ordem_id)
        if o["status"] not in ("concluida", "cancelada"):
            raise ValueError(f"OP {o['numero']} está {o['status_rotulo'].lower()} — não há o que reabrir.")
        ja_rodou = c.execute(
            "SELECT 1 FROM ordem_execucoes WHERE ordem_id = ? LIMIT 1", (ordem_id,)).fetchone()
        novo = "pausada" if ja_rodou else ("liberada" if o["liberada_ms"] else "planejada")
        c.execute("UPDATE ordens SET status = ?, concluida_ms = NULL, meta_notificada = 0, "
                  "atualizado_ms = ? WHERE id = ?", (novo, agora_ms(), ordem_id))
        _evento(c, ordem_id, "reaberta", f"voltou para {ROTULOS[novo].lower()}", usuario)


# ------------------------------------------------------------ automatismos
def candidata_auto_inicio(c: sqlite3.Connection, maquina_id: int, molde_id: int,
                          ts_ms: int) -> int | None:
    """OP liberada cujo molde acabou de começar a ciclar nesta máquina.

    Só considera OP liberada ANTES do instante do ciclo — senão um histórico
    antigo (backfill) iniciaria no passado uma OP liberada hoje.
    """
    row = c.execute(
        "SELECT id FROM ordens WHERE status = 'liberada' AND molde_id = ? "
        "AND (maquina_id = ? OR maquina_id IS NULL) AND liberada_ms IS NOT NULL AND liberada_ms <= ? "
        "ORDER BY (maquina_id IS NULL), prioridade, id LIMIT 1",
        (molde_id, maquina_id, ts_ms)).fetchone()
    return row["id"] if row else None


def notificar_meta(c: sqlite3.Connection, ordem_ids) -> int:
    """Publica 'ordem.meta_atingida' uma única vez por OP."""
    n = 0
    for ordem_id in set(ordem_ids):
        row = c.execute(
            "SELECT id FROM ordens WHERE id = ? AND meta_notificada = 0 AND quantidade > 0 "
            "AND status IN ('em_producao', 'pausada') AND (qtd_produzida - qtd_refugo) >= quantidade",
            (ordem_id,)).fetchone()
        if not row:
            continue
        c.execute("UPDATE ordens SET meta_notificada = 1 WHERE id = ?", (ordem_id,))
        _evento(c, ordem_id, "meta_atingida", "peças boas alcançaram a quantidade planejada")
        eventos.publicar(c, "ordem.meta_atingida", payload(c, ordem_id))
        n += 1
    return n


def encerrar_inativas(dias: int) -> int:
    """Conclui OPs pausadas que saíram da máquina pelo cartão e não voltaram.

    Em fábrica com cartão de OP ninguém "conclui" na tela: a OP só deixa de ser
    apontada. Sem esta faxina, a lista de pausadas cresceria para sempre.
    """
    if dias <= 0:
        return 0
    limite = agora_ms() - dias * 86400 * 1000
    n = 0
    with get_conn() as c:
        candidatas = c.execute("""
            SELECT o.id,
                   (SELECT MAX(e.fim_ms) FROM ordem_execucoes e WHERE e.ordem_id = o.id) AS ultimo_fim,
                   (SELECT e.origem FROM ordem_execucoes e WHERE e.ordem_id = o.id
                     ORDER BY e.inicio_ms DESC LIMIT 1) AS ultima_origem
            FROM ordens o WHERE o.status = 'pausada'""").fetchall()
        for r in candidatas:
            if r["ultima_origem"] != "rfid" or r["ultimo_fim"] is None or r["ultimo_fim"] > limite:
                continue
            c.execute("UPDATE ordens SET status = 'concluida', concluida_ms = ?, atualizado_ms = ? WHERE id = ?",
                      (r["ultimo_fim"], agora_ms(), r["id"]))
            _evento(c, r["id"], "concluida", f"encerrada automaticamente: {dias} dias sem voltar à máquina")
            eventos.publicar(c, "ordem.concluida", payload(c, r["id"]))
            n += 1
    return n
