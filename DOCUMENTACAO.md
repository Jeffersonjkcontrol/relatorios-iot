# 📊 Relatórios IoT — Documentação Completa

Sistema web para consulta histórica, cálculo de OEE, análise de moldes e chat com IA sobre dados de máquinas injetoras conectadas às plataformas **Ubidots Industrial** e **JKControl (NEXUS CORE)**.

**Repositório:** https://github.com/Jeffersonjkcontrol/relatorios-iot
**Imagem Docker:** `ghcr.io/jeffersonjkcontrol/relatorios-iot:latest`
**Contato:** jefferson.piccirillo@jkcontrol.com.br

---

## 📑 Índice

1. [Visão geral](#1-visão-geral)
2. [Arquitetura](#2-arquitetura)
3. [Instalação no mini PC Linux](#3-instalação-no-mini-pc-linux)
4. [Configuração dos tokens (.env)](#4-configuração-dos-tokens-env)
5. [Páginas do app](#5-páginas-do-app)
6. [Acesso na LAN (mDNS / hostname amigável)](#6-acesso-na-lan-mdns--hostname-amigável)
7. [Acesso remoto via Tailscale](#7-acesso-remoto-via-tailscale)
8. [Painel Admin](#8-painel-admin)
9. [IA — provedores e uso](#9-ia--provedores-e-uso)
10. [Manutenção: backup, restauração, atualização](#10-manutenção)
11. [Troubleshooting](#11-troubleshooting)
12. [Stack técnico](#12-stack-técnico)
13. [Cheatsheet de comandos](#13-cheatsheet-de-comandos)
14. [Playbook de entrega para cliente novo](#14-playbook-de-entrega-para-cliente-novo)
15. [Roadmap](#15-roadmap)

---

## 1. Visão geral

### Funcionalidades

- 📊 **Relatórios** — consulta histórica de variáveis com preview, exportação PDF/CSV
- ⚙️ **OEE** — cálculo de Disponibilidade × Performance × Qualidade com filtro de outliers
- 🤖 **IA** — chat em português que consulta as APIs (Anthropic / OpenAI / Google / Ollama)
- 📡 **Dashboard ao vivo** — status das máquinas atualizando a cada 10s
- 🔧 **Análise de moldes** — extração e agregação a partir do `context.molde` dos ciclos
- 🔐 **Multi-usuário** — papéis "gestor" (total) e "operador" (leitura)
- 🛠️ **Painel Admin** — modo manutenção, restart, backup, logs
- 🐳 **Containerizado** — imagem Docker ~76MB
- 🌐 **Acesso remoto** — via Tailscale (zero config de NAT)

### Plataformas suportadas

| Plataforma | Auth | Endpoint principal |
|---|---|---|
| **Ubidots Industrial** | header `X-Auth-Token` | `/api/v2.0/devices/`, `/api/v1.6/.../values` |
| **JKControl (NEXUS CORE)** | header `Authorization: Bearer ag_...` | `/api/devices` (array), `/api/devices/.../data` |

---

## 2. Arquitetura

```
relatorios_ubidots/
├── app/
│   ├── main.py                    # FastAPI — rotas
│   ├── config.py                  # pydantic-settings (.env)
│   ├── transforms.py              # conversão (ms→s, etc.)
│   ├── oee.py                     # cálculo OEE
│   ├── live.py                    # snapshot ao vivo
│   ├── moldes.py                  # parser e agregação de moldes
│   │
│   ├── auth/                      # autenticação multi-usuário
│   │   ├── db.py                  # SQLite + bcrypt
│   │   └── session.py             # cookies HTTPOnly assinados
│   │
│   ├── admin/                     # painel admin
│   │   ├── settings.py            # modo manutenção
│   │   └── operations.py          # Docker API via unix socket
│   │
│   ├── clients/                   # clientes HTTP
│   │   ├── ubidots.py             # Ubidots v2.0/v1.6 + cache + retry 429
│   │   └── nexus.py               # NEXUS CORE
│   │
│   ├── reports/                   # geração PDF/CSV (reportlab)
│   │   ├── csv_report.py
│   │   ├── pdf_report.py
│   │   └── pdf_oee.py
│   │
│   ├── ai/                        # IA
│   │   ├── agent.py               # loop agêntico
│   │   ├── tools.py               # 8 ferramentas chamáveis pela IA
│   │   ├── db.py                  # SQLite de conversas
│   │   └── providers/             # adapters Anthropic, OpenAI, Google, Ollama
│   │
│   ├── templates/                 # Jinja2 (10 páginas)
│   └── static/                    # CSS + JS
│
├── deploy/linux/
│   ├── install.sh                 # instalador automatizado Ubuntu 22/24
│   ├── update.sh                  # atualizar mini PC já instalado
│   └── uninstall.sh
│
├── Dockerfile                     # multi-stage, imagem final ~76MB
├── docker-compose.yml             # com docker socket + volumes
├── entrypoint.sh                  # ajusta GID do docker socket
└── requirements.txt
```

### Stack

- **Backend:** Python 3.12 + FastAPI + httpx + pydantic-settings
- **Banco:** SQLite (usuários, sessões, histórico IA, modo manutenção)
- **PDF:** reportlab
- **Frontend:** Jinja2 + JS vanilla + marked.js (markdown da IA)
- **Auth:** bcrypt + itsdangerous (cookies HTTPOnly)
- **IA:** Anthropic SDK / OpenAI SDK / Google GenAI SDK / Ollama REST
- **Empacotamento:** Docker multi-stage
- **CI/CD:** GitHub Actions → ghcr.io
- **Acesso remoto:** Tailscale (mesh VPN)

---

## 3. Instalação no mini PC Linux

### Hardware recomendado

- CPU: Intel N100 ou superior
- RAM: 4 GB mínimo, 8 GB recomendado
- Disco: 64 GB SSD (uso real: ~5 GB)
- Rede: Ethernet Gigabit
- OS: **Ubuntu Server 24.04 LTS** (mínimo recomendado)

### Pré-requisitos no mini PC

1. Ubuntu Server 24.04 instalado, SSH habilitado
2. Usuário com permissão `sudo` (ex.: `jkcontrol`)
3. IP fixo na LAN (configurar via `/etc/netplan/`)

### Instalação automatizada (1 comando)

No SSH do mini PC:

```bash
sudo apt update && sudo apt install -y curl

curl -fsSL https://raw.githubusercontent.com/Jeffersonjkcontrol/relatorios-iot/main/deploy/linux/install.sh | sudo bash
```

O instalador faz tudo:
- ✅ Atualiza apt
- ✅ Instala Docker CE oficial
- ✅ Cria estrutura em `/opt/relatorios-iot`
- ✅ Configura firewall UFW (libera porta 8000 só da LAN `192.168.0.0/16`)
- ✅ Cria systemd service (autostart no boot)
- ✅ Configura backup diário automático (02:00, retém 14 dias)
- ✅ Baixa imagem Docker e sobe container

Tempo total: ~5-10 minutos.

### Variáveis opcionais

```bash
# Mudar porta do app:
PORT=80 curl -fsSL https://raw.githubusercontent.com/Jeffersonjkcontrol/relatorios-iot/main/deploy/linux/install.sh | sudo bash

# Mudar faixa da LAN no firewall:
LAN_CIDR=10.0.0.0/8 curl ... | sudo bash

# Instalar Tailscale junto:
TAILSCALE_AUTH_KEY=tskey-auth-xxx curl ... | sudo bash
```

### Renomear hostname para nome amigável (recomendado)

Em vez do hostname feio (ex.: `jkcontrol-mini-pc`), use algo curto que o cliente reconheça:

```bash
sudo hostnamectl set-hostname relatorios
sudo sed -i 's/jkcontrol-mini-pc/relatorios/g' /etc/hosts
sudo systemctl restart avahi-daemon
sudo systemctl restart relatorios-iot
```

Depois disso o cliente acessa via **mDNS** (zero config): `http://relatorios.local:8000`.

### Acesso final

| De onde | URL |
|---|---|
| **LAN da fábrica** (qualquer device) | `http://relatorios.local:8000` ⭐ |
| LAN pelo IP (fallback) | `http://192.168.0.123:8000` |
| Via Tailscale (você, de qualquer lugar) | `http://100.x.x.x:8000` ou `http://injequaly-relatorios-iot:8000` |
| Local no próprio mini PC | `http://localhost:8000` |

Login inicial: `admin` / `admin` (força trocar senha no primeiro acesso).

---

## 4. Configuração dos tokens (.env)

```bash
sudo nano /opt/relatorios-iot/.env
```

Conteúdo:

```env
# === Plataformas IoT ===
JKCONTROL_BASE_URL=https://jkcontrol.online
JKCONTROL_TOKEN=ag_seuTokenJKControlAqui

UBIDOTS_BASE_URL=https://industrial.api.ubidots.com
UBIDOTS_TOKEN=BBUS-seuTokenUbidotsAqui

# === IA (configure ao menos 1 provedor) ===
ANTHROPIC_API_KEY=sk-ant-...    # $5 grátis no signup
GOOGLE_API_KEY=AIza...          # free tier ~1500 req/dia (Gemini Flash)
OPENAI_API_KEY=                 # $5 grátis 90 dias (opcional)
OLLAMA_BASE_URL=                # http://localhost:11434 se rodar Ollama local

# === Servidor ===
HOST=0.0.0.0
PORT=8000

# === Sessão (TROQUE EM PRODUÇÃO!) ===
SESSION_SECRET=stringAleatoriaDe32CaracteresOuMais
```

**⚠️ NÃO use aspas em volta dos valores.** O app aceita, mas é confusão evitar.

Salvar (`Ctrl+O` Enter `Ctrl+X`) e reiniciar:

```bash
sudo systemctl restart relatorios-iot
```

### Obtendo os tokens

| Token | Onde gerar |
|---|---|
| `UBIDOTS_TOKEN` | https://industrial.ubidots.com → seu user → API Credentials → Tokens |
| `JKCONTROL_TOKEN` | https://jkcontrol.online → Tokens de Acesso → Criar token organizacional |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com → API Keys → Create Key |
| `GOOGLE_API_KEY` | https://aistudio.google.com/apikey → Create API key (free tier) |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys → Create new secret key |

---

## 5. Páginas do app

### 🏠 `/` — Relatórios

Consulta histórica e exportação. Fluxo:
1. Selecionar Grupo (opcional) → Dispositivo → Variável
2. Período (datetime-local) → "Visualizar"
3. Preview com estatísticas + tabela das últimas amostras
4. Botões **Baixar PDF** / **Baixar CSV**

**Detalhes:**
- Datalist auto-preenche conforme você digita
- Transformações automáticas (ex.: `ciclo` em ms → s)
- Context dos pontos (molde) vai pro CSV em colunas separadas (`ctx.molde`, etc.)
- PDF em landscape se houver context; em retrato se não

### ⚙️ `/oee` — OEE

Cálculo Overall Equipment Effectiveness.

**Inputs:**
- Máquina (dispositivo)
- Variável de ciclo (padrão: `ciclo`)
- Período início/fim
- **Ciclo ideal** (segundos) — parâmetro do molde
- **Refugo** (peças não conformes)
- **Filtro de outliers** (multiplicador do ciclo ideal, padrão 3×)

**Fórmulas:**
```
Disponibilidade = tempo_produzindo / tempo_planejado
Performance     = (ciclo_ideal × peças_totais) / tempo_produzindo
Qualidade       = peças_boas / peças_totais
OEE             = A × P × Q
```

**Filtro de outliers:** ciclos maiores que `N × ideal` viram "paradas" (não somam no tempo produzindo). Útil pra ignorar ciclos absurdos (paradas longas, problemas).

**Saída:** 4 KPI cards coloridos (verde ≥85%, amarelo ≥60%, vermelho abaixo), tabela detalhada e memorial de cálculo. Botão **Baixar PDF**.

### 📡 `/live` — Dashboard ao vivo

Grid de cards mostrando o status atual de cada máquina:

| Status | Critério |
|---|---|
| 🟢 **Ativo** | Último ciclo há menos de 2min |
| 🟡 **Ocioso** | Entre 2 e 10min |
| 🔴 **Parado** | Mais de 10min |
| ⚫ **Offline** | Sem dados na última hora |

**Polling:** 10 segundos. Cache backend de 8s evita martelar a API Ubidots.

**Mostra:** valor atual, média recente, tendência (↑↓→), molde corrente (do `context`), botão Pausar.

### 🔧 `/moldes` — Análise de moldes

Parser inteligente do `context.molde` (`"Pe.Front.Babel|#89-01617|#45seg"`).

**Tabela ordenada por peças totais:**
- Código do molde
- **Ciclo ideal** (extraído do `#45seg` no nome)
- **Ciclo médio real** observado
- **Desvio %** colorido (verde <5%, amarelo <15%, vermelho ≥15%)
- Em quantas máquinas o molde rodou
- Último uso

**Filtro de período:** hoje (24h), 7/30/90 dias.

### 🤖 `/ai` — Chat com IA

Sidebar de conversas anteriores + chat principal.

**Provedores disponíveis:** Anthropic Claude, OpenAI GPT, Google Gemini, Ollama (local).

**Modelos recomendados:**
- Claude Haiku 4.5 (~$0,005 por conversa, mais barato)
- Gemini 2.5 Flash (free tier generoso)
- GPT-4o-mini (~$0,002 por conversa)

**Tools que a IA pode chamar:**
- `list_platforms`, `list_groups`, `list_devices`, `list_variables`
- `summarize_variable` — estatísticas + 10 últimas amostras
- `compute_oee` — cálculo completo
- `compare_devices` — comparar várias máquinas
- `generate_report_link` — gera botão de download PDF/CSV

**Escopo limitado:** se você perguntar sobre clima, política, receitas, etc., ela recusa educadamente.

**Exemplos:**
- "Liste todas as injetoras"
- "Resumo de ciclo da inj82 nas últimas 24h"
- "Calcule OEE da inj80 hoje, ciclo ideal 60s, sem refugo"
- "Compare ciclo médio das inj81 e inj82 nesta semana"
- "Gere PDF de OEE da inj82 ontem"

### 👥 `/users` — Usuários (só gestor)

Listar, criar, excluir usuários.

**Papéis:**
- **Gestor:** acesso total (todas páginas + admin)
- **Operador:** somente leitura (Relatórios, OEE, IA, Live, Moldes)

### 🛠️ `/admin` — Painel admin (só gestor)

Veja [seção 7](#7-painel-admin).

### ⚙️ `/config` — Configurações (só gestor)

- Seleciona plataforma ativa (Ubidots ou JKControl) — fica salva no navegador
- Mostra status dos provedores de IA configurados
- Info do app (versão, fuso horário, etc.)

---

## 6. Acesso na LAN (mDNS / hostname amigável)

Dentro da fábrica, o cliente **não precisa decorar IP**. O Ubuntu vem com `avahi-daemon` que anuncia o hostname na rede local. Qualquer dispositivo conectado acessa pelo nome.

### Setup

Renomear hostname pra algo curto:
```bash
sudo hostnamectl set-hostname relatorios
sudo sed -i 's/<hostname-antigo>/relatorios/g' /etc/hosts
sudo systemctl restart avahi-daemon
```

### Acesso

De qualquer device da LAN:
```
http://relatorios.local:8000
```

**Compatibilidade:**

| Sistema | mDNS funciona? |
|---|---|
| Windows 10/11 | ✅ Nativo |
| macOS / iOS / iPadOS | ✅ Bonjour nativo |
| Linux (Ubuntu, etc.) | ✅ avahi |
| Android (recente) | ⚠️ Parcial — Chrome 113+ funciona; apps antigos podem não. App "Fing" da Play Store ajuda |

### Plano B: reserva DHCP (se mDNS não funcionar na rede do cliente)

Algumas redes corporativas bloqueiam mDNS. Nesse caso, a TI do cliente reserva um IP fixo no roteador:

1. Roteador → "Reserva DHCP" ou "Static Lease"
2. MAC do mini PC → IP livre (ex.: `192.168.0.50`)
3. Cliente acessa `http://192.168.0.50:8000`

### Plano C: bookmark no navegador

Independente da URL, ensine o cliente a salvar nos favoritos no primeiro acesso. Aí ele clica direto, sem digitar.

---

## 7. Acesso remoto via Tailscale

### O que é

Tailscale é uma "VPN mesh" gratuita. Cria uma rede privada virtual entre seus dispositivos com IPs `100.x.x.x`. Você acessa o mini PC de **qualquer lugar do mundo**, sem mexer no roteador da fábrica.

### Vantagens críticas (por que adotar)

- **Independência de IP local** — cliente troca de roteador, muda sub-rede, leva mini PC pra outra unidade → seu acesso continua igual
- **Sem port forward** — não precisa configurar nada no firewall do cliente
- **Funciona atrás de CGNAT** — operadoras 4G/5G que não dão IP público funcionam normalmente
- **Nome amigável (MagicDNS)** — `http://injequaly-relatorios-iot:8000` em vez de IP
- **SSH sem chaves** — `ssh jkcontrol@injequaly-relatorios-iot` direto
- **Logs de auditoria** — painel mostra quem entrou, quando, de onde
- **Plano gratuito até 100 dispositivos** — cobre dezenas de clientes

### Como funciona

```
[Seu celular]                            [Mini PC fábrica]
100.81.170.59  ←─ Tailscale mesh ─→     100.69.219.59
                                          ↓
                                      Roda o app em :8000
```

### Instalação no mini PC (se ainda não fez)

```bash
ssh jkcontrol@192.168.0.123

# Instala Tailscale:
curl -fsSL https://tailscale.com/install.sh | sudo sh

# Conecta (vai mostrar uma URL pra autorizar no navegador):
sudo tailscale up --hostname=injequaly-relatorios-iot --ssh

# Após autorizar, ver o IP:
sudo tailscale ip -4
```

### Instalação nos seus dispositivos

| Dispositivo | Como |
|---|---|
| **Windows / Mac / Linux** | Baixe em https://tailscale.com/download |
| **Android** | Play Store → "Tailscale" |
| **iPhone / iPad** | App Store → "Tailscale" |

Em todos: faz login com a mesma conta (jkcontrol.com.br tenant).

### Acesso

Depois disso, em qualquer dispositivo seu:
- `http://100.69.219.59:8000` — pelo IP
- `http://injequaly-relatorios-iot:8000` — pelo nome (MagicDNS)

Funciona em 4G do celular, WiFi de casa, hotel, etc.

### Painel Tailscale

https://login.tailscale.com/admin/machines

Mostra todos os dispositivos conectados, último acesso, e permite revogar acesso individual.

### Segurança

- Tráfego **end-to-end criptografado** (WireGuard)
- Quem não tem Tailscale instalado **não consegue acessar** o IP `100.x.x.x`
- Login do app ainda é necessário — Tailscale só dá o "túnel"
- Operadores comuns não precisam de Tailscale — acessam só pela LAN local

### Compartilhar acesso com o cliente (opcional)

Se o gerente do cliente quiser acessar de fora da fábrica também:

1. No painel Tailscale → **Machines** → clica no device do cliente
2. Botão **"Share..."** (ou três pontinhos `...` → Share)
3. Coloca o e-mail dele
4. Ele recebe convite → cria conta Tailscale → instala app no celular/PC
5. Acessa pelo IP `100.x.x.x` ou nome

⚠️ Você compartilha **APENAS aquele device** — ele não vê seus outros clientes.

### Alternativa: Tailscale Funnel (expor na internet)

Se quiser que o app fique acessível **publicamente na internet** com HTTPS (sem cliente precisar de Tailscale):

```bash
sudo tailscale funnel 8000
```

Gera uma URL pública tipo `https://injequaly-relatorios-iot.taild123.ts.net`.

⚠️ Cuidado: app fica exposto na web — confie nas senhas dos usuários.

### Alternativa: Cloudflare Tunnel (domínio próprio + HTTPS)

Para URL personalizada (ex.: `https://relatorios.injequaly.com.br`):

1. Cria conta gratuita em https://cloudflare.com
2. Instala `cloudflared` no mini PC
3. Cria tunnel apontando pra `localhost:8000`
4. Configura subdomain no Cloudflare DNS

Combina bem com **Cloudflare Access** (autenticação por e-mail antes do app).

---

## 8. Painel Admin

Acessível em `/admin` apenas pra usuários **gestor**.

### 1️⃣ Modo manutenção

Ativa uma tela `/maintenance` pra operadores. Você (gestor) continua usando normal.

**Como usar:**
1. Digita uma mensagem (ex.: "Sistema em manutenção até 18h")
2. Clica "Ativar manutenção"
3. Operadores que tentarem acessar caem na tela de splash (auto-refresh a cada 30s)
4. Quando desliga, todos voltam ao normal

**Casos de uso:**
- Atualização rápida (`docker compose pull`)
- Mudança de tokens
- Cliente em atraso (bloqueia sem quebrar)
- Investigar bug

### 2️⃣ Status do container

Cards em tempo real:
- Estado (`running ✓`)
- Saúde (`healthy`)
- CPU %
- Memória (MB + %)
- Restarts (quantidade)
- Iniciado em (data/hora)

### 3️⃣ Ações

- **↻ Reiniciar container** — restart em ~10s sem precisar SSH
- **💾 Backup agora** — backup manual do volume
- **📋 Ver logs** — últimas 200 linhas em terminal escuro

### 4️⃣ Backups recentes

Lista dos arquivos `.tar.gz` em `/opt/relatorios-iot/backups/` com tamanho e data.

---

## 9. IA — provedores e uso

### Comparação de custos

| Provedor / Modelo | Custo / conversa típica | Free tier |
|---|---|---|
| **Claude Haiku 4.5** ⭐ | ~$0,005 (½ centavo) | $5 grátis no signup (~1000 conversas) |
| Claude Sonnet 4.5 | ~$0,03 | — |
| GPT-4o-mini | ~$0,002 | $5 grátis 90 dias |
| GPT-4o | ~$0,02 | — |
| **Gemini 2.5 Flash** ⭐ | ~$0,001 ou grátis | Free tier ~1500 req/dia |
| Gemini 2.5 Pro | ~$0,01 | Free tier limitado |
| Ollama (local) | $0 | Roda 100% local, qualidade depende do modelo |

### Recomendação

- **Começar:** Gemini 2.5 Flash (free tier alto, basta criar conta Google)
- **Produção robusta:** Claude Haiku 4.5 (mais confiável em tool use)
- **Análises complexas:** Claude Sonnet 4.5
- **Total privacidade:** Ollama com Llama 3.1 ou Qwen 2.5

### Configuração

No `.env`, configure ao menos uma chave. Reinicie o serviço.

Na página `/config`, você verá quais provedores estão ativos.

Na página `/ai`, escolha provedor + modelo no dropdown do topo.

### Como funciona o tool use

1. Você pergunta em português
2. A IA decide chamar uma "tool" (ex.: `list_devices`)
3. Backend executa a tool e devolve JSON
4. A IA recebe o resultado e formula a resposta
5. Pode chamar várias tools em sequência se a pergunta exigir

**Tudo isso é transparente — você só vê a resposta final.**

### Histórico de conversas

Salvo em SQLite. Sidebar à esquerda lista conversas anteriores (clique pra reabrir). Botão "+ Nova conversa" pra começar do zero.

Cada conversa fica até você apagá-la manualmente (botão ×).

---

## 10. Manutenção

### Backup automático

Configurado pelo `install.sh`:
- Diariamente às **02:00**
- Mantém **14 dias** de backups
- Armazena em `/opt/relatorios-iot/backups/relatorios-AAAAMMDD_HHMMSS.tar.gz`
- Contém: SQLite (conversas IA, usuários, settings) + relatórios em `output/`

**Verificar status:**
```bash
sudo systemctl status relatorios-iot-backup.timer
ls -lh /opt/relatorios-iot/backups/
```

### Backup manual

```bash
sudo /usr/local/bin/relatorios-iot-backup
```

Ou via painel `/admin` → botão "Backup agora".

### Restaurar backup

```bash
sudo systemctl stop relatorios-iot
docker run --rm \
    -v relatorios-iot-data:/data \
    -v /opt/relatorios-iot/backups:/backup \
    alpine sh -c "rm -rf /data/* && tar xzf /backup/relatorios-AAAAMMDD_HHMMSS.tar.gz -C /data"
sudo systemctl start relatorios-iot
```

Substitua `AAAAMMDD_HHMMSS` pelo nome do arquivo desejado.

### Atualizar para versão nova

Quando há nova versão publicada no GitHub:

**Opção A — script de update (recomendado):**
```bash
curl -fsSL https://raw.githubusercontent.com/Jeffersonjkcontrol/relatorios-iot/main/deploy/linux/update.sh | sudo bash
```

**Opção B — manual:**
```bash
cd /opt/relatorios-iot
sudo docker compose pull
sudo docker compose down
sudo docker compose up -d
```

Downtime: ~10 segundos.

### Logs

```bash
# Logs em tempo real:
cd /opt/relatorios-iot
sudo docker compose logs -f

# Últimas 200 linhas:
sudo docker compose logs --tail 200
```

Ou via painel `/admin` → "Ver logs".

### Recursos do servidor

```bash
# CPU/RAM do container:
docker stats relatorios-iot --no-stream

# Espaço em disco:
df -h /
du -sh /opt/relatorios-iot
```

### Reiniciar serviço

```bash
sudo systemctl restart relatorios-iot
```

Ou via painel `/admin` → "Reiniciar container".

### Parar serviço

```bash
sudo systemctl stop relatorios-iot
```

---

## 11. Troubleshooting

### App não responde / página não abre

1. Verifique se o container está rodando:
   ```bash
   sudo systemctl status relatorios-iot
   docker ps | grep relatorios-iot
   ```
2. Veja logs:
   ```bash
   cd /opt/relatorios-iot && sudo docker compose logs --tail 100
   ```
3. Tente reiniciar:
   ```bash
   sudo systemctl restart relatorios-iot
   ```

### Token inválido / erro 401

1. Edite o `.env`:
   ```bash
   sudo nano /opt/relatorios-iot/.env
   ```
2. **Confira que os tokens NÃO estão entre aspas**
3. Reinicie:
   ```bash
   sudo systemctl restart relatorios-iot
   ```

### Erro 429 "Too Many Requests"

Rate limit da API. O app já tem cache de 5min e retry com backoff. Se persistir:
- Outras integrações estão usando o mesmo token? Aguarde alguns minutos
- Token Ubidots free tier limita ~4 req/s — pode atingir em horários de pico

### IA não responde

- Verifique se há chave de provedor configurada (`/config`)
- Verifique se há crédito no provedor escolhido
- Recarregue a página, abra nova conversa

### Esqueci a senha do admin

Acesso ao SQLite via container:
```bash
docker exec -it relatorios-iot python -c "
from app.auth.db import change_password, get_user_by_username
res = get_user_by_username('admin')
change_password(res[0].id, 'novaSenhaForte123')
print('Senha do admin redefinida para: novaSenhaForte123')
"
```

### Espaço em disco cheio

```bash
# Limpa imagens Docker antigas:
sudo docker image prune -af

# Apaga backups manuais (mantém os automáticos do cron):
find /opt/relatorios-iot/backups -name "*.tar.gz" -mtime +30 -delete
```

### Container fica reiniciando

```bash
# Ver os últimos logs antes do crash:
sudo docker compose logs --tail 200

# Se é problema de configuração, edite .env e restart
# Se persistir, abra issue no GitHub
```

### `relatorios.local` não abre no navegador

**No PC do cliente, testar resolução:**

```bash
# Windows (cmd):
ping relatorios.local

# Linux/Mac:
ping relatorios.local
```

Se o ping não resolve:

1. **avahi-daemon parou?** No mini PC:
   ```bash
   sudo systemctl status avahi-daemon
   sudo systemctl enable --now avahi-daemon
   sudo systemctl restart avahi-daemon
   ```

2. **Wi-Fi com isolamento de clientes?** Alguns roteadores bloqueiam comunicação entre dispositivos por padrão. TI precisa desativar "AP isolation" ou "Client isolation". Alternativa: conectar via cabo.

3. **Android antigo?** mDNS não funciona em Chrome Android <113. Solução: instalar app "Fing" da Play Store, ou usar IP direto.

4. **Fallback:** o IP `192.168.0.123:8000` sempre funciona enquanto não trocar de IP.

### Tailscale offline no painel

No mini PC:
```bash
sudo tailscale status
sudo tailscale up    # reconecta
sudo systemctl restart tailscaled
```

Se Tailscale "perdeu" a conexão (raro), use `sudo tailscale up --reset` pra limpar e reautenticar.

---

## 12. Stack técnico

### Backend (Python)

- **FastAPI 0.115** — framework HTTP async
- **httpx** — cliente HTTP (com cache compartilhado e retry 429)
- **pydantic-settings** — leitura do `.env`
- **bcrypt 4.x** — hash de senhas
- **itsdangerous** — assinatura de cookies de sessão
- **reportlab 4.x** — geração de PDFs
- **anthropic-sdk / openai-sdk / google-genai** — IA
- **tzdata** — fuso horário (necessário no Windows)

### Infraestrutura

- **Docker** multi-stage (Python 3.12-slim, imagem final ~76MB)
- **SQLite** — banco único pra conversas IA, usuários, settings
- **systemd** — autostart no boot
- **UFW** — firewall (libera porta 8000 só da LAN)
- **Tailscale** — VPN mesh pra acesso remoto

### CI/CD

- **GitHub Actions** — build automático em cada push pra main
- **ghcr.io** — registry da imagem (público)

---

## 13. Cheatsheet de comandos

### Status e diagnóstico

```bash
sudo systemctl status relatorios-iot                # status do serviço
docker ps | grep relatorios-iot                     # container rodando?
docker stats relatorios-iot --no-stream             # CPU/RAM
cd /opt/relatorios-iot && sudo docker compose logs -f  # logs em tempo real
```

### Operação

```bash
sudo systemctl restart relatorios-iot               # reiniciar
sudo systemctl stop relatorios-iot                  # parar
sudo systemctl start relatorios-iot                 # iniciar
sudo nano /opt/relatorios-iot/.env                  # editar config
```

### Backup

```bash
sudo /usr/local/bin/relatorios-iot-backup           # backup manual
ls -lh /opt/relatorios-iot/backups/                 # listar backups
```

### Atualização

```bash
curl -fsSL https://raw.githubusercontent.com/Jeffersonjkcontrol/relatorios-iot/main/deploy/linux/update.sh | sudo bash
```

### Tailscale

```bash
sudo tailscale ip -4                                # IP 100.x.x.x
sudo tailscale status                               # quem está conectado
sudo tailscale up                                   # reconectar
sudo tailscale logout                               # desconectar (cuidado!)
sudo tailscale funnel 8000                          # expor publicamente na internet
```

### Hostname / mDNS

```bash
hostname                                            # ver hostname atual
sudo hostnamectl set-hostname relatorios            # mudar hostname
sudo systemctl restart avahi-daemon                 # anunciar nome novo na LAN
ping relatorios.local                               # testar resolução mDNS de outro device
```

### Docker compose direto

```bash
cd /opt/relatorios-iot
sudo docker compose pull                            # baixar nova imagem
sudo docker compose up -d                           # subir
sudo docker compose down                            # parar
sudo docker compose ps                              # status
sudo docker compose logs --tail 200                 # logs
```

### Acessos

| O quê | URL |
|---|---|
| **App via mDNS (cliente, na LAN)** ⭐ | `http://relatorios.local:8000` |
| App pela LAN (IP) | `http://192.168.0.123:8000` |
| App via Tailscale (você) | `http://100.69.219.59:8000` |
| App via nome Tailscale (você) ⭐ | `http://injequaly-relatorios-iot:8000` |
| SSH local | `ssh jkcontrol@relatorios.local` |
| SSH via Tailscale | `ssh jkcontrol@injequaly-relatorios-iot` |
| GitHub repo | https://github.com/Jeffersonjkcontrol/relatorios-iot |
| Painel Tailscale | https://login.tailscale.com/admin/machines |
| GitHub Actions | https://github.com/Jeffersonjkcontrol/relatorios-iot/actions |
| Imagem Docker | `ghcr.io/jeffersonjkcontrol/relatorios-iot:latest` |

---

## 14. Playbook de entrega para cliente novo

Passo a passo reproduzível pra cada nova instalação. Tempo total: ~30-45 minutos.

### Pré-instalação (você prepara)

1. Compra/separa mini PC (Beelink S12, Mele Quieter, ou similar — ver seção 3)
2. Cria pendrive bootável Ubuntu Server 24.04 LTS
3. (Opcional) Pré-gera Tailscale auth key descartável em https://login.tailscale.com/admin/settings/keys

### No cliente (presencial ou remoto via VNC)

```
[1] Instala Ubuntu Server 24.04
    - Hostname temporário: qualquer (vai trocar depois)
    - Habilita SSH durante a instalação
    - Cria usuário 'jkcontrol' com senha forte

[2] Conecta na rede do cliente (cabo de rede ou Wi-Fi)

[3] Descobre IP via DHCP
    - Roda 'ip a' no console local OU
    - Procura no painel do roteador

[4] SSH no mini PC
    ssh jkcontrol@<ip-descoberto>

[5] Instala curl
    sudo apt update && sudo apt install -y curl

[6] Roda o instalador
    curl -fsSL https://raw.githubusercontent.com/Jeffersonjkcontrol/relatorios-iot/main/deploy/linux/install.sh | sudo bash

[7] Renomeia o hostname
    sudo hostnamectl set-hostname relatorios
    sudo sed -i 's/<hostname-antigo>/relatorios/g' /etc/hosts
    sudo systemctl restart avahi-daemon
    sudo systemctl restart relatorios-iot
    exit  # sai e reconecta com nome novo

[8] Configura tokens
    ssh jkcontrol@relatorios.local
    sudo nano /opt/relatorios-iot/.env
    # cola UBIDOTS_TOKEN, JKCONTROL_TOKEN, GOOGLE_API_KEY (ou outras)
    sudo systemctl restart relatorios-iot

[9] Instala Tailscale (acesso remoto)
    curl -fsSL https://tailscale.com/install.sh | sudo sh
    sudo tailscale up --hostname=<cliente>-relatorios-iot --ssh
    # autoriza no navegador via URL que aparecer

[10] Primeiro login no app
     Abre http://relatorios.local:8000
     admin / admin → troca senha forte

[11] Cria usuário gestor real
     Vai em /users → Cria 'gestor' / senha forte / papel gestor
     (mantém o admin como backup ou exclui se preferir)

[12] (Opcional) Cria operadores
     Mesmo passo, papel 'operador'
```

### Entrega final

Imprime/envia ao cliente um cartão:

```
RELATÓRIOS IoT — <Nome do Cliente>
─────────────────────────────────────

🌐 Acesso (qualquer dispositivo da rede da fábrica):
    http://relatorios.local:8000

🔑 Login fornecido em separado.

📞 Suporte:
   Jefferson Piccirillo
   jefferson.piccirillo@jkcontrol.com.br
```

E pra você fica:
```
Acesso remoto: http://<cliente>-relatorios-iot:8000
SSH:           ssh jkcontrol@<cliente>-relatorios-iot
```

### Checklist de validação (antes de sair)

- [ ] App responde em `http://relatorios.local:8000` (mDNS)
- [ ] Login funciona (admin trocou senha)
- [ ] Página `/relatorios` lista dispositivos (token Ubidots OK)
- [ ] Página `/live` mostra cards atualizando
- [ ] Página `/ai` aceita perguntas (provedor IA OK)
- [ ] Tailscale aparece no painel com nome correto
- [ ] SSH via Tailscale funciona do seu notebook
- [ ] Container reinicia limpo (`sudo systemctl restart relatorios-iot`)

---

## 15. Roadmap

### Próximas features sugeridas

- 🔔 **Alertas via Telegram** — IA monitora e avisa quando OEE cai
- 📺 **Modo kiosk** — tela cheia rotativa pra TV de chão de fábrica
- 🔄 **Comparação entre turnos** — agrupar dados manhã/tarde/noite
- 📊 **Excel formatado** — `.xlsx` com fórmulas, formatação condicional
- 🔐 **Kill-switch online** — bloqueio remoto pra modelo SaaS
- 🔄 **Atualização via UI** — botão "Atualizar app" no painel admin
- 🗄️ **Restaurar backup via UI** — sem precisar SSH
- 📈 **Gráficos no preview** — Chart.js inline nos relatórios

### Como contribuir / pedir features

Abrir issue em https://github.com/Jeffersonjkcontrol/relatorios-iot/issues ou contato direto.

---

## 📞 Suporte

**Jefferson Piccirillo**
✉️ jefferson.piccirillo@jkcontrol.com.br
🌐 https://jkcontrol.com.br

**Versão da documentação:** 1.6.0 — Maio/2026

### Changelog

- **1.6.0** — Hostname amigável `relatorios.local` (mDNS), playbook de entrega, troubleshooting expandido, compartilhamento Tailscale, Tailscale Funnel/Cloudflare Tunnel
- **1.5.0** — Versão inicial com login multi-usuário, dashboard ao vivo, moldes, painel admin, Tailscale
