# Deploy do Relatorios IoT (Docker)

App de relatorios, OEE e IA para plataformas Ubidots Industrial / NEXUS CORE (jkcontrol.online).
Roda em qualquer servidor com Docker. Recomendado: servidor local da fabrica acessivel na LAN.

## Requisitos no servidor do cliente

- Docker 24+ ou Docker Desktop
- Docker Compose v2 (vem junto)
- 512MB RAM, 1 vCPU (uso tipico bem abaixo disso)
- 1GB de disco (imagem + historico)
- Porta 8000 liberada na LAN

## Instalacao (TI do cliente, ~5 minutos)

### 1. Pegue os arquivos do app
```bash
# Via git:
git clone <repo-url> relatorios-iot
cd relatorios-iot

# OU baixe o ZIP e extraia em qualquer pasta
```

### 2. Configure as credenciais
```bash
cp .env.example .env
nano .env   # ou notepad .env no Windows
```

Preencha:
- `JKCONTROL_TOKEN` ou `UBIDOTS_TOKEN` (pelo menos 1)
- `GOOGLE_API_KEY` (opcional, para IA - tier gratuito em https://aistudio.google.com/apikey)

### 3. Suba o container
```bash
docker compose up -d
```

Na 1a vez, o Docker vai baixar a imagem base do Python (~120MB) e instalar as deps (~2 min).
A partir da 2a vez, sobe em 5 segundos.

### 4. Acesse
```
http://<IP-DO-SERVIDOR>:8000
```

Por exemplo `http://192.168.1.10:8000` se o servidor tem esse IP na LAN. Operadores e gerentes acessam pelo navegador de qualquer PC da rede.

## Operacao do dia a dia

### Ver logs ao vivo
```bash
docker compose logs -f
```

### Reiniciar
```bash
docker compose restart
```

### Parar
```bash
docker compose down
```

### Atualizar para nova versao
```bash
docker compose pull              # baixa a nova imagem
docker compose up -d             # sobe (downtime ~5s)
```

### Backup do historico de IA
O SQLite e os relatorios ficam no volume `relatorios-iot-data`. Pra backup:
```bash
docker run --rm -v relatorios-iot-data:/data -v $(pwd):/backup \
    alpine tar czf /backup/relatorios-backup-$(date +%F).tar.gz -C /data .
```

### Mover pra outro servidor
1. Backup do volume (acima)
2. Copie a pasta com `docker-compose.yml` + `.env` + tar do volume
3. No novo servidor: `docker compose up -d`
4. Restaure o volume: `docker run --rm -v relatorios-iot-data:/data -v $(pwd):/backup alpine tar xzf /backup/relatorios-backup-XXX.tar.gz -C /data`

## Customizacoes possiveis

### Mudar a porta
Edite `docker-compose.yml`:
```yaml
ports:
  - "9000:8000"   # vai acessar via porta 9000
```

### Usar HTTPS / dominio interno
Coloque um reverse proxy na frente:
- **Caddy** (mais simples, HTTPS automatico)
- **Traefik** (mais flexivel)
- **Nginx** (mais tradicional)

Exemplo Caddyfile (1 linha):
```
relatorios.minhaempresa.local {
    reverse_proxy localhost:8000
}
```

### Limitar acesso por IP na LAN
Configure no firewall do servidor (Windows Defender / iptables / pfSense), nao no Docker.

## Troubleshooting

### Container nao sobe
```bash
docker compose logs            # ver erro
docker compose ps              # status
```

### Erro "Address already in use"
Outra coisa esta usando a porta 8000. Mude para outra:
```yaml
ports:
  - "8080:8000"
```

### Performance baixa
Verifique CPU/RAM do servidor:
```bash
docker stats relatorios-iot
```
Uso tipico: <50MB RAM, <1% CPU em idle.

### IA nao funciona
- Verifique que pelo menos 1 chave de API esta no `.env` (`GOOGLE_API_KEY` recomendado)
- Reinicie: `docker compose down && docker compose up -d`
- Acesse `/config` no app para ver quais providers estao ativos

## Suporte

- E-mail: jefferson.piccirillo@jkcontrol.com.br
- Versao: 1.5.0
