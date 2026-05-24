# Como publicar este projeto no GitHub

Guia rapido pra criar o repo, fazer o primeiro push e habilitar o CI/CD
que builda e publica a imagem Docker automaticamente em `ghcr.io`.

## 1. Crie o repositorio no GitHub (1 minuto)

1. Vai em https://github.com/new
2. Preencha:
   - **Repository name:** `relatorios-iot`
   - **Description:** App de relatorios, OEE e IA para Ubidots / NEXUS CORE
   - **Visibility:** Private (recomendado pra projeto comercial)
   - **NAO** marque "Initialize with README" (vamos enviar o local)
3. Click **Create repository**

Anote a URL que aparece (ex.: `https://github.com/Jeffersonjkcontrol/relatorios-iot.git`).

## 2. Configure o Git local (so na 1a vez)

Se ainda nao configurou:
```bash
git config --global user.name "Seu Nome"
git config --global user.email "seu@email.com"
```

## 3. Inicialize e envie o codigo (de dentro da pasta do projeto)

No PowerShell ou Git Bash, **dentro de `C:\Users\JKControl\Desktop\PROJETOS IA\relatorios_ubidots`**:

```bash
git init
git add .
git status                  # confira que .env e python_runtime/ NAO aparecem
git commit -m "feat: versao inicial v1.0 - relatorios + OEE + IA + Docker"

# Cria branch main e adiciona o remote (TROQUE Jeffersonjkcontrol)
git branch -M main
git remote add origin https://github.com/Jeffersonjkcontrol/relatorios-iot.git
git push -u origin main
```

Vai pedir login no GitHub. Use **Personal Access Token (PAT)** como senha:
- Gera um em https://github.com/settings/tokens?type=beta
- Permissoes: **Contents: Read and write**, **Workflows: Read and write**, **Packages: Read and write**
- Cola onde pedir "password" (NAO sua senha real do GitHub)

## 4. Habilite o GitHub Actions buildar imagem Docker

Em https://github.com/Jeffersonjkcontrol/relatorios-iot/settings/actions:

- Em **Actions permissions**, deixe "Allow all actions"
- Role pra baixo ate **Workflow permissions** e marque **"Read and write permissions"** (necessario pra publicar em ghcr.io)
- Click **Save**

Pronto. Agora todo `git push` na main vai disparar o workflow `.github/workflows/docker-publish.yml`
que:
- Builda a imagem (multi-arch: amd64 + arm64)
- Publica em `ghcr.io/Jeffersonjkcontrol/relatorios-iot:latest`
- Tagueia com `main`, SHA curto, etc.

## 5. Use a imagem publicada nos clientes

Edite o `docker-compose.yml` do cliente trocando o `build: .` por:
```yaml
services:
  relatorios:
    image: ghcr.io/Jeffersonjkcontrol/relatorios-iot:latest
```

Se o repo for **privado**, antes do `docker compose pull` o cliente precisa fazer login:
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u Jeffersonjkcontrol --password-stdin
```
Onde `$GITHUB_TOKEN` eh um PAT do GitHub com `read:packages`.

Pra **repo publico**, nao precisa de login - `docker pull` funciona direto.

## 6. Fluxo de atualizacao

### Voce (desenvolvedor):
```bash
# Faz alteracoes no codigo
git add .
git commit -m "fix: corrige bug do PDF OEE"

# Versao "rolling" (latest sempre atualiza)
git push

# OU versao taggeada (recomendado para producao)
git tag v1.1
git push origin v1.1
# ^ dispara build de ghcr.io/.../relatorios-iot:1.1
```

GitHub Actions builda em ~3 minutos.

### Cliente (na fabrica):
```bash
cd /opt/relatorios-iot
sudo docker compose pull           # baixa nova imagem (latest ou tag)
sudo docker compose up -d          # restart com a nova versao (~5s downtime)
```

Pra fixar versao especifica em vez de `latest`:
```yaml
image: ghcr.io/Jeffersonjkcontrol/relatorios-iot:1.1
```

## 7. (Opcional) Tornar a imagem publica mesmo com repo privado

Em `github.com/Jeffersonjkcontrol?tab=packages`, click em `relatorios-iot`:
- **Package settings** -> Change visibility -> Public

Isso permite `docker pull` sem login no cliente, mesmo o repo sendo privado.

## Estrutura final

```
relatorios-iot (GitHub repo)
├── Codigo fonte
├── .github/workflows/docker-publish.yml  ← builda toda vez que voce faz push
└── Releases (tags v1.0, v1.1, ...)

ghcr.io/Jeffersonjkcontrol/relatorios-iot (registry)
├── :latest         ← ultima da main
├── :main           ← ultima da main (alias)
├── :1.0, :1.1      ← versoes especificas
└── :<sha-curto>    ← cada commit individual
```

## Troubleshooting

**`git push` pede senha e nao aceita:** GitHub nao aceita mais senha pelo git. Use PAT (passo 3).

**Workflow falha com "permission denied":** voce esqueceu o passo 4 (Workflow permissions = Read and write).

**`docker compose pull` falha no cliente com 404:** repo privado, falta `docker login ghcr.io`. Ou torne a imagem publica (passo 7).

**Imagem nao aparece em ghcr.io:** entre em **Actions** no GitHub, click no workflow que falhou e veja o log.
