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

from app.oee import OEEResult
from app.transforms import format_br

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def _kpi_color(value: float) -> colors.Color:
    """Verde >=85%, amarelo >=60%, vermelho abaixo."""
    if value >= 0.85:
        return colors.HexColor("#10b981")
    if value >= 0.60:
        return colors.HexColor("#f59e0b")
    return colors.HexColor("#ef4444")


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace(".", ",")


def _fmt_dur(s: float) -> str:
    """Formata segundos como Hh Mm Ss para >60s, senao decimal."""
    if s < 60:
        return f"{format_br(s, 2)} s"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    if h > 0:
        return f"{h}h {m:02d}m {sec:02d}s"
    return f"{m}m {sec:02d}s"


def _kpi_card(label: str, value: float, sub: str = "") -> Table:
    """KPI card com 3 linhas fixas (label / valor / sub) e altura uniforme."""
    color = _kpi_color(value)
    pct = _fmt_pct(value)

    styles = getSampleStyleSheet()
    label_style = ParagraphStyle(
        "kpiLbl", parent=styles["Normal"],
        fontSize=9, leading=11, textColor=colors.white,
        alignment=1,  # center
    )
    value_style = ParagraphStyle(
        "kpiVal", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=22, leading=26,
        textColor=colors.white, alignment=1,
    )
    sub_style = ParagraphStyle(
        "kpiSub", parent=styles["Normal"],
        fontSize=7, leading=9,
        textColor=colors.HexColor("#ffffffcc"), alignment=1,
    )

    # Sempre 3 linhas (sub vazio vira espaco em branco) - todos os cards mesma altura
    rows = [
        [Paragraph(label.upper(), label_style)],
        [Paragraph(f"<b>{pct}</b>", value_style)],
        [Paragraph(sub or "&nbsp;", sub_style)],
    ]
    t = Table(rows, colWidths=[40 * mm], rowHeights=[10 * mm, 14 * mm, 9 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def build_pdf_oee(
    r: OEEResult,
    device_label: str,
    variable_label: str,
    platform_label: str,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"OEE {device_label}",
        author="JKControl",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Tit", parent=styles["Title"], fontSize=18, spaceAfter=4)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=12, spaceBefore=10, spaceAfter=6)

    story = []
    story.append(Paragraph("Relatório de OEE — Máquina Injetora", title_style))
    gerado = datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")
    story.append(Paragraph(f"Gerado em {gerado} — {platform_label}", sub_style))
    story.append(Spacer(1, 6 * mm))

    # ----- Identificação -----
    inicio = datetime.fromtimestamp(r.janela_inicio_ms / 1000, LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")
    fim = datetime.fromtimestamp(r.janela_fim_ms / 1000, LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")
    ident = [
        ["Máquina", device_label],
        ["Variável de ciclo", variable_label],
        ["Plataforma", platform_label],
        ["Início do período", inicio],
        ["Fim do período", fim],
        ["Duração planejada", _fmt_dur(r.tempo_planejado_s)],
    ]
    tbl = Table(ident, colWidths=[55 * mm, 120 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8 * mm))

    # ----- KPIs -----
    story.append(Paragraph("Indicadores", h3))
    kpis = [[
        _kpi_card("OEE", r.oee, "indicador global"),
        _kpi_card("Disponibilidade", r.disponibilidade, f"{_fmt_dur(r.tempo_produzindo_s)} / {_fmt_dur(r.tempo_planejado_s)}"),
        _kpi_card("Performance", r.performance, f"ideal {format_br(r.ciclo_ideal_s, 2)}s × médio {format_br(r.ciclo_medio_s, 2)}s"),
        _kpi_card("Qualidade", r.qualidade, f"{r.pecas_boas} boas / {r.pecas_totais} totais"),
    ]]
    kpi_table = Table(kpis, colWidths=[44 * mm] * 4)
    kpi_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 8 * mm))

    # ----- Produção -----
    story.append(Paragraph("Produção", h3))
    prod = [
        ["Peças totais produzidas (válidas)", str(r.pecas_totais)],
        ["Peças não conformes (refugo)", str(r.pecas_nao_conformes)],
        ["Peças boas", str(r.pecas_boas)],
        ["Refugo (%)", _fmt_pct(r.pecas_nao_conformes / r.pecas_totais if r.pecas_totais else 0)],
    ]
    if r.outlier_factor > 0:
        prod.append([
            f"Filtro de outliers (>{format_br(r.outlier_threshold_s, 1)}s = {format_br(r.outlier_factor, 1)}× ideal)",
            f"{r.ciclos_descartados} ciclo(s) descartado(s), {_fmt_dur(r.tempo_descartado_s)} no total",
        ])
    tbl_prod = Table(prod, colWidths=[80 * mm, 95 * mm])
    tbl_prod.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(tbl_prod)
    story.append(Spacer(1, 6 * mm))

    # ----- Tempos -----
    story.append(Paragraph("Tempos", h3))
    tempos = [
        ["Tempo planejado", _fmt_dur(r.tempo_planejado_s)],
        ["Tempo produzindo (soma dos ciclos)", _fmt_dur(r.tempo_produzindo_s)],
        ["Tempo parado", _fmt_dur(r.tempo_parado_s)],
        ["Ciclo ideal", f"{format_br(r.ciclo_ideal_s, 3)} s"],
        ["Ciclo médio", f"{format_br(r.ciclo_medio_s, 3)} s"],
        ["Ciclo mínimo", f"{format_br(r.ciclo_min_s, 3)} s"],
        ["Ciclo máximo", f"{format_br(r.ciclo_max_s, 3)} s"],
    ]
    tbl_t = Table(tempos, colWidths=[80 * mm, 95 * mm])
    tbl_t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(tbl_t)
    story.append(Spacer(1, 6 * mm))

    # ----- Fórmula -----
    story.append(Paragraph("Memorial de cálculo", h3))
    formula = f"""
    <b>Disponibilidade</b> = tempo_produzindo / tempo_planejado = {_fmt_dur(r.tempo_produzindo_s)} / {_fmt_dur(r.tempo_planejado_s)} = <b>{_fmt_pct(r.disponibilidade)}</b><br/>
    <b>Performance</b> = (ciclo_ideal × peças_totais) / tempo_produzindo = ({format_br(r.ciclo_ideal_s, 2)} × {r.pecas_totais}) / {format_br(r.tempo_produzindo_s, 1)}s = <b>{_fmt_pct(r.performance)}</b><br/>
    <b>Qualidade</b> = peças_boas / peças_totais = {r.pecas_boas} / {r.pecas_totais} = <b>{_fmt_pct(r.qualidade)}</b><br/>
    <b>OEE</b> = A × P × Q = {_fmt_pct(r.disponibilidade)} × {_fmt_pct(r.performance)} × {_fmt_pct(r.qualidade)} = <b>{_fmt_pct(r.oee)}</b>
    """
    story.append(Paragraph(formula, ParagraphStyle("F", parent=styles["Normal"], fontSize=9, leading=14)))

    if r.warnings:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Avisos", h3))
        for w in r.warnings:
            story.append(Paragraph(f"&bull; {w}", ParagraphStyle("W", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#92400e"))))

    doc.build(story)
    return buf.getvalue()
