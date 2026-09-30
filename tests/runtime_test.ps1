# Teste de execucao REAL do AgenteLocal.exe em ambiente isolado (LOCALAPPDATA de teste).
# Cenario: instancia A sobe e fica >60s; instancia B e aberta (= lojista clicando no .exe).
# Esperado: A abre o painel "Status - Concentrador"; B encerra sozinha; flag consumido; sem [UPDATE].
# Restaura no fim EXATAMENTE a chave Run e o atalho de Startup lidos no inicio (o agente
# reescreve os dois para a propria pasta ao subir). Nao mexe na instancia dev desta maquina.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\runtime_test.ps1 [-Dist dist_5.79]
param([string]$Dist = 'dist_5.79')
$ErrorActionPreference = 'Continue'
$aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Split-Path -Parent $aqui
$exe  = Join-Path $proj "$Dist\AgenteLocal.exe"
$la   = Join-Path $aqui '_la_teste'
$log  = Join-Path $la 'AgenteLocalMIA\agente.log'
$flag = Join-Path $la 'AgenteLocalMIA\abrir_janela.flag'
$inst = Join-Path $la 'AgenteLocalMIA\instances'
if (-not (Test-Path $exe)) { Write-Host "nao achei $exe"; exit 1 }

# estado original (para restaurar)
$runOrig = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal -ErrorAction SilentlyContinue).AgenteLocal
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\AgenteLocal MIA.lnk'
$ws = New-Object -ComObject WScript.Shell
$lnkOrig = $null; if (Test-Path $lnk) { $s0 = $ws.CreateShortcut($lnk); $lnkOrig = @{ t = $s0.TargetPath; w = $s0.WorkingDirectory; s = $s0.WindowStyle } }

# ambiente isolado (config falsa: token invalido => 401 => nao imprime nada real)
New-Item -ItemType Directory -Force (Join-Path $la 'AgenteLocalMIA') | Out-Null
$cfgFile = Join-Path $la 'AgenteLocalMIA\config.json'
if (-not (Test-Path $cfgFile)) {
    # UTF-8 SEM BOM: Set-Content -Encoding UTF8 grava BOM e o json.loads do agente (<=5.78) estourava no boot
    $cfgJson = '{"token":"token-de-teste-local-nao-e-real","restaurant_id":"00000000-0000-0000-0000-000000000000","restaurant_name":"TESTE LOCAL","impressoras":[{"nome":"Caixa","area":"caixa","printer_type":"receipt","tipo":"comum_win32","nome_impressora":"Microsoft Print to PDF"}]}'
    [IO.File]::WriteAllText($cfgFile, $cfgJson, (New-Object System.Text.UTF8Encoding($false)))
}
if (Test-Path $log)  { Remove-Item $log -Force }
if (Test-Path $inst) { Remove-Item $inst -Recurse -Force }
if (Test-Path $flag) { Remove-Item $flag -Force }
$env:LOCALAPPDATA = $la

$pa = Start-Process -FilePath $exe -PassThru
Write-Host ("A lancada " + (Get-Date -Format 'HH:mm:ss') + " (bootloader PID=" + $pa.Id + "). Aguardando 66s para A ficar 'antiga'...")
Start-Sleep 66
$procsA = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'AgenteLocal*' -and $_.ExecutablePath -like "*\$Dist\*" })
Write-Host ("Apos 66s: processos de teste = " + $procsA.Count + "  (esperado 2 = 1 instancia)")
$pb = Start-Process -FilePath $exe -PassThru
Write-Host ("B lancada " + (Get-Date -Format 'HH:mm:ss') + " (bootloader PID=" + $pb.Id + "). Aguardando 12s...")
Start-Sleep 12
$procsAfter = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'AgenteLocal*' -and $_.ExecutablePath -like "*\$Dist\*" })
Write-Host ("Apos B+12s: processos de teste = " + $procsAfter.Count + "  (esperado 2: B encerrou sozinha)")
$procsAfter | ForEach-Object { Write-Host ("   PID=" + $_.ProcessId + " PPID=" + $_.ParentProcessId + " inicio=" + $_.CreationDate.ToString('HH:mm:ss')) }
Write-Host ("flag abrir_janela.flag ainda existe? " + (Test-Path $flag) + "  (esperado False = consumido)")

Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public class WinEnum {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<string> Titles(HashSet<uint> pids) {
    var res = new List<string>();
    EnumWindows((h, l) => { uint pid; GetWindowThreadProcessId(h, out pid);
      if (pids.Contains(pid) && IsWindowVisible(h)) { var sb = new StringBuilder(256); GetWindowText(h, sb, 256); if (sb.Length > 0) res.Add(pid + ": " + sb.ToString()); }
      return true; }, IntPtr.Zero);
    return res; }
}
"@
$pids = New-Object 'System.Collections.Generic.HashSet[uint32]'
$procsAfter | ForEach-Object { [void]$pids.Add([uint32]$_.ProcessId) }
Write-Host "Janelas VISIVEIS da instancia de teste (esperado: 'Status - Concentrador'):"
$titles = [WinEnum]::Titles($pids)
if ($titles.Count -eq 0) { Write-Host "   (nenhuma)" } else { $titles | ForEach-Object { Write-Host ("   " + $_) } }
Write-Host "--- log isolado (linhas relevantes) ---"
Get-Content $log -Encoding UTF8 | Select-String -Pattern 'ELEICAO|\[GUI\]|iniciando|UPDATE|POLL\] Largura' | ForEach-Object { Write-Host ("   " + $_.Line) }

# ---------------- limpeza e restauracao ----------------
Write-Host "--- limpeza: encerrando instancia de teste, restaurando Run/.lnk originais, limpando $Dist ---"
$procsAfter | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
$env:LOCALAPPDATA = Join-Path $env:USERPROFILE 'AppData\Local'
if ($runOrig) { Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal -Value $runOrig }
if ($lnkOrig) { $s = $ws.CreateShortcut($lnk); $s.TargetPath = $lnkOrig.t; $s.WorkingDirectory = $lnkOrig.w; $s.WindowStyle = $lnkOrig.s; $s.Save() }
Write-Host ("RUN restaurado: " + (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal).AgenteLocal)
foreach ($f in '.boot_ok', 'update_apply.bat', 'update_lock.tmp', '.update_attempt.lock', 'AgenteLocal.bak.exe', 'AgenteLocal_update.tmp') {
    $p = Join-Path (Join-Path $proj $Dist) $f
    if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("   removido " + $f) }
}
Write-Host "--- processos AgenteLocal ao final ---"
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'AgenteLocal*' } | ForEach-Object { Write-Host ("   PID=" + $_.ProcessId + " " + $_.ExecutablePath) }
