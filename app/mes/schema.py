"""Schema do banco do MES, em migrações numeradas.

REGRA: nunca edite uma migração já publicada — acrescente uma nova ao fim da
lista. Cada item roda uma única vez, em ordem, controlado por PRAGMA user_version.

Convenções:
- Instantes em epoch **milissegundos** (colunas *_ms), sempre UTC.
- 'dia' é o dia fabril (YYYY-MM-DD no fuso da fábrica), definido pelos turnos.
- Totais desnormalizados (ordens.qtd_*, lotes.pecas...) são sempre RECALCULADOS
  a partir das tabelas de fatos (contagens/refugos), nunca incrementados.
- Códigos (produto, molde, OP, máquina...) são únicos SEM diferenciar maiúsculas:
  "P-1" e "p-1" são o mesmo item, como em qualquer ERP.
"""

V1 = """
-- ------------------------------------------------------------------ config
CREATE TABLE config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    atualizado_ms INTEGER NOT NULL
);

-- --------------------------------------------------------------- cadastros
CREATE TABLE maquinas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plataforma TEXT NOT NULL,                 -- 'ubidots' | 'jkcontrol'
    codigo TEXT NOT NULL COLLATE NOCASE,      -- label do dispositivo na plataforma (inj82)
    nome TEXT NOT NULL,
    setor TEXT NOT NULL DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,         -- 1 = coletar dados desta máquina
    fonte_contagem TEXT NOT NULL DEFAULT 'contador'
        CHECK (fonte_contagem IN ('contador', 'ciclo')),
    ciclos_por_leitura REAL NOT NULL DEFAULT 1,   -- só no modo 'ciclo'
    cavidades_padrao INTEGER NOT NULL DEFAULT 1,
    observacao TEXT NOT NULL DEFAULT '',
    criado_ms INTEGER NOT NULL,
    atualizado_ms INTEGER NOT NULL,
    UNIQUE (plataforma, codigo)
);

CREATE TABLE produtos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE COLLATE NOCASE,
    descricao TEXT NOT NULL,
    unidade TEXT NOT NULL DEFAULT 'PC',
    peso_g REAL,                              -- peso da peça (g): consumo teórico de MP
    cliente TEXT NOT NULL DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    origem TEXT NOT NULL DEFAULT 'manual',    -- manual | erp | csv | auto
    criado_ms INTEGER NOT NULL,
    atualizado_ms INTEGER NOT NULL
);

CREATE TABLE moldes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE COLLATE NOCASE,
    descricao TEXT NOT NULL,
    cavidades INTEGER NOT NULL DEFAULT 1,
    ciclo_padrao_s REAL,
    produto_id INTEGER REFERENCES produtos(id) ON DELETE SET NULL,
    manut_intervalo_ciclos INTEGER,           -- preventiva a cada N batidas
    ciclos_acumulados INTEGER NOT NULL DEFAULT 0,
    ciclos_ultima_manut INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    origem TEXT NOT NULL DEFAULT 'manual',    -- manual | auto (detectado nos ciclos) | erp
    ctx_raw TEXT NOT NULL DEFAULT '',         -- última string de contexto vista
    criado_ms INTEGER NOT NULL,
    atualizado_ms INTEGER NOT NULL
);

CREATE TABLE materias_primas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE COLLATE NOCASE,
    descricao TEXT NOT NULL,
    unidade TEXT NOT NULL DEFAULT 'KG',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_ms INTEGER NOT NULL
);

CREATE TABLE lotes_mp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mp_id INTEGER NOT NULL REFERENCES materias_primas(id),
    codigo TEXT NOT NULL COLLATE NOCASE,      -- lote do fornecedor / interno
    fornecedor TEXT NOT NULL DEFAULT '',
    quantidade REAL,
    recebido_ms INTEGER,
    observacao TEXT NOT NULL DEFAULT '',
    criado_ms INTEGER NOT NULL,
    UNIQUE (mp_id, codigo)
);

CREATE TABLE turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sigla TEXT NOT NULL UNIQUE COLLATE NOCASE, -- T1, T2, T3 (entra no código do lote)
    nome TEXT NOT NULL,
    inicio TEXT NOT NULL,                     -- 'HH:MM'
    fim TEXT NOT NULL,                        -- 'HH:MM' (pode cruzar a meia-noite)
    dias TEXT NOT NULL DEFAULT '0123456',     -- dias em que o turno COMEÇA (0 = segunda)
    ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE motivos_refugo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE COLLATE NOCASE,
    descricao TEXT NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1
);

-- ------------------------------------------------------- ordens de produção
CREATE TABLE ordens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero TEXT NOT NULL UNIQUE COLLATE NOCASE,
    produto_id INTEGER REFERENCES produtos(id),      -- NULL: OP vista no cartão, ainda sem cadastro
    molde_id INTEGER REFERENCES moldes(id),
    maquina_id INTEGER REFERENCES maquinas(id),      -- máquina planejada
    quantidade INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0),   -- 0 = não informada
    cavidades INTEGER,                               -- cavidades ativas (sobrepõe o molde)
    ciclo_padrao_s REAL,                             -- sobrepõe o molde
    status TEXT NOT NULL DEFAULT 'planejada'
        CHECK (status IN ('planejada','liberada','em_producao','pausada','concluida','cancelada')),
    prioridade INTEGER NOT NULL DEFAULT 0,           -- posição na fila (menor = antes)
    data_inicio_prev TEXT,                           -- YYYY-MM-DD
    data_fim_prev TEXT,
    cliente TEXT NOT NULL DEFAULT '',
    pedido TEXT NOT NULL DEFAULT '',
    observacao TEXT NOT NULL DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'manual',           -- manual | erp | csv | chao (cartão de OP)
    ref_externa TEXT NOT NULL DEFAULT '',
    liberada_ms INTEGER,                             -- quando o PCP liberou para o chão de fábrica
    iniciada_ms INTEGER,
    concluida_ms INTEGER,
    qtd_produzida INTEGER NOT NULL DEFAULT 0,        -- recalculado: SUM(contagens.pecas)
    qtd_refugo INTEGER NOT NULL DEFAULT 0,           -- recalculado: SUM(refugos.quantidade)
    meta_notificada INTEGER NOT NULL DEFAULT 0,
    criado_por TEXT NOT NULL DEFAULT '',
    criado_ms INTEGER NOT NULL,
    atualizado_ms INTEGER NOT NULL
);
CREATE INDEX ix_ordens_status ON ordens (status);
CREATE INDEX ix_ordens_fila ON ordens (maquina_id, status, prioridade);

-- Intervalos em que a OP esteve em execução numa máquina. É a espinha dorsal
-- da rastreabilidade: todo ciclo/contagem é atribuído à OP pelo instante.
CREATE TABLE ordem_execucoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ordem_id INTEGER NOT NULL REFERENCES ordens(id) ON DELETE CASCADE,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    inicio_ms INTEGER NOT NULL,
    fim_ms INTEGER,                                  -- NULL = em andamento
    iniciado_por TEXT NOT NULL DEFAULT '',
    encerrado_por TEXT NOT NULL DEFAULT '',
    motivo_fim TEXT NOT NULL DEFAULT '',             -- pausa | conclusao | cancelamento | troca
    origem TEXT NOT NULL DEFAULT 'manual'            -- manual | rfid (cartão de OP) | molde
);
CREATE INDEX ix_exec_maquina ON ordem_execucoes (maquina_id, inicio_ms);
CREATE INDEX ix_exec_ordem ON ordem_execucoes (ordem_id);
CREATE UNIQUE INDEX ux_exec_aberta_maquina ON ordem_execucoes (maquina_id) WHERE fim_ms IS NULL;

CREATE TABLE ordem_eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ordem_id INTEGER NOT NULL REFERENCES ordens(id) ON DELETE CASCADE,
    ts_ms INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    detalhe TEXT NOT NULL DEFAULT '',
    usuario TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_ordem_eventos ON ordem_eventos (ordem_id, ts_ms);

-- --------------------------------------------------- lotes de produção
-- Lote = OP × máquina × dia fabril × turno × molde. Criado automaticamente
-- pela ingestão; é a unidade de rastreabilidade (etiqueta).
CREATE TABLE lotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE COLLATE NOCASE,
    ordem_id INTEGER REFERENCES ordens(id) ON DELETE SET NULL,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    molde_id INTEGER REFERENCES moldes(id) ON DELETE SET NULL,
    dia TEXT NOT NULL,
    turno_sigla TEXT NOT NULL DEFAULT '',
    turno_nome TEXT NOT NULL DEFAULT '',
    inicio_ms INTEGER NOT NULL,
    fim_ms INTEGER NOT NULL,
    pecas INTEGER NOT NULL DEFAULT 0,
    refugo INTEGER NOT NULL DEFAULT 0,
    ciclos INTEGER NOT NULL DEFAULT 0,               -- leituras de ciclo no lote
    criado_ms INTEGER NOT NULL
);
CREATE INDEX ix_lotes_ordem ON lotes (ordem_id);
CREATE INDEX ix_lotes_maquina_dia ON lotes (maquina_id, dia);
CREATE INDEX ix_lotes_dia ON lotes (dia);

-- ------------------------------------------------ fatos do chão de fábrica
CREATE TABLE ciclos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    ts_ms INTEGER NOT NULL,
    duracao_ms INTEGER NOT NULL,
    op_ctx TEXT,                                     -- nº da OP vindo no contexto (cartão RFID)
    molde_id INTEGER,
    ordem_id INTEGER,
    lote_id INTEGER,
    UNIQUE (maquina_id, ts_ms)
);
CREATE INDEX ix_ciclos_ordem ON ciclos (ordem_id, ts_ms);
CREATE INDEX ix_ciclos_lote ON ciclos (lote_id);

-- Produção: cada linha é um intervalo com N peças. No modo 'contador' vem do
-- delta entre leituras do totalizador; no modo 'ciclo', de cada leitura de ciclo.
-- A leitura das 14:00 cobre o que foi feito das 13:45 às 14:00: por isso dia,
-- turno, hora e lote usam ref_ms (meio do intervalo), não o instante da leitura.
CREATE TABLE contagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    ts_ms INTEGER NOT NULL,                          -- instante da leitura
    ref_ms INTEGER NOT NULL,                         -- instante de ATRIBUIÇÃO (meio do intervalo coberto)
    contador REAL,                                   -- leitura bruta (NULL no modo 'ciclo')
    pecas INTEGER NOT NULL,
    anomalia INTEGER NOT NULL DEFAULT 0,             -- 1 = reinício do contador, 2 = salto descartado
    op_ctx TEXT,                                     -- nº da OP vindo no contexto
    molde_id INTEGER,
    ordem_id INTEGER,
    lote_id INTEGER,
    UNIQUE (maquina_id, ts_ms)
);
CREATE INDEX ix_contagens_ref ON contagens (ref_ms);
CREATE INDEX ix_contagens_maq_ref ON contagens (maquina_id, ref_ms);
CREATE INDEX ix_contagens_ordem ON contagens (ordem_id, ref_ms);
CREATE INDEX ix_contagens_lote ON contagens (lote_id);

-- Sinais de baixa frequência, guardados crus. 'variavel' é o PAPEL, não o label
-- da plataforma: parada, cavidade, alerta, motivo, funcionario, op, qualidade,
-- bloqueio, reinicio.
CREATE TABLE sinais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    variavel TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    valor REAL,
    codigo TEXT,                                     -- código do cartão / status / nº da OP
    contexto TEXT,
    UNIQUE (maquina_id, variavel, ts_ms)
);
CREATE INDEX ix_sinais_papel ON sinais (variavel, ts_ms);

CREATE TABLE paradas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    inicio_ms INTEGER NOT NULL,                      -- início REAL (sinal menos o atraso do alerta)
    sinal_ms INTEGER,                                -- quando o dispositivo publicou parada = 1
    fim_ms INTEGER,                                  -- NULL = em andamento
    inicio_estimado INTEGER NOT NULL DEFAULT 0,      -- 1 = já estava parada quando a coleta começou
    motivo_codigo TEXT,
    motivo_origem TEXT,                              -- 'rfid' | 'manual'
    motivo_ts_ms INTEGER,
    funcionario_codigo TEXT,
    observacao TEXT NOT NULL DEFAULT '',
    apontado_por TEXT NOT NULL DEFAULT '',
    ordem_id INTEGER,
    UNIQUE (maquina_id, inicio_ms)
);
CREATE INDEX ix_paradas_inicio ON paradas (inicio_ms);
CREATE INDEX ix_paradas_abertas ON paradas (maquina_id) WHERE fim_ms IS NULL;

CREATE TABLE refugos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    ordem_id INTEGER REFERENCES ordens(id) ON DELETE SET NULL,
    lote_id INTEGER REFERENCES lotes(id) ON DELETE SET NULL,
    ts_ms INTEGER NOT NULL,
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    motivo_id INTEGER REFERENCES motivos_refugo(id),
    observacao TEXT NOT NULL DEFAULT '',
    usuario TEXT NOT NULL DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'manual'            -- manual | api
);
CREATE INDEX ix_refugos_ordem ON refugos (ordem_id);
CREATE INDEX ix_refugos_maquina ON refugos (maquina_id, ts_ms);
CREATE INDEX ix_refugos_lote ON refugos (lote_id);

-- Lote de matéria-prima abastecido numa máquina (para a genealogia)
CREATE TABLE consumo_mp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_mp_id INTEGER NOT NULL REFERENCES lotes_mp(id),
    maquina_id INTEGER NOT NULL REFERENCES maquinas(id),
    ordem_id INTEGER REFERENCES ordens(id) ON DELETE SET NULL,
    inicio_ms INTEGER NOT NULL,
    fim_ms INTEGER,                                  -- NULL = ainda em uso
    quantidade REAL,
    usuario TEXT NOT NULL DEFAULT '',
    observacao TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_consumo_lote ON consumo_mp (lote_mp_id);
CREATE INDEX ix_consumo_maquina ON consumo_mp (maquina_id, inicio_ms);

-- -------------------------------------------------------------- integração
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    prefixo TEXT NOT NULL,                           -- primeiros caracteres, só para identificar
    hash TEXT NOT NULL UNIQUE,                       -- sha256 da chave completa
    escopo TEXT NOT NULL DEFAULT 'rw' CHECK (escopo IN ('ro', 'rw')),
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_por TEXT NOT NULL DEFAULT '',
    criado_ms INTEGER NOT NULL,
    ultimo_uso_ms INTEGER
);

CREATE TABLE webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    url TEXT NOT NULL,
    segredo TEXT NOT NULL DEFAULT '',
    eventos TEXT NOT NULL DEFAULT '*',               -- '*' ou lista separada por vírgula
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_ms INTEGER NOT NULL
);

CREATE TABLE webhook_entregas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    evento TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'ok', 'falha')),
    tentativas INTEGER NOT NULL DEFAULT 0,
    proxima_ms INTEGER NOT NULL,
    http_status INTEGER,
    ultimo_erro TEXT NOT NULL DEFAULT '',
    criado_ms INTEGER NOT NULL,
    entregue_ms INTEGER
);
CREATE INDEX ix_entregas_pendentes ON webhook_entregas (status, proxima_ms);

CREATE TABLE integracao_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    direcao TEXT NOT NULL,                           -- entrada | saida
    canal TEXT NOT NULL,                             -- api | csv | webhook
    recurso TEXT NOT NULL DEFAULT '',
    resumo TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    autor TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_integracao_log_ts ON integracao_log (ts_ms);

-- ------------------------------------------------------- dados iniciais
-- Três turnos de 8 h (padrão comum em injeção); o gestor ajusta em Cadastros.
INSERT INTO turnos (sigla, nome, inicio, fim, dias) VALUES
    ('T1', '1º turno', '06:00', '14:00', '0123456'),
    ('T2', '2º turno', '14:00', '22:00', '0123456'),
    ('T3', '3º turno', '22:00', '06:00', '0123456');

-- Defeitos típicos de peça injetada
INSERT INTO motivos_refugo (codigo, descricao) VALUES
    ('FALHA',   'Falha de injeção (peça incompleta)'),
    ('REBARBA', 'Rebarba'),
    ('RECHUPE', 'Rechupe'),
    ('BOLHA',   'Bolha'),
    ('QUEIMA',  'Queima / mancha'),
    ('SOLDA',   'Linha de solda'),
    ('EMPENO',  'Empenamento'),
    ('CONTAM',  'Contaminação'),
    ('DIMENS',  'Dimensional fora do especificado'),
    ('REGUL',   'Peça de regulagem (start-up)'),
    ('OUTRO',   'Outro');
"""

MIGRATIONS: list[str] = [V1]
