"""Transformacoes de exibicao por (plataforma, variavel).

Adicione novas conversoes aqui quando o valor armazenado nao estiver na unidade
desejada (ex.: cronometro em ms mas usuario quer ver em segundos).

Chave: (platform_id, variable_label) - case-sensitive.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Transform:
    factor: float = 1.0          # value_exibido = valor_bruto * factor
    unit: str = ""               # unidade pra mostrar (substitui a unit do Ubidots)
    decimals: int = 3            # casas decimais
    description: str = ""        # nota interna


TRANSFORMS: dict[tuple[str, str], Transform] = {
    # Ubidots
    ("ubidots", "ciclo"): Transform(
        factor=1 / 1000, unit="s", decimals=3,
        description="ciclo gravado em ms, exibir em segundos",
    ),
    # adicione aqui novos casos:
    # ("ubidots", "tempo_parada"): Transform(factor=1/60000, unit="min", decimals=2),
    # ("jkcontrol", "alguma_var"): Transform(factor=10, unit="kWh"),
}


def get_transform(platform: str, variable: str) -> Transform | None:
    return TRANSFORMS.get((platform, variable))


def apply_value(value, transform: Transform | None):
    """Aplica o factor. Retorna o valor convertido (ou original se nao numerico)."""
    if transform is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return value * transform.factor


def format_br(value, decimals: int = 3) -> str:
    """Formata numero no padrao brasileiro: 1.234.567,89"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value) if value is not None else ""
    s = f"{value:,.{decimals}f}"
    # swap '.' e ',' usando placeholder
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_value(value, transform: Transform | None) -> str:
    """Aplica transform + formata em BR. Sempre retorna string pronta para exibicao."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "" if value is None else str(value)
    converted = apply_value(value, transform)
    decimals = transform.decimals if transform else 3
    text = format_br(converted, decimals)
    unit = transform.unit if transform and transform.unit else ""
    return f"{text} {unit}".strip()
