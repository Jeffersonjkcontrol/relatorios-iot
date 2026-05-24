# Relatorios IoT

App web pra consultar dados historicos, calcular **OEE de injetoras** e fazer **analise com IA** das plataformas
**Ubidots Industrial** e **JKControl (NEXUS CORE)**.

Tres tipos de saida: **Preview na tela**, **PDF formatado**, **CSV pra Excel/PowerBI**.

## Funcionalidades

- 📊 **Relatorios** - filtro por dispositivo/variavel/periodo, com transformacoes de unidade configuraveis
- ⚙️ **OEE** - calculo Disponibilidade × Performance × Qualidade com filtro de outliers (paradas longas)
- 🤖 **IA** - chat em portugues que consulta as APIs e analisa os dados (Claude / GPT / Gemini / Ollama)
- 🐳 **Docker** - empacotado pra rodar em qualquer servidor Linux

## Arquitetura

```
app/
├── main.py                 # FastAPI, rotas /, /oee, /ai, /config + endpoints /api/*
├── config.py               # pydantic-settings (.env), tokens das plataformas
├── transforms.py           # mapa (plataforma, variavel) -> conversao (ex.: ms->s)
├── oee.py                  # calculo OEE com outlier filter
├── clients/
│   ├── ubidots.py          # cliente Ubidots v2.0/v1.6 (X-Auth-Token)
│   ├── nexus.py            # cliente NEXUS CORE (Bearer ag_...)
│   └── __init__.py         # factory make_client(platform)
├── reports/
│   ├── csv_report.py       # UTF-8 BOM, colunas ctx.* separadas
│   ├── pdf_report.py       # reportlab, landscape se tem context
│   └── pdf_oee.py          # PDF de OEE com KPI cards coloridos
├── ai/
│   ├── agent.py            # loop agentico (LLM -> tool -> LLM)
│   ├── tools.py            # 8 tools (list_devices, compute_oee, generate_report_link...)
│   ├── db.py               # SQLite local pra historico de conversas
│   └── providers/          # adapters Anthropic / OpenAI / Google / Ollama
├── templates/              # Jinja2 (base + 4 paginas)
└── static/                 # CSS (design tokens, dark mode) + app.js
```

## Como rodar - 3 opcoes

### Opcao A: Local Windows (desenvolvimento)
Veja [README-DEV.md](README-DEV.md) - usa Python embarcado, basta duplo-click no `run.bat`.

### Opcao B: Docker (recomendado para producao)
```bash
# Cliente:
git clone https://github.com/<Jeffersonjkcontrol>/relatorios-iot
cd relatorios-iot
cp .env.example .env
nano .env                       # tokens
docker compose up -d            # builda e sobe
```
Acesse `http://localhost:8000`.

### Opcao C: Mini PC Ubuntu 24.04 (instalacao em campo)
Use o instalador one-liner:
```bash
curl -fsSL https://raw.githubusercontent.com/<Jeffersonjkcontrol>/relatorios-iot/main/deploy/linux/install.sh | sudo bash
```
Documentacao completa em [deploy/linux/README.md](deploy/linux/README.md).

## Configuracao

`.env` na raiz do projeto:
```env
# Plataformas IoT (pelo menos uma)
JKCONTROL_BASE_URL=https://jkcontrol.online
JKCONTROL_TOKEN=ag_...
UBIDOTS_BASE_URL=https://industrial.api.ubidots.com
UBIDOTS_TOKEN=BBUS-...

# IA (opcional)
GOOGLE_API_KEY=AIza...        # https://aistudio.google.com/apikey (free tier)
ANTHROPIC_API_KEY=sk-ant-...  # https://console.anthropic.com ($5 gratis)
OPENAI_API_KEY=sk-...         # https://platform.openai.com ($5 gratis)
OLLAMA_BASE_URL=              # http://localhost:11434 se rodar local
```

## API REST

| Metodo | Rota | Descricao |
|---|---|---|
| GET | `/api/groups?platform=X` | Lista grupos de dispositivos |
| GET | `/api/devices?platform=X&group=Y` | Lista dispositivos |
| GET | `/api/variables?platform=X&device=Y` | Lista variaveis |
| GET | `/api/preview?platform=X&device=Y&variable=Z&start=&end=` | Preview com stats |
| POST | `/relatorio` | PDF/CSV de uma variavel |
| POST | `/relatorio_oee` | PDF de OEE |
| GET | `/api/oee?...` | Calculo OEE (JSON) |
| GET | `/api/ai/providers` | LLM providers configurados |
| POST | `/api/ai/chat` | Chat com IA (SSE streaming) |

## Plataformas suportadas

| Plataforma | Auth | Listagem | Historico |
|---|---|---|---|
| **Ubidots Industrial** | `X-Auth-Token` | `/api/v2.0/devices/`, suporta grupos | `/api/v1.6/devices/<d>/<v>/values` |
| **JKControl (NEXUS CORE)** | `Authorization: Bearer ag_...` | `/api/devices` retorna array, vars embutidas | `/api/devices/<d>/variables/<v>/data` |

Codigo dos clientes em `app/clients/`.

## Stack

- **Backend:** Python 3.12 + FastAPI 0.115 + httpx + reportlab + pydantic-settings
- **Frontend:** Jinja2 + JS vanilla (sem build), marked.js pra markdown da IA
- **IA:** Anthropic SDK 0.39 / OpenAI SDK 1.54 / Google GenAI / Ollama via REST
- **Banco:** SQLite (apenas para historico de IA)
- **Empacotamento:** Docker multi-stage (~76MB final)

## Licenca

Proprietary. Contato: jefferson.piccirillo@jkcontrol.com.br
