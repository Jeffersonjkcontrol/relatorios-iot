"""Permissões por papel, configuráveis pelo gestor (página Usuários).

- gestor: acesso total, sempre. Não é configurável.
- supervisor e operador: o gestor marca o que cada um pode fazer.

Algumas áreas ficam SEMPRE só com o gestor (Admin, usuários e permissões, coleta
do MES, integrações, conhecimento da IA): se fossem delegáveis, quem as tivesse
poderia se dar acesso total.

A verificação é no servidor (dependência `require_perm`), não só no menu: abrir
um endereço sem permissão devolve 403 — e a tentativa vai para a auditoria.
"""
from __future__ import annotations

import json

from fastapi import HTTPException, Request

from app.admin.settings import _get, _set

PAPEIS = {"gestor": "Gestor", "supervisor": "Supervisor", "operador": "Operador"}
CONFIGURAVEIS = ("supervisor", "operador")

# chave: (rótulo, descrição)
PERMISSOES: dict[str, tuple[str, str]] = {
    "op_executar": ("Executar OP", "Iniciar, pausar e concluir ordens de produção"),
    "apontar": ("Apontar", "Apontar refugo e motivo de parada"),
    "op_planejar": ("Planejar OP", "Criar, editar, liberar, cancelar e reabrir OPs; importar OPs por CSV"),
    "refugo_excluir": ("Excluir refugo", "Excluir um apontamento de refugo"),
    "legenda": ("Legenda", "Editar códigos de parada e funcionários"),
    "cadastros": ("Produtos e turnos", "Editar produtos (ciclo padrão) e turnos"),
    "cartoes": ("Cartões RFID", "Gravar e ler cartões RFID"),
    "relatorios": ("Relatórios", "Gerar relatórios em PDF e CSV"),
    "ia": ("IA", "Usar o assistente de IA"),
    "auditoria": ("Auditoria", "Ver o registro de quem fez o quê"),
}

PADRAO: dict[str, list[str]] = {
    "operador": ["op_executar", "apontar", "relatorios", "ia"],
    "supervisor": ["op_executar", "apontar", "op_planejar", "refugo_excluir", "legenda", "cadastros",
                   "cartoes", "relatorios", "ia"],
}

# Abas de Configurações e a permissão que cada uma exige (None = só gestor)
ABAS_CONFIG = {
    "geral": None, "paradas": "legenda", "funcionarios": "legenda", "cartoes": "cartoes",
    "produtos": "cadastros", "turnos": "cadastros", "coleta": None, "integracoes": None,
}


def matriz() -> dict[str, set[str]]:
    """{papel: permissões}. O que não estiver gravado usa o padrão."""
    try:
        gravado = json.loads(_get("permissoes", "") or "{}")
    except ValueError:
        gravado = {}
    return {p: set(gravado.get(p, PADRAO[p])) & set(PERMISSOES) for p in CONFIGURAVEIS}


def salvar(novo: dict[str, list[str]]) -> None:
    limpo = {p: sorted(set(novo.get(p, [])) & set(PERMISSOES)) for p in CONFIGURAVEIS}
    _set("permissoes", json.dumps(limpo))


def pode(user, permissao: str) -> bool:
    if user is None:
        return False
    if user.role == "gestor":
        return True
    return permissao in matriz().get(user.role, set())


def do_usuario(user) -> dict[str, bool]:
    """Todas as permissões do usuário — vai para as telas decidirem o que mostrar."""
    return {p: pode(user, p) for p in PERMISSOES}


def abas_config(user) -> list[str]:
    return [aba for aba, perm in ABAS_CONFIG.items()
            if (user and user.role == "gestor") or (perm and pode(user, perm))]


def require_perm(*permissoes: str):
    """Dependência FastAPI: exige ao menos uma das permissões (gestor sempre passa)."""
    from app.auth.session import require_user

    def dependencia(request: Request):
        user = require_user(request)
        if not any(pode(user, p) for p in permissoes):
            nomes = " ou ".join(PERMISSOES[p][0] for p in permissoes)
            raise HTTPException(status_code=403, detail=f"Sem permissão ({nomes}). Fale com o gestor.")
        return user
    return dependencia
