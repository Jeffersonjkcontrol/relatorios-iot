#!/usr/bin/env bash
# ================================================================
# Instalador do Relatorios IoT para Ubuntu 22.04 / 24.04 LTS
# Mini PC industrial - operacao 24/7
#
# O QUE FAZ:
#   1. Instala Docker CE oficial (nao o snap)
#   2. Cria diretorio /opt/relatorios-iot
#   3. Copia docker-compose.yml + .env.example
#   4. Configura UFW (firewall) liberando porta 8000 so na LAN
#   5. Instala systemd service para autostart no boot
#   6. Configura backup diario automatico (mantem 14 dias)
#
# USO:
#   curl -fsSL https://raw.githubusercontent.com/<seu-user>/<repo>/main/deploy/linux/install.sh | sudo bash
#   OU
#   sudo bash install.sh
# ================================================================
set -euo pipefail

APP_DIR="/opt/relatorios-iot"
APP_NAME="relatorios-iot"
LAN_CIDR="${LAN_CIDR:-192.168.0.0/16}"   # override: LAN_CIDR=10.0.0.0/8 bash install.sh
IMAGE="${IMAGE:-ghcr.io/jeffersonjkcontrol/relatorios-iot:latest}"
PORT="${PORT:-8000}"

# ----------------------------------------------------------------
log()  { echo -e "\n\033[1;34m[*]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
err()  { echo -e "\033[1;31m[X]\033[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Execute com sudo"

# ----------------------------------------------------------------
log "Atualizando sistema"
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg ufw cron

# ----------------------------------------------------------------
log "Instalando Docker CE oficial (se necessario)"
if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  ok "Docker instalado: $(docker --version)"
else
  ok "Docker ja instalado: $(docker --version)"
fi

# ----------------------------------------------------------------
log "Criando estrutura em $APP_DIR"
mkdir -p "$APP_DIR" "$APP_DIR/backups"
cd "$APP_DIR"

if [[ ! -f docker-compose.yml ]]; then
  cat > docker-compose.yml <<COMPOSE
services:
  relatorios:
    image: $IMAGE
    container_name: $APP_NAME
    restart: unless-stopped
    ports:
      - "$PORT:8000"
    env_file:
      - .env
    volumes:
      - data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).status == 200 else 1)"]
      interval: 30s
      timeout: 5s
      start_period: 15s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  data:
    name: ${APP_NAME}-data
COMPOSE
  ok "docker-compose.yml criado"
fi

if [[ ! -f .env ]]; then
  cat > .env <<'ENVF'
# === Credenciais Ubidots ===
JKCONTROL_BASE_URL=https://jkcontrol.online
JKCONTROL_TOKEN=

UBIDOTS_BASE_URL=https://industrial.api.ubidots.com
UBIDOTS_TOKEN=

# === IA (opcional - veja README-DEPLOY.md) ===
GOOGLE_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

HOST=0.0.0.0
PORT=8000
ENVF
  chmod 600 .env
  warn ".env criado vazio - edite com seus tokens: sudo nano $APP_DIR/.env"
fi

# ----------------------------------------------------------------
log "Configurando firewall UFW"
if ! ufw status | grep -q "Status: active"; then
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow ssh
  ufw --force enable
fi
ufw allow from "$LAN_CIDR" to any port "$PORT" proto tcp comment "$APP_NAME LAN"
ok "Firewall: porta $PORT liberada apenas para $LAN_CIDR"

# ----------------------------------------------------------------
log "Instalando systemd service (autostart no boot)"
cat > /etc/systemd/system/${APP_NAME}.service <<UNIT
[Unit]
Description=Relatorios IoT (Docker Compose)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose pull && /usr/bin/docker compose up -d
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ${APP_NAME}.service
ok "Service ${APP_NAME}.service habilitado"

# ----------------------------------------------------------------
log "Configurando backup automatico diario (02:00, retencao 14 dias)"
cat > /usr/local/bin/${APP_NAME}-backup <<'BKP'
#!/usr/bin/env bash
set -e
APP_DIR=/opt/relatorios-iot
BACKUP_DIR=$APP_DIR/backups
STAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"
docker run --rm -v relatorios-iot-data:/data -v "$BACKUP_DIR:/backup" \
    alpine tar czf "/backup/relatorios-${STAMP}.tar.gz" -C /data .
find "$BACKUP_DIR" -name "relatorios-*.tar.gz" -mtime +14 -delete
echo "Backup OK: $BACKUP_DIR/relatorios-${STAMP}.tar.gz"
BKP
chmod +x /usr/local/bin/${APP_NAME}-backup

cat > /etc/systemd/system/${APP_NAME}-backup.service <<'UNIT'
[Unit]
Description=Backup do volume Relatorios IoT
[Service]
Type=oneshot
ExecStart=/usr/local/bin/relatorios-iot-backup
UNIT
cat > /etc/systemd/system/${APP_NAME}-backup.timer <<'TIMER'
[Unit]
Description=Backup diario as 02:00
[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true
[Install]
WantedBy=timers.target
TIMER
systemctl daemon-reload
systemctl enable --now ${APP_NAME}-backup.timer
ok "Backup automatico ativado (proximo: $(systemctl list-timers --no-pager | grep ${APP_NAME}-backup | awk '{print $1, $2}'))"

# ----------------------------------------------------------------
log "Subindo o app (1a vez pode demorar pra baixar a imagem)"
cd "$APP_DIR"
docker compose pull 2>&1 | tail -5 || warn "Pull falhou - verifique se a imagem $IMAGE existe ou troque IMAGE= no install.sh"
docker compose up -d
sleep 3

# ----------------------------------------------------------------
echo
echo "================================================================"
ok "INSTALACAO CONCLUIDA"
echo "================================================================"
echo
echo "  Aplicacao: http://$(hostname -I | awk '{print $1}'):$PORT"
echo "  Local:     http://localhost:$PORT"
echo
echo "  Configuracao:    sudo nano $APP_DIR/.env"
echo "  Logs:            cd $APP_DIR && sudo docker compose logs -f"
echo "  Restart:         sudo systemctl restart ${APP_NAME}"
echo "  Atualizar:       cd $APP_DIR && sudo docker compose pull && sudo docker compose up -d"
echo "  Backup manual:   sudo /usr/local/bin/${APP_NAME}-backup"
echo "  Listar backups:  ls -lh $APP_DIR/backups/"
echo "  Status:          sudo systemctl status ${APP_NAME}"
echo
warn "Nao esqueca de editar o .env e reiniciar:"
echo "    sudo nano $APP_DIR/.env"
echo "    sudo systemctl restart ${APP_NAME}"
