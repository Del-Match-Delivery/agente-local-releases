# ============================================================================
#  PAGINA DE PROVA DE ACENTOS - descobre por que uma impressora termica troca/perde letras
#
#  Uso (na maquina da loja, logado no Windows que tem a impressora):
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\pagina_prova_impressora.ps1 -Listar
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\pagina_prova_impressora.ps1 -Impressora "POS-58"   (fila do Windows)
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\pagina_prova_impressora.ps1 -Porta COM3            (USB/serial, como o agente Tauri)
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\pagina_prova_impressora.ps1 -Ip 192.168.0.50       (rede, porta 9100)
#     powershell -NoProfile -ExecutionPolicy Bypass -File .\pagina_prova_impressora.ps1 -Arquivo prova.bin     (so grava os bytes)
#
#  Imprime a MESMA frase "Nao Paraiso" (com acentos) de 7 jeitos, cada um com os bytes exatos que
#  um agente mandaria. A linha que sair certa diz a causa e o que fazer (explicacao na tela depois
#  de imprimir). O '|' no fim de cada linha mostra se a letra depois do acento foi engolida.
#     1 = agente Tauri ANTIGO (CP850, sem FS .)      2 = agente Tauri com o PR #13 (WPC1252, sem FS .)
#     3 = CP850 com FS . (agente Python, padrao)     4 = WPC1252 com FS . (agente Tauri com o PR novo)
#     5 = CP860 com FS .                             6 = UTF-8                  7 = sem acento (controle)
#
#  ASCII puro (o Windows PowerShell 5.1 le .ps1 sem BOM na pagina ANSI). Nao muda configuracao
#  nenhuma: so manda um cupom de teste para a impressora. Feche o agente antes de usar -Porta
#  (a porta serial so aceita um programa por vez).
# ============================================================================
param(
    [string]$Impressora,
    [string]$Porta,
    [int]$Baud = 9600,
    [string]$Ip,
    [string]$Arquivo,
    [switch]$Listar
)
$ErrorActionPreference = 'Stop'

