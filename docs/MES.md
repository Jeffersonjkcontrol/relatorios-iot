# MES — execução da manufatura

Módulo que transforma o que as injetoras publicam em produção rastreável: ordens
de produção, peças por máquina e por turno, paradas com motivo, OEE e lotes.

Referência de desenvolvimento: a conta Ubidots da **Nitron** (45 injetoras,
firmware InjetNitron V12.x). Nada é fixo para ela — nomes de variáveis, turnos e
a legenda dos cartões são configuráveis por empresa.

---

## 1. Como funciona

```
 injetoras ──MQTT──▶ plataforma IoT ──(1 consulta em lote por minuto)──▶ coletor
                                                                            │
   mes.db (SQLite local) ◀── ingestão: ciclos, contagens, paradas, lotes ◀──┘
        │
        └──▶ painéis (/mes) · API do ERP (/api/v1) · webhooks
```

- O **coletor** roda dentro do app. A cada minuto busca os pontos novos de todas
  as máquinas e os grava em `mes.db`. Abrir os painéis não consulta a plataforma.
- Na ativação ele traz o histórico (7 dias por padrão; cerca de 16 minutos para
  45 máquinas). Se o app ficar desligado, a lacuna é preenchida ao voltar.
- Rodar com **um único processo** (`uvicorn` sem `--workers`): dois processos
  seriam dois coletores.

### O que é lido das máquinas

| Dado | Variável padrão | Uso |
|---|---|---|
| Tempo de ciclo | `ciclo` | ciclo real; o contexto traz o nº da OP |
| Totalizador de peças | `injecao_hora` | **fonte das peças produzidas** |
| Parada | `parada` | eventos de parada |
| Cartão de máquina | `maq_rfid` | motivo da parada |
| Cartão do operador | `fun_rfid` | quem estava na máquina |
| Cartão da OP | `op_rfid` | qual OP está rodando |
| Cartão da qualidade | `quali_rfid` | aprovado / reprovado |
| Cavidades | `cavidade` | peças por ciclo |
| Diagnóstico | `cont_bloq`, `reset_reason` | contagem bloqueada, reinícios |

Os nomes são editáveis em **Configurações → Coleta MES**. Variável em branco não é coletada.

### Três regras que explicam os números

1. **As peças vêm do totalizador, não da contagem de ciclos.** O firmware publica
   `ciclo` a cada 2 ciclos da máquina; contar leituras daria metade da produção.
2. **Cada leitura do totalizador vale para o intervalo que ela cobre.** A leitura
   das 14:00 conta o que foi feito das 13:45 às 14:00, então pertence ao turno que
   terminou às 14:00 — não ao seguinte.
3. **A parada começa quando a máquina parou, não quando o dispositivo avisou.** O
   firmware só publica `parada = 1` depois de 5 minutos sem injeção; o MES desconta
   esse atraso (ajustável em `alerta_parada_s`).

---

## 2. Colocar para funcionar

1. `.env` com o token da plataforma (`UBIDOTS_TOKEN`).
2. **Configurações → Coleta MES → Verificar a conta**: mostra os dispositivos e
   quais variáveis existem.
3. **Ativar coleta**. Dispositivo sem variável de ciclo (esteira, gateway) fica de fora.
4. **Configurações → Códigos de parada**: dar nome aos códigos (seção 4).
5. Abrir **Fábrica** (`/mes`).

Desligar de emergência sem mexer na tela: variável de ambiente `MES_COLETOR=0`.

---

## 3. Ordens de produção

A OP é identificada pelo **número**. Ela pode nascer de três jeitos:

| Origem | Como |
|---|---|
| Cartão na máquina | o operador bate o cartão da OP; se o MES não conhece o número, cria a OP na hora, sem produto nem quantidade ("sem cadastro") |
| ERP | `POST /api/v1/ordens` |
| CSV | Configurações → Integrações |

Como o número é a chave, importar do ERP uma OP que já nasceu do cartão **só
completa o cadastro** — a produção contada é mantida.

Estados: planejada → liberada → em produção ⇄ pausada → concluída (ou cancelada).

