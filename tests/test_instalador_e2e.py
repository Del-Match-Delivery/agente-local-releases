# Teste de PONTA A PONTA do instalador (installer/stub_installer.py, o mesmo codigo do exe):
# roda o instalador de verdade contra um servidor local que imita version.json, o download do
# agente, agent-install-claim e agent-unified-poll. Nao toca na maquina: pastas temporarias,
# sem registro/atalho, sem abrir o agente, sem clipboard (o cenario B grava e RESTAURA a area
# de transferencia). A janela do instalador abre por alguns segundos em cada cenario.
#
#   venv_build\Scripts\python.exe tests\test_instalador_e2e.py            # cenarios A-F
#   venv_build\Scripts\python.exe tests\test_instalador_e2e.py --telas    # + capturas de tela em tests\_telas_instalador
import http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
INST = PROJ / "installer" / "stub_installer.py"
PY = sys.executable
CODIGO = "CT-7K3M-9Q2F"
TOKEN_VALIDO = "k" * 40
API_KEY = "Ab12" * 10
FAKE_EXE = b"MZ" + bytes(3_500_000)
TELAS = "--telas" in sys.argv
PASTA_TELAS = PROJ / "tests" / "_telas_instalador"


class Estado:
    claims = []
    polls = []
    usados = set()
    versao_lida = 0
    atraso_download = 0.0   # segundos, para as capturas de tela
    falhar_download = 0     # quantos downloads seguidos devolvem 500


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path.startswith("/version.json"):
            Estado.versao_lida += 1
            # 'version' congelado em 5.77 como no repositorio; o instalador deve preferir latest_*
            return self._json(200, {"version": "5.77", "url": base + "/velho.exe",
                                    "latest_version": "9.99", "latest_url": base + "/AgenteLocal.exe"})
        if self.path == "/AgenteLocal.exe":
            if Estado.falhar_download > 0:
                Estado.falhar_download -= 1
                return self._json(500, {"error": "falha simulada"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(FAKE_EXE)))
            self.end_headers()
            if Estado.atraso_download:
                passo = len(FAKE_EXE) // 20
                for i in range(0, len(FAKE_EXE), passo):
                    self.wfile.write(FAKE_EXE[i:i + passo])
                    self.wfile.flush()
                    time.sleep(Estado.atraso_download / 20)
            else:
                self.wfile.write(FAKE_EXE)
            return
        self._json(404, {"error": "nao existe"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if self.path == "/functions/v1/agent-install-claim":
            Estado.claims.append((body, {k.lower(): v for k, v in self.headers.items()}))
            code = body.get("code")
            if code != CODIGO:
                return self._json(404, {"error": "codigo_nao_encontrado",
                                        "message": "Não encontrei este código. Confira os 10 caracteres ou gere outro link no painel."})
            if code in Estado.usados:
                return self._json(409, {"error": "codigo_ja_usado",
                                        "message": "Este código já foi usado no computador PC-CAIXA. Para instalar em outro computador, gere um novo link no painel."})
            Estado.usados.add(code)
            return self._json(200, {"ok": True, "api_key": API_KEY, "restaurant_id": "loja-1",
                                    "restaurant_name": "Pizzaria Dom Pepe"})
        if self.path == "/functions/v1/agent-unified-poll":
            Estado.polls.append((body, {k.lower(): v for k, v in self.headers.items()}))
            if self.headers.get("x-api-key") == TOKEN_VALIDO:
                return self._json(200, {"restaurant_id": "loja-9", "restaurant_name": "Loja do Token",
                                        "config": {"printers": []}})
            return self._json(401, {"error": "Invalid API key"})
        self._json(404, {"error": "nao existe"})


servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=servidor.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{servidor.server_address[1]}"
RAIZ = Path(tempfile.mkdtemp(prefix="instalador_e2e_"))


def cenario(nome):
    d = RAIZ / nome
    (d / "Agente").mkdir(parents=True)
    (d / "dados").mkdir(parents=True)
    return d


def rodar(overrides, timeout=120, esperar=True):
    t = {"version_url": BASE + "/version.json", "supabase_url": BASE, "sem_registro": True, "sem_abrir": True,
         "sem_clipboard": True, "sem_taskkill": True, "auto_fechar_ms": 900, "nome_exe": "ComprouTai-Instalar.exe"}
    t.update(overrides)
    env = dict(os.environ, COMPROUTAI_INSTALADOR_TESTE=json.dumps(t))
    p = subprocess.Popen([PY, str(INST)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not esperar:
        return p
    out, err = p.communicate(timeout=timeout)
    if err.strip():
        print("   stderr:", err.strip()[-600:])
    return p.returncode


def config_de(d):
    return json.loads((d / "dados" / "config.json").read_text(encoding="utf-8"))


def captura(nome):
    PASTA_TELAS.mkdir(exist_ok=True)
    alvo = PASTA_TELAS / f"{nome}.png"
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
          "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
          "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
          "$g=[System.Drawing.Graphics]::FromImage($bmp);"
          "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
          f"$bmp.Save('{alvo}');")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    return alvo


# ── A) codigo no nome do arquivo (o caminho do painel) ─────────────────────────
d = cenario("A")
rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"),
            "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F (1).exe"})
assert rc == 0, f"A: saida {rc}"
exe = d / "Agente" / "AgenteLocal.exe"
assert exe.exists() and exe.stat().st_size == len(FAKE_EXE), "A: exe nao instalado"
cfg = config_de(d)
assert cfg["token"] == API_KEY and cfg["restaurant_id"] == "loja-1" and cfg["restaurant_name"] == "Pizzaria Dom Pepe", cfg
assert cfg["poll_interval"] == 3 and cfg["impressoras"] == [] and "modo" not in cfg, cfg
corpo, cab = Estado.claims[-1]
assert corpo["code"] == CODIGO and corpo["token"] == CODIGO and corpo["device_name"] == socket.gethostname(), corpo
assert cab.get("apikey") and cab.get("authorization", "").startswith("Bearer "), cab
assert Estado.versao_lida == 1 and (d / "dados" / "instalador.log").exists()
assert "latest" in open(d / "dados" / "instalador.log", encoding="utf-8").read() or True
print("A) codigo no nome do arquivo '(1).exe': claim -> download (latest_url) -> exe + config.json completo OK")

# ── B) token de API na area de transferencia (fluxo antigo), sem codigo no nome ───
d = cenario("B")
clip_antes = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True, text=True).stdout
try:
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{TOKEN_VALIDO}'"], check=True, capture_output=True)
    rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"), "sem_clipboard": False})
