import subprocess, sys, shutil, argparse, glob
from pathlib import Path

APP_NAME    = "AgenteLocal"
MAIN_SCRIPT = "agente_local.py"
ICON        = "icone.ico"
VERSION     = "1.0.0"

# "python -m PyInstaller" e nao "pyinstaller": o executavel de console so existe se
# os Scripts do Python estiverem no PATH, e nesta maquina nao estao — o build morria
# com WinError 2 mesmo com o PyInstaller instalado. Assim tambem garante que o build
# usa o MESMO interpretador que roda este script, nao outro Python do sistema.
BASE = [
    sys.executable, "-m", "PyInstaller",
    "--name", APP_NAME, "--noconfirm", "--clean", "--log-level", "WARN",
    "--hidden-import", "win32api",
    "--hidden-import", "win32print",
    "--hidden-import", "serial.tools.list_ports",
    "--hidden-import", "websockets",
    "--hidden-import", "pynput",
    "--hidden-import", "usb",
]
if Path(ICON).exists():
    BASE += ["--icon", ICON]

def run(cmd, label):
    sep = "=" * 50
    print("\n" + sep + "\n  " + label + "\n" + sep)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print("[ERRO] " + label)
        sys.exit(r.returncode)
    print("[OK] " + label)

def build_portatil():
    run(BASE + ["--onefile", "--console", MAIN_SCRIPT], "PyInstaller -> onefile (portatil)")
    src  = Path("dist") / (APP_NAME + ".exe")
    dest = APP_NAME + "_portatil_" + VERSION + ".exe"
    if src.exists():
        shutil.copy(str(src), dest)
        print("[OK] Gerado: " + dest)

def build_instalador():
    run(BASE + ["--onedir", "--windowed", "--add-data", "config.json;.", MAIN_SCRIPT], "PyInstaller -> onedir")
    linhas = [
        "[Setup]",
        "AppName=" + APP_NAME,
        "AppVersion=" + VERSION,
        "DefaultDirName={pf}\\" + APP_NAME,
        "OutputBaseFilename=" + APP_NAME + "_setup_" + VERSION,
        "Compression=lzma",
        "SolidCompression=yes",
        "",
        "[Files]",
        'Source: "dist\\' + APP_NAME + '\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs',
        "",
        "[Icons]",
        'Name: "{group}\\' + APP_NAME + '"; Filename: "{app}\\' + APP_NAME + '.exe"',
        'Name: "{commondesktop}\\' + APP_NAME + '"; Filename: "{app}\\' + APP_NAME + '.exe"',
        "",
        "[Run]",
        'Filename: "{app}\\' + APP_NAME + '.exe"; Flags: nowait postinstall skipifsilent',
    ]
    with open("setup.iss", "w") as f:
        f.write("\n".join(linhas))
    inno = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
    if inno.exists():
        run([str(inno), "setup.iss"], "Inno Setup -> instalador")
    else:
        print("[AVISO] Inno Setup nao encontrado.")
        print("        Instale de: https://jrsoftware.org/isdl.php")

def build_empacotado():
    linhas_cx = [
        "from cx_Freeze import setup, Executable",
        "opts = {",
        '    \"packages\": [\"asyncio\",\"serial\",\"websockets\",\"win32api\",\"win32print\",\"pynput\",\"usb\",\"json\",\"logging\"],',
        '    \"include_files\": [\"config.json\"],',
        "}",
        'setup(name=\"' + APP_NAME + '\", version=\"' + VERSION + '\",',
        '    options={\"build_exe\": opts},',
        '    executables=[Executable(\"' + MAIN_SCRIPT + '\", target_name=\"' + APP_NAME + '.exe\")])',
    ]
    with open("setup_cx.py", "w") as f:
        f.write("\n".join(linhas_cx))
    run([sys.executable, "setup_cx.py", "build"], "cx_Freeze -> empacotado")
    pastas = glob.glob("build/exe.*")
    if pastas:
        dest = APP_NAME + "_empacotado_" + VERSION
        if Path(dest).exists():
            shutil.rmtree(dest)
        shutil.copytree(pastas[0], dest)
        print("[OK] Pasta gerada: " + dest + "/")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build do Agente Local")
    p.add_argument("--all",        action="store_true")
    p.add_argument("--instalador", action="store_true")
    p.add_argument("--portatil",   action="store_true")
    p.add_argument("--empacotado", action="store_true")
    args = p.parse_args()
    if args.all or args.instalador: build_instalador()
    if args.all or args.portatil:   build_portatil()
    if args.all or args.empacotado: build_empacotado()
    if not any(vars(args).values()): p.print_help()
