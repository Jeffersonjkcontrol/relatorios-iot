"""Cadastros do MES: máquinas, produtos, moldes, matérias-primas, turnos e
motivos de refugo.

Erros de validação saem como ValueError com mensagem pronta para o usuário.
(Motivos de PARADA continuam no cadastro RFID existente, em app/cadastros.py.)
"""
from __future__ import annotations

import re
import sqlite3

from app.mes import tempo
from app.mes.db import agora_ms, get_conn
from app.mes.molde_ctx import MoldeCtx


# ------------------------------------------------------------------ validação
def _texto(valor, campo: str, minimo: int = 1, maximo: int = 120) -> str:
    s = str(valor or "").strip()
    if len(s) < minimo:
        raise ValueError(f"{campo}: informe ao menos {minimo} caractere(s).")
    if len(s) > maximo:
        raise ValueError(f"{campo}: máximo de {maximo} caracteres.")
    return s


def _opcional(valor, maximo: int = 200) -> str:
    return str(valor or "").strip()[:maximo]


def _inteiro(valor, campo: str, minimo: int | None = None, maximo: int | None = None,
             obrigatorio: bool = True) -> int | None:
    if valor is None or str(valor).strip() == "":
        if obrigatorio:
            raise ValueError(f"{campo}: valor obrigatório.")
        return None
    try:
        n = int(float(str(valor).replace(",", ".")))
    except ValueError:
        raise ValueError(f"{campo}: número inválido.")
    if minimo is not None and n < minimo:
        raise ValueError(f"{campo}: o mínimo é {minimo}.")
    if maximo is not None and n > maximo:
        raise ValueError(f"{campo}: o máximo é {maximo}.")
    return n


def _decimal(valor, campo: str, minimo: float | None = None, obrigatorio: bool = False) -> float | None:
    if valor is None or str(valor).strip() == "":
        if obrigatorio:
            raise ValueError(f"{campo}: valor obrigatório.")
        return None
    try:
        n = float(str(valor).replace(",", "."))
    except ValueError:
        raise ValueError(f"{campo}: número inválido.")
    if minimo is not None and n < minimo:
        raise ValueError(f"{campo}: o mínimo é {minimo}.")
    return n


def _bool(valor) -> int:
    if isinstance(valor, str):
        return 1 if valor.strip().lower() in ("1", "true", "sim", "on") else 0
    return 1 if valor else 0


