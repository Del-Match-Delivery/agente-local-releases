@echo off
REM ============================================
REM Atualizador Forcado - Agente Local v5.49
REM Resolve clientes presos em versoes antigas com loop infinito
REM ============================================

setlocal enabledelayedexpansion

echo.
echo ============================================
echo   ATUALIZADOR FORCADO - AGENTE LOCAL v5.49
echo ============================================
echo.

REM Detecta possiveis caminhos de instalacao
set "CAMINHOS=C:\Del Match\Agente Local\dist;C:\Del Match\Agente Local\Agente Local\dist;%USERPROFILE%\Desktop\Agente Local\dist;%USERPROFILE%\Desktop\Agente Local\agente-local-releases\dist"

set "INSTALL_DIR="
for %%P in ("%CAMINHOS:;=" "%") do (
    if exist "%%~P\AgenteLocal.exe" (
        set "INSTALL_DIR=%%~P"
        goto :found
    )
)

REM Se nao encontrou, pede ao usuario
echo Nao foi possivel detectar a pasta de instalacao automaticamente.
echo Digite o caminho COMPLETO da pasta onde esta o AgenteLocal.exe:
echo Exemplo: C:\Del Match\Agente Local\dist
echo.
set /p INSTALL_DIR="Caminho: "
if not exist "%INSTALL_DIR%\AgenteLocal.exe" (
    echo.
    echo ERRO: AgenteLocal.exe nao encontrado em "%INSTALL_DIR%"
    pause
    exit /b 1
)

:found
echo.
echo Pasta detectada: %INSTALL_DIR%
echo.
echo Etapa 1/5: Matando todas as instancias do agente...
taskkill /F /IM AgenteLocal.exe >nul 2>&1
taskkill /F /IM AgenteLocal_*.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo Etapa 2/5: Matando processos remanescentes...
for /f "skip=1 tokens=2 delims=," %%P in ('wmic process where "name like 'AgenteLocal%%'" get processid /format:csv 2^>nul') do (
    taskkill /F /PID %%P >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Etapa 3/5: Limpando arquivos antigos e locks...
del /F /Q "%INSTALL_DIR%\AgenteLocal.exe" 2>nul
del /F /Q "%INSTALL_DIR%\AgenteLocal_*.exe" 2>nul
del /F /Q "%INSTALL_DIR%\.update_attempt.lock" 2>nul
del /F /Q "%INSTALL_DIR%\update_apply.bat" 2>nul
del /F /Q "%INSTALL_DIR%\update_lock.tmp" 2>nul

echo Etapa 4/5: Baixando v5.49 do GitHub...
curl -L -o "%INSTALL_DIR%\AgenteLocal.exe" "https://github.com/Del-Match-Delivery/agente-local-releases/releases/download/v5.49/AgenteLocal.exe"
if errorlevel 1 (
    echo.
    echo ERRO: Falha no download. Verifique a conexao com internet.
    pause
    exit /b 1
)

REM Verifica tamanho minimo do arquivo (precisa ser >5MB)
for %%A in ("%INSTALL_DIR%\AgenteLocal.exe") do set "TAMANHO=%%~zA"
if %TAMANHO% LSS 5000000 (
    echo.
    echo ERRO: Arquivo baixado parece corrompido ^(tamanho: %TAMANHO% bytes^)
    echo Tente novamente.
    pause
    exit /b 1
)

echo.
echo Etapa 5/5: Iniciando v5.49...
start "" "%INSTALL_DIR%\AgenteLocal.exe"

echo.
echo ============================================
echo   ATUALIZACAO CONCLUIDA COM SUCESSO!
echo ============================================
echo.
echo O agente v5.49 esta iniciando.
echo.
echo Se o token estiver invalido, a janela de configuracoes
echo abrira automaticamente. Gere um novo token no MIA e cole.
echo.
pause