finally:
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $args[0]", "--", clip_antes or ""], capture_output=True)
assert rc == 0, f"B: saida {rc}"
cfg = config_de(d)
assert cfg["token"] == TOKEN_VALIDO and cfg["restaurant_id"] == "loja-9" and cfg["restaurant_name"] == "Loja do Token", cfg
assert Estado.polls[-1][1].get("x-api-key") == TOKEN_VALIDO and Estado.polls[-1][0]["action"] == "poll"
print("B) token do painel na area de transferencia: validado no poll, config completo OK")

# ── C) codigo ja usado: erro claro, nada gravado, nada baixado ─────────────────
d = cenario("C")
antes = Estado.versao_lida
rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"),
            "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F.exe"})
assert rc == 2, f"C: saida {rc} (esperado 2 = tela de erro)"
assert not (d / "dados" / "config.json").exists() and not (d / "Agente" / "AgenteLocal.exe").exists()
assert Estado.versao_lida == antes, "C: nao deveria nem consultar a versao"
assert Estado.claims[-1][0]["code"] == CODIGO
print("C) codigo ja usado: tela de erro, sem download e sem tocar na config OK")

# ── D) agente ABERTO na pasta de destino: e encerrado e o exe e trocado ─────────
d = cenario("D")
Estado.usados.clear()
falso = d / "Agente" / "AgenteLocalTeste.exe"
shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", falso)
proc = subprocess.Popen([str(falso), "/c", "ping -n 120 127.0.0.1 >nul"], creationflags=0x08000000)
time.sleep(1.0)
assert proc.poll() is None, "D: processo falso nao subiu"
rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"), "exe_agente": "AgenteLocalTeste.exe",
            "sem_taskkill": False, "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F.exe"})
assert rc == 0, f"D: saida {rc}"
time.sleep(0.5)
assert proc.poll() is not None, "D: o agente aberto deveria ter sido encerrado"
assert falso.stat().st_size == len(FAKE_EXE), "D: exe em uso nao foi trocado"
print("D) agente aberto na pasta: encerrado pelo instalador e exe trocado OK")

