"""Leituras para os painéis: estado das máquinas, produção, paradas e OEE.

Tudo sai do banco local (mes.db) — abrir o painel não gera nenhuma chamada à
plataforma IoT, então dez telas abertas custam o mesmo que uma.

OEE = Disponibilidade × Performance × Qualidade, por máquina e período:
- tempo planejado = janela − paradas de classe 'planejada' (refeição, sem programação)
- Disponibilidade  = (planejado − paradas não planejadas e de setup) / planejado
- Performance      = tempo ideal das peças feitas / tempo produzindo
                     (tempo ideal = peças ÷ cavidades × ciclo padrão)
- Qualidade        = (peças − refugo) / peças
O ciclo padrão vem da OP ou do molde; sem cadastro, usa-se como referência o
ciclo que a própria máquina sustentou no período (percentil 20) e o resultado
sai marcado como estimado.
"""
from __future__ import annotations

from app.cadastros import motivos_info, resolve_map
from app.mes import cadastros, config, tempo
from app.mes.db import agora_ms, get_conn

HORA_MS = 3600 * 1000
SEM_MOTIVO = "Sem motivo apontado"


# -------------------------------------------------------------------- período
def resolver_periodo(nome: str = "hoje", inicio: str = "", fim: str = "") -> dict:
    """Converte 'hoje' | 'ontem' | 'turno' | '7d' | '30d' | datas em janela [ini, fim)."""
    turnos = cadastros.turnos_ativos()
    agora = agora_ms()
    hoje = tempo.dia_fabril(agora, turnos)
    if inicio:
        ini = tempo.parse_local(inicio)
        fim_ms = tempo.parse_local(fim) if fim else agora
        rotulo = f"{tempo.fmt(ini)} a {tempo.fmt(fim_ms)}"
    elif nome == "ontem":
        ini, fim_ms = tempo.janela_dia(tempo.somar_dias(hoje, -1), turnos)
        rotulo = "Ontem"
    elif nome == "turno":
        dia, turno = tempo.classificar(agora, turnos)
        if turno:
            ini, fim_ms = tempo.janela_turno(dia, turno)
            rotulo = f"Turno atual ({turno.nome})"
        else:
            ini, fim_ms = tempo.janela_dia(hoje, turnos)
            rotulo = "Hoje (fora de turno)"
    elif nome in ("7d", "30d"):
        dias = int(nome[:-1])
        ini = tempo.janela_dia(tempo.somar_dias(hoje, -(dias - 1)), turnos)[0]
        fim_ms = tempo.janela_dia(hoje, turnos)[1]
        rotulo = f"Últimos {dias} dias"
    else:
        ini, fim_ms = tempo.janela_dia(hoje, turnos)
        rotulo = "Hoje"
    return {"ini_ms": ini, "fim_ms": fim_ms, "ate_ms": min(fim_ms, agora), "rotulo": rotulo, "nome": nome}


# ------------------------------------------------------------------- legenda
def _legenda() -> dict[str, dict]:
    return motivos_info()


def descrever_motivo(codigo: str | None, legenda: dict[str, dict]) -> dict:
    if codigo is None:
        return {"codigo": None, "nome": SEM_MOTIVO, "classe": "parada", "cor": "#94a3b8", "cadastrado": True}
    info = legenda.get(codigo)
    if info:
        return {"codigo": codigo, "nome": info["nome"], "classe": info["classe"], "cor": info["cor"],
                "cadastrado": True}
    return {"codigo": codigo, "nome": f"Código {codigo} (sem nome)", "classe": "parada", "cor": "#ef4444",
            "cadastrado": False}


