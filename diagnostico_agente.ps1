# ============================================================================
#  DIAGNOSTICO DO AGENTE LOCAL MIA  (somente leitura - nao altera nada)
#  Uso na maquina do cliente (Windows PowerShell 5.1 ou superior):
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\diagnostico_agente.ps1
#  Gera o arquivo  Desktop\diagnostico_agente.txt  e mostra o mesmo conteudo na tela.
# ============================================================================
$ErrorActionPreference = 'SilentlyContinue'
$out  = Join-Path ([Environment]::GetFolderPath('Desktop')) 'diagnostico_agente.txt'
$data = Join-Path $env:LOCALAPPDATA 'AgenteLocalMIA'
$lines = New-Object System.Collections.Generic.List[string]
# (nomes Wl/Hd de proposito: "h" e "w" sao aliases nativos do PowerShell)
function Wl($t) { $script:lines.Add([string]$t) }
function Hd($t) { Wl ''; Wl ('==================== ' + $t + ' ====================') }
function Fmt-Item($i) {
    $sz = '<DIR>'
    if (-not $i.PSIsContainer) { $sz = [string]$i.Length }
    return ('    ' + $i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + '  ' + ('{0,12}' -f $sz) + '  ' + $i.Name)
}
# Builds conhecidos (sha256 -> versao). Tamanho serve como 2a pista.
$KNOWN = @{
    '928C4773FEBBEF90A589CA9177BA316CC95E9891C9FA9E04BA8628D7B35ED8AF' = 'v5.78 FINAL 09/09 (clique abre painel, AGENDADO, hora BRT, auto-update consertado, le latest_*)'
    '10F59D2A00C8150D475C0304F13317F7BDA94E22315B20B47F9297C39678B9D6' = 'v5.78 build 08/09 14:36 (sem a hora BRT no cabecalho; substitua pela final)'
    '1D84EA8412E15D0A56B43AF52086B3D90AFEEFE0C65EAE80861BD1BB645FD753' = 'v5.78 build 08/09 12:04 (sem latest_* nem teto de tentativas; substitua pela final)'
    '46AA4077213263767816DB0D7F5C01DF0BCC9232D22B8469C8A1DE2A1E116E80' = 'v5.77 (OK, mas auto-update de dentro do agente nao aplica: bat morre no taskkill)'
    'A4CA3CC5E43D1989DD8BD3442EFC538A6D3ABB5BC3509332C21A0B5FB2D1B9B8' = 'v5.76 (QUEBRADA - sem runtime persistente, abre e fecha)'
}
$KNOWN_SIZE = @{ 20328340 = 'v5.78 final 09/09'; 20326383 = 'v5.78 (build 08/09 14:36)'; 20324743 = 'v5.78 (build 08/09 12:04)'; 20312299 = 'v5.77'; 20319145 = 'v5.76' }
function Ident-Exe($path) {
    $fi = Get-Item $path
    $h  = (Get-FileHash $path -Algorithm SHA256).Hash
    $id = $KNOWN[$h]
    if (-not $id) {
        $id = $KNOWN_SIZE[[long]$fi.Length]
        if ($id) { $id = $id + '? (mesmo tamanho, hash diferente)' } else { $id = 'versao desconhecida (nao e a 5.76 nem a 5.77 publicadas)' }
    }
    return ($fi.Length.ToString() + ' bytes, modificado ' + $fi.LastWriteTime + '  => ' + $id + '   sha256=' + $h.Substring(0, 12) + '...')
}

Wl ('DIAGNOSTICO AGENTE LOCAL MIA  -  ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
$os = Get-CimInstance Win32_OperatingSystem
Wl ('Maquina: ' + $env:COMPUTERNAME + '   Usuario: ' + $env:USERNAME)
Wl ('Windows: ' + $os.Caption + '  build ' + $os.Version + '   ligado desde ' + $os.LastBootUpTime)
Wl ('PowerShell ' + $PSVersionTable.PSVersion + '   TLS padrao do .NET: ' + [Net.ServicePointManager]::SecurityProtocol)
$vurl = 'https://raw.githubusercontent.com/delmatch-user/agente-local-releases/main/version.json'
$pub = $null; $tlsFix = $false; $errGit = ''
try { $pub = Invoke-RestMethod -UseBasicParsing -TimeoutSec 8 $vurl } catch { $errGit = $_.Exception.Message }
if (-not $pub) {
    # Windows/.NET antigos nao usam TLS 1.2 por padrao e o GitHub exige: "A conexao foi fechada de modo inesperado".
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; $pub = Invoke-RestMethod -UseBasicParsing -TimeoutSec 8 $vurl; $tlsFix = $true } catch { $errGit = $_.Exception.Message }
}
if ($pub -and $tlsFix) { Wl ('Versao publicada no GitHub: v' + $pub.version + '   *** SO funcionou FORCANDO TLS 1.2: o ATUALIZAR_E_ABRIR_AGENTE.bat antigo FALHA nesta maquina (erro "conexao fechada de modo inesperado") ***') }
elseif ($pub) { Wl ('Versao publicada no GitHub: v' + $pub.version) }
else { Wl ('Versao publicada no GitHub: FALHOU mesmo com TLS 1.2 -> sem internet, proxy ou antivirus bloqueando o GitHub. Erro: ' + $errGit) }