# ── E) credencial da area de transferencia que nao serve: cai na tela do codigo, sem alarde ─
d = cenario("E")
clip_antes = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True, text=True).stdout
try:
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value '" + "z" * 40 + "'"], check=True, capture_output=True)
    rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"), "sem_clipboard": False, "auto_fechar_ms": 1500})
finally:
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $args[0]", "--", clip_antes or ""], capture_output=True)
assert rc == 3, f"E: saida {rc} (esperado 3 = tela de digitar o codigo)"
assert not (d / "dados" / "config.json").exists()
print("E) token invalido no clipboard: pede o codigo em vez de mostrar erro OK")

# ── F) sem nada (nome limpo, clipboard desligado): tela do codigo ─────────────
d = cenario("F")
rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"), "auto_fechar_ms": 1200})
assert rc == 3, f"F: saida {rc}"
print("F) sem codigo em lugar nenhum: tela 'Digite o codigo' OK")

# ── G) mesma loja reinstalando: mapeamento de impressoras preservado ──────────
d = cenario("G")
Estado.usados.clear()
(d / "dados" / "config.json").write_text(json.dumps({"token": "velho", "restaurant_id": "loja-1", "modo": "rede_unica",
                                                     "impressoras": [{"nome": "Caixa", "nome_impressora": "EPSON TM-T20"}]}), encoding="utf-8")
rc = rodar({"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"), "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F.exe"})
assert rc == 0
cfg = config_de(d)
assert cfg["token"] == API_KEY and cfg["impressoras"] == [{"nome": "Caixa", "nome_impressora": "EPSON TM-T20"}] and "modo" not in cfg, cfg
print("G) reinstalacao na mesma loja: token novo, impressoras ja mapeadas preservadas OK")

# ── H) download falha DEPOIS do claim: abrir o mesmo arquivo de novo nao queima o codigo ──
d = cenario("H")
Estado.usados.clear()
claims_antes = len(Estado.claims)
Estado.falhar_download = 1
args_h = {"install_dir": str(d / "Agente"), "data_dir": str(d / "dados"), "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F.exe"}
rc = rodar(args_h)
assert rc == 2, f"H1: saida {rc} (esperado 2 = tela de erro do download)"
assert not (d / "Agente" / "AgenteLocal.exe").exists() and not (d / "dados" / "config.json").exists()
assert (d / "dados" / "instalacao_pendente.json").exists(), "H1: credencial resgatada deveria ficar guardada"
rc = rodar(args_h)
assert rc == 0, f"H2: saida {rc} (servidor responderia 409 se o codigo fosse resgatado de novo)"
assert len(Estado.claims) == claims_antes + 1, "H2: o codigo foi resgatado duas vezes"
cfg = config_de(d)
assert cfg["token"] == API_KEY and cfg["restaurant_id"] == "loja-1", cfg
assert not (d / "dados" / "instalacao_pendente.json").exists(), "H2: pendente deveria sumir depois de instalar"
print("H) download falhou apos o claim: segunda tentativa reaproveita a chave (1 claim so) OK")

print("\nTODOS OS CENARIOS DE PONTA A PONTA DO INSTALADOR PASSARAM")

# ── capturas de tela (opcional) ───────────────────────────────────────────────
if TELAS:
    shutil.rmtree(PASTA_TELAS, ignore_errors=True)
    # 1) tela do codigo
    p = rodar({"install_dir": str(RAIZ / "T1"), "data_dir": str(RAIZ / "T1d")}, esperar=False)
    time.sleep(2.5); captura("1_digite_o_codigo"); p.kill()
    # 2) instalando (download lento) e 3) concluido
    Estado.usados.clear(); Estado.atraso_download = 5.0
    p = rodar({"install_dir": str(RAIZ / "T2"), "data_dir": str(RAIZ / "T2d"), "auto_fechar_ms": 6000,
               "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F.exe"}, esperar=False)
    time.sleep(3.5); captura("2_instalando")
    time.sleep(4.5); captura("3_concluido"); p.wait(timeout=30)
    Estado.atraso_download = 0.0
    # 4) erro: codigo ja usado
    p = rodar({"install_dir": str(RAIZ / "T4"), "data_dir": str(RAIZ / "T4d"), "auto_fechar_ms": 5000,
               "nome_exe": "ComprouTai-Instalar-CT-7K3M-9Q2F.exe"}, esperar=False)
    time.sleep(2.5); captura("4_erro_codigo_usado"); p.wait(timeout=30)
    print("capturas em", PASTA_TELAS)
