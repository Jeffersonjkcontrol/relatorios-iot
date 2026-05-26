#!/usr/bin/env bash
# Atualiza um mini PC ja instalado para a versao mais recente:
# 1. Atualiza docker-compose.yml (necessario quando muda volumes/montagens)
# 2. Faz docker compose pull
# 3. docker compose up -d (restart com a imagem nova)
# 4. Mostra status final
#
# Uso no mini PC:
#   curl -fsSL https://raw.githubusercontent.com/Jeffersonjkcontrol/relatorios-iot/main/deploy/linux/update.sh | sudo bash
# OU:
#   sudo bash update.sh

set -euo pipefail

APP_DIR="/opt/relatorios-iot"
APP_NAME="relatorios-iot"
IMAGE="${IMAGE:-ghcr.io/jeffersonjkcontrol/relatorios-iot:latest}"
PORT="${PORT:-8000}"

log()  { echo -e "\n\033[1;34m[*]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
err()  { echo -e "\033[1;31m[X]\033[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Execute com sudo"
[[ -d "$APP_DIR" ]] || err "$APP_DIR não existe. Use install.sh primeiro."

cd "$APP_DIR"

# Backup do compose atual
if [[ -f docker-compose.yml ]]; then
    cp docker-compose.yml docker-compose.yml.bak.$(date +%Y%m%d_%H%M%S)
    ok "Backup do docker-compose.yml salvo"
fi

# Garante que o diretório de backups existe (necessário pra montagem readonly)
mkdir -p "$APP_DIR/backups"

# Regenera o docker-compose.yml com as montagens novas (Docker socket + backups)
log "Atualizando docker-compose.yml"
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
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - $APP_DIR/backups:/app/backups:ro
    environment:
      - CONTAINER_NAME=$APP_NAME
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
ok "docker-compose.yml regenerado"

log "Baixando nova imagem (pode levar alguns segundos)"
docker compose pull

log "Reiniciando o container com a versão nova"
docker compose down 2>&1 | tail -3 || true
docker compose up -d

sleep 4

log "Status final"
docker compose ps
echo
IP=$(hostname -I | awk '{print $1}')
ok "Atualização concluída!"
echo "  Acesse: http://$IP:$PORT"
if command -v tailscale &>/dev/null; then
    TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
    [[ -n "$TS_IP" ]] && echo "  Tailscale: http://$TS_IP:$PORT"
fi
echo "  Logs:  cd $APP_DIR && sudo docker compose logs -f"
echo
warn "Se este é o primeiro update com o painel /admin:"
echo "    1. Login com sua conta gestor em http://$IP:$PORT/login"
echo "    2. Veja o novo menu 'Admin' no topo"
echo "    3. Teste a página /admin (status do container, modo manutenção, etc.)"
