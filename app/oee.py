"""Calculo de OEE (Overall Equipment Effectiveness) para maquinas injetoras.

Definicoes:
- Pecas totais produzidas = numero de leituras de `ciclo` no intervalo
- Tempo produzindo (run time) = soma dos ciclos (cada ciclo eh o tempo de 1 peca)
- Tempo planejado (planned production time) = janela selecionada (end - start)
- Tempo parado = tempo planejado - tempo produzindo
- Ciclo ideal = tempo otimo teorico (informado manualmente)
- Pecas nao conformes = refugo informado manualmente
- Pecas boas = pecas totais - pecas nao conformes

Formula OEE classica:
  Disponibilidade (A) = tempo_produzindo / tempo_planejado
  Performance     (P) = (ciclo_ideal * pecas_totais) / tempo_produzindo
  Qualidade       (Q) = pecas_boas / pecas_totais
  OEE             = A * P * Q

Todos os tempos em segundos.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from app.clients.ubidots import ValuePoint
from app.transforms import Transform, apply_value


@dataclass
class OEEResult:
    # Inputs
    ciclo_ideal_s: float
    pecas_nao_conformes: int
    janela_inicio_ms: int
    janela_fim_ms: int
    outlier_factor: float
    outlier_threshold_s: float

    # Contagens
    pecas_totais: int
    pecas_boas: int
    ciclos_descartados: int
    tempo_descartado_s: float

    # Tempos (segundos)
    tempo_planejado_s: float
    tempo_produzindo_s: float
    tempo_parado_s: float
    ciclo_medio_s: float
    ciclo_min_s: float
    ciclo_max_s: float

    # KPIs (fracao 0-1)
    disponibilidade: float
    performance: float
    qualidade: float
    oee: float

    # Diagnostico
    warnings: list[str]


def _ciclos_em_segundos(points: list[ValuePoint], transform: Transform | None) -> list[float]:
    """Aplica o transform (ex.: ms->s) e devolve so valores numericos > 0."""
    out: list[float] = []
    for p in points:
        v = p.value
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if transform:
            v = apply_value(v, transform)
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        out.append(float(v))
    return out


def compute_oee(
    points: list[ValuePoint],
    ciclo_ideal_s: float,
    pecas_nao_conformes: int,
    start_ms: int | None,
    end_ms: int | None,
    transform: Transform | None = None,
    outlier_factor: float = 3.0,
) -> OEEResult:
    """outlier_factor: ciclos > ciclo_ideal * factor sao tratados como paradas/anomalias
    e excluidos dos KPIs (informados separadamente). 0 desabilita o filtro."""
    warnings: list[str] = []

    todos = _ciclos_em_segundos(points, transform)
    threshold = ciclo_ideal_s * outlier_factor if (outlier_factor > 0 and ciclo_ideal_s > 0) else 0

    if threshold > 0:
        ciclos = [c for c in todos if c <= threshold]
        outliers = [c for c in todos if c > threshold]
    else:
        ciclos = todos
        outliers = []

    descartados = len(outliers)
    tempo_descartado = sum(outliers)
    pecas_totais = len(ciclos)

    if descartados > 0:
        warnings.append(
            f"{descartados} ciclo(s) > {threshold:.0f}s descartado(s) "
            f"({_fmt_short_dur(tempo_descartado)} totais) - tratados como paradas/anomalias."
        )

    if pecas_totais == 0:
        return OEEResult(
            ciclo_ideal_s=ciclo_ideal_s,
            pecas_nao_conformes=pecas_nao_conformes,
            janela_inicio_ms=start_ms or 0,
            janela_fim_ms=end_ms or 0,
            outlier_factor=outlier_factor,
            outlier_threshold_s=threshold,
            pecas_totais=0, pecas_boas=0,
            ciclos_descartados=descartados,
            tempo_descartado_s=tempo_descartado,
            tempo_planejado_s=0, tempo_produzindo_s=0, tempo_parado_s=0,
            ciclo_medio_s=0, ciclo_min_s=0, ciclo_max_s=0,
            disponibilidade=0, performance=0, qualidade=0, oee=0,
            warnings=warnings + ["Sem ciclos validos apos filtro de outliers."],
        )

    if pecas_nao_conformes > pecas_totais:
        warnings.append(
            f"Pecas nao conformes ({pecas_nao_conformes}) > pecas totais ({pecas_totais}). "
            "Qualidade limitada a 0."
        )
        pecas_nao_conformes = pecas_totais

    pecas_boas = pecas_totais - pecas_nao_conformes
    tempo_produzindo_s = sum(ciclos)

    # Janela planejada: usa start/end se informados; caso contrario, primeiro->ultimo ponto
    if start_ms is not None and end_ms is not None:
        tempo_planejado_s = (end_ms - start_ms) / 1000.0
    elif points:
        tempo_planejado_s = (points[-1].timestamp_ms - points[0].timestamp_ms) / 1000.0
    else:
        tempo_planejado_s = tempo_produzindo_s

    if tempo_planejado_s <= 0:
        tempo_planejado_s = tempo_produzindo_s
        warnings.append("Janela planejada invalida, usando soma dos ciclos como tempo planejado.")

    if tempo_produzindo_s > tempo_planejado_s:
        warnings.append(
            f"Soma dos ciclos ({tempo_produzindo_s:.1f}s) > janela planejada ({tempo_planejado_s:.1f}s). "
            "Disponibilidade limitada a 100%."
        )
        tempo_produzindo_s = tempo_planejado_s

    tempo_parado_s = max(0.0, tempo_planejado_s - tempo_produzindo_s)

    # KPIs
    disponibilidade = tempo_produzindo_s / tempo_planejado_s if tempo_planejado_s > 0 else 0
    if ciclo_ideal_s > 0 and tempo_produzindo_s > 0:
        performance = (ciclo_ideal_s * pecas_totais) / tempo_produzindo_s
        if performance > 1.0:
            warnings.append(
                f"Performance > 100% ({performance*100:.1f}%) - ciclo ideal ({ciclo_ideal_s}s) "
                f"pode estar superestimado em relacao ao ciclo medio."
            )
            performance = 1.0
    else:
        performance = 0
        if ciclo_ideal_s <= 0:
            warnings.append("Ciclo ideal nao informado - Performance = 0.")

    qualidade = pecas_boas / pecas_totais if pecas_totais > 0 else 0
    oee = disponibilidade * performance * qualidade

    return OEEResult(
        ciclo_ideal_s=ciclo_ideal_s,
        pecas_nao_conformes=pecas_nao_conformes,
        janela_inicio_ms=start_ms or (points[0].timestamp_ms if points else 0),
        janela_fim_ms=end_ms or (points[-1].timestamp_ms if points else 0),
        outlier_factor=outlier_factor,
        outlier_threshold_s=threshold,
        pecas_totais=pecas_totais,
        pecas_boas=pecas_boas,
        ciclos_descartados=descartados,
        tempo_descartado_s=tempo_descartado,
        tempo_planejado_s=tempo_planejado_s,
        tempo_produzindo_s=tempo_produzindo_s,
        tempo_parado_s=tempo_parado_s,
        ciclo_medio_s=sum(ciclos) / len(ciclos),
        ciclo_min_s=min(ciclos),
        ciclo_max_s=max(ciclos),
        disponibilidade=disponibilidade,
        performance=performance,
        qualidade=qualidade,
        oee=oee,
        warnings=warnings,
    )


def _fmt_short_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}min"
    return f"{s/3600:.1f}h"


def to_dict(r: OEEResult) -> dict:
    return asdict(r)
