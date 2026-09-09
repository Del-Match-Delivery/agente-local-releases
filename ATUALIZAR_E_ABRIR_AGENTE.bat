@echo off
setlocal
title Atualizador do Agente MIA
set "MIA_BATDIR=%~dp0"
echo.
echo  ================================================
echo     ATUALIZADOR DO AGENTE MIA
echo     (baixa a versao mais nova, troca e reabre)
echo  ================================================
echo.
REM v5.78: (a) forca TLS 1.2 - Windows/.NET antigos nao usam por padrao e o GitHub exige;
REM        sem isso dava "A conexao foi fechada de modo inesperado" e nada era baixado.
REM        (b) baixa ANTES de encerrar o agente: se o download falhar, a loja continua
REM        com o agente rodando em vez de ficar sem agente ate o fallback.
REM        (c) le latest_version/latest_url do version.json (version/url ficam congelados
REM        em 5.77 para os agentes antigos, cujo auto-update e quebrado, nao se matarem).
REM        (d) limpa travas de update antigas (update_lock.tmp etc.) que bloqueavam todo
REM        update futuro em silencio.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch {}; $alvo=$null; $matou=$false; try { Write-Host '[1/5] Localizando o agente instalado...'; try { $rv=(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal -ErrorAction Stop).AgenteLocal; $alvo=$rv.Trim([char]34) } catch {}; if(-not $alvo -or -not (Test-Path (Split-Path $alvo -Parent))){ $alvo=Join-Path $env:MIA_BATDIR 'AgenteLocal.exe' }; Write-Host ('        Alvo: '+$alvo); Write-Host '[2/5] Baixando a versao mais recente...'; $vj=Invoke-RestMethod -UseBasicParsing 'https://raw.githubusercontent.com/delmatch-user/agente-local-releases/main/version.json'; $ver=$vj.version; $url=$vj.url; if($vj.latest_version -and $vj.latest_url){ $ver=$vj.latest_version; $url=$vj.latest_url }; Write-Host ('        Versao publicada: v'+$ver); $tmp=Join-Path $env:TEMP 'AgenteLocal_new.exe'; Invoke-WebRequest -UseBasicParsing $url -OutFile $tmp; $len=(Get-Item $tmp).Length; if($len -lt 3MB){ throw ('download muito pequeno: '+$len+' bytes') }; $fs=[IO.File]::OpenRead($tmp); $b=New-Object byte[] 2; [void]$fs.Read($b,0,2); $fs.Close(); if($b[0] -ne 77 -or $b[1] -ne 90){ throw 'arquivo baixado nao e um .exe valido' }; Write-Host ('        OK ('+$len+' bytes baixados).'); Write-Host '[3/5] Encerrando instancias do agente...'; $matou=$true; & taskkill /F /FI 'IMAGENAME eq AgenteLocal*' /T *> $null; Start-Sleep 4; Write-Host '[4/5] Limpando estado de instancias e travas de update antigas...'; $inst=Join-Path $env:LOCALAPPDATA 'AgenteLocalMIA\instances'; if(Test-Path $inst){ Remove-Item $inst -Recurse -Force -ErrorAction SilentlyContinue }; $pasta=Split-Path $alvo -Parent; foreach($r in 'update_lock.tmp','update_apply.bat','.update_attempt.lock','AgenteLocal.bak.exe','AgenteLocal_update.tmp'){ $p=Join-Path $pasta $r; if(Test-Path $p){ Remove-Item $p -Force -ErrorAction SilentlyContinue } }; Write-Host '[5/5] Aplicando e abrindo o agente...'; Copy-Item $tmp $alvo -Force; Start-Process -FilePath $alvo; Write-Host ''; Write-Host ('   PRONTO! Agente v'+$ver+' instalado e aberto.'); Write-Host '   Ele fica na BANDEJA (icone perto do relogio). Para ver a tela:'; Write-Host '   duplo clique no icone, ou clique de novo no AgenteLocal.exe.' } catch { Write-Host ''; Write-Host ('   ERRO: '+$_.Exception.Message); if($matou){ Write-Host '   Reabrindo a versao que ja estava instalada...'; try { if($alvo -and (Test-Path $alvo)){ Start-Process -FilePath $alvo } } catch {} } else { Write-Host '   Nada foi alterado: o agente continua rodando na versao atual.' } }"
echo.
echo  (Esta janela fecha em 15 segundos)
timeout /t 15 /nobreak >nul
