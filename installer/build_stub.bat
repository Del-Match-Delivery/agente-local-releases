@echo off
echo ============================================
echo  Build: ComprouTai-Instalar.exe (instalador do agente)
echo ============================================
echo.
:: Usa o MESMO Python/PyInstaller do build do agente (venv_build), nao o pip do sistema.
cd /d "%~dp0.."
if not exist "venv_build\Scripts\python.exe" (
    echo ERRO: venv_build nao encontrado. Rode a partir da pasta do projeto do agente.
    pause
    exit /b 1
)
:: truststore + certifi: sem eles o instalador nao baixa em PDV com Windows sem a raiz do
:: GitHub (CERTIFICATE_VERIFY_FAILED). O exe compilaria assim mesmo, so que sem a protecao;
:: o teste unitario 6 barra o build se faltar algum dos dois.
venv_build\Scripts\python.exe -m pip install --quiet --disable-pip-version-check truststore certifi
if errorlevel 1 (
    echo ERRO: nao consegui instalar truststore/certifi no venv_build.
    pause
    exit /b 1
)
echo [1/2] Compilando installer\dist\ComprouTai-Instalar.exe...
venv_build\Scripts\python.exe -m PyInstaller --onefile --windowed --noconfirm --clean --log-level WARN ^
    --name ComprouTai-Instalar --distpath installer\dist --workpath installer\build --specpath installer\build ^
    installer\stub_installer.py
if errorlevel 1 (
    echo ERRO: compilacao falhou.
    pause
    exit /b 1
)
echo [2/2] Testes do instalador (unitarios + ponta a ponta contra servidor local)...
venv_build\Scripts\python.exe tests\test_instalador.py
if errorlevel 1 ( echo ERRO: teste unitario falhou. & pause & exit /b 1 )
venv_build\Scripts\python.exe tests\test_instalador_e2e.py
if errorlevel 1 ( echo ERRO: teste ponta a ponta falhou. & pause & exit /b 1 )
echo.
echo ============================================
echo  SUCESSO: installer\dist\ComprouTai-Instalar.exe
echo  Publicar: bucket agent-downloads/windows/ComprouTai-Instalar.exe (Supabase)
echo            e, por compatibilidade, o asset InstalarAgente.exe da tag "installer" no GitHub.
echo ============================================
pause
