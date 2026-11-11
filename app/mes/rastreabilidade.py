"""Rastreabilidade: do lote para trás (quem, onde, quando, em que condições).

O lote é criado sozinho pela ingestão (OP × máquina × dia fabril × turno), então
toda peça contada já pertence a um. A genealogia junta, pela janela de tempo do
lote naquela máquina: a OP, os operadores que bateram cartão, as liberações da
qualidade, as paradas, o comportamento do ciclo, o refugo e a matéria-prima.
"""
from __future__ import annotations

import io
from statistics import mean, pstdev

from app.cadastros import resolve_map
from app.mes import apontamentos, producao, tempo
from app.mes.db import get_conn

_SQL = """
    SELECT l.*, q.codigo AS maquina_codigo, q.nome AS maquina_nome,
           o.numero AS ordem_numero, o.status AS ordem_status, o.quantidade AS ordem_quantidade,
           o.cliente, o.pedido, o.ciclo_padrao_s,
           p.codigo AS produto_codigo, p.descricao AS produto_descricao,
           m.codigo AS molde_codigo, m.descricao AS molde_descricao
    FROM lotes l
    JOIN maquinas q ON q.id = l.maquina_id
    LEFT JOIN ordens o ON o.id = l.ordem_id
    LEFT JOIN produtos p ON p.id = o.produto_id
    LEFT JOIN moldes m ON m.id = l.molde_id
"""


def buscar(texto: str = "", dia: str = "", maquina_id: int | None = None, limite: int = 200) -> list[dict]:
    """Lotes por trecho do código, nº da OP, produto ou cliente; opcionalmente por dia e máquina."""
    sql, args = _SQL + " WHERE l.pecas > 0", []
    if texto.strip():
        like = f"%{texto.strip()}%"
        sql += (" AND (l.codigo LIKE ? OR o.numero LIKE ? OR p.codigo LIKE ? OR p.descricao LIKE ? "
                "OR o.cliente LIKE ? OR o.pedido LIKE ?)")
        args += [like] * 6
    if dia:
        sql += " AND l.dia = ?"
        args.append(tempo.parse_data(dia))
    if maquina_id:
        sql += " AND l.maquina_id = ?"
        args.append(maquina_id)
    sql += " ORDER BY l.inicio_ms DESC LIMIT ?"
    args.append(limite)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def _pessoas(c, maquina_id: int, papel: str, ini_ms: int, fim_ms: int, nomes: dict) -> list[dict]:
    """Quem estava na máquina: o último cartão batido antes do lote + os batidos durante."""
    antes = c.execute(
        "SELECT ts_ms, codigo FROM sinais WHERE maquina_id = ? AND variavel = ? AND ts_ms < ? "
        "AND ts_ms >= ? ORDER BY ts_ms DESC LIMIT 1",
        (maquina_id, papel, ini_ms, ini_ms - 24 * 3600 * 1000)).fetchall()
    durante = c.execute(
        "SELECT ts_ms, codigo FROM sinais WHERE maquina_id = ? AND variavel = ? AND ts_ms >= ? AND ts_ms <= ? "
        "ORDER BY ts_ms", (maquina_id, papel, ini_ms, fim_ms)).fetchall()
    vistos, out = set(), []
    for r in list(antes) + list(durante):
        if not r["codigo"] or r["codigo"] in vistos:
            continue
        vistos.add(r["codigo"])
        out.append({"codigo": r["codigo"], "nome": nomes.get(r["codigo"]), "desde_ms": r["ts_ms"],
                    "ja_estava": r["ts_ms"] < ini_ms})
    return out


