"""Interpreta o campo `molde` que o firmware envia no contexto de cada ciclo.

Formato de campo, do jeito que as máquinas publicam (amostras reais):

    "Botão|#89-09307|#28seg"            nome | código | ciclo padrão
    "TAMPA|#89-01933|#33segseg"         sufixo repetido
    "Console.OPP|#89-09267|#65seg."     ponto final
    "REGUA|#89-01946|#40.0"             ciclo sem unidade
    "CHANNEL|#89-01909/911|#70segseg"   código com barra
    "maniFornoGlpPrt#89-07003#32seg."   sem pipes

O texto é digitado por pessoas no setup do molde, então o parser é tolerante.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARADORES = re.compile(r"[|#]+")
# número (inteiro ou decimal) + "seg" obrigatório, repetido ou não, ponto final opcional
_CICLO_COM_UNIDADE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:s|seg|segundos?)+\.?$", re.IGNORECASE)
_NUMERO_PURO = re.compile(r"^(\d+(?:[.,]\d+)?)\.?$")


@dataclass(frozen=True)
class MoldeCtx:
    nome: str
    codigo: str                   # '' quando o texto não traz código
    ciclo_padrao_s: float | None
    raw: str

    @property
    def chave(self) -> str:
        """Identidade do molde: o código quando existe, senão o nome."""
        return self.codigo or self.nome


def _numero(txt: str) -> float | None:
    try:
        v = float(txt.replace(",", "."))
    except ValueError:
        return None
    return v if 0 < v < 36000 else None


def parse(raw) -> MoldeCtx | None:
    if not isinstance(raw, str):
        return None
    texto = raw.strip()
    if not texto:
        return None
    partes = [p.strip() for p in _SEPARADORES.split(texto) if p.strip()]
    if not partes:
        return None

    nome = partes[0][:80]
    codigo = ""
    ciclo: float | None = None
    resto = partes[1:]
    for i, parte in enumerate(resto):
        m = _CICLO_COM_UNIDADE.match(parte)
        if m:
            ciclo = _numero(m.group(1)) or ciclo
            continue
        # Número sem unidade só é ciclo se for o último campo e já houver código
        # antes ("...|#89-01946|#40.0"); sozinho, é mais provável ser o código.
        m = _NUMERO_PURO.match(parte)
        if m and i == len(resto) - 1 and codigo:
            ciclo = _numero(m.group(1)) or ciclo
            continue
        if not codigo:
            codigo = parte[:40]
    return MoldeCtx(nome=nome, codigo=codigo, ciclo_padrao_s=ciclo, raw=texto)


def do_contexto(contexto) -> MoldeCtx | None:
    """Extrai o molde de um contexto (dict vindo da plataforma)."""
    if not isinstance(contexto, dict):
        return None
    return parse(contexto.get("molde"))
