"""Gera o PDF de apresentação comercial do sistema a partir de conteúdo estruturado.

Uso (a partir da raiz do projeto):
    .\\python_runtime\\python.exe tools\\gerar_apresentacao_pdf.py

Saída: dist/Relatorios-IoT-Apresentacao.pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, PageBreak, ListFlowable, ListItem,
)

# ---------------------------------------------------------------- paleta
INDIGO = colors.HexColor("#4f46e5")
VIOLET = colors.HexColor("#7c3aed")
CYAN = colors.HexColor("#06b6d4")
GREEN = colors.HexColor("#10b981")
AMBER = colors.HexColor("#f59e0b")
RED = colors.HexColor("#ef4444")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
SUBTLE = colors.HexColor("#94a3b8")
LINE = colors.HexColor("#e2e8f0")
SURFACE = colors.HexColor("#f8fafc")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# ---------------------------------------------------------------- estilos
_ss = getSampleStyleSheet()

S = {
    "h1": ParagraphStyle("h1", parent=_ss["Title"], fontName="Helvetica-Bold",
                         fontSize=26, leading=30, textColor=INK, alignment=TA_CENTER,
                         spaceAfter=2),
    "sub": ParagraphStyle("sub", parent=_ss["Normal"], fontName="Helvetica",
                          fontSize=12, leading=17, textColor=MUTED, alignment=TA_CENTER),
    "h2": ParagraphStyle("h2", parent=_ss["Heading2"], fontName="Helvetica-Bold",
                         fontSize=15, leading=19, textColor=INK,
                         spaceBefore=13, spaceAfter=5),
    "h3": ParagraphStyle("h3", parent=_ss["Heading3"], fontName="Helvetica-Bold",
                         fontSize=11.5, leading=15, textColor=INDIGO,
                         spaceBefore=9, spaceAfter=3),
    "body": ParagraphStyle("body", parent=_ss["Normal"], fontName="Helvetica",
                           fontSize=9.6, leading=14.2, textColor=INK,
                           spaceAfter=5, alignment=TA_JUSTIFY),
    "bullet": ParagraphStyle("bullet", parent=_ss["Normal"], fontName="Helvetica",
                             fontSize=9.4, leading=13.4, textColor=INK),
    "quote": ParagraphStyle("quote", parent=_ss["Normal"], fontName="Helvetica-Oblique",
                            fontSize=10, leading=15, textColor=MUTED,
                            leftIndent=8, spaceAfter=4),
    "benefit": ParagraphStyle("benefit", parent=_ss["Normal"], fontName="Helvetica",
                              fontSize=9.4, leading=13.2, textColor=INK),
    "cell": ParagraphStyle("cell", parent=_ss["Normal"], fontName="Helvetica",
                           fontSize=8.8, leading=12, textColor=INK),
    "cellb": ParagraphStyle("cellb", parent=_ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=8.8, leading=12, textColor=INK),
    "cellh": ParagraphStyle("cellh", parent=_ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=8.8, leading=12, textColor=colors.white),
    "small": ParagraphStyle("small", parent=_ss["Normal"], fontName="Helvetica",
                            fontSize=8.4, leading=11.5, textColor=MUTED),
}


# ---------------------------------------------------------------- helpers
def hx(c: colors.Color) -> str:
    """'#rrggbb' — formato exigido pelo markup inline do reportlab."""
    return "#" + c.hexval()[2:]


def sec(numero: str, titulo: str, cor=INDIGO):
    """Cabeçalho de seção: quadradinho colorido com número + título."""
    badge = Table([[Paragraph(f'<font color="white"><b>{numero}</b></font>', S["cell"])]],
                  colWidths=[9 * mm], rowHeights=[9 * mm])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), cor),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("ROUNDEDCORNERS", [3, 3, 3, 3]),
    ]))
    t = Table([[badge, Paragraph(titulo, S["h2"])]], colWidths=[12 * mm, CONTENT_W - 12 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def callout(texto: str, cor=GREEN, rotulo="Benefício direto"):
    """Caixa com borda colorida à esquerda."""
    inner = Paragraph(f'<font color="{hx(cor)}"><b>{rotulo}:</b></font> {texto}', S["benefit"])
    t = Table([[inner]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("LINEBEFORE", (0, 0), (0, -1), 2.6, cor),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def bullets(itens: list[str], cor=INDIGO):
    return ListFlowable(
        [ListItem(Paragraph(i, S["bullet"]), leftIndent=12) for i in itens],
        bulletType="bullet", start="circle", bulletFontSize=5,
        bulletColor=cor, leftIndent=12, spaceAfter=5,
    )


def tabela(header: list[str], linhas: list[list[str]], widths: list[float],
           bold_col0=True, header_bg=INDIGO):
    data = [[Paragraph(h, S["cellh"]) for h in header]]
    for ln in linhas:
        row = []
        for i, c in enumerate(ln):
            st = S["cellb"] if (i == 0 and bold_col0) else S["cell"]
            row.append(Paragraph(c, st))
        data.append(row)
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
    ]))
    return t


def status_chip(cor, rotulo, desc):
    """Linha do dashboard ao vivo: bolinha colorida + texto."""
    dot = Table([[""]], colWidths=[4 * mm], rowHeights=[4 * mm])
    dot.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), cor),
        ("ROUNDEDCORNERS", [2, 2, 2, 2]),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [dot, Paragraph(f"<b>{rotulo}</b> — {desc}", S["cell"])]


def url_box(url: str, legenda: str = ""):
    txt = f'<font size="15" color="{hx(INDIGO)}"><b>{url}</b></font>'
    if legenda:
        txt += f'<br/><font size="8.5" color="{hx(MUTED)}">{legenda}</font>'
    t = Table([[Paragraph(txt, ParagraphStyle("u", parent=S["body"], alignment=TA_CENTER))]],
              colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef2ff")),
        ("BOX", (0, 0), (-1, -1), 0.8, INDIGO),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [5, 5, 5, 5]),
    ]))
    return t


# ---------------------------------------------------------------- página
def _decor(canvas, doc):
    canvas.saveState()
    # faixa superior fina com gradiente simulado (2 retângulos)
    canvas.setFillColor(INDIGO)
    canvas.rect(0, PAGE_H - 5 * mm, PAGE_W * 0.55, 5 * mm, stroke=0, fill=1)
    canvas.setFillColor(VIOLET)
    canvas.rect(PAGE_W * 0.55, PAGE_H - 5 * mm, PAGE_W * 0.45, 5 * mm, stroke=0, fill=1)
    # rodapé
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(SUBTLE)
    canvas.drawString(MARGIN, 11 * mm, "Relatórios IoT  ·  JKControl")
    canvas.drawRightString(PAGE_W - MARGIN, 11 * mm, f"{doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 14.5 * mm, PAGE_W - MARGIN, 14.5 * mm)
    canvas.restoreState()


def build(dest: Path):
    doc = BaseDocTemplate(
        str(dest), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=20 * mm,
        title="Relatórios IoT — Apresentação",
        author="JKControl", subject="Sistema de monitoramento e análise de produção",
    )
    frame = Frame(MARGIN, 20 * mm, CONTENT_W, PAGE_H - MARGIN - 20 * mm, id="main")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=_decor)])

    st: list = []

    # ============================ CAPA ============================
    st.append(Spacer(1, 26 * mm))
    logo = Table([[Paragraph('<font color="white" size="20"><b>JK</b></font>',
                             ParagraphStyle("lg", parent=S["body"], alignment=TA_CENTER))]],
                 colWidths=[20 * mm], rowHeights=[20 * mm])
    logo.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INDIGO),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    wrap = Table([[logo]], colWidths=[CONTENT_W])
    wrap.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    st += [wrap, Spacer(1, 9 * mm)]
    st.append(Paragraph("Relatórios IoT", S["h1"]))
    st.append(Spacer(1, 2 * mm))
    st.append(Paragraph("Sua fábrica na palma da mão", S["sub"]))
    st.append(Spacer(1, 5 * mm))
    st.append(Paragraph(
        "Sistema de monitoramento e análise de produção<br/>para máquinas injetoras", S["sub"]))
    st.append(Spacer(1, 16 * mm))
    st.append(url_box("http://relatorios.local",
                      "Acesse de qualquer computador, celular ou tablet da rede da fábrica"))
    st.append(Spacer(1, 14 * mm))

    # cards de destaque na capa
    def card(titulo, txt, cor):
        inner = Paragraph(
            f'<font color="{hx(cor)}" size="9.5"><b>{titulo}</b></font><br/>'
            f'<font size="8.4" color="{hx(MUTED)}">{txt}</font>',
            ParagraphStyle("c", parent=S["cell"], leading=12))
        t = Table([[inner]], colWidths=[(CONTENT_W - 8 * mm) / 3])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
            ("LINEABOVE", (0, 0), (-1, 0), 2.2, cor),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return t

    row = Table([[card("Tempo real", "Todas as máquinas numa tela, atualizando sozinha", GREEN),
                  "",
                  card("Sem papel", "Paradas e motivos registrados automaticamente", CYAN),
                  "",
                  card("Com IA", "Pergunte em português e receba a análise pronta", VIOLET)]],
                colWidths=[(CONTENT_W - 8 * mm) / 3, 4 * mm, (CONTENT_W - 8 * mm) / 3,
                           4 * mm, (CONTENT_W - 8 * mm) / 3])
    row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    st.append(row)
    st.append(PageBreak())

    # ====================== O PROBLEMA / SOLUÇÃO ======================
    st.append(Paragraph("O problema que resolvemos", S["h2"]))
    st.append(Paragraph(
        "Hoje, para saber o que realmente aconteceu na produção, alguém precisa:", S["body"]))
    st.append(bullets([
        "Anotar paradas em papel ou planilha — <b>quando anota</b>",
        'Perguntar ao operador <i>"por que a máquina ficou parada ontem?"</i>',
        "Esperar o fechamento do mês para descobrir que a produtividade caiu",
        "Montar relatórios manualmente, juntando dados de várias fontes",
        "Confiar na memória para saber qual molde rodou em qual máquina",
    ], RED))
    st.append(callout(
        "decisões atrasadas, paradas que se repetem pelos mesmos motivos e horas de "
        "trabalho administrativo que não agregam valor.",
        RED, "Resultado"))

    st.append(Spacer(1, 6 * mm))
    st.append(Paragraph("A solução", S["h2"]))
    st.append(Paragraph(
        "Um sistema <b>já conectado às suas injetoras</b>, que transforma os dados que elas "
        "geram em respostas prontas. Funciona pelo navegador, sem instalar nada, em qualquer "
        "computador, celular ou tablet da fábrica. Cada pessoa tem seu login: operadores "
        "consultam, gestores administram.", S["body"]))

    st.append(Spacer(1, 5 * mm))
    st.append(Paragraph("O que muda na prática", S["h3"]))
    st.append(tabela(
        ["Antes", "Depois"],
        [["Paradas anotadas em papel (às vezes)",
          "Todas as paradas registradas automaticamente, com motivo e duração"],
         ['<i>"Acho que a máquina 23 para muito"</i>',
          "<b>A inj23 parou 16 vezes em 24h — 21h53m paradas.</b> Motivo nº 1: troca de molde"],
         ["Relatório manual, uma tarde de trabalho", "PDF pronto em 30 segundos"],
         ["Eficiência descoberta no fechamento do mês",
          "OEE de qualquer período, a qualquer momento"],
         ["Molde com problema descoberto pelo refugo",
          "Desvio de ciclo alertado em amarelo/vermelho na tela"],
         ["Análise depende de quem domina Excel",
          "Qualquer um pergunta à IA em português"]],
        [CONTENT_W * 0.40, CONTENT_W * 0.60], bold_col0=False))
    st.append(PageBreak())

    # ====================== FUNCIONALIDADES ======================
    st.append(Paragraph("O que o sistema faz por você", S["h2"]))
    st.append(Spacer(1, 2 * mm))

    # 1 — Ao vivo
    st.append(sec("1", "Dashboard Ao Vivo — a fábrica em tempo real", GREEN))
    st.append(Paragraph(
        "Uma tela que mostra <b>todas as máquinas ao mesmo tempo</b>, atualizando sozinha "
        "a cada 10 segundos:", S["body"]))
    chips = Table([status_chip(GREEN, "Verde", "máquina produzindo agora"),
                   status_chip(AMBER, "Amarelo", "máquina ociosa (sem ciclo há alguns minutos)"),
                   status_chip(RED, "Vermelho", "máquina parada")],
                  colWidths=[6 * mm, CONTENT_W - 6 * mm])
    chips.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    st.append(chips)
    st.append(Spacer(1, 2 * mm))
    st.append(Paragraph(
        "Cada cartão mostra o ciclo atual, a média recente, a tendência e <b>qual molde está "
        "montado</b>. Ideal para deixar aberto numa TV no chão de fábrica ou na sala do gestor.",
        S["body"]))
    st.append(callout("você enxerga uma máquina parada em segundos — não horas depois.", GREEN))

    # 2 — Paradas
    st.append(sec("2", "Análise de Paradas — saiba por que e por quanto tempo", RED))
    st.append(Paragraph(
        "O sistema detecta <b>automaticamente</b> cada parada de cada máquina e monta o "
        "quadro completo:", S["body"]))
    st.append(bullets([
        "Quantas paradas ocorreram, quanto tempo somaram e a duração média",
        "<b>Ranking dos motivos</b> (Pareto): qual problema mais rouba tempo da fábrica",
        "Qual máquina mais para e qual foi a maior parada do período",
        "<b>Quem estava na máquina</b> e o tempo médio de retorno por funcionário",
    ], RED))
    st.append(Paragraph(
        "<b>Como funciona no dia a dia:</b> quando a máquina para, o operador bate o cartão "
        "do motivo no leitor RFID (Falta de material, Troca de molde, Manutenção…). Só isso. "
        "O sistema cruza horários, calcula durações e monta os relatórios sozinho.", S["body"]))
    st.append(callout(
        "em vez de discutir impressões, a reunião de produção começa com fatos: "
        '<i>"Troca de molde consumiu 14 horas essa semana — 40% de todo o tempo parado."</i>',
        RED))

    # 3 — OEE
    st.append(sec("3", "OEE — o indicador que as melhores fábricas usam", INDIGO))
    st.append(Paragraph(
        "OEE (<i>Overall Equipment Effectiveness</i>) é o padrão mundial para medir eficiência "
        "de máquina. O sistema calcula com um clique:", S["body"]))
    st.append(tabela(
        ["Indicador", "O que mede"],
        [["Disponibilidade", "Quanto do tempo planejado a máquina realmente rodou"],
         ["Performance", "O quão perto do ciclo ideal ela produziu"],
         ["Qualidade", "Proporção de peças boas em relação ao refugo"]],
        [CONTENT_W * 0.28, CONTENT_W * 0.72]))
    st.append(Spacer(1, 3 * mm))
    st.append(Paragraph(
        "Com cores intuitivas (verde/amarelo/vermelho), memorial de cálculo transparente e "
        "filtro inteligente que separa paradas longas de ciclos reais.", S["body"]))
    st.append(callout(
        "você descobre <b>onde</b> está perdendo dinheiro — máquina parada, ritmo lento ou "
        "refugo — e ataca a causa certa.", INDIGO))
    st.append(PageBreak())

    # 4 — Relatórios
    st.append(sec("4", "Relatórios em PDF e Excel — sem trabalho manual", CYAN))
    st.append(Paragraph(
        "Qualquer histórico, de qualquer máquina, em qualquer período:", S["body"]))
    st.append(bullets([
        "Escolha a máquina e a variável",
        "Escolha o período",
        "Clique em <b>Visualizar</b> — confira na tela antes de exportar",
        "Baixe em <b>PDF</b> (pronto para imprimir/enviar) ou <b>CSV</b> (abre no Excel)",
    ], CYAN))
    st.append(Paragraph(
        "Os relatórios saem formatados, com estatísticas já calculadas e o contexto de cada "
        "leitura (molde em uso, etc.).", S["body"]))
    st.append(callout("o relatório que levava uma tarde para montar sai em 30 segundos.", CYAN))

    # 5 — Moldes
    st.append(sec("5", "Análise de Moldes — cada molde sob controle", AMBER))
    st.append(Paragraph("O sistema agrupa a produção <b>por molde</b>, automaticamente:", S["body"]))
    st.append(bullets([
        "Quantas peças cada molde produziu no período",
        "Ciclo médio real <b>versus</b> ciclo especificado do molde",
        "<b>Desvio %</b> com alerta de cor — molde rodando 15% mais lento fica vermelho",
        "Em quais máquinas cada molde rodou",
    ], AMBER))
    st.append(callout(
        "identifica molde desgastado ou mal regulado <b>antes</b> que vire refugo e atraso "
        "de entrega.", AMBER))

    # 6 — IA
    st.append(sec("6", "Assistente de IA — pergunte em português", VIOLET))
    st.append(Paragraph(
        "A funcionalidade que impressiona: um chat onde você pergunta em linguagem natural "
        "e a IA consulta os dados reais e responde na hora.", S["body"]))
    for q in ['"Quais máquinas mais pararam essa semana?"',
              '"Qual o ciclo médio da injetora 82 hoje?"',
              '"Calcule o OEE da inj80 ontem, ciclo ideal 60 segundos"',
              '"Me gera um PDF das paradas dos últimos 7 dias"']:
        st.append(Paragraph(q, S["quote"]))
    st.append(Spacer(1, 2 * mm))
    st.append(Paragraph(
        "A IA responde com tabelas, destaca problemas que você nem perguntou "
        '(ex.: <i>"a injetora 03 está parada há 24 horas contínuas — verifique"</i>) e sugere '
        "ações. Ela conhece as regras da <b>sua</b> fábrica: turnos, metas e ciclos ideais de "
        "cada molde — tudo configurável.", S["body"]))
    st.append(callout(
        "qualquer pessoa da equipe extrai análises que antes dependiam de alguém "
        '"que sabe mexer no Excel".', VIOLET))
    st.append(PageBreak())

    # ====================== COMO CADA UM USA ======================
    st.append(Paragraph("Como cada pessoa usa", S["h2"]))
    st.append(Spacer(1, 2 * mm))

    st.append(Paragraph("Operador — 2 minutos de treinamento", S["h3"]))
    st.append(bullets([
        "No início do turno: <b>bate o cartão de identificação</b> no leitor da máquina",
        "Se a máquina parar: <b>bate o cartão do motivo</b> (ficam pendurados ao lado do leitor)",
        "Pronto. É só isso que o sistema precisa dele.",
    ], GREEN))
    st.append(Paragraph(
        "Se quiser, pode acessar o sistema pelo celular e acompanhar sua máquina — "
        "mas não é obrigatório.", S["small"]))

    st.append(Paragraph("Líder / Supervisor", S["h3"]))
    st.append(bullets([
        "Abre <b>relatorios.local</b> no navegador (salva nos favoritos)",
        "<b>Ao Vivo</b> para acompanhar o turno em tempo real",
        "<b>Paradas</b> no fim do turno: confere os motivos e cobra apontamentos faltantes",
        "Baixa o PDF do turno se precisar reportar",
    ], CYAN))

    st.append(Paragraph("Gestor / Diretor", S["h3"]))
    st.append(bullets([
        "<b>OEE</b> semanal por máquina — enxerga a eficiência real",
        "<b>Paradas → Pareto de motivos</b> — prioriza investimentos pelo que dá mais retorno",
        "<b>Moldes</b> — decide manutenção de ferramental com base em dados",
        "<b>IA</b> — perguntas rápidas sem depender de ninguém montar relatório",
        "Administra usuários, cadastro de motivos e configurações",
    ], INDIGO))

    st.append(Spacer(1, 6 * mm))
    st.append(Paragraph("Perguntas frequentes", S["h2"]))
    faq = [
        ("Vai dar trabalho para o operador?",
         "Não. O único gesto novo é bater um cartão quando a máquina para — leva 2 segundos. "
         "Todo o resto é automático."),
        ("E se ninguém bater o cartão?",
         "O sistema mostra o <b>% de paradas com motivo apontado</b>. Se estiver baixo, o gestor "
         "sabe exatamente onde cobrar. A parada é registrada de qualquer forma — nada se perde."),
        ("Precisa de internet?",
         "O sistema roda num mini computador <b>dentro da fábrica</b>. O acesso pela rede local "
         "funciona mesmo se a internet cair. Internet é necessária apenas para buscar os dados "
         "das máquinas na plataforma e para o assistente de IA."),
        ("Os dados ficam seguros?",
         "Sim. Cada pessoa tem login e senha; operadores não alteram configurações; e o sistema "
         "faz <b>backup automático todos os dias</b>. O suporte técnico acessa por canal "
         "criptografado apenas para manutenção."),
        ("Funciona no celular?",
         "Sim — qualquer dispositivo com navegador conectado na rede da fábrica: PC, notebook, "
         "tablet ou celular."),
        ("E se precisarmos de um relatório novo?",
         "O sistema tem suporte ativo e evolui continuamente — melhorias chegam por atualização "
         "remota, sem parar a fábrica e sem ninguém precisar ir até as máquinas."),
    ]
    for p, r in faq:
        st.append(KeepTogether([
            Paragraph(f'<font color="{hx(INDIGO)}"><b>{p}</b></font>',
                      ParagraphStyle("q", parent=S["body"], spaceAfter=1)),
            Paragraph(r, ParagraphStyle("a", parent=S["body"], spaceAfter=7)),
        ]))

    st.append(Spacer(1, 6 * mm))
    st.append(Paragraph("Comece hoje", S["h2"]))
    st.append(bullets([
        "Acesse <b>http://relatorios.local</b> de qualquer dispositivo da rede da fábrica",
        "Entre com o login fornecido pelo gestor",
        "Explore o <b>Ao Vivo</b> primeiro — é a porta de entrada natural",
        "Na primeira semana, foque em <b>apontar os motivos de parada</b> — "
        "é o combustível de todas as análises",
    ], INDIGO))

    st.append(Spacer(1, 5 * mm))
    frase = Table([[Paragraph(
        f'<font size="11" color="{hx(INK)}"><i>"O que não se mede, não se gerencia."</i></font>'
        f'<br/><font size="9" color="{hx(MUTED)}">O sistema mede por você. Gerenciar fica com '
        f'quem entende do negócio: <b>vocês</b>.</font>',
        ParagraphStyle("f", parent=S["body"], alignment=TA_CENTER, leading=16))]],
        colWidths=[CONTENT_W])
    frase.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("LINEABOVE", (0, 0), (-1, 0), 2, INDIGO),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    st.append(frase)

    st.append(Spacer(1, 7 * mm))
    st.append(Paragraph(
        '<b>Suporte técnico</b><br/>Jefferson Piccirillo — JKControl<br/>'
        'jefferson.piccirillo@jkcontrol.com.br',
        ParagraphStyle("sup", parent=S["small"], alignment=TA_CENTER, leading=13)))

    doc.build(st)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "dist"
    out_dir.mkdir(exist_ok=True)
    dest = out_dir / "Relatorios-IoT-Apresentacao.pdf"
    build(dest)
    print(f"OK -> {dest}  ({dest.stat().st_size/1024:.0f} KB)")
