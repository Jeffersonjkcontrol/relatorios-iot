@echo off
setlocal
cd /d "%~dp0"

set "PY=%~dp0python_runtime\python.exe"

if not exist "%PY%" (
    echo [ERRO] Runtime Python nao encontrado em %PY%
    echo Reinstale executando setup_python.ps1
    pause
    exit /b 1
)

if not exist ".env" (
    echo [AVISO] Arquivo .env nao encontrado. Copiando .env.example -^> .env
    copy .env.example .env >nul
    echo Edite o .env e preencha seus tokens antes de gerar relatorios.
    echo.
)

echo.
echo Iniciando servidor em http://127.0.0.1:8000
echo Pressione CTRL+C para encerrar.
echo.
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

endlocal
