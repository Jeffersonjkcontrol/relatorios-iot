from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
MAX_EVENTOS_PDF = 200


def _fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f} s"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    sec = int(s % 60)
    return f"{m}m {sec:02d}s"


def _fmt_dt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, LOCAL_TZ).strftime("%d/%m %H:%M")


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%".replace(".", ",")


def build_pdf_paradas(data: dict, platform_label: str) -> bytes:
    """Gera PDF do relatório de paradas a partir do dict de analyze_paradas."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title="Relatório de Paradas", author="JKControl",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Tit", parent=styles["Title"], fontSize=17, spaceAfter=4)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=12, spaceBefore=10, spaceAfter=6)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=10)

    s = data["summary"]
    story = []
    story.append(Paragraph("Relatório de Paradas — Máquinas Injetoras", title_style))
    gerado = datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")
    inicio = _fmt_dt(data["start_ms"])
    fim = _fmt_dt(data["end_ms"])
    filtro = f" — máquina {data['device_filtro']}" if data.get("device_filtro") else ""
    story.append(Paragraph(
        f"Gerado em {gerado} — {platform_label} — período {inicio} a {fim} ({data['days']} dias){filtro}",
        sub_style,
    ))
    story.append(Spacer(1, 6 * mm))

    # ---- Resumo ----
    resumo = [
        ["Total de paradas", str(s["total_paradas"])],
        ["Tempo total parado", _fmt_dur(s["tempo_total_s"])],
        ["Tempo médio por parada", _fmt_dur(s["tempo_medio_s"])],
        ["Com motivo apontado", _fmt_pct(s["pct_com_motivo"])],
        ["Paradas em andamento", str(s["em_andamento"])],
        ["Apontamentos órfãos (badge sem parada)", str(s["apontamentos_orfaos"])],
        ["Motivo campeão", s["motivo_campeao"] or "—"],
        ["Máquinas com paradas", f"{data['maquinas_com_paradas']} de {data['maquinas_consultadas']}"],
    ]
    tbl = Table(resumo, colWidths=[85 * mm, 90 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)

    # ---- Pareto por motivo ----
    if data["por_motivo"]:
        story.append(Paragraph("Pareto por motivo", h3))
        rows = [["Motivo", "Categoria", "Qtd", "Tempo total", "Tempo médio", "% do tempo"]]
        for m in data["por_motivo"]:
            rows.append([
                Paragraph(m["motivo"], small),
                m["categoria"] or "—",
                str(m["count"]),
                _fmt_dur(m["tempo_s"]),
                _fmt_dur(m["tempo_medio_s"]),
                _fmt_pct(m["pct_tempo"]),
            ])
        t = Table(rows, colWidths=[62 * mm, 25 * mm, 14 * mm, 26 * mm, 26 * mm, 22 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)

    # ---- Por máquina ----
    if data["por_maquina"]:
        story.append(Paragraph("Por máquina", h3))
        rows = [["Máquina", "Paradas", "Tempo total", "Maior parada", "% do tempo"]]
        for m in data["por_maquina"]:
            rows.append([
                m["name"], str(m["count"]), _fmt_dur(m["tempo_s"]),
                _fmt_dur(m["maior_s"]), _fmt_pct(m["pct_tempo"]),
            ])
        t = Table(rows, colWidths=[60 * mm, 22 * mm, 32 * mm, 32 * mm, 29 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        story.append(t)

    # ---- Por funcionário ----
    if data["por_funcionario"]:
        story.append(Paragraph("Por funcionário", h3))
        rows = [["Funcionário", "Paradas no plantão", "Tempo médio de retorno"]]
        for f in data["por_funcionario"]:
            rows.append([f["funcionario"], str(f["count"]), _fmt_dur(f["tempo_medio_s"])])
        t = Table(rows, colWidths=[80 * mm, 45 * mm, 50 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        story.append(t)

    # ---- Eventos ----
    eventos = data["eventos"][:MAX_EVENTOS_PDF]
    if eventos:
        titulo = "Eventos"
        if len(data["eventos"]) > MAX_EVENTOS_PDF:
            titulo += f" (primeiros {MAX_EVENTOS_PDF} de {len(data['eventos'])})"
        story.append(Paragraph(titulo, h3))
        rows = [["Máquina", "Início", "Fim", "Duração", "Motivo", "Funcionário"]]
        for e in eventos:
            fim = "em andamento" if e["em_andamento"] else _fmt_dt(e["fim_ms"])
            rows.append([
                e["device_name"],
                _fmt_dt(e["inicio_ms"]),
                fim,
                _fmt_dur(e["duracao_s"]),
                Paragraph(e["motivo"], small),
                Paragraph(e["funcionario"], small),
            ])
        t = Table(rows, colWidths=[28 * mm, 24 * mm, 24 * mm, 20 * mm, 46 * mm, 33 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)

    if not data["eventos"]:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Nenhuma parada registrada no período.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()
