"""MES — execução da manufatura sobre os dados das injetoras.

Módulos:
- db / schema     banco dedicado (mes.db) e migrações
- config          configuração editável pelo gestor
- tempo           fuso, dia fabril e turnos
- molde_ctx       parser do molde enviado no contexto dos ciclos
- cadastros       máquinas, produtos, moldes, matérias-primas, turnos, motivos
- ordens          ordens de produção: fila, execução e auditoria
- ingestao        pontos da plataforma -> ciclos, contagens, paradas, lotes
- coletor         laço que busca os pontos na plataforma IoT
- producao        agregações e OEE
- rastreabilidade genealogia de lotes
- integracao      chaves de API, webhooks e CSV (ERP)
"""