if ($Listar) {
    Write-Host "Impressoras instaladas neste Windows (use com -Impressora):"
    Get-CimInstance Win32_Printer | Sort-Object Name | ForEach-Object {
        Write-Host ("  " + $_.Name + "   (porta " + $_.PortName + $(if ($_.Default) { ', PADRAO' } else { '' }) + ")")
    }
    Write-Host "Portas seriais / USB-serial (use com -Porta; e o que o agente Tauri usa como 'usb'):"
    $portas = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object)
    if ($portas.Count -eq 0) { Write-Host "  (nenhuma)" } else { $portas | ForEach-Object { Write-Host ("  " + $_) } }
    exit 0
}
if (-not ($Impressora -or $Porta -or $Ip -or $Arquivo)) {
    Write-Host "Informe -Impressora ""<nome do Windows>"", -Porta COMx, -Ip <endereco> ou -Arquivo <saida.bin>. Use -Listar para ver os nomes." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------- bytes da pagina
$buf = New-Object System.Collections.Generic.List[byte]
function T([string]$t) { $buf.AddRange([byte[]][Text.Encoding]::ASCII.GetBytes($t)) }
function B([int[]]$x) { foreach ($v in $x) { $buf.Add([byte]$v) } }
$RESET = @(0x1B, 0x40)          # ESC @   (volta a impressora ao estado de fabrica)
$SEMCHINES = @(0x1C, 0x2E)      # FS .    (cancela o modo chines / Kanji)
function Tabela([int]$n) { B @(0x1B, 0x74, $n) }   # ESC t n

B $RESET
T "PAGINA DE PROVA - ACENTOS`n"
T "Certa = Nao/Paraiso COM acento`n"
T "e com o | no fim da linha.`n"
T ("-" * 30 + "`n")
# 1) agente Tauri ANTIGO: CP850, sem FS .
B $RESET; Tabela 2;  T "1 850 s/FS: N";  B @(0xC6); T "o Para"; B @(0xA1); T "so|`n"
# 2) agente Tauri com o PR #13: WPC1252, sem FS .
B $RESET; Tabela 16; T "2 1252 s/FS: N"; B @(0xE3); T "o Para"; B @(0xED); T "so|`n"
# 3) CP850 com FS .  (= agente Python, padrao)
B $RESET; B $SEMCHINES; Tabela 2;  T "3 850 c/FS: N";  B @(0xC6); T "o Para"; B @(0xA1); T "so|`n"
# 4) WPC1252 com FS .  (= agente Tauri com o PR novo)
B $RESET; B $SEMCHINES; Tabela 16; T "4 1252 c/FS: N"; B @(0xE3); T "o Para"; B @(0xED); T "so|`n"
# 5) CP860 (portugues) com FS .
B $RESET; B $SEMCHINES; Tabela 3;  T "5 860 c/FS: N";  B @(0x84); T "o Para"; B @(0xA1); T "so|`n"
# 6) UTF-8 de verdade (impressora em modo UTF-8 ignora ESC t)
B $RESET; B $SEMCHINES; T "6 UTF-8: N"; B @(0xC3, 0xA3); T "o Para"; B @(0xC3, 0xAD); T "so|`n"
# 7) sem acento: controle, tem que sair certo em QUALQUER impressora
B $RESET; T "7 sem acento: Nao Paraiso|`n"
B $RESET
T ("-" * 30 + "`n")
T "Anote as linhas que sairam`n"
T "certas e envie ao suporte.`n"
B @(0x0A, 0x0A, 0x0A, 0x0A, 0x1B, 0x64, 0x05, 0x1D, 0x56, 0x00)   # avanco + corte
$bytes = $buf.ToArray()

# ---------------------------------------------------------------- envio
if ($Arquivo) {
    [IO.File]::WriteAllBytes($Arquivo, $bytes)
    Write-Host ("Bytes da pagina gravados em " + $Arquivo + " (" + $bytes.Length + " bytes).")
}
if ($Impressora) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class ProvaRawPrint {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public class DOCINFO {
        [MarshalAs(UnmanagedType.LPWStr)] public string pDocName;
        [MarshalAs(UnmanagedType.LPWStr)] public string pOutputFile;
        [MarshalAs(UnmanagedType.LPWStr)] public string pDataType;
    }
    [DllImport("winspool.drv", EntryPoint = "OpenPrinterW", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool OpenPrinter(string name, out IntPtr h, IntPtr defaults);
    [DllImport("winspool.drv", SetLastError = true)] static extern bool ClosePrinter(IntPtr h);
    [DllImport("winspool.drv", EntryPoint = "StartDocPrinterW", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern int StartDocPrinter(IntPtr h, int level, [In, MarshalAs(UnmanagedType.LPStruct)] DOCINFO di);
    [DllImport("winspool.drv", SetLastError = true)] static extern bool EndDocPrinter(IntPtr h);
    [DllImport("winspool.drv", SetLastError = true)] static extern bool StartPagePrinter(IntPtr h);
    [DllImport("winspool.drv", SetLastError = true)] static extern bool EndPagePrinter(IntPtr h);
    [DllImport("winspool.drv", SetLastError = true)] static extern bool WritePrinter(IntPtr h, byte[] buf, int count, out int written);
    public static string Send(string printer, byte[] data) {
        IntPtr h;
        if (!OpenPrinter(printer, out h, IntPtr.Zero)) return "nao abri a impressora (erro " + Marshal.GetLastWin32Error() + "). Confira o nome com -Listar.";
        try {
            DOCINFO di = new DOCINFO(); di.pDocName = "Prova de acentos"; di.pDataType = "RAW";
            if (StartDocPrinter(h, 1, di) == 0) return "StartDocPrinter falhou (erro " + Marshal.GetLastWin32Error() + ")";
            try {
                if (!StartPagePrinter(h)) return "StartPagePrinter falhou (erro " + Marshal.GetLastWin32Error() + ")";
                int w;
                bool ok = WritePrinter(h, data, data.Length, out w);
                int err = ok ? 0 : Marshal.GetLastWin32Error();   // antes do EndPagePrinter, que sobrescreve o codigo
                EndPagePrinter(h);
                if (!ok || w != data.Length) return "WritePrinter falhou (erro " + err + ", " + w + "/" + data.Length + " bytes)";
            } finally { EndDocPrinter(h); }
        } finally { ClosePrinter(h); }
        return "";
    }
}
"@
    $erro = [ProvaRawPrint]::Send($Impressora, $bytes)
    if ($erro) { Write-Host ("ERRO: " + $erro) -ForegroundColor Red; exit 1 }
    Write-Host ("Pagina de prova enviada para '" + $Impressora + "' (" + $bytes.Length + " bytes, modo RAW).") -ForegroundColor Green
}
if ($Porta) {
    # mesmos parametros do agente Tauri (print_usb): 9600 8N1, sem controle de fluxo
    $sp = New-Object System.IO.Ports.SerialPort($Porta, $Baud, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One))
    $sp.Handshake = [System.IO.Ports.Handshake]::None; $sp.DtrEnable = $true; $sp.WriteTimeout = 10000
    try {
        $sp.Open(); $sp.Write($bytes, 0, $bytes.Length); $sp.BaseStream.Flush()
        Write-Host ("Pagina de prova enviada para " + $Porta + " (" + $bytes.Length + " bytes, " + $Baud + " 8N1).") -ForegroundColor Green
    } catch {
        Write-Host ("ERRO: " + $_.Exception.Message + "  (feche o agente: ele pode estar com a porta aberta)") -ForegroundColor Red; exit 1
    } finally { if ($sp.IsOpen) { $sp.Close() }; $sp.Dispose() }
}
if ($Ip) {
    $hostIp = $Ip; $portaTcp = 9100
    if ($Ip -match '^(.+):(\d+)$') { $hostIp = $Matches[1]; $portaTcp = [int]$Matches[2] }
    $cli = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $cli.BeginConnect($hostIp, $portaTcp, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(5000)) { throw "sem resposta em ${hostIp}:$portaTcp (5 s)" }
        $cli.EndConnect($ar)
        $st = $cli.GetStream(); $st.Write($bytes, 0, $bytes.Length); $st.Flush()
        Write-Host ("Pagina de prova enviada para " + $hostIp + ":" + $portaTcp + " (" + $bytes.Length + " bytes).") -ForegroundColor Green
    } catch { Write-Host ("ERRO: " + $_.Exception.Message) -ForegroundColor Red; exit 1 }
    finally { $cli.Close() }
}

# ---------------------------------------------------------------- como ler o resultado
Write-Host ""
Write-Host "COMO LER: compare cada linha com 'Nao Paraiso' COM acento e com o '|' no fim."
Write-Host "  Linha 7 errada: problema de conexao/driver, nao de acento. Resolva isso antes."
Write-Host ""
Write-Host "AGENTE TAURI (tabela fixa, nao tem opcao de acentos):"
Write-Host "  4 certa ........................ o PR novo do Tauri (FS . + WPC1252) resolve esta impressora."
Write-Host "  4 errada e 1 certa ............. esta impressora NAO tem WPC1252: o Tauri antigo (CP850) funcionava aqui e o"
Write-Host "                                   PR #13 / PR novo quebram os acentos nela. Nao atualize o Tauri desta loja;"
Write-Host "                                   se a loja reclamou com o Tauri antigo, o erro esta no texto do cadastro."
Write-Host "  1, 2 e 4 erradas ............... o Tauri nao imprime acento nesta impressora em NENHUMA versao. Use o agente"
Write-Host "                                   Python (abaixo), ou tire a impressora do modo chines/UTF-8 pelo utilitario"
Write-Host "                                   do fabricante ou pela configuracao do autoteste (FEED ao ligar)."
Write-Host ""
Write-Host "AGENTE PYTHON (AgenteLocal.exe) - em Configuracoes > impressora > Acentos, escolha a 1a linha certa:"
Write-Host "  3 certa = cp850 (padrao)   4 certa = cp1252   5 certa = cp860   6 certa = utf8   so a 7 = ascii"
Write-Host ""
Write-Host "DIAGNOSTICO (para o suporte):"
Write-Host "  1 errada e 3 certa ............. a impressora estava em MODO CHINES (o FS . resolve)."
Write-Host "  so 6 certa (entre 1 e 6) ....... MODO UTF-8: nenhuma tabela nem o FS . resolvem; so texto em UTF-8 ou sem acento."