def _linhas(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- máquinas
def listar_maquinas(so_ativas: bool = False) -> list[dict]:
    sql = "SELECT * FROM maquinas"
    if so_ativas:
        sql += " WHERE ativo = 1"
    with get_conn() as c:
        return _linhas(c.execute(sql + " ORDER BY nome COLLATE NOCASE"))


def obter_maquina(maquina_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM maquinas WHERE id = ?", (maquina_id,)).fetchone()
    return dict(row) if row else None


def obter_maquina_por_codigo(codigo: str, plataforma: str | None = None) -> dict | None:
    """Busca pelo código do dispositivo (sem diferenciar maiúsculas)."""
    sql = "SELECT * FROM maquinas WHERE codigo = ? COLLATE NOCASE"
    args: list = [str(codigo or "").strip()]
    if plataforma:
        sql += " AND plataforma = ?"
        args.append(plataforma)
    with get_conn() as c:
        row = c.execute(sql + " ORDER BY ativo DESC LIMIT 1", args).fetchone()
    return dict(row) if row else None


def sincronizar_maquinas(plataforma: str, dispositivos: list[dict]) -> dict:
    """Cadastra os dispositivos da plataforma que ainda não são máquinas.

    Não altera as existentes — o gestor pode ter renomeado ou desativado.
    """
    novas = 0
    agora = agora_ms()
    with get_conn() as c:
        for d in dispositivos:
            codigo = str(d.get("label") or "").strip()
            if not codigo:
                continue
            nome = str(d.get("name") or codigo).strip()[:120]
            cur = c.execute(
                "INSERT OR IGNORE INTO maquinas (plataforma, codigo, nome, criado_ms, atualizado_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (plataforma, codigo, nome, agora, agora),
            )
            novas += cur.rowcount
        total = c.execute(
            "SELECT COUNT(*) FROM maquinas WHERE plataforma = ?", (plataforma,)
        ).fetchone()[0]
    return {"novas": novas, "total": total}


def desativar_recem_criadas(plataforma: str, codigos: list[str]) -> int:
    """Desativa máquinas recém-cadastradas que não são de produção (dispositivo sem
    variável de ciclo: esteira, gateway...). Não toca em máquina que o gestor já
    editou nem em máquina que já tem ciclos coletados."""
    if not codigos:
        return 0
    marcas = ",".join("?" * len(codigos))
    with get_conn() as c:
        return c.execute(
            f"UPDATE maquinas SET ativo = 0 WHERE plataforma = ? AND codigo IN ({marcas}) "
            "AND criado_ms = atualizado_ms "
            "AND NOT EXISTS (SELECT 1 FROM ciclos x WHERE x.maquina_id = maquinas.id)",
            [plataforma, *codigos]).rowcount


def atualizar_maquina(maquina_id: int, dados: dict) -> None:
    sets, vals = [], []
    if "nome" in dados:
        sets.append("nome = ?"); vals.append(_texto(dados["nome"], "Nome", 2))
    if "setor" in dados:
        sets.append("setor = ?"); vals.append(_opcional(dados["setor"], 60))
    if "observacao" in dados:
        sets.append("observacao = ?"); vals.append(_opcional(dados["observacao"], 300))
    if "ativo" in dados:
        sets.append("ativo = ?"); vals.append(_bool(dados["ativo"]))
    if "fonte_contagem" in dados:
        fonte = str(dados["fonte_contagem"]).strip()
        if fonte not in ("contador", "ciclo"):
            raise ValueError("Fonte de contagem: use 'contador' ou 'ciclo'.")
        sets.append("fonte_contagem = ?"); vals.append(fonte)
    if "ciclos_por_leitura" in dados:
        n = _decimal(dados["ciclos_por_leitura"], "Ciclos por leitura", 0.1, obrigatorio=True)
        sets.append("ciclos_por_leitura = ?"); vals.append(n)
    if "cavidades_padrao" in dados:
        sets.append("cavidades_padrao = ?")
        vals.append(_inteiro(dados["cavidades_padrao"], "Cavidades", 1, 128))
    if not sets:
        return
    sets.append("atualizado_ms = ?"); vals.append(agora_ms())
    vals.append(maquina_id)
    with get_conn() as c:
        cur = c.execute(f"UPDATE maquinas SET {', '.join(sets)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            raise ValueError("Máquina não encontrada.")


# ------------------------------------------------------------------- produtos
def listar_produtos(busca: str = "", so_ativos: bool = False, limite: int = 500) -> list[dict]:
    sql = "SELECT * FROM produtos WHERE 1 = 1"
    args: list = []
    if so_ativos:
        sql += " AND ativo = 1"
    if busca:
        sql += " AND (codigo LIKE ? OR descricao LIKE ? OR cliente LIKE ?)"
        like = f"%{busca.strip()}%"
        args += [like, like, like]
    sql += " ORDER BY codigo COLLATE NOCASE LIMIT ?"
    args.append(limite)
    with get_conn() as c:
        return _linhas(c.execute(sql, args))


def obter_produto(produto_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,)).fetchone()
    return dict(row) if row else None


def obter_produto_por_codigo(codigo: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM produtos WHERE codigo = ? COLLATE NOCASE", (str(codigo or "").strip(),)
        ).fetchone()
    return dict(row) if row else None


def _salvar_produto_base(dados: dict, produto_id: int | None = None, origem: str = "manual") -> int:
    codigo = _texto(dados.get("codigo"), "Código", 1, 40)
    descricao = _texto(dados.get("descricao"), "Descrição", 2)
    unidade = _opcional(dados.get("unidade") or "PC", 6).upper() or "PC"
    peso = _decimal(dados.get("peso_g"), "Peso (g)", 0)
    cliente = _opcional(dados.get("cliente"), 80)
    ativo = _bool(dados.get("ativo", 1))
    agora = agora_ms()
    try:
        with get_conn() as c:
            if produto_id is None:
                cur = c.execute(
                    "INSERT INTO produtos (codigo, descricao, unidade, peso_g, cliente, ativo, origem, "
                    "criado_ms, atualizado_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (codigo, descricao, unidade, peso, cliente, ativo, origem, agora, agora),
                )
                return cur.lastrowid
            cur = c.execute(
                "UPDATE produtos SET codigo = ?, descricao = ?, unidade = ?, peso_g = ?, cliente = ?, "
                "ativo = ?, atualizado_ms = ? WHERE id = ?",
                (codigo, descricao, unidade, peso, cliente, ativo, agora, produto_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Produto não encontrado.")
            return produto_id
    except sqlite3.IntegrityError:
        raise ValueError(f"Já existe um produto com o código '{codigo}'.")


def salvar_produto(dados: dict, produto_id: int | None = None, origem: str = "manual") -> int:
    """Cria/atualiza o produto. Ciclo padrão e cavidades são opcionais e servem
    de padrão para as OPs desse produto (base da Performance no OEE)."""
    ciclo = _decimal(dados.get("ciclo_padrao_s"), "Ciclo padrão (s)", 0.1)
    cav = _inteiro(dados.get("cavidades"), "Cavidades", 1, 128, obrigatorio=False)
    pid = _salvar_produto_base(dados, produto_id, origem)
    if "ciclo_padrao_s" in dados or "cavidades" in dados:
        with get_conn() as c:
            c.execute("UPDATE produtos SET ciclo_padrao_s = ?, cavidades = ? WHERE id = ?", (ciclo, cav, pid))
    return pid


def garantir_produto(codigo: str, descricao: str = "", origem: str = "erp", cliente: str = "") -> int:
    """Devolve o id do produto com esse código, criando-o se não existir.

    Usado pela integração: o ERP manda a OP com o item e o MES não deve recusar
    só porque o cadastro ainda não foi feito aqui.
    """
    existente = obter_produto_por_codigo(codigo)
    if existente:
        return existente["id"]
    return salvar_produto(
        {"codigo": codigo, "descricao": descricao or codigo, "cliente": cliente}, origem=origem
    )


def excluir_produto(produto_id: int) -> None:
    with get_conn() as c:
        em_uso = c.execute("SELECT COUNT(*) FROM ordens WHERE produto_id = ?", (produto_id,)).fetchone()[0]
        if em_uso:
            raise ValueError(
                f"Produto usado em {em_uso} ordem(ns) de produção — desative-o em vez de excluir."
            )
        c.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))


# --------------------------------------------------------------------- moldes
_SQL_MOLDES = """
    SELECT m.*, p.codigo AS produto_codigo, p.descricao AS produto_descricao
    FROM moldes m LEFT JOIN produtos p ON p.id = m.produto_id
"""


def listar_moldes(busca: str = "", so_ativos: bool = False, limite: int = 500) -> list[dict]:
    sql = _SQL_MOLDES + " WHERE 1 = 1"
    args: list = []
    if so_ativos:
        sql += " AND m.ativo = 1"
    if busca:
        sql += " AND (m.codigo LIKE ? OR m.descricao LIKE ?)"
        like = f"%{busca.strip()}%"
        args += [like, like]
    sql += " ORDER BY m.codigo COLLATE NOCASE LIMIT ?"
    args.append(limite)
    with get_conn() as c:
        return _linhas(c.execute(sql, args))


def obter_molde(molde_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute(_SQL_MOLDES + " WHERE m.id = ?", (molde_id,)).fetchone()
    return dict(row) if row else None


def obter_molde_por_codigo(codigo: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            _SQL_MOLDES + " WHERE m.codigo = ? COLLATE NOCASE", (str(codigo or "").strip(),)
        ).fetchone()
    return dict(row) if row else None


def salvar_molde(dados: dict, molde_id: int | None = None, origem: str = "manual") -> int:
    codigo = _texto(dados.get("codigo"), "Código", 1, 40)
    descricao = _texto(dados.get("descricao"), "Descrição", 2)
    cavidades = _inteiro(dados.get("cavidades", 1), "Cavidades", 1, 128)
    ciclo = _decimal(dados.get("ciclo_padrao_s"), "Ciclo padrão (s)", 0.1)
    produto_id = _inteiro(dados.get("produto_id"), "Produto", obrigatorio=False)
    intervalo = _inteiro(dados.get("manut_intervalo_ciclos"), "Intervalo de manutenção", 1,
                         obrigatorio=False)
    ativo = _bool(dados.get("ativo", 1))
    agora = agora_ms()
    try:
        with get_conn() as c:
            if molde_id is None:
                cur = c.execute(
                    "INSERT INTO moldes (codigo, descricao, cavidades, ciclo_padrao_s, produto_id, "
                    "manut_intervalo_ciclos, ativo, origem, criado_ms, atualizado_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (codigo, descricao, cavidades, ciclo, produto_id, intervalo, ativo, origem, agora, agora),
                )
                return cur.lastrowid
            cur = c.execute(
                "UPDATE moldes SET codigo = ?, descricao = ?, cavidades = ?, ciclo_padrao_s = ?, "
                "produto_id = ?, manut_intervalo_ciclos = ?, ativo = ?, atualizado_ms = ? WHERE id = ?",
                (codigo, descricao, cavidades, ciclo, produto_id, intervalo, ativo, agora, molde_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Molde não encontrado.")
            return molde_id
    except sqlite3.IntegrityError as e:
        if "FOREIGN KEY" in str(e):
            raise ValueError("Produto informado não existe.")
        raise ValueError(f"Já existe um molde com o código '{codigo}'.")


def garantir_molde_do_contexto(c: sqlite3.Connection, ctx: MoldeCtx, cavidades: int,
                               cache: dict[str, int] | None = None) -> int:
    """Devolve o id do molde descrito no contexto do ciclo, cadastrando-o na 1ª vez.

    Recebe a conexão da ingestão para participar da mesma transação.
    """
    chave = ctx.chave[:40]
    if cache is not None and chave in cache:
        return cache[chave]
    row = c.execute("SELECT id, ctx_raw FROM moldes WHERE codigo = ? COLLATE NOCASE", (chave,)).fetchone()
    if row:
        molde_id = row["id"]
        if row["ctx_raw"] != ctx.raw:
            c.execute("UPDATE moldes SET ctx_raw = ? WHERE id = ?", (ctx.raw, molde_id))
    else:
        agora = agora_ms()
        cur = c.execute(
            "INSERT INTO moldes (codigo, descricao, cavidades, ciclo_padrao_s, origem, ctx_raw, "
            "criado_ms, atualizado_ms) VALUES (?, ?, ?, ?, 'auto', ?, ?, ?)",
            (chave, ctx.nome or chave, max(1, int(cavidades or 1)), ctx.ciclo_padrao_s, ctx.raw, agora, agora),
        )
        molde_id = cur.lastrowid
    if cache is not None:
        cache[chave] = molde_id
    return molde_id


def registrar_manutencao_molde(molde_id: int) -> None:
    """Marca a preventiva como feita: a contagem até a próxima recomeça agora."""
    with get_conn() as c:
        cur = c.execute(
            "UPDATE moldes SET ciclos_ultima_manut = ciclos_acumulados, atualizado_ms = ? WHERE id = ?",
            (agora_ms(), molde_id),
        )
        if cur.rowcount == 0:
            raise ValueError("Molde não encontrado.")


def excluir_molde(molde_id: int) -> None:
    with get_conn() as c:
        em_ops = c.execute("SELECT COUNT(*) FROM ordens WHERE molde_id = ?", (molde_id,)).fetchone()[0]
        em_ciclos = c.execute("SELECT 1 FROM ciclos WHERE molde_id = ? LIMIT 1", (molde_id,)).fetchone()
        if em_ops or em_ciclos:
            raise ValueError("Molde com histórico de produção — desative-o em vez de excluir.")
        c.execute("DELETE FROM moldes WHERE id = ?", (molde_id,))


# ------------------------------------------------------------ matérias-primas
def listar_mps(so_ativas: bool = False) -> list[dict]:
    sql = "SELECT * FROM materias_primas"
    if so_ativas:
        sql += " WHERE ativo = 1"
    with get_conn() as c:
        return _linhas(c.execute(sql + " ORDER BY codigo COLLATE NOCASE"))


def salvar_mp(dados: dict, mp_id: int | None = None) -> int:
    codigo = _texto(dados.get("codigo"), "Código", 1, 40)
    descricao = _texto(dados.get("descricao"), "Descrição", 2)
    unidade = _opcional(dados.get("unidade") or "KG", 6).upper() or "KG"
    ativo = _bool(dados.get("ativo", 1))
    try:
        with get_conn() as c:
            if mp_id is None:
                return c.execute(
                    "INSERT INTO materias_primas (codigo, descricao, unidade, ativo, criado_ms) "
                    "VALUES (?, ?, ?, ?, ?)", (codigo, descricao, unidade, ativo, agora_ms()),
                ).lastrowid
            cur = c.execute(
                "UPDATE materias_primas SET codigo = ?, descricao = ?, unidade = ?, ativo = ? WHERE id = ?",
                (codigo, descricao, unidade, ativo, mp_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Matéria-prima não encontrada.")
            return mp_id
    except sqlite3.IntegrityError:
        raise ValueError(f"Já existe uma matéria-prima com o código '{codigo}'.")


def excluir_mp(mp_id: int) -> None:
    with get_conn() as c:
        if c.execute("SELECT 1 FROM lotes_mp WHERE mp_id = ? LIMIT 1", (mp_id,)).fetchone():
            raise ValueError("Matéria-prima com lotes cadastrados — desative-a em vez de excluir.")
        c.execute("DELETE FROM materias_primas WHERE id = ?", (mp_id,))


_SQL_LOTES_MP = """
    SELECT l.*, mp.codigo AS mp_codigo, mp.descricao AS mp_descricao, mp.unidade AS mp_unidade,
           (SELECT COUNT(*) FROM consumo_mp k WHERE k.lote_mp_id = l.id) AS usos
    FROM lotes_mp l JOIN materias_primas mp ON mp.id = l.mp_id
"""


def listar_lotes_mp(busca: str = "", mp_id: int | None = None, limite: int = 300) -> list[dict]:
    sql = _SQL_LOTES_MP + " WHERE 1 = 1"
    args: list = []
    if mp_id:
        sql += " AND l.mp_id = ?"; args.append(mp_id)
    if busca:
        sql += " AND (l.codigo LIKE ? OR mp.codigo LIKE ? OR mp.descricao LIKE ? OR l.fornecedor LIKE ?)"
        like = f"%{busca.strip()}%"
        args += [like, like, like, like]
    sql += " ORDER BY l.criado_ms DESC LIMIT ?"
    args.append(limite)
    with get_conn() as c:
        return _linhas(c.execute(sql, args))


def obter_lote_mp(lote_mp_id: int) -> dict | None:
    with get_conn() as c:
        row = c.execute(_SQL_LOTES_MP + " WHERE l.id = ?", (lote_mp_id,)).fetchone()
    return dict(row) if row else None


def salvar_lote_mp(dados: dict, lote_mp_id: int | None = None) -> int:
    mp_id = _inteiro(dados.get("mp_id"), "Matéria-prima")
    codigo = _texto(dados.get("codigo"), "Código do lote", 1, 60)
    fornecedor = _opcional(dados.get("fornecedor"), 80)
    quantidade = _decimal(dados.get("quantidade"), "Quantidade", 0)
    observacao = _opcional(dados.get("observacao"), 300)
    recebido = dados.get("recebido_em")
    recebido_ms = tempo.parse_local(recebido) if recebido else None
    try:
        with get_conn() as c:
            if lote_mp_id is None:
                return c.execute(
                    "INSERT INTO lotes_mp (mp_id, codigo, fornecedor, quantidade, recebido_ms, "
                    "observacao, criado_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (mp_id, codigo, fornecedor, quantidade, recebido_ms, observacao, agora_ms()),
                ).lastrowid
            cur = c.execute(
                "UPDATE lotes_mp SET mp_id = ?, codigo = ?, fornecedor = ?, quantidade = ?, "
                "recebido_ms = ?, observacao = ? WHERE id = ?",
                (mp_id, codigo, fornecedor, quantidade, recebido_ms, observacao, lote_mp_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Lote de matéria-prima não encontrado.")
            return lote_mp_id
    except sqlite3.IntegrityError as e:
        if "FOREIGN KEY" in str(e):
            raise ValueError("Matéria-prima informada não existe.")
        raise ValueError(f"O lote '{codigo}' já está cadastrado para essa matéria-prima.")


def excluir_lote_mp(lote_mp_id: int) -> None:
    with get_conn() as c:
        if c.execute("SELECT 1 FROM consumo_mp WHERE lote_mp_id = ? LIMIT 1", (lote_mp_id,)).fetchone():
            raise ValueError("Lote já abastecido em produção — ele faz parte da rastreabilidade.")
        c.execute("DELETE FROM lotes_mp WHERE id = ?", (lote_mp_id,))


# --------------------------------------------------------------------- turnos
_SIGLA_RE = re.compile(r"^[A-Z0-9]{1,6}$")


def listar_turnos(so_ativos: bool = False) -> list[dict]:
    sql = "SELECT * FROM turnos"
    if so_ativos:
        sql += " WHERE ativo = 1"
    with get_conn() as c:
        return _linhas(c.execute(sql + " ORDER BY inicio, sigla"))


def _para_turno(row) -> tempo.Turno:
    return tempo.Turno(
        sigla=row["sigla"], nome=row["nome"],
        inicio_min=tempo.hhmm_para_min(row["inicio"]), fim_min=tempo.hhmm_para_min(row["fim"]),
        dias=frozenset(int(d) for d in row["dias"] if d.isdigit()),
    )


def turnos_ativos() -> list[tempo.Turno]:
    return [_para_turno(r) for r in listar_turnos(so_ativos=True)]


def _intervalos_semana(t: tempo.Turno) -> list[tuple[int, int]]:
    """Intervalos [ini, fim) do turno em minutos-da-semana, quebrando na virada."""
    out = []
    for dia in t.dias:
        ini = dia * 1440 + t.inicio_min
        fim = ini + t.duracao_min
        if fim <= 10080:
            out.append((ini, fim))
        else:
            out += [(ini, 10080), (0, fim - 10080)]
    return out


def _sobrepoe(a: tempo.Turno, b: tempo.Turno) -> bool:
    return any(x0 < y1 and y0 < x1
               for x0, x1 in _intervalos_semana(a) for y0, y1 in _intervalos_semana(b))


def salvar_turno(dados: dict, turno_id: int | None = None) -> int:
    sigla = _texto(dados.get("sigla"), "Sigla", 1, 6).upper()
    if not _SIGLA_RE.match(sigla):
        raise ValueError("Sigla: use até 6 letras/números, sem espaços (ex.: T1).")
    nome = _texto(dados.get("nome"), "Nome", 2, 40)
    inicio = tempo.min_para_hhmm(tempo.hhmm_para_min(dados.get("inicio")))
    fim = tempo.min_para_hhmm(tempo.hhmm_para_min(dados.get("fim")))
    dias = "".join(sorted({d for d in str(dados.get("dias", "0123456")) if d in "0123456"}))
    if not dias:
        raise ValueError("Dias: selecione ao menos um dia da semana.")
    ativo = _bool(dados.get("ativo", 1))

    novo = tempo.Turno(sigla, nome, tempo.hhmm_para_min(inicio), tempo.hhmm_para_min(fim),
                       frozenset(int(d) for d in dias))
    if ativo:
        for row in listar_turnos(so_ativos=True):
            if row["id"] == turno_id:
                continue
            if _sobrepoe(novo, _para_turno(row)):
                raise ValueError(
                    f"O horário se sobrepõe ao turno {row['sigla']} ({row['inicio']}–{row['fim']})."
                )
    try:
        with get_conn() as c:
            if turno_id is None:
                return c.execute(
                    "INSERT INTO turnos (sigla, nome, inicio, fim, dias, ativo) VALUES (?, ?, ?, ?, ?, ?)",
                    (sigla, nome, inicio, fim, dias, ativo),
                ).lastrowid
            cur = c.execute(
                "UPDATE turnos SET sigla = ?, nome = ?, inicio = ?, fim = ?, dias = ?, ativo = ? WHERE id = ?",
                (sigla, nome, inicio, fim, dias, ativo, turno_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Turno não encontrado.")
            return turno_id
    except sqlite3.IntegrityError:
        raise ValueError(f"Já existe um turno com a sigla '{sigla}'.")


def excluir_turno(turno_id: int) -> None:
    # Lotes guardam sigla/nome do turno por valor, então o histórico não quebra.
    with get_conn() as c:
        c.execute("DELETE FROM turnos WHERE id = ?", (turno_id,))


# ------------------------------------------------------------ motivos de refugo
def listar_motivos_refugo(so_ativos: bool = False) -> list[dict]:
    sql = "SELECT * FROM motivos_refugo"
    if so_ativos:
        sql += " WHERE ativo = 1"
    with get_conn() as c:
        return _linhas(c.execute(sql + " ORDER BY descricao COLLATE NOCASE"))


def salvar_motivo_refugo(dados: dict, motivo_id: int | None = None) -> int:
    codigo = _texto(dados.get("codigo"), "Código", 1, 20).upper()
    descricao = _texto(dados.get("descricao"), "Descrição", 2, 80)
    ativo = _bool(dados.get("ativo", 1))
    try:
        with get_conn() as c:
            if motivo_id is None:
                return c.execute(
                    "INSERT INTO motivos_refugo (codigo, descricao, ativo) VALUES (?, ?, ?)",
                    (codigo, descricao, ativo),
                ).lastrowid
            cur = c.execute(
                "UPDATE motivos_refugo SET codigo = ?, descricao = ?, ativo = ? WHERE id = ?",
                (codigo, descricao, ativo, motivo_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Motivo não encontrado.")
            return motivo_id
    except sqlite3.IntegrityError:
        raise ValueError(f"Já existe um motivo de refugo com o código '{codigo}'.")


def excluir_motivo_refugo(motivo_id: int) -> None:
    with get_conn() as c:
        if c.execute("SELECT 1 FROM refugos WHERE motivo_id = ? LIMIT 1", (motivo_id,)).fetchone():
            raise ValueError("Motivo já usado em apontamentos — desative-o em vez de excluir.")
        c.execute("DELETE FROM motivos_refugo WHERE id = ?", (motivo_id,))
