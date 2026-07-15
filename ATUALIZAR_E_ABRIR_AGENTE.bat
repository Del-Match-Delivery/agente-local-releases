@echo off
setlocal
title Atualizador do Agente MIA
set "MIA_BATDIR=%~dp0"
echo.
echo  ================================================
echo     ATUALIZADOR DO AGENTE MIA
echo     (encerra, baixa a versao mais nova e reabre)
echo  ================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $alvo=$null; try { Write-Host '[1/5] Encerrando instancias do agente...'; & taskkill /F /FI 'IMAGENAME eq AgenteLocal*' /T *> $null; Start-Sleep 4; Write-Host '[2/5] Limpando estado de instancias...'; $inst=Join-Path $env:LOCALAPPDATA 'AgenteLocalMIA\instances'; if(Test-Path $inst){ Remove-Item $inst -Recurse -Force -ErrorAction SilentlyContinue }; Write-Host '[3/5] Localizando o agente instalado...'; try { $rv=(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal -ErrorAction Stop).AgenteLocal; $alvo=$rv.Trim([char]34) } catch {}; if(-not $alvo -or -not (Test-Path (Split-Path $alvo -Parent))){ $alvo=Join-Path $env:MIA_BATDIR 'AgenteLocal.exe' }; Write-Host ('        Alvo: '+$alvo); Write-Host '[4/5] Baixando a versao mais recente...'; $vj=Invoke-RestMethod -UseBasicParsing 'https://raw.githubusercontent.com/delmatch-user/agente-local-releases/main/version.json'; Write-Host ('        Versao publicada: v'+$vj.version); $tmp=Join-Path $env:TEMP 'AgenteLocal_new.exe'; Invoke-WebRequest -UseBasicParsing $vj.url -OutFile $tmp; $len=(Get-Item $tmp).Length; if($len -lt 3MB){ throw ('download muito pequeno: '+$len+' bytes') }; $fs=[IO.File]::OpenRead($tmp); $b=New-Object byte[] 2; [void]$fs.Read($b,0,2); $fs.Close(); if($b[0] -ne 77 -or $b[1] -ne 90){ throw 'arquivo baixado nao e um .exe valido' }; Copy-Item $tmp $alvo -Force; Write-Host ('        OK ('+$len+' bytes gravados).'); Write-Host '[5/5] Abrindo o agente...'; Start-Process -FilePath $alvo; Write-Host ''; Write-Host '   PRONTO! Agente atualizado e aberto.' } catch { Write-Host ''; Write-Host ('   ERRO: '+$_.Exception.Message); Write-Host '   Tentando abrir a versao que ja esta instalada...'; try { if($alvo -and (Test-Path $alvo)){ Start-Process -FilePath $alvo } } catch {} }"
echo.
echo  (Esta janela fecha em 8 segundos)
timeout /t 8 /nobreak >nul