# --------------------------------------------------------- estado das máquinas
def estado_maquinas() -> list[dict]:
    """Um retrato de cada máquina ativa: status, OP, último ciclo, peças do dia."""
    agora = agora_ms()
    turnos = cadastros.turnos_ativos()
    ini_dia, _ = tempo.janela_dia(tempo.dia_fabril(agora, turnos), turnos)
    ocioso_ms = max(1, config.get_int("ocioso_min", 3)) * 60 * 1000
    legenda = _legenda()
    nomes_func = resolve_map("funcionario")
    out = []
    with get_conn() as c:
        maquinas = c.execute("SELECT * FROM maquinas WHERE ativo = 1 ORDER BY nome COLLATE NOCASE").fetchall()
        ultimos = {r["maquina_id"]: r for r in c.execute(
            "SELECT x.maquina_id, x.ts_ms, x.duracao_ms FROM ciclos x JOIN "
            "(SELECT maquina_id, MAX(ts_ms) AS t FROM ciclos GROUP BY maquina_id) u "
            "ON u.maquina_id = x.maquina_id AND u.t = x.ts_ms")}
        paradas = {r["maquina_id"]: r for r in c.execute("SELECT * FROM paradas WHERE fim_ms IS NULL")}
        pecas = {r[0]: r[1] for r in c.execute(
            "SELECT maquina_id, SUM(pecas) FROM contagens WHERE ref_ms >= ? GROUP BY maquina_id", (ini_dia,))}
        ops = {r["maquina_id"]: r for r in c.execute("""
            SELECT e.maquina_id, e.inicio_ms, o.id, o.numero, o.quantidade, o.qtd_produzida, o.qtd_refugo,
                   o.ciclo_padrao_s, p.codigo AS produto_codigo, p.descricao AS produto_descricao
            FROM ordem_execucoes e JOIN ordens o ON o.id = e.ordem_id
            LEFT JOIN produtos p ON p.id = o.produto_id WHERE e.fim_ms IS NULL""")}
        operadores = {r["maquina_id"]: r["codigo"] for r in c.execute(
            "SELECT s.maquina_id, s.codigo FROM sinais s JOIN "
            "(SELECT maquina_id, MAX(ts_ms) AS t FROM sinais WHERE variavel = 'funcionario' AND ts_ms >= ? "
            " GROUP BY maquina_id) u ON u.maquina_id = s.maquina_id AND u.t = s.ts_ms "
            "WHERE s.variavel = 'funcionario'", (agora - 24 * HORA_MS,))}
    for m in maquinas:
        mid = m["id"]
        ult, par, op = ultimos.get(mid), paradas.get(mid), ops.get(mid)
        if par:
            status = "parada"
        elif ult and agora - ult["ts_ms"] <= ocioso_ms:
            status = "produzindo"
        elif ult:
            status = "ociosa"
        else:
            status = "sem_dados"
        item = {
            "id": mid, "codigo": m["codigo"], "nome": m["nome"], "status": status,
            "ultimo_ciclo_ms": ult["ts_ms"] if ult else None,
            "ciclo_s": round(ult["duracao_ms"] / 1000, 2) if ult else None,
            "pecas_dia": pecas.get(mid, 0) or 0,
            "operador": None, "parada": None, "op": None,
        }
        cod = operadores.get(mid)
        if cod:
            item["operador"] = {"codigo": cod, "nome": nomes_func.get(cod)}
        if par:
            item["parada"] = {"id": par["id"], "inicio_ms": par["inicio_ms"],
                              "duracao_s": round((agora - par["inicio_ms"]) / 1000),
                              **descrever_motivo(par["motivo_codigo"], legenda)}
        if op:
            boas = max(0, op["qtd_produzida"] - op["qtd_refugo"])
            item["op"] = {
                "id": op["id"], "numero": op["numero"], "inicio_ms": op["inicio_ms"],
                "produto": op["produto_descricao"] or op["produto_codigo"],
                "quantidade": op["quantidade"], "boas": boas,
                "pct": round(min(100, boas / op["quantidade"] * 100), 1) if op["quantidade"] else None,
                "ciclo_padrao_s": op["ciclo_padrao_s"],
            }
        out.append(item)
    return out


