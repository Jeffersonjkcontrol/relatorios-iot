"""Apontamentos feitos por pessoas: refugo, matéria-prima e motivo de parada.

(Produção e paradas em si são automáticas — vêm da ingestão.)
"""
from __future__ import annotations

from app.cadastros import _normalize_codigo, list_codigos
from app.mes import cadastros, eventos, lotes, ordens, tempo
from app.mes.db import agora_ms, get_conn
from app.mes.lotes import FIM_DOS_TEMPOS


def _instante(valor, rotulo: str) -> int:
    """Aceita epoch ms, texto de data/hora ou vazio (= agora). Nunca no futuro."""
    agora = agora_ms()
    if valor is None or str(valor).strip() == "":
        return agora
    ms = int(valor) if isinstance(valor, (int, float)) else tempo.parse_local(str(valor))
    if ms > agora + 60_000:
        raise ValueError(f"{rotulo} não pode estar no futuro.")
    return ms


def _resolver_ordem_maquina(c, dados: dict, ts_ms: int) -> tuple[int | None, dict]:
    """Descobre (ordem_id, máquina) a partir do que foi informado.

    Com a OP: a máquina é a informada, senão a da execução aberta, senão a planejada.
    Só com a máquina: a OP é a que estava em execução naquele instante.
    """
    ordem = None
    if dados.get("ordem_id"):
        ordem = ordens.obter(int(dados["ordem_id"]))
    elif str(dados.get("ordem") or "").strip():
        ordem = ordens.obter_por_numero(str(dados["ordem"]))
    if (dados.get("ordem_id") or dados.get("ordem")) and not ordem:
        raise ValueError("Ordem de produção não encontrada.")

    maquina = None
    if dados.get("maquina_id"):
        maquina = cadastros.obter_maquina(int(dados["maquina_id"]))
    elif str(dados.get("maquina") or "").strip():
        maquina = cadastros.obter_maquina_por_codigo(str(dados["maquina"]))
    if (dados.get("maquina_id") or dados.get("maquina")) and not maquina:
        raise ValueError("Máquina não encontrada.")

    if ordem and not maquina:
        row = c.execute(
            "SELECT maquina_id FROM ordem_execucoes WHERE ordem_id = ? ORDER BY inicio_ms DESC LIMIT 1",
            (ordem["id"],)).fetchone()
        mid = row["maquina_id"] if row else ordem["maquina_id"]
        maquina = cadastros.obter_maquina(mid) if mid else None
    if not maquina:
        raise ValueError("Informe a máquina (ou uma OP que já tenha máquina).")

    if ordem:
        return ordem["id"], maquina
    row = c.execute(
        "SELECT ordem_id FROM ordem_execucoes WHERE maquina_id = ? AND inicio_ms <= ? "
        "AND COALESCE(fim_ms, ?) > ? ORDER BY inicio_ms DESC LIMIT 1",
        (maquina["id"], ts_ms, FIM_DOS_TEMPOS, ts_ms)).fetchone()
    return (row["ordem_id"] if row else None), maquina


# ------------------------------------------------------------------- refugo
def apontar_refugo(dados: dict, usuario: str = "", origem: str = "manual") -> int:
    quantidade = cadastros._inteiro(dados.get("quantidade"), "Quantidade", 1)
    ts_ms = _instante(dados.get("ts"), "O horário")
    observacao = cadastros._opcional(dados.get("observacao"), 300)
    with get_conn() as c:
        motivo_id = None
        if dados.get("motivo_id"):
            motivo_id = int(dados["motivo_id"])
        elif str(dados.get("motivo") or "").strip():
            row = c.execute("SELECT id FROM motivos_refugo WHERE codigo = ? COLLATE NOCASE",
                            (str(dados["motivo"]).strip(),)).fetchone()
            if not row:
                raise ValueError(f"Motivo de refugo '{dados['motivo']}' não cadastrado.")
            motivo_id = row["id"]
        if motivo_id and not c.execute("SELECT 1 FROM motivos_refugo WHERE id = ?", (motivo_id,)).fetchone():
            raise ValueError("Motivo de refugo não encontrado.")

        ordem_id, maquina = _resolver_ordem_maquina(c, dados, ts_ms)
        lote_id = lotes.lote_para_refugo(c, maquina["id"], ordem_id, ts_ms)
        refugo_id = c.execute(
            "INSERT INTO refugos (maquina_id, ordem_id, lote_id, ts_ms, quantidade, motivo_id, observacao, "
            "usuario, origem) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (maquina["id"], ordem_id, lote_id, ts_ms, quantidade, motivo_id, observacao, usuario or "", origem),
        ).lastrowid
        lotes.recalcular_lotes(c, [lote_id])
        lotes.recalcular_ordens(c, [ordem_id])
        numero = None
        if ordem_id:
            numero = c.execute("SELECT numero FROM ordens WHERE id = ?", (ordem_id,)).fetchone()["numero"]
        eventos.publicar(c, "refugo.apontado", {
            "ordem": numero, "maquina": maquina["codigo"], "quantidade": quantidade,
            "motivo_id": motivo_id, "ts": tempo.ms_para_dt(ts_ms).isoformat(timespec="seconds")})
    return refugo_id