# ---------------------------------------------------------------------------
Hd '1) PROCESSOS AgenteLocal* RODANDO AGORA'
Wl 'Nota: cada instancia saudavel aparece como 2 processos (pai bootloader + filho app).'
$procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'AgenteLocal*' })
if ($procs.Count -eq 0) {
    Wl '*** NENHUM processo AgenteLocal rodando -> o agente realmente nao esta aberto. ***'
} else {
    foreach ($p in $procs) {
        Wl ('PID=' + $p.ProcessId + '  PPID=' + $p.ParentProcessId + '  inicio=' + $p.CreationDate + '  mem=' + [math]::Round($p.WorkingSetSize / 1MB) + 'MB')
        Wl ('    exe=' + $p.ExecutablePath)
    }
    Wl ('Total: ' + $procs.Count + ' processo(s) = ~' + [math]::Floor($procs.Count / 2) + ' instancia(s)')
}

# ---------------------------------------------------------------------------
Hd '2) INICIALIZACAO AUTOMATICA (registro Run + atalho Startup)'
$rv = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal).AgenteLocal
$exeRun = $null
if ($rv) {
    $exeRun = $rv.Trim('"')
    Wl ('Registro Run: ' + $rv)
    if (Test-Path $exeRun) {
        Wl ('    exe EXISTE: ' + (Ident-Exe $exeRun))
        $zone = Get-Item $exeRun -Stream Zone.Identifier
        if ($zone) { Wl '    AVISO: exe marcado como "baixado da internet" (SmartScreen pode bloquear ao clicar).' }
    } else {
        Wl '    *** exe do registro NAO EXISTE (apagado, em quarentena do antivirus ou pasta movida) ***'
    }
} else {
    Wl 'Registro Run: AUSENTE (agente nao inicia com o Windows)'
}
$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$lnks = @(Get-ChildItem $startup -Filter 'AgenteLocal*.lnk')
if ($lnks.Count -eq 0) { Wl 'Atalho Startup: nenhum AgenteLocal*.lnk' }
foreach ($l in $lnks) {
    $ws = New-Object -ComObject WScript.Shell
    $s  = $ws.CreateShortcut($l.FullName)
    Wl ('Atalho Startup: ' + $l.Name + ' -> ' + $s.TargetPath + '   existe=' + (Test-Path $s.TargetPath))
}

# ---------------------------------------------------------------------------
Hd '3) PASTA DO EXE (restos de update / locks)'
$exeDir = $null
if ($exeRun) { $exeDir = Split-Path $exeRun -Parent }
if ($exeDir -and (Test-Path $exeDir)) {
    Wl ('Pasta: ' + $exeDir)
    Get-ChildItem $exeDir -Force | Where-Object { $_.Name -like 'AgenteLocal*' -or $_.Name -like '*.bat' -or $_.Name -like '*.lock' -or $_.Name -like '*.tmp' -or $_.Name -eq '.boot_ok' -or $_.Name -eq 'config.json' } | ForEach-Object { Wl (Fmt-Item $_) }
    $bo = Join-Path $exeDir '.boot_ok'
    if (Test-Path $bo) { Wl ('.boot_ok = v' + (Get-Content $bo -Raw).Trim() + '  (ultimo boot confirmado em ' + (Get-Item $bo).LastWriteTime + ')') }
    if (Test-Path (Join-Path $exeDir 'update_apply.bat')) { Wl '*** update_apply.bat SOBROU na pasta: o update automatico morreu no meio (ate a v5.77 o bat era filho do agente e morria no proprio taskkill /T, deixando a loja sem agente). Corrigido na v5.78. Rode o ATUALIZAR_E_ABRIR_AGENTE.bat. ***' }
    foreach ($v in @(Get-ChildItem $exeDir -Filter 'AgenteLocal_*.exe')) { Wl ('    exe versionado sobrando: ' + $v.Name + ' -> ' + (Ident-Exe $v.FullName)) }
} else {
    Wl 'Pasta do exe nao identificada (registro Run ausente ou invalido).'
}

