# Deploy em Mini PC com Ubuntu 24.04 LTS

Guia passo-a-passo pra preparar um mini PC industrial com o Relatorios IoT
rodando 24/7 na rede da fabrica.

## 1. Preparar o mini PC

### Hardware sugerido
- **Beelink Mini S12** (N100, 16GB, 500GB) - ~R$ 1.200
- **Mele Quieter 4C** (fanless, ideal pra fabrica) - ~R$ 1.500
- Qualquer mini PC com Intel N100 ou superior, 4GB+ RAM, 64GB+ SSD

### Sistema operacional
Baixe **Ubuntu Server 24.04 LTS** (NAO o Desktop):
https://ubuntu.com/download/server

Grave em pendrive com **balenaEtcher** ou **Rufus**.

### Instalacao
Durante o setup:
- **Hostname:** `relatorios-iot` (ou `iot-fabrica`)
- **Username/password:** anote (sera o login SSH)
- **Storage:** usar o disco inteiro (LVM ok)
- **SSH:** marque "Install OpenSSH server"
- **Snaps:** NAO instale o snap do Docker - usamos o oficial

## 2. Configurar IP fixo na LAN

Apos primeiro boot, descubra o nome da interface:
```bash
ip a
# procura algo como "enp1s0", "eth0", "eno1"
```

Edite o arquivo de rede:
```bash
sudo nano /etc/netplan/50-cloud-init.yaml
```

Conteudo (ajuste IP/gateway pra sua rede):
```yaml
network:
  version: 2
  ethernets:
    enp1s0:
      addresses: [192.168.1.50/24]
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses: [192.168.1.1, 8.8.8.8]
```

Aplicar:
```bash
sudo netplan apply
```

## 3. Instalar o Relatorios IoT (1 comando)

### Opcao A: via curl (recomendado)
```bash
curl -fsSL https://raw.githubusercontent.com/<seu-user>/<repo>/main/deploy/linux/install.sh | sudo bash
```

### Opcao B: baixando manualmente
```bash
wget https://raw.githubusercontent.com/<seu-user>/<repo>/main/deploy/linux/install.sh
sudo bash install.sh
```

### Configurar rede LAN diferente
Se a sua LAN nao for `192.168.0.0/16`:
```bash
LAN_CIDR=10.0.0.0/8 sudo bash install.sh
```

O script faz tudo: instala Docker, baixa a imagem, cria firewall, autostart e backup automatico.

## 4. Configurar tokens

```bash
sudo nano /opt/relatorios-iot/.env
```

Preencha as chaves (Ubidots, JKControl, Gemini). Salve (Ctrl+O Enter, Ctrl+X).

Aplique:
```bash
sudo systemctl restart relatorios-iot
```

## 5. Acessar

Pelo navegador de qualquer PC na LAN:
```
http://192.168.1.50:8000
```
(troque pelo IP que voce configurou)

## Operacao do dia a dia

| Tarefa | Comando |
|---|---|
| Ver status | `sudo systemctl status relatorios-iot` |
| Ver logs ao vivo | `cd /opt/relatorios-iot && sudo docker compose logs -f` |
| Reiniciar | `sudo systemctl restart relatorios-iot` |
| Atualizar | `cd /opt/relatorios-iot && sudo docker compose pull && sudo docker compose up -d` |
| Backup manual | `sudo /usr/local/bin/relatorios-iot-backup` |
| Listar backups | `ls -lh /opt/relatorios-iot/backups/` |
| Restaurar backup | (veja secao abaixo) |

## Restaurar de backup

```bash
sudo systemctl stop relatorios-iot
docker run --rm -v relatorios-iot-data:/data -v /opt/relatorios-iot/backups:/backup \
    alpine sh -c "rm -rf /data/* && tar xzf /backup/relatorios-AAAAMMDD_HHMMSS.tar.gz -C /data"
sudo systemctl start relatorios-iot
```

## Manutencao mensal

- Verificar espaco em disco: `df -h /`
- Atualizar Ubuntu: `sudo apt update && sudo apt upgrade -y && sudo reboot`
- Limpar imagens Docker antigas: `sudo docker image prune -af`

## Acesso remoto seguro (opcional)

Se quiser acessar o app de fora da fabrica, NAO exponha a porta 8000 na internet.
Use uma destas opcoes:

1. **Tailscale** (mais facil, gratuito ate 100 dispositivos):
```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
```
Voce ganha um IP privado tipo `100.x.x.x` acessivel de qualquer lugar com Tailscale.

2. **WireGuard** (VPN tradicional)

3. **Cloudflare Tunnel** (sem expor porta, dominio HTTPS gratuito)

## Desinstalar

```bash
sudo bash /opt/relatorios-iot/uninstall.sh
```

## Suporte

- Jefferson Piccirillo - jefferson.piccirillo@jkcontrol.com.br