def genealogia(codigo: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(_SQL + " WHERE l.codigo = ?", (codigo.strip(),)).fetchone()
        if not row:
            return None
        lote = dict(row)
        mid, ini, fim = lote["maquina_id"], lote["inicio_ms"], lote["fim_ms"]
        duracoes = [r[0] for r in c.execute("SELECT duracao_ms FROM ciclos WHERE lote_id = ?", (lote["id"],))]
        qualidade = [dict(r) for r in c.execute(
            "SELECT ts_ms, codigo FROM sinais WHERE maquina_id = ? AND variavel = 'qualidade' "
            "AND ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms", (mid, ini, fim))]
        operadores = _pessoas(c, mid, "funcionario", ini, fim, resolve_map("funcionario"))
        materia_prima = apontamentos.consumos_no_intervalo(c, mid, ini, fim)
        outros = [dict(r) for r in c.execute(
            "SELECT codigo, dia, turno_sigla, pecas FROM lotes WHERE ordem_id = ? AND id <> ? AND pecas > 0 "
            "ORDER BY inicio_ms", (lote["ordem_id"], lote["id"]))] if lote["ordem_id"] else []
    refugos = apontamentos.listar_refugos(maquina_id=mid, ini_ms=ini, fim_ms=fim + 1)
    refugos = [r for r in refugos if r["lote_id"] == lote["id"]]
    par = producao.paradas(ini, fim, mid)

    ciclo = None
    if duracoes:
        seg = [d / 1000 for d in duracoes]
        ciclo = {"leituras": len(seg), "medio_s": round(mean(seg), 2), "min_s": round(min(seg), 2),
                 "max_s": round(max(seg), 2), "desvio_s": round(pstdev(seg), 2),
                 "padrao_s": lote["ciclo_padrao_s"]}
    lote["boas"] = max(0, lote["pecas"] - lote["refugo"])
    return {
        "lote": lote, "ciclo": ciclo, "operadores": operadores, "qualidade": qualidade,
        "paradas": {"total": par["total"], "tempo_s": par["tempo_s"], "eventos": par["eventos"]},
        "refugos": refugos, "materia_prima": materia_prima, "outros_lotes_da_op": outros,
    }


def etiqueta_pdf(codigo: str, url: str) -> bytes | None:
    """Etiqueta 100 × 70 mm com QR code apontando para a genealogia do lote."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    g = genealogia(codigo)
    if not g:
        return None
    lt = g["lote"]
    buf = io.BytesIO()
    larg, alt = 100 * mm, 70 * mm
    pdf = canvas.Canvas(buf, pagesize=(larg, alt))
    pdf.setTitle(f"Lote {lt['codigo']}")

    qr = QrCodeWidget(url, barLevel="M")
    x0, y0, x1, y1 = qr.getBounds()
    lado = 30 * mm
    d = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0), 0, 0])
    d.add(qr)
    renderPDF.draw(d, pdf, larg - lado - 4 * mm, alt - lado - 5 * mm)

    def linha(y, rotulo, valor, tam=9):
        pdf.setFont("Helvetica", 6.5)
        pdf.setFillGray(0.4)
        pdf.drawString(5 * mm, y + 3.6 * mm, rotulo.upper())
        pdf.setFillGray(0)
        pdf.setFont("Helvetica-Bold", tam)
        pdf.drawString(5 * mm, y, str(valor)[:34])

    produto = lt["produto_descricao"] or lt["produto_codigo"] or "(produto não informado)"
    linha(alt - 12 * mm, "Produto", produto, 11)
    linha(alt - 23 * mm, "Ordem de produção", lt["ordem_numero"] or "sem OP", 11)
    linha(alt - 33 * mm, "Máquina", lt["maquina_nome"])
    linha(alt - 43 * mm, "Produção", f"{tempo.fmt(lt['inicio_ms'], '%d/%m/%Y %H:%M')} a {tempo.fmt(lt['fim_ms'], '%H:%M')}"
          f"  ·  {lt['turno_nome']}")
    linha(alt - 53 * mm, "Peças boas", f"{lt['boas']:,}".replace(",", "."))
    pdf.setFont("Helvetica", 6.5)
    pdf.setFillGray(0.4)
    pdf.drawString(5 * mm, 9 * mm, "LOTE")
    pdf.setFillGray(0)
    pdf.setFont("Courier-Bold", 10)
    pdf.drawString(5 * mm, 4.5 * mm, lt["codigo"])
    pdf.showPage()
    pdf.save()
    return buf.getvalue()