# ---------------------------------------------------------------------------
Hd ('4) PASTA DE DADOS  ' + $data)
$logp = Join-Path $data 'agente.log'
$rtOk = $false
if (-not (Test-Path $data)) {
    Wl '*** Pasta NAO existe -> o agente nunca chegou a rodar neste usuario do Windows (ou e versao muito antiga). ***'
} else {
    Get-ChildItem $data -Force | ForEach-Object { Wl (Fmt-Item $_) }
    $cfgp = Join-Path $data 'config.json'
    if (Test-Path $cfgp) {
        $c = Get-Content $cfgp -Raw -Encoding UTF8 | ConvertFrom-Json
        $tok = 'AUSENTE'
        if ($c.token) { $tok = 'presente (' + ([string]$c.token).Length + ' chars)' }
        $nImp = 0; if ($c.impressoras) { $nImp = @($c.impressoras).Count }
        Wl ('config.json: restaurant_id=' + $c.restaurant_id + '  restaurant_name=' + $c.restaurant_name + '  token=' + $tok + '  impressoras=' + $nImp)
        if (-not $c.token -or -not $c.restaurant_id) { Wl '    -> SEM token/restaurant_id: o agente abre a tela de BOAS-VINDAS a cada inicio.' }
    } else {
        Wl 'config.json: NAO existe -> agente vai abrir BOAS-VINDAS (primeira execucao).'
    }
    $inst = Join-Path $data 'instances'
    $regs = @(Get-ChildItem $inst -Filter '*.json')
    Wl ('Registros de eleicao (instances): ' + $regs.Count)
    foreach ($r in $regs) {
        $j = Get-Content $r.FullName -Raw | ConvertFrom-Json
        $age = [int]((Get-Date) - [DateTimeOffset]::FromUnixTimeSeconds([long]$j.hb).LocalDateTime).TotalSeconds
        $alive = [bool](Get-Process -Id $j.pid -ErrorAction SilentlyContinue)
        Wl ('    pid=' + $j.pid + '  v' + $j.version + '  heartbeat ha ' + $age + 's  processo_vivo=' + $alive)
        Wl ('        exe=' + $j.exe)
    }
    $rt = Join-Path $data 'runtime'
    if (Test-Path $rt) { $rtOk = $true; Wl ('runtime persistente (receita v5.77): existe, ' + @(Get-ChildItem $rt).Count + ' pasta(s) _MEI dentro') }
    else { Wl 'runtime persistente: NAO existe -> exe atual descompacta em %TEMP% (receita quebrada da v5.76 ou versao antiga)' }
}
$mei = @(Get-ChildItem $env:TEMP -Directory -Filter '_MEI*')
Wl ('Pastas _MEI* em %TEMP%: ' + $mei.Count)

# ---------------------------------------------------------------------------
Hd '5) agente.log  (resumo + ultimas 80 linhas)'
$lastBoot = ''
if (Test-Path $logp) {
    $lf = Get-Item $logp
    Wl ('tamanho=' + [math]::Round($lf.Length / 1KB) + ' KB   ultima escrita=' + $lf.LastWriteTime)
    $boots = @(Select-String -Path $logp -Pattern 'iniciando ===' -Encoding UTF8)
    if ($boots.Count -gt 0) { $lastBoot = $boots[-1].Line }
    Wl ('Linhas "iniciando" no log (total): ' + $boots.Count)
    Wl ('Ultimo boot registrado: ' + $lastBoot)
    $tail = @(Get-Content $logp -Tail 400 -Encoding UTF8)
    $erros = @($tail | Where-Object { $_ -match '\[ERROR\]|\[WARNING\]|Traceback|Exception' })
    Wl ('Erros/avisos nas ultimas 400 linhas: ' + $erros.Count)
    foreach ($e in ($erros | Select-Object -Last 15)) { Wl ('    ' + $e) }
    Wl '--- ultimas 80 linhas ---'
    foreach ($ln in ($tail | Select-Object -Last 80)) { Wl $ln }
} else {
    Wl '*** agente.log NAO existe -> o exe nem chegou a executar o codigo Python (bloqueio de antivirus/SmartScreen, exe corrompido ou faltando). ***'
}

