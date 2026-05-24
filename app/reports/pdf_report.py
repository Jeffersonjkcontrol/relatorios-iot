from __future__ import annotations

import io
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)

from app.clients.ubidots import ValuePoint
from app.transforms import Transform, apply_value, format_br, format_value

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
CTX_MAX_LEN = 90


def _fmt_context(ctx: dict | None) -> str:
    if not isinstance(ctx, dict) or not ctx:
        return ""
    parts = [f"{k}={v}" for k, v in ctx.items()]
    s = ", ".join(parts)
    if len(s) > CTX_MAX_LEN:
        s = s[: CTX_MAX_LEN - 1] + "…"
    return s


def _has_context(points: list[ValuePoint]) -> bool:
    return any(isinstance(p.context, dict) and p.context for p in points)


def build_pdf(
    points: list[ValuePoint],
    device_label: str,
    variable_label: str,
    platform_label: str,
    start_iso: str | None,
    end_iso: str | None,
    transform: Transform | None = None,
) -> bytes:
    buf = io.BytesIO()
    show_ctx = _has_context(points)
    page_size = landscape(A4) if show_ctx else A4

    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Relatorio {device_label}/{variable_label}",
        author="JKControl",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Titulo", parent=styles["Title"], fontSize=16, spaceAfter=6)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7, leading=9)

    story = []
    story.append(Paragraph("Relatorio de Leituras", title_style))
    gerado_em = datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")
    story.append(Paragraph(f"Gerado em {gerado_em} - {platform_label}", sub_style))
    story.append(Spacer(1, 5 * mm))

    info = [
        ["Dispositivo", device_label],
        ["Variavel", variable_label],
        ["Inicio", start_iso or "-"],
        ["Fim", end_iso or "-"],
        ["Total de amostras", str(len(points))],
        ["Possui context", "Sim" if show_ctx else "Nao"],
    ]
    if transform:
        info.append(["Conversao", f"x {transform.factor} {transform.unit}".strip()])
        if transform.description:
            info.append(["Nota", transform.description])

    valores = [p.value for p in points if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)]
    if valores:
        converted = [apply_value(v, transform) for v in valores]
        decimals = transform.decimals if transform else 3
        unit = (" " + transform.unit) if transform and transform.unit else ""
        info += [
            ["Minimo", format_br(min(converted), decimals) + unit],
            ["Maximo", format_br(max(converted), decimals) + unit],
            ["Media", format_br(mean(converted), decimals) + unit],
        ]

    info_table = Table(info, colWidths=[45 * mm, 160 * mm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6 * mm))

    if not points:
        story.append(Paragraph("Sem dados no periodo selecionado.", styles["Normal"]))
    else:
        story.append(Paragraph("Amostras", styles["Heading3"]))
        valor_header = "Valor"
        if transform and transform.unit:
            valor_header = f"Valor ({transform.unit})"

        if show_ctx:
            header = ["#", "Data/Hora (local)", valor_header, "Contexto"]
            col_widths = [12 * mm, 38 * mm, 30 * mm, 195 * mm]
        else:
            header = ["#", "Data/Hora (local)", valor_header]
            col_widths = [15 * mm, 70 * mm, 80 * mm]

        rows = [header]
        for i, p in enumerate(points, start=1):
            local = p.datetime_utc.astimezone(LOCAL_TZ)
            row = [
                str(i),
                local.strftime("%d/%m/%Y %H:%M:%S"),
                format_value(p.value, transform) if transform else format_br(p.value, 3) if isinstance(p.value, (int, float)) and not isinstance(p.value, bool) else str(p.value),
            ]
            if show_ctx:
                row.append(Paragraph(_fmt_context(p.context), cell_style))
            rows.append(row)

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)

    doc.build(story)
    return buf.getvalue()
