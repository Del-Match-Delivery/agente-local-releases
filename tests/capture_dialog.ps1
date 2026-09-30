# Le o texto da caixa "Unhandled exception in script" (PyInstaller) de um exe rodando isolado,
# e compara com outra build no mesmo ambiente. Uso: -Dists dist_5.79,dist_5.78
param([string]$Dists = 'dist_5.79')
$aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Split-Path -Parent $aqui
$la = Join-Path $aqui '_la_teste'
$runOrig = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal -ErrorAction SilentlyContinue).AgenteLocal
$env:LOCALAPPDATA = $la
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public class WinDlg {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<string> DialogTexts(uint pid) {
    var res = new List<string>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid) { var t = new StringBuilder(512); GetWindowText(h, t, 512);
        if (t.ToString().Contains("Unhandled exception")) {
          EnumChildWindows(h, (c, l2) => { var cls = new StringBuilder(64); GetClassName(c, cls, 64);
            var txt = new StringBuilder(16384); GetWindowText(c, txt, 16384);
            if (txt.Length > 0) res.Add(cls.ToString() + ": " + txt.ToString()); return true; }, IntPtr.Zero); } }
      return true; }, IntPtr.Zero);
    return res; }
}
"@
foreach ($dist in ($Dists -split ',')) {
  $exe = Join-Path $proj "$dist\AgenteLocal.exe"
  $log = Join-Path $la 'AgenteLocalMIA\agente.log'
  if (Test-Path $log) { Clear-Content $log }
  $p = Start-Process -FilePath $exe -PassThru; Start-Sleep 14
  $procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'AgenteLocal*' -and $_.ExecutablePath -like ('*\' + $dist + '\*') })
  Write-Host ("=== " + $dist + " : processos=" + $procs.Count)
  foreach ($q in $procs) { $t = [WinDlg]::DialogTexts([uint32]$q.ProcessId); foreach ($x in $t) { Write-Host ("  [PID " + $q.ProcessId + "] " + $x) } }
  if (Test-Path $log) { Write-Host ("  log: " + (Get-Item $log).Length + " bytes"); Get-Content $log -Tail 3 | ForEach-Object { Write-Host ("    " + $_) } } else { Write-Host "  log: NAO existe" }
  $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 2
}
Write-Host "--- pasta de dados isolada ---"; Get-ChildItem (Join-Path $la 'AgenteLocalMIA') -Force | ForEach-Object { Write-Host ("  " + $_.Name + "  " + $_.Length) }
$env:LOCALAPPDATA = Join-Path $env:USERPROFILE 'AppData\Local'
if ($runOrig) { Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal -Value $runOrig }
Write-Host ("RUN restaurado: " + (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name AgenteLocal).AgenteLocal)
