#!/usr/bin/env bash
# Desinstalador completo do Relatorios IoT
set -e
APP_DIR="/opt/relatorios-iot"
APP_NAME="relatorios-iot"

[[ $EUID -eq 0 ]] || { echo "Execute com sudo"; exit 1; }

echo "Isto vai REMOVER:"
echo "  - Service systemd $APP_NAME"
echo "  - Timer de backup"
echo "  - Container e imagem Docker"
echo "  - Regras de firewall criadas"
echo "  - Volume de dados (perdera o historico de IA)"
echo "  - Diretorio $APP_DIR (incluindo backups)"
echo
read -rp "Tem certeza? (digite SIM): " conf
[[ "$conf" == "SIM" ]] || { echo "Cancelado"; exit 0; }

systemctl disable --now ${APP_NAME}.service 2>/dev/null || true
systemctl disable --now ${APP_NAME}-backup.timer 2>/dev/null || true
rm -f /etc/systemd/system/${APP_NAME}.service /etc/systemd/system/${APP_NAME}-backup.service /etc/systemd/system/${APP_NAME}-backup.timer
rm -f /usr/local/bin/${APP_NAME}-backup
systemctl daemon-reload

cd "$APP_DIR" 2>/dev/null && docker compose down -v 2>/dev/null || true
docker volume rm ${APP_NAME}-data 2>/dev/null || true
docker image rm $(docker images --filter "reference=*relatorios-iot*" -q) 2>/dev/null || true

ufw delete allow from 192.168.0.0/16 to any port 8000 2>/dev/null || true

rm -rf "$APP_DIR"

echo "Removido completamente."