def excluir_refugo(refugo_id: int) -> None:
    with get_conn() as c:
        row = c.execute("SELECT ordem_id, lote_id FROM refugos WHERE id = ?", (refugo_id,)).fetchone()
        if not row:
            raise ValueError("Apontamento de refugo não encontrado.")
        c.execute("DELETE FROM refugos WHERE id = ?", (refugo_id,))
        lotes.recalcular_lotes(c, [row["lote_id"]])
        lotes.recalcular_ordens(c, [row["ordem_id"]])


def listar_refugos(ordem_id: int | None = None, maquina_id: int | None = None,
                   ini_ms: int | None = None, fim_ms: int | None = None, limite: int = 300) -> list[dict]:
    sql = """
        SELECT r.*, mr.codigo AS motivo_codigo, mr.descricao AS motivo_descricao,
               q.codigo AS maquina_codigo, q.nome AS maquina_nome,
               o.numero AS ordem_numero, l.codigo AS lote_codigo
        FROM refugos r
        JOIN maquinas q ON q.id = r.maquina_id
        LEFT JOIN motivos_refugo mr ON mr.id = r.motivo_id
        LEFT JOIN ordens o ON o.id = r.ordem_id
        LEFT JOIN lotes l ON l.id = r.lote_id
        WHERE 1 = 1"""
    args: list = []
    for cond, valor in (("r.ordem_id = ?", ordem_id), ("r.maquina_id = ?", maquina_id),
                        ("r.ts_ms >= ?", ini_ms), ("r.ts_ms < ?", fim_ms)):
        if valor is not None:
            sql += f" AND {cond}"
            args.append(valor)
    sql += " ORDER BY r.ts_ms DESC LIMIT ?"
    args.append(limite)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


# ------------------------------------------------------------ matéria-prima
def abastecer_mp(dados: dict, usuario: str = "") -> int:
    """Registra que um lote de matéria-prima entrou na máquina.

    Por padrão encerra o lote anterior da MESMA matéria-prima naquela máquina —
    no chão de fábrica, abastecer um lote novo é o que marca o fim do antigo.
    """
    lote_mp_id = cadastros._inteiro(dados.get("lote_mp_id"), "Lote de matéria-prima")
    lote_mp = cadastros.obter_lote_mp(lote_mp_id)
    if not lote_mp:
        raise ValueError("Lote de matéria-prima não encontrado.")
    inicio = _instante(dados.get("inicio"), "O horário")
    quantidade = cadastros._decimal(dados.get("quantidade"), "Quantidade", 0)
    observacao = cadastros._opcional(dados.get("observacao"), 300)
    encerrar_anterior = cadastros._bool(dados.get("encerrar_anterior", 1))
    with get_conn() as c:
        ordem_id, maquina = _resolver_ordem_maquina(c, dados, inicio)
        if encerrar_anterior:
            c.execute(
                "UPDATE consumo_mp SET fim_ms = ? WHERE maquina_id = ? AND fim_ms IS NULL AND inicio_ms <= ? "
                "AND lote_mp_id IN (SELECT id FROM lotes_mp WHERE mp_id = ?)",
                (inicio, maquina["id"], inicio, lote_mp["mp_id"]))
        return c.execute(
            "INSERT INTO consumo_mp (lote_mp_id, maquina_id, ordem_id, inicio_ms, quantidade, usuario, observacao) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lote_mp_id, maquina["id"], ordem_id, inicio, quantidade, usuario or "", observacao),
        ).lastrowid


