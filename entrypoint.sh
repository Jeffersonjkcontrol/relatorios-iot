#!/bin/bash
# Entrypoint que ajusta o GID do grupo docker pra casar com o socket montado,
# permitindo que o user 'app' (não-root) acesse /var/run/docker.sock.
set -e

DOCKER_SOCK=/var/run/docker.sock

if [[ -S "$DOCKER_SOCK" ]]; then
    SOCK_GID=$(stat -c '%g' "$DOCKER_SOCK")
    if [[ "$SOCK_GID" != "0" ]]; then
        # Cria grupo 'docker' com o GID do socket (se ainda não existir)
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -g "$SOCK_GID" docker 2>/dev/null || true
        fi
        GROUP_NAME=$(getent group "$SOCK_GID" | cut -d: -f1)
        # Adiciona o usuário 'app' ao grupo
        if id app >/dev/null 2>&1 && [[ -n "$GROUP_NAME" ]]; then
            usermod -aG "$GROUP_NAME" app 2>/dev/null || true
        fi
    fi
fi

# Drop pra usuário 'app' e executa o comando original (gosu = setuid limpo)
if command -v gosu >/dev/null 2>&1 && [[ "$(id -u)" = "0" ]]; then
    exec gosu app "$@"
else
    exec "$@"
fi