# ---------------------------------------------------------------------------
Hd '6) ANTIVIRUS / DEFENDER'
$avs = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct)
foreach ($a in $avs) { Wl ('Antivirus instalado: ' + $a.displayName + '  (state=' + $a.productState + ')') }
$mp = Get-MpComputerStatus
if ($mp) { Wl ('Defender: modo=' + $mp.AMRunningMode + '  protecao tempo real=' + $mp.RealTimeProtectionEnabled) }
$det = @(Get-MpThreatDetection | Where-Object { ($_.Resources -join ' ') -match 'AgenteLocal|_MEI|AgenteLocalMIA' })
if ($det.Count -eq 0) { Wl 'Defender: nenhuma deteccao envolvendo AgenteLocal.' }
foreach ($d in $det) {
    Wl ('*** DETECCAO Defender: ' + $d.InitialDetectionTime + '  acao=' + $d.CleaningActionID + '  recursos=' + ($d.Resources -join ' | '))
}

# ---------------------------------------------------------------------------
Hd '7) EVENTOS DO WINDOWS: crash/travamento do AgenteLocal (ultimas 72h)'
$ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = @('Application Error', 'Application Hang', 'Windows Error Reporting'); StartTime = (Get-Date).AddHours(-72) } -MaxEvents 2000 |
        Where-Object { $_.Message -match 'AgenteLocal' } | Select-Object -First 15)
if ($ev.Count -eq 0) { Wl 'Nenhum crash/hang do AgenteLocal registrado pelo Windows.' }
foreach ($e in $ev) {
    $msg = ($e.Message -replace '\s+', ' ')
    if ($msg.Length -gt 350) { $msg = $msg.Substring(0, 350) + '...' }
    Wl ('*** ' + $e.TimeCreated + '  [' + $e.ProviderName + ' id=' + $e.Id + ']  ' + $msg)
}

# ---------------------------------------------------------------------------
Hd '8) LEITURA RAPIDA'
$nInst = [math]::Floor($procs.Count / 2)
if ($procs.Count -eq 0 -and -not (Test-Path $logp)) {
    Wl 'Processo NAO roda e NAO ha log -> o exe esta sendo bloqueado antes de iniciar (antivirus/SmartScreen) ou o arquivo esta faltando/corrompido. Veja secoes 2 e 6.'
} elseif ($procs.Count -eq 0) {
    Wl 'Processo NAO roda mas ha log -> o agente sobe e cai. Veja o fim do log (secao 5) e os crashes do Windows (secao 7).'
    if ($lastBoot -match 'v5\.76' -and -not $rtOk) { Wl '*** Ultimo boot foi da v5.76 SEM runtime persistente: e o bug conhecido "abre e fecha sozinho". Solucao: atualizar para a v5.77 (ATUALIZAR_E_ABRIR_AGENTE.bat). ***' }
    elseif ($lastBoot -and $lastBoot -eq ($tail | Select-Object -Last 1)) { Wl '*** A ultima linha do log e o proprio "iniciando": o exe morreu logo apos subir, antes da GUI. ***' }
} elseif ($nInst -ge 1) {
    Wl ('Ha ' + $nInst + ' instancia(s) rodando -> o agente ESTA aberto, mas so na BANDEJA (icone perto do relogio, talvez escondido na setinha "^").')
    Wl 'Clicar no .exe de novo NAO abre janela: a nova copia perde a eleicao e e encerrada em ~15s. Para abrir a tela: duplo clique no icone da bandeja (Status) ou botao direito > Configuracoes.'
    if ($nInst -ge 2) { Wl '*** Mais de uma instancia viva: a eleicao deveria colapsar para 1 em ~15s. Se persistir, rode o ATUALIZAR_E_ABRIR_AGENTE.bat. ***' }
    if ($pub -and $lastBoot -and ($lastBoot -notmatch ('v' + [regex]::Escape($pub.version)))) { Wl ('*** Versao rodando difere da publicada (v' + $pub.version + '): agente desatualizado ou auto-update falhando. ***') }
}
Wl ''
Wl ('Relatorio salvo em: ' + $out)

$lines | Set-Content -Path $out -Encoding UTF8
$lines | ForEach-Object { Write-Host $_ }