# ------------------------------------------------------------------ produção
def producao_por_hora(ini_ms: int, fim_ms: int, maquina_id: int | None = None) -> list[dict]:
    """Peças por hora cheia (no fuso da fábrica), incluindo as horas sem produção."""
    filtro, args = "", [ini_ms, fim_ms]
    if maquina_id:
        filtro = " AND maquina_id = ?"
        args.append(maquina_id)
    with get_conn() as c:
        linhas = c.execute(
            f"SELECT ref_ms, pecas FROM contagens WHERE ref_ms >= ? AND ref_ms < ? AND pecas > 0{filtro}",
            args).fetchall()
    baldes: dict[int, int] = {}
    for r in linhas:
        dt = tempo.ms_para_dt(r["ref_ms"]).replace(minute=0, second=0, microsecond=0)
        chave = tempo.dt_para_ms(dt)
        baldes[chave] = baldes.get(chave, 0) + r["pecas"]
    out = []
    hora = tempo.dt_para_ms(tempo.ms_para_dt(ini_ms).replace(minute=0, second=0, microsecond=0))
    limite = min(fim_ms, agora_ms())
    while hora < limite:
        out.append({"hora_ms": hora, "rotulo": tempo.fmt(hora, "%H:%M"), "pecas": baldes.get(hora, 0)})
        hora += HORA_MS
    return out


def producao_por_maquina(ini_ms: int, fim_ms: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("""
            SELECT q.id, q.codigo, q.nome, COALESCE(SUM(k.pecas), 0) AS pecas,
                   (SELECT COUNT(*) FROM ciclos x WHERE x.maquina_id = q.id AND x.ts_ms >= ? AND x.ts_ms < ?) AS leituras,
                   (SELECT ROUND(AVG(x.duracao_ms) / 1000.0, 2) FROM ciclos x
                     WHERE x.maquina_id = q.id AND x.ts_ms >= ? AND x.ts_ms < ?) AS ciclo_medio_s
            FROM maquinas q LEFT JOIN contagens k ON k.maquina_id = q.id AND k.ref_ms >= ? AND k.ref_ms < ?
            WHERE q.ativo = 1 GROUP BY q.id ORDER BY pecas DESC, q.nome""",
            (ini_ms, fim_ms, ini_ms, fim_ms, ini_ms, fim_ms))]


# -------------------------------------------------------------------- paradas
def _paradas_no_periodo(c, ini_ms: int, ate_ms: int, maquina_id: int | None = None):
    filtro, args = "", [ate_ms, ate_ms, ini_ms]
    if maquina_id:
        filtro = " AND p.maquina_id = ?"
        args.append(maquina_id)
    return c.execute(f"""
        SELECT p.*, q.codigo AS maquina_codigo, q.nome AS maquina_nome, o.numero AS ordem_numero
        FROM paradas p JOIN maquinas q ON q.id = p.maquina_id LEFT JOIN ordens o ON o.id = p.ordem_id
        WHERE q.ativo = 1 AND p.inicio_ms < ? AND COALESCE(p.fim_ms, ?) > ?{filtro}
        ORDER BY p.inicio_ms DESC""", args).fetchall()


def paradas(ini_ms: int, fim_ms: int, maquina_id: int | None = None, limite: int = 500) -> dict:
    """Eventos do período (duração recortada na janela) + Pareto por motivo."""
    ate = min(fim_ms, agora_ms())
    legenda = _legenda()
    nomes_func = resolve_map("funcionario")
    with get_conn() as c:
        linhas = _paradas_no_periodo(c, ini_ms, ate, maquina_id)
    eventos, por_motivo, por_classe = [], {}, {}
    total_s = 0.0
    for p in linhas:
        fim = p["fim_ms"] if p["fim_ms"] is not None else ate
        dur = max(0, min(fim, ate) - max(p["inicio_ms"], ini_ms)) / 1000
        desc = descrever_motivo(p["motivo_codigo"], legenda)
        if desc["classe"] == "producao":
            continue
        total_s += dur
        chave = desc["codigo"] or ""
        ag = por_motivo.setdefault(chave, {**desc, "ocorrencias": 0, "tempo_s": 0.0})
        ag["ocorrencias"] += 1
        ag["tempo_s"] += dur
        por_classe[desc["classe"]] = por_classe.get(desc["classe"], 0) + dur
        if len(eventos) < limite:
            eventos.append({
                "id": p["id"], "maquina_id": p["maquina_id"], "maquina": p["maquina_nome"],
                "inicio_ms": p["inicio_ms"], "fim_ms": p["fim_ms"], "duracao_s": round(dur),
                "em_andamento": p["fim_ms"] is None, "motivo": desc,
                "motivo_origem": p["motivo_origem"], "ordem": p["ordem_numero"],
                "funcionario": nomes_func.get(p["funcionario_codigo"]) or p["funcionario_codigo"],
            })
    pareto = sorted(por_motivo.values(), key=lambda m: -m["tempo_s"])
    for m in pareto:
        m["pct"] = round(m["tempo_s"] / total_s * 100, 1) if total_s else 0
        m["tempo_s"] = round(m["tempo_s"])
    com_motivo = sum(m["ocorrencias"] for m in pareto if m["codigo"])
    n = sum(m["ocorrencias"] for m in pareto)
    return {
        "total": n, "tempo_s": round(total_s), "pct_apontadas": round(com_motivo / n * 100, 1) if n else None,
        "por_classe": {k: round(v) for k, v in por_classe.items()}, "pareto": pareto, "eventos": eventos,
    }