- Outra OP apontada na mesma máquina: a anterior fica **pausada**, ou **concluída**
  se já atingiu a quantidade planejada.
- A mesma OP pode rodar em mais de uma máquina ao mesmo tempo.
- OP pausada que não volta à máquina em 7 dias é encerrada sozinha (`auto_concluir_dias`).
- Cartão batido de novo reabre uma OP concluída.

---

## 4. Legenda dos cartões

**Configurações → Códigos de parada** e **→ Funcionários**. Os códigos que as
máquinas já publicaram aparecem listados para preencher; dá para importar uma
lista `código;nome` copiada de planilha. `06` e `6` são o mesmo código.

Cada código de parada tem um tipo, que define o efeito no OEE:

| Tipo | Exemplo | Efeito |
|---|---|---|
| Parada não planejada | falta de material, manutenção | reduz a disponibilidade |
| Setup / troca de molde | troca de molde, regulagem | reduz a disponibilidade, mostrado à parte |
| Parada planejada | refeição, sem programação | sai do tempo planejado (não penaliza) |
| Produção (liberação) | cartão que libera a máquina | não é motivo de parada |

Mudar a legenda **corrige o histórico**: as paradas dos últimos 90 dias são reavaliadas.

Regra de associação: vale a última batida de cartão entre 10 minutos antes do
início da parada e 2 minutos depois do fim. Motivo apontado manualmente na tela
tem precedência sobre o cartão.

---

## 5. OEE

`OEE = Disponibilidade × Performance × Qualidade`, por máquina e período.

- **Tempo planejado** = período − paradas planejadas.
- **Disponibilidade** = (planejado − paradas não planejadas e de setup) ÷ planejado.
- **Performance** = tempo ideal das peças feitas ÷ tempo produzindo, onde tempo
  ideal = peças ÷ cavidades × ciclo padrão.
- **Qualidade** = (peças − refugo) ÷ peças.

O ciclo padrão vem da OP. **Sem ciclo padrão cadastrado**, a referência é o ciclo
que a própria máquina sustentou no período (percentil 20) e a Performance sai
marcada com `*` (estimada). Máquina sem produção no período não entra no OEE da fábrica.

Tempo parado sem motivo conta como parada não planejada. Enquanto a noite sem
turno não tiver um código de "parada planejada", ela derruba a disponibilidade.

---

## 6. Rastreabilidade

O lote é criado sozinho: **OP × máquina × dia fabril × turno**. Código:

```
112729.261110.T1.NITRON_INJ13     OP · AAMMDD · turno · máquina
SEMOP.261110.T1.NITRON_INJ13      produção sem OP apontada
```

O dia fabril começa no primeiro turno (06:00 por padrão): a produção das 03:00
pertence ao dia anterior, turno da noite.

A genealogia do lote (**Fábrica → Rastreabilidade**) mostra OP e produto, período,
peças boas e refugo, ciclo médio/mínimo/máximo, operadores, cartões da qualidade,
paradas com motivo e matéria-prima apontada. A etiqueta em PDF (100 × 70 mm) traz
um QR code que abre essa página.

---

## 7. Integração com ERP

### CSV

Configurações → Integrações. Cabeçalho obrigatório; separador `;`, `,` ou tab.

```
numero;produto_codigo;produto_descricao;quantidade;maquina;ciclo_padrao_s;data_fim_prev;cliente;liberar
112729;TP-01;Tampa 20L azul;5000;nitron_inj13;17,2;14/11/2026;ACME;sim
```

Obrigatórias: `numero`, `produto_codigo`, `quantidade`. Aceita também `molde_codigo`,
`cavidades`, `data_inicio_prev`, `pedido`, `observacao`. Reimportar o mesmo arquivo não duplica.

### API `/api/v1`

Chave criada em Configurações → Integrações, enviada em `X-API-Key: mes_...`
(ou `Authorization: Bearer mes_...`). Chave de leitura não envia dados.
Documentação interativa em `/docs` (com login).

