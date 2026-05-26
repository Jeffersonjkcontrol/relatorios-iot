# ========================================================================
# Multi-stage build do app Relatorios IoT
# Imagem final ~200MB, sem build tools, sem cache de pip
# ========================================================================

# --- Stage 1: builder (instala deps em diretorio isolado) ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .

RUN pip install --no-cache-dir --target=/install -r requirements.txt


# --- Stage 2: runtime ---
FROM python:3.12-slim

# Metadata
LABEL org.opencontainers.image.title="Relatorios IoT" \
      org.opencontainers.image.description="App de relatorios, OEE e IA para plataformas Ubidots / NEXUS CORE" \
      org.opencontainers.image.vendor="JKControl" \
      org.opencontainers.image.licenses="Proprietary"

WORKDIR /app

# Copia deps do builder
COPY --from=builder /install /usr/local/lib/python3.12/site-packages

# Codigo da app (so o necessario - vide .dockerignore)
COPY app/ ./app/

# Instala gosu pra drop privileges limpo no entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

# Cria diretorio de dados (volume sera montado aqui)
RUN mkdir -p /app/data /app/data/output /app/backups

# Variaveis de ambiente
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/app/data

# Usuario nao-root (seguranca). NÃO usa USER aqui - entrypoint faz o drop.
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app

# Entrypoint ajusta GID do grupo docker (se /var/run/docker.sock montado)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

# Healthcheck: GET / deve responder 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).status == 200 else 1)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