def codigos_vistos(papel: str = "motivo", dias: int = 90) -> list[dict]:
    """Códigos de cartão que as máquinas publicaram, com quantas vezes — base
    para preencher a legenda sem precisar adivinhar o que existe."""
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT codigo, COUNT(*) AS vezes, MAX(ts_ms) AS ultima_ms FROM sinais "
            "WHERE variavel = ? AND codigo IS NOT NULL AND ts_ms >= ? GROUP BY codigo ORDER BY vezes DESC",
            (papel, agora_ms() - dias * 86400 * 1000))]


# ------------------------------------------------------------------------ OEE
def _percentil(valores: list[int], p: float) -> float | None:
    if len(valores) < 20:
        return None
    valores = sorted(valores)
    return valores[int(len(valores) * p)]


def oee(ini_ms: int, fim_ms: int, maquina_id: int | None = None) -> dict:
    """OEE por máquina e consolidado. Máquina sem produção no período fica fora
    do consolidado (não estava programada)."""
    ate = min(fim_ms, agora_ms())
    janela_s = max(0, ate - ini_ms) / 1000
    legenda = _legenda()
    filtro = " AND q.id = ?" if maquina_id else ""
    args_m = [maquina_id] if maquina_id else []
    with get_conn() as c:
        maquinas = c.execute(f"SELECT q.* FROM maquinas q WHERE q.ativo = 1{filtro} ORDER BY q.nome", args_m).fetchall()
        prod = c.execute("""
            SELECT k.maquina_id, k.ordem_id, SUM(k.pecas) AS pecas,
                   COALESCE(o.cavidades, m.cavidades, q.cavidades_padrao, 1) AS cav,
                   COALESCE(o.ciclo_padrao_s, m.ciclo_padrao_s) AS ciclo_s
            FROM contagens k JOIN maquinas q ON q.id = k.maquina_id
            LEFT JOIN ordens o ON o.id = k.ordem_id
            LEFT JOIN moldes m ON m.id = COALESCE(o.molde_id, k.molde_id)
            WHERE k.ref_ms >= ? AND k.ref_ms < ? AND k.pecas > 0 GROUP BY k.maquina_id, k.ordem_id""",
            (ini_ms, ate)).fetchall()
        refugo = {r[0]: r[1] for r in c.execute(
            "SELECT maquina_id, SUM(quantidade) FROM refugos WHERE ts_ms >= ? AND ts_ms < ? GROUP BY maquina_id",
            (ini_ms, ate))}
        ciclos = {}
        for r in c.execute("SELECT maquina_id, duracao_ms FROM ciclos WHERE ts_ms >= ? AND ts_ms < ?", (ini_ms, ate)):
            ciclos.setdefault(r["maquina_id"], []).append(r["duracao_ms"])
        linhas = _paradas_no_periodo(c, ini_ms, ate, maquina_id)
    parado: dict[int, dict[str, float]] = {}
    for p in linhas:
        fim = p["fim_ms"] if p["fim_ms"] is not None else ate
        dur = max(0, min(fim, ate) - max(p["inicio_ms"], ini_ms)) / 1000
        classe = descrever_motivo(p["motivo_codigo"], legenda)["classe"]
        if classe != "producao":
            d = parado.setdefault(p["maquina_id"], {})
            d[classe] = d.get(classe, 0) + dur

    out, soma = [], {"planejado": 0.0, "produzindo": 0.0, "ideal": 0.0, "pecas": 0, "refugo": 0}
    for m in maquinas:
        mid = m["id"]
        grupos = [g for g in prod if g["maquina_id"] == mid]
        pecas = sum(g["pecas"] for g in grupos)
        par = parado.get(mid, {})
        planejado = max(0.0, janela_s - par.get("planejada", 0))
        indisponivel = min(planejado, par.get("parada", 0) + par.get("setup", 0))
        produzindo = planejado - indisponivel
        ref = _percentil(ciclos.get(mid, []), 0.2)
        ideal, estimado = 0.0, False
        for g in grupos:
            ciclo_s = g["ciclo_s"]
            if not ciclo_s:
                ciclo_s, estimado = (ref / 1000 if ref else None), True
            if ciclo_s:
                ideal += g["pecas"] / max(1, g["cav"]) * ciclo_s
        ref_q = min(refugo.get(mid, 0) or 0, pecas)
        disp = produzindo / planejado if planejado else None
        perf = min(1.0, ideal / produzindo) if produzindo and ideal else None
        qual = (pecas - ref_q) / pecas if pecas else None
        valor = disp * perf * qual if None not in (disp, perf, qual) else None
        item = {
            "id": mid, "codigo": m["codigo"], "nome": m["nome"], "pecas": pecas, "refugo": ref_q,
            "planejado_s": round(planejado), "parado_s": round(indisponivel),
            "setup_s": round(par.get("setup", 0)), "planejada_s": round(par.get("planejada", 0)),
            "disponibilidade": disp, "performance": perf, "qualidade": qual, "oee": valor,
            "ciclo_estimado": estimado and pecas > 0,
        }
        out.append(item)
        if pecas > 0:
            soma["planejado"] += planejado
            soma["produzindo"] += produzindo
            soma["ideal"] += min(ideal, produzindo)
            soma["pecas"] += pecas
            soma["refugo"] += ref_q
    d = soma["produzindo"] / soma["planejado"] if soma["planejado"] else None
    p = soma["ideal"] / soma["produzindo"] if soma["produzindo"] and soma["ideal"] else None
    q = (soma["pecas"] - soma["refugo"]) / soma["pecas"] if soma["pecas"] else None
    return {
        "geral": {"disponibilidade": d, "performance": p, "qualidade": q,
                  "oee": d * p * q if None not in (d, p, q) else None,
                  "pecas": soma["pecas"], "refugo": soma["refugo"],
                  "maquinas_com_producao": sum(1 for i in out if i["pecas"] > 0)},
        "maquinas": sorted(out, key=lambda i: (i["oee"] is None, -(i["oee"] or 0))),
    }


def resumo(ini_ms: int, fim_ms: int) -> dict:
    """Números do topo do painel."""
    estados = estado_maquinas()
    contagem = {"produzindo": 0, "parada": 0, "ociosa": 0, "sem_dados": 0}
    for e in estados:
        contagem[e["status"]] += 1
    o = oee(ini_ms, fim_ms)
    par = paradas(ini_ms, fim_ms, limite=0)
    with get_conn() as c:
        ops = c.execute("SELECT COUNT(DISTINCT ordem_id) FROM ordem_execucoes WHERE fim_ms IS NULL").fetchone()[0]
        incompletas = c.execute(
            "SELECT COUNT(*) FROM ordens WHERE status = 'em_producao' AND (produto_id IS NULL OR quantidade = 0)"
        ).fetchone()[0]
    return {"maquinas": contagem, "total_maquinas": len(estados), "oee": o["geral"],
            "ops_em_producao": ops, "ops_incompletas": incompletas,
            "paradas": {"total": par["total"], "tempo_s": par["tempo_s"], "pct_apontadas": par["pct_apontadas"]}}