| Método | Rota | Para quê |
|---|---|---|
| GET | `/api/v1/status` | situação da coleta |
| POST | `/api/v1/ordens` | cria/atualiza uma OP ou uma lista, pelo número |
| GET | `/api/v1/ordens?status=em_producao` | lista OPs |
| GET | `/api/v1/ordens/{numero}` | uma OP com a produção contada |
| GET | `/api/v1/apontamentos?desde=&ate=&ordem=` | produção por lote (últimas 24 h se sem datas) |
| GET | `/api/v1/paradas?desde=&ate=` | paradas com motivo |
| GET | `/api/v1/lotes/{codigo}` | genealogia do lote |
| POST | `/api/v1/funcionarios` | sincroniza `[{codigo, nome}]` |

```bash
curl -X POST http://relatorios.local/api/v1/ordens -H "X-API-Key: mes_..." \
  -H "Content-Type: application/json" \
  -d '{"numero":"112729","produto_codigo":"TP-01","produto_descricao":"Tampa 20L","quantidade":5000,"liberar":true}'
```

Datas: `AAAA-MM-DD` ou `AAAA-MM-DDTHH:MM` (horário da fábrica).

### Webhooks

O MES faz `POST` no endereço cadastrado quando ocorre um evento: `ordem.criada`,
`ordem.liberada`, `ordem.iniciada`, `ordem.pausada`, `ordem.concluida`,
`ordem.cancelada`, `ordem.meta_atingida`, `refugo.apontado`, `parada.iniciada`,
`parada.encerrada`.

- Corpo: `{"evento": "...", "ts_ms": ..., "dados": {...}}`.
- Cabeçalhos: `X-MES-Evento` e `X-MES-Assinatura: sha256=<HMAC-SHA256 do corpo com o segredo>`.
- Falhou (erro de rede ou HTTP ≥ 300): novas tentativas em 1 min, 5 min, 30 min, 2 h e 6 h.
- Eventos antigos trazidos pelo histórico inicial não disparam webhook de parada/início.

---

## 8. Para quem desenvolve

```
app/mes/
  schema.py, db.py     banco mes.db (SQLite, WAL) e migrações numeradas
  config.py            configuração editável (tabela config)
  tempo.py             fuso, dia fabril, turnos
  cadastros.py         máquinas, produtos, moldes, matérias-primas, turnos, refugo
  ordens.py            OPs: estados, execuções, auditoria
  lotes.py             atribuição de produção a OP e lote
  ingestao.py          pontos da plataforma -> ciclos, contagens, paradas
  coletor.py           laço de coleta, histórico inicial
  producao.py          estado das máquinas, produção, paradas, OEE
  rastreabilidade.py   genealogia e etiqueta
  apontamentos.py      refugo, matéria-prima, motivo manual
  integracao.py        chaves, CSV, webhooks
  api.py               /api/v1 (ERP)      rotas.py   /api/mes/* (telas)
app/cadastros.py       legenda dos cartões RFID (fica em ai_history.db)
```

- **Migrações**: nunca editar uma já publicada; acrescentar ao fim de `MIGRATIONS`.
- **Totais** (`ordens.qtd_*`, `lotes.pecas`) são sempre recalculados a partir dos
  fatos, nunca incrementados.
- **Ingestão idempotente**: reprocessar os mesmos pontos não duplica nada.
- **Testes**: `python_runtime\python.exe -m pip install -r requirements-dev.txt`,
  depois `python_runtime\python.exe -m pytest tests -q`. Não tocam na plataforma
  nem no banco real.
- **Backup**: `mes.db` fica no mesmo volume de dados já copiado pelo backup diário.

### Limites conhecidos

- Contagem com resolução do totalizador (15 min na Nitron): o painel de peças
  atualiza nessa cadência; ciclo e status são de minuto em minuto.
- Eventos de cartão enfileirados pelo firmware sem rede chegam com o horário da
  reconexão, não o da batida.
- Os ciclos brutos não são expurgados (cerca de 90 mil linhas/dia com 45 máquinas).
- Sem telas ainda para: criar OP manualmente, apontar refugo e matéria-prima,
  cadastro de produtos/moldes/turnos (as funções existem e são testadas).
