"""Configuração do MES, editável pelo gestor e persistida em mes.db (tabela config).

Fica no banco (e não no .env) porque muda em operação — ativar a coleta, trocar
o nome de uma variável — sem reiniciar o container.

Os nomes de variável são configuráveis porque cada firmware publica com seus
próprios labels. Os padrões abaixo são os do firmware JKControl V12.x.
"""
from __future__ import annotations

import os

from app.mes.db import agora_ms, get_conn

PADROES: dict[str, str] = {
    # --- coleta ---
    "ativo": "0",                    # MES ligado? (o gestor ativa explicitamente)
    "plataforma": "ubidots",
    "intervalo_s": "60",             # período do coletor
    "backfill_dias": "3",            # histórico trazido na ativação (cerca de 45 s por dia com 45 máquinas)

    # --- variáveis na plataforma IoT (papel -> label) ---
    "var_ciclo": "ciclo",            # tempo de ciclo; o contexto traz a OP (e/ou o molde)
    "var_contador": "injecao_hora",  # totalizador de peças
    "var_parada": "parada",          # 0/1
    "var_cavidade": "cavidade",      # cavidades configuradas no dispositivo
    "var_alerta": "alertaparada",    # tempo sem injeção até sinalizar parada (ms)
    "var_motivo": "maq_rfid",        # cartão de máquina: evento/motivo de parada
    "var_funcionario": "fun_rfid",   # cartão do operador
    "var_cod_fun": "",               # cartão cod_fun: fora de uso (deixe vazio)
    "var_op": "op_rfid",             # cartão da ordem de produção
    "var_qualidade": "quali_rfid",   # cartão da qualidade (aprovado / reprovado)
    "var_bloqueio": "cont_bloq",     # 1 = contagem de peças bloqueada no dispositivo
    "var_reinicio": "reset_reason",  # causa do último reinício do dispositivo

    # --- chaves do contexto que acompanha ciclo e contador ---
    "ctx_op": "op_rfid",             # nº da OP apontada no leitor da máquina
    "ctx_molde": "molde",            # descrição do molde ("nome|#código|#30seg")

    # --- regras ---
    "alerta_parada_s": "300",        # usado quando a variável de alerta não existe na conta
    "auto_cadastrar_moldes": "1",    # molde novo no contexto vira cadastro
    "auto_iniciar_op": "1",          # OP liberada inicia quando o molde dela começa a ciclar
    "auto_concluir_na_troca": "1",   # OP substituída na máquina com a meta batida é concluída
    "auto_concluir_dias": "7",       # OP pausada sem produção há N dias é encerrada (0 = nunca)
    "ciclos_por_publicacao": "2",    # o firmware publica 1 leitura de ciclo a cada N ciclos da máquina
    "ocioso_min": "3",               # sem ciclo há N minutos (e sem parada) = ociosa
}

# Papel usado pela ingestão -> chave de configuração com o label da variável
PAPEL_PARA_CONFIG: dict[str, str] = {
    "ciclo": "var_ciclo", "contador": "var_contador", "parada": "var_parada",
    "cavidade": "var_cavidade", "alerta": "var_alerta", "motivo": "var_motivo",
    "funcionario": "var_funcionario", "cod_fun": "var_cod_fun", "op": "var_op",
    "qualidade": "var_qualidade", "bloqueio": "var_bloqueio", "reinicio": "var_reinicio",
}

# Na ativação, se o label padrão não existir na conta, tenta estes (outros firmwares)
ALTERNATIVAS: dict[str, list[str]] = {
    "var_contador": ["injecao_hora", "producao"],
}

DESCRICOES: dict[str, str] = {
    "backfill_dias": "Dias de histórico trazidos ao ativar",
    "var_ciclo": "Tempo de ciclo",
    "var_contador": "Totalizador de peças",
    "var_parada": "Parada (0/1)",
    "var_cavidade": "Cavidades",
    "var_alerta": "Tempo de alerta de parada",
    "var_motivo": "Cartão de máquina (evento de parada)",
    "var_funcionario": "Cartão do operador",
    "var_cod_fun": "Cartão cod_fun (código de funcionário)",
    "var_op": "Cartão da OP",
    "var_qualidade": "Cartão da qualidade",
    "var_bloqueio": "Contagem bloqueada",
    "var_reinicio": "Reinício do dispositivo",
    "ciclos_por_publicacao": "Ciclos da máquina por leitura de ciclo publicada",
    "ctx_op": "Chave do contexto com o nº da OP",
    "ctx_molde": "Chave do contexto com o molde",
}


def get(chave: str, default: str | None = None) -> str:
    with get_conn() as c:
        row = c.execute("SELECT valor FROM config WHERE chave = ?", (chave,)).fetchone()
    if row is not None:
        return row["valor"]
    if default is not None:
        return default
    return PADROES.get(chave, "")


def get_int(chave: str, default: int = 0) -> int:
    try:
        return int(float(get(chave)))
    except (TypeError, ValueError):
        return default


def get_bool(chave: str) -> bool:
    return get(chave).strip().lower() in ("1", "true", "sim", "on")


def set_(chave: str, valor) -> None:
    if isinstance(valor, bool):
        valor = "1" if valor else "0"
    with get_conn() as c:
        c.execute(
            "INSERT INTO config (chave, valor, atualizado_ms) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_ms = excluded.atualizado_ms",
            (chave, str(valor), agora_ms()),
        )


def todas() -> dict[str, str]:
    """Padrões sobrepostos pelo que está gravado."""
    out = dict(PADROES)
    with get_conn() as c:
        for row in c.execute("SELECT chave, valor FROM config"):
            out[row["chave"]] = row["valor"]
    return out


def variaveis(cfg: dict[str, str] | None = None) -> dict[str, str]:
    """{papel: label} só com os papéis que têm variável configurada."""
    cfg = cfg or todas()
    return {papel: cfg[chave].strip() for papel, chave in PAPEL_PARA_CONFIG.items()
            if cfg.get(chave, "").strip()}


def coletor_permitido() -> bool:
    """Trava de emergência por variável de ambiente: MES_COLETOR=0 desliga a coleta
    mesmo com o MES ativo (útil em bancada / segunda instância do app)."""
    return os.environ.get("MES_COLETOR", "1").strip() not in ("0", "false", "off")


def mes_ativo() -> bool:
    return get_bool("ativo")
