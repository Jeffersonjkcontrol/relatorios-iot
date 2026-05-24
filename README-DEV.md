# Desenvolvimento local (Windows)

Para dev no Windows sem Docker, o repo usa Python embarcado.

## Setup

A pasta `python_runtime/` esta no `.gitignore`. Pra criar localmente:

```powershell
# Baixa o Python embeddable
Invoke-WebRequest "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip" `
  -OutFile "$env:TEMP\py.zip"
Expand-Archive -Path "$env:TEMP\py.zip" -DestinationPath ".\python_runtime" -Force

# Habilita site-packages
(Get-Content .\python_runtime\python312._pth) -replace '#import site', 'import site' | Set-Content .\python_runtime\python312._pth
Add-Content .\python_runtime\python312._pth "`nLib\site-packages"

# Instala pip e deps
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile .\python_runtime\get-pip.py
.\python_runtime\python.exe .\python_runtime\get-pip.py --no-warn-script-location
.\python_runtime\python.exe -m pip install --no-warn-script-location -r requirements.txt

# .env
Copy-Item .env.example .env
notepad .env   # preencha tokens
```

## Rodar

Duplo-clique em `run.bat` ou:
```powershell
.\python_runtime\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

`--reload` re-importa o codigo a cada mudanca - util pra desenvolvimento.

## Estrutura de pastas (post-setup)

```
relatorios_ubidots/
├── python_runtime/       # gitignored - Python 3.12 + deps
├── app/                  # codigo
├── ai_history.db         # gitignored - SQLite de conversas
├── output/               # gitignored - relatorios gerados
├── dist/                 # gitignored - pacote pra distribuicao Docker
└── .env                  # gitignored - tokens
```

## Testando rotas

```powershell
# Server up
curl http://127.0.0.1:8000/

# Listar devices Ubidots
curl "http://127.0.0.1:8000/api/devices?platform=ubidots"

# Preview de uma variavel
curl "http://127.0.0.1:8000/api/preview?platform=ubidots&device=inj82&variable=ciclo&start=-2h&end=now&limit=5"

# Calcular OEE
curl "http://127.0.0.1:8000/api/oee?platform=ubidots&device=inj82&variable=ciclo&start=-24h&end=now&ciclo_ideal=45"

# Providers de IA ativos
curl http://127.0.0.1:8000/api/ai/providers
```

## Build da imagem Docker pra distribuicao

```powershell
docker build -t relatorios-iot:1.0 -t relatorios-iot:latest .
docker save -o dist\relatorios-iot-v1.0\relatorios-iot.tar relatorios-iot:1.0
```

Tamanho final: ~76 MB.