def encerrar_consumo(consumo_id: int, fim=None) -> None:
    fim_ms = _instante(fim, "O horário")
    with get_conn() as c:
        row = c.execute("SELECT inicio_ms, fim_ms FROM consumo_mp WHERE id = ?", (consumo_id,)).fetchone()
        if not row:
            raise ValueError("Abastecimento não encontrado.")
        if fim_ms < row["inicio_ms"]:
            raise ValueError("O fim não pode ser anterior ao abastecimento.")
        c.execute("UPDATE consumo_mp SET fim_ms = ? WHERE id = ?", (fim_ms, consumo_id))


def excluir_consumo(consumo_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM consumo_mp WHERE id = ?", (consumo_id,))


_SQL_CONSUMO = """
    SELECT k.*, l.codigo AS lote_codigo, l.fornecedor, mp.codigo AS mp_codigo, mp.descricao AS mp_descricao,
           mp.unidade AS mp_unidade, q.codigo AS maquina_codigo, q.nome AS maquina_nome, o.numero AS ordem_numero
    FROM consumo_mp k
    JOIN lotes_mp l ON l.id = k.lote_mp_id
    JOIN materias_primas mp ON mp.id = l.mp_id
    JOIN maquinas q ON q.id = k.maquina_id
    LEFT JOIN ordens o ON o.id = k.ordem_id
"""


def listar_consumos(ordem_id: int | None = None, maquina_id: int | None = None,
                    so_abertos: bool = False, limite: int = 200) -> list[dict]:
    sql = _SQL_CONSUMO + " WHERE 1 = 1"
    args: list = []
    if ordem_id is not None:
        sql += " AND k.ordem_id = ?"; args.append(ordem_id)
    if maquina_id is not None:
        sql += " AND k.maquina_id = ?"; args.append(maquina_id)
    if so_abertos:
        sql += " AND k.fim_ms IS NULL"
    sql += " ORDER BY k.inicio_ms DESC LIMIT ?"
    args.append(limite)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def consumos_no_intervalo(c, maquina_id: int, ini_ms: int, fim_ms: int) -> list[dict]:
    """Lotes de MP que estavam na máquina em algum momento de [ini, fim]."""
    return [dict(r) for r in c.execute(
        _SQL_CONSUMO + " WHERE k.maquina_id = ? AND k.inicio_ms <= ? AND COALESCE(k.fim_ms, ?) >= ? "
        "ORDER BY k.inicio_ms", (maquina_id, fim_ms, FIM_DOS_TEMPOS, ini_ms))]


# ---------------------------------------------------------- motivo de parada
def motivos_parada() -> list[dict]:
    """Motivos disponíveis para apontamento (cadastro RFID existente)."""
    return [{"codigo": m.codigo, "nome": m.nome, "categoria": m.categoria,
             "planejada": bool(getattr(m, "planejada", False))}
            for m in list_codigos("motivo") if m.ativo]


def apontar_motivo_parada(parada_id: int, motivo_codigo: str, usuario: str = "",
                          observacao: str = "") -> None:
    """Apontamento manual (tela). Tem precedência sobre o cartão RFID."""
    codigo = _normalize_codigo(motivo_codigo)
    if codigo not in {m["codigo"] for m in motivos_parada()}:
        raise ValueError(f"Motivo de parada '{codigo}' não cadastrado.")
    with get_conn() as c:
        cur = c.execute(
            "UPDATE paradas SET motivo_codigo = ?, motivo_origem = 'manual', motivo_ts_ms = ?, "
            "apontado_por = ?, observacao = ? WHERE id = ?",
            (codigo, agora_ms(), usuario or "", cadastros._opcional(observacao, 300), parada_id))
        if cur.rowcount == 0:
            raise ValueError("Parada não encontrada.")


def limpar_motivo_parada(parada_id: int) -> None:
    """Desfaz o apontamento manual; o cartão RFID volta a valer na próxima coleta."""
    with get_conn() as c:
        cur = c.execute(
            "UPDATE paradas SET motivo_codigo = NULL, motivo_origem = NULL, motivo_ts_ms = NULL, "
            "apontado_por = '', observacao = '' WHERE id = ?", (parada_id,))
        if cur.rowcount == 0:
            raise ValueError("Parada não encontrada.")
