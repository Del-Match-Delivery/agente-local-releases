"""
ComprouTai - Instalador do agente de impressao (Windows)
=========================================================
Zero cliques: abre, conecta com a loja, baixa o agente atual, instala em
C:\\ComprouTai\\Agente e abre o agente ja conectado. Para o lojista sobra so
escolher as impressoras na janela do agente.

De onde vem a credencial (nesta ordem):
  1. CODIGO DE INSTALACAO no nome do proprio arquivo. O painel (Integracoes >
     Impressoras) gera o link e o navegador grava o arquivo como
     "ComprouTai-Instalar-CT-XXXX-XXXX.exe". O codigo e trocado por uma chave
     em agent-install-claim (uso unico, 24 h). Sobrevive a "(1)" no nome.
  2. --codigo CT-XXXX-XXXX na linha de comando.
  3. Area de transferencia: um codigo, ou um token de API copiado do painel
     (fluxo antigo). Se nao servir, cai na tela de digitar, sem alarde.
  4. Tela "Digite o codigo" (aceita tambem o token de API).

O config.json e gravado COMPLETO (token + restaurant_id + nome) direto na pasta
de dados do agente (%LOCALAPPDATA%\\AgenteLocalMIA, a mesma de _resolver_data_dir
em agente_local.py): o agente sobe sem a tela de boas-vindas.

Compilar (PyInstaller do venv_build, a partir da pasta do projeto):
    venv_build\\Scripts\\python.exe -m PyInstaller --onefile --windowed --name ComprouTai-Instalar ^
        --distpath installer\\dist --workpath installer\\build --specpath installer\\build installer\\stub_installer.py

Teste de ponta a ponta, sem internet e sem tocar na maquina: tests\\test_instalador_e2e.py
(variavel COMPROUTAI_INSTALADOR_TESTE com JSON de overrides; ver _teste()).
"""
import ctypes
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import winreg
from datetime import datetime
from pathlib import Path
from threading import Thread

import tkinter as tk
import tkinter.ttk as ttk

def _teste():
    """Overrides para o teste de ponta a ponta (tests/test_instalador_e2e.py). Fora do teste
    a variavel nao existe e tudo segue o padrao. Chaves: version_url, supabase_url, install_dir,
    data_dir, nome_exe, exe_agente, sem_registro, sem_abrir, sem_clipboard, sem_taskkill, auto_fechar_ms,
    ssl_simular_windows_sem_raiz (imita o PDV sem a raiz do GitHub no Windows)."""
    raw = os.environ.get("COMPROUTAI_INSTALADOR_TESTE", "")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


T = _teste()

INSTALADOR_VERSAO = "2.1"
PRODUTO = "ComprouTai"
NOME_AGENTE = "ComprouTai Agente"
EXE_AGENTE = T.get("exe_agente") or "AgenteLocal.exe"   # o agente espera este nome (auto-update troca este arquivo)
SUPABASE_URL = "https://szlyzyflalerxuyxfxzh.supabase.co"
SUPABASE_ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN6bHl6eWZsYWxlcnh1eXhmeHpoIiwi"
                 "cm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMDkyNTQsImV4cCI6MjA4OTU4NTI1NH0.2UewBvzucel7wiuXv14mvgDmi_FmzCc-Zh2CISL9_VI")
VERSION_URL = "https://raw.githubusercontent.com/delmatch-user/agente-local-releases/main/version.json"
ONDE_PEGAR_CODIGO = "no painel ComprouTai, em Integrações › Impressoras"
_TAMANHO_MIN_EXE = 3 * 1024 * 1024   # piso de sanidade; o agente real tem ~20 MB
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE = "AgenteLocal"
_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008

# Mesma regra de supabase/functions/_shared/install-code.ts (ComprouTai): alfabeto sem 0/O/1/I,
# "CT" + 4 + 4, tolerante a espaco/hifen e ao "(1)" que o navegador acrescenta ao nome.
_ALFABETO = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_RE = re.compile(r"CT[-\s]?([%s]{4})[-\s]?([%s]{4})" % (_ALFABETO, _ALFABETO), re.I)
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{32,64}$")   # chave de API do painel: 40 chars A-Za-z0-9

BG, BG2, ACCENT, TEXT, TEXT2, GREEN, RED, YELLOW = (
    "#0f172a", "#1e293b", "#6366f1", "#f1f5f9", "#94a3b8", "#22c55e", "#f87171", "#fbbf24")


_LOG_LINHAS = []


def _log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    _LOG_LINHAS.append(linha)
    try:
        d = pasta_dados()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "instalador.log", "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


class ErroInstalacao(Exception):
    """Erro com mensagem pronta para o lojista. pode_digitar=True oferece a tela do codigo."""
    def __init__(self, msg, pode_digitar=False):
        super().__init__(msg)
        self.pode_digitar = pode_digitar


# ── Credencial ────────────────────────────────────────────────────────────────
def normalizar_codigo(texto):
    """'ct 7k3m 9q2f', 'CT-7K3M-9Q2F', 'ComprouTai-Instalar-CT-7K3M-9Q2F (1).exe' -> 'CT-7K3M-9Q2F'."""
    if not texto:
        return None
    m = CODE_RE.search(str(texto))
    if not m:
        return None
    return f"CT-{m.group(1).upper()}-{m.group(2).upper()}"


def parece_token(texto):
    return bool(TOKEN_RE.match((texto or "").strip()))


def codigo_do_nome_do_exe():
    nomes = []
    if T.get("nome_exe"):
        nomes.append(str(T["nome_exe"]))
    nomes.append(Path(sys.executable).name)
    if sys.argv and sys.argv[0]:
        nomes.append(Path(sys.argv[0]).name)
    for n in nomes:
        c = normalizar_codigo(n)
        if c:
            return c
    return None


def codigo_da_linha_de_comando(args=None):
    args = list(sys.argv[1:] if args is None else args)
    for i, a in enumerate(args):
        if a.lower() in ("--codigo", "--code", "-c") and i + 1 < len(args):
            return normalizar_codigo(args[i + 1])
    for a in args:
        if a.upper().startswith("CT"):
            c = normalizar_codigo(a)
            if c:
                return c
    return None


def credencial_do_clipboard(root):
    """('codigo', 'CT-...') | ('token', '...') | None. Validacao generica: o que estiver errado
    cai na tela de digitar sem alarde (e silencioso porque o lojista nao pediu nada)."""
    if T.get("sem_clipboard"):
        return None
    try:
        texto = root.clipboard_get().strip()
    except Exception:
        return None
    if not texto or len(texto) > 500 or "\n" in texto:
        return None
    c = normalizar_codigo(texto)
    if c and len(texto) <= 24:
        return ("codigo", c)
    if parece_token(texto):
        return ("token", texto)
    return None


# ── Pastas ────────────────────────────────────────────────────────────────────
def pasta_dados():
    """= DATA_DIR do agente (agente_local.py: _resolver_data_dir). E onde o config.json vive."""
    if T.get("data_dir"):
        return Path(T["data_dir"])
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "AgenteLocalMIA"


def _pasta_escrevivel(p):
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".escrita.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def pasta_instalacao():
    """C:\\ComprouTai\\Agente (visivel, sem administrador na configuracao padrao do Windows);
    se a politica da maquina bloquear a raiz do C:, cai para a pasta do usuario."""
    candidatas = []
    if T.get("install_dir"):
        candidatas.append(Path(T["install_dir"]))
    else:
        drive = os.environ.get("SystemDrive", "C:")
        candidatas.append(Path(drive + "\\") / PRODUTO / "Agente")
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        candidatas.append(Path(base) / PRODUTO / "Agente")
    for p in candidatas:
        if _pasta_escrevivel(p):
            return p
    raise ErroInstalacao("Não consegui criar a pasta de instalação (C:\\ComprouTai\\Agente nem a pasta do usuário). "
                         "Verifique as permissões deste Windows ou peça ajuda ao suporte.")


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _supabase_url():
    return (T.get("supabase_url") or SUPABASE_URL).rstrip("/")


def _version_url():
    return T.get("version_url") or VERSION_URL


def _ssl_sem_verificacao():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# Certificado em PDV com Windows desatualizado: falta no repositorio do Windows a raiz que o
# GitHub usa hoje, e o Python acusa "CERTIFICATE_VERIFY_FAILED ... unable to get local issuer
# certificate". O navegador baixa porque o Windows busca a raiz na hora; o OpenSSL do Python so
# le o que ja esta no repositorio. Entao tentamos, SEMPRE verificando o certificado:
#   1) windows    - repositorio do Windows (padrao do Python)
#   2) truststore - validacao feita pelo proprio Windows (CryptoAPI), que busca a raiz que falta
#   3) certifi    - lista de raizes da Mozilla embutida no exe, somada a do Windows
# Download do agente e version.json NUNCA caem para "sem verificacao": seria aceitar um exe
# trocado no caminho.
def _ctx_windows():
    if T.get("ssl_simular_windows_sem_raiz"):
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)   # teste: verifica, mas sem nenhuma raiz carregada
    return ssl.create_default_context()


def _ctx_truststore():
    import truststore
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _ctx_certifi():
    import certifi
    ctx = _ctx_windows()
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


_CONTEXTOS_SSL = (("windows", _ctx_windows), ("truststore", _ctx_truststore), ("certifi", _ctx_certifi))


def _erro_de_certificado(e):
    motivo = getattr(e, "reason", e)
    return isinstance(motivo, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def _abrir(req, timeout):
    """urlopen com certificado verificado, tentando os contextos de _CONTEXTOS_SSL em ordem.
    So passa para o proximo em erro de CERTIFICADO; HTTPError e falta de rede sobem na hora."""
    ultimo = None
    for nome, fabrica in _CONTEXTOS_SSL:
        try:
            ctx = fabrica()
        except Exception as e:
            _log(f"[SSL] {nome} indisponivel: {e!r}")
            continue
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ssl.SSLError) as e:
            if not _erro_de_certificado(e):
                raise
            _log(f"[SSL] {nome}: certificado recusado ({getattr(e, 'reason', e)})")
            ultimo = e
            continue
        if nome != "windows":
            _log(f"[SSL] {req.full_url.split('?')[0]}: certificado verificado via {nome}")
        return r
    raise ultimo or urllib.error.URLError("nenhum contexto SSL disponivel")


def _msg_rede(e, oque):
    """Mensagem para o lojista. Certificado tem causa e remedio proprios (relogio errado e o
    classico); o resto e internet."""
    if e is not None and _erro_de_certificado(e):
        txt = str(getattr(e, "reason", e)).lower()
        if "expired" in txt or "not yet valid" in txt:
            return (f"{oque}: a data ou a hora deste computador parece errada. "
                    "Acerte o relógio do Windows (data, hora e fuso horário) e tente de novo.")
        return (f"{oque}: este Windows não reconheceu o certificado de segurança do site. "
                "Atualize o Windows ou chame o suporte e mostre esta tela.")
    return f"{oque}. Verifique a internet deste computador e tente de novo."


def _http_json(url, payload, headers=None, timeout=25):
    """POST JSON. Tenta com verificacao de certificado (_abrir); so se todos os contextos
    recusarem, repete sem verificar, como o proprio agente faz em _post."""
    body = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "apikey": SUPABASE_ANON,
         "Authorization": f"Bearer {SUPABASE_ANON}", "User-Agent": f"ComprouTai-Instalador/{INSTALADOR_VERSAO}"}
    if headers:
        h.update(headers)
    falha = "Não consegui falar com o servidor. Verifique a internet deste computador e tente de novo."
    for verificar in (True, False):
        req = urllib.request.Request(url, data=body, method="POST", headers=h)
        try:
            resp = _abrir(req, timeout) if verificar else urllib.request.urlopen(
                req, timeout=timeout, context=_ssl_sem_verificacao())
            with resp as r:
                return json.loads(r.read() or b"{}"), r.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}"), e.code
            except Exception:
                return {"error": str(e)}, e.code
        except (urllib.error.URLError, ssl.SSLError) as e:
            if verificar and _erro_de_certificado(e):
                _log(f"[HTTP] certificado nao verificavel nesta maquina; repetindo sem verificacao ({e})")
                continue
            _log(f"[HTTP] {url}: {e}")
            raise ErroInstalacao(falha)
        except Exception as e:
            _log(f"[HTTP] {url}: {e!r}")
            raise ErroInstalacao(falha)
    raise ErroInstalacao(falha)


def resgatar_codigo(codigo):
    """Troca o codigo pela chave do agente (agent-install-claim). Uso unico."""
    url = f"{_supabase_url()}/functions/v1/agent-install-claim"
    resp, status = _http_json(url, {"code": codigo, "token": codigo, "device_name": socket.gethostname(),
                                    "installer_version": INSTALADOR_VERSAO})
    if status == 200 and isinstance(resp, dict) and resp.get("api_key"):
        return {"token": resp["api_key"], "restaurant_id": resp.get("restaurant_id", ""),
                "restaurant_name": resp.get("restaurant_name", "")}
    msg = (resp or {}).get("message") if isinstance(resp, dict) else None
    if not msg:
        msg = f"O servidor não aceitou o código (erro {status}). Gere outro link {ONDE_PEGAR_CODIGO} e tente de novo."
    _log(f"[CLAIM] {codigo}: HTTP {status} {resp}")
    raise ErroInstalacao(msg, pode_digitar=status in (400, 404, 409, 410))


def _arquivo_pendente():
    return pasta_dados() / "instalacao_pendente.json"


def credencial_pendente(codigo):
    """O claim QUEIMA o codigo (servidor responde 409 na segunda vez). Se o download falhar
    depois dele, 'Tentar de novo' ou abrir o mesmo arquivo outra vez reaproveita a chave ja
    emitida para este codigo, em vez de mandar o lojista gerar outro link no painel."""
    try:
        d = json.loads(_arquivo_pendente().read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("codigo") == codigo and d.get("token"):
            return {"token": d["token"], "restaurant_id": d.get("restaurant_id", ""),
                    "restaurant_name": d.get("restaurant_name", "")}
    except Exception:
        pass
    return None


def guardar_pendente(codigo, cred):
    try:
        p = _arquivo_pendente()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dict(cred, codigo=codigo), ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        _log(f"[PENDENTE] nao gravei: {e}")


def apagar_pendente():
    try:
        _arquivo_pendente().unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"[PENDENTE] nao apaguei: {e}")


def validar_token(token):
    """Fluxo antigo (token de API copiado do painel): confirma no agent-unified-poll e pega a loja."""
    url = f"{_supabase_url()}/functions/v1/agent-unified-poll"
    resp, status = _http_json(url, {"action": "poll", "device_name": socket.gethostname()},
                              headers={"x-api-key": token})
    if status == 200 and isinstance(resp, dict) and resp.get("restaurant_id"):
        return {"token": token, "restaurant_id": resp["restaurant_id"], "restaurant_name": resp.get("restaurant_name", "")}
    _log(f"[TOKEN] recusado: HTTP {status} {resp}")
    raise ErroInstalacao(f"Este token não foi aceito pelo sistema. Gere um link de instalação {ONDE_PEGAR_CODIGO} "
                         "e baixe o instalador por ele.", pode_digitar=True)


def ler_versao_publicada():
    req = urllib.request.Request(_version_url(), headers={"Cache-Control": "no-cache",
                                                          "User-Agent": f"ComprouTai-Instalador/{INSTALADOR_VERSAO}"})
    try:
        with _abrir(req, 20) as r:
            info = json.loads(r.read())
    except Exception as e:
        _log(f"[VERSAO] {e}")
        raise ErroInstalacao(_msg_rede(e, "Não consegui descobrir a versão atual do agente"))
    # latest_* antes de version/url: 'version' fica congelado em 5.77 para os agentes antigos
    url = info.get("latest_url") or info.get("url")
    versao = info.get("latest_version") or info.get("version")
    if not url:
        raise ErroInstalacao("O arquivo de versões está sem o endereço do agente. Avise o suporte.")
    return url, str(versao or "?")


def baixar_agente(url, destino, progresso=None):
    req = urllib.request.Request(url, headers={"User-Agent": f"ComprouTai-Instalador/{INSTALADOR_VERSAO}"})
    total = lido = 0
    try:
        with _abrir(req, 60) as r, open(destino, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                bloco = r.read(256 * 1024)
                if not bloco:
                    break
                f.write(bloco)
                lido += len(bloco)
                if progresso and total > 0:
                    progresso(min(lido * 100 // total, 100), total)
    except Exception as e:
        _log(f"[DOWNLOAD] {e}")
        raise ErroInstalacao(_msg_rede(e, "O download do agente falhou"))
    if total and lido < total:
        raise ErroInstalacao(f"O download veio incompleto ({lido} de {total} bytes). Tente de novo.")
    tamanho = destino.stat().st_size
    if tamanho < _TAMANHO_MIN_EXE:
        raise ErroInstalacao(f"O download veio incompleto ({tamanho} bytes). Tente de novo.")
    with open(destino, "rb") as fp:
        if fp.read(2) != b"MZ":
            raise ErroInstalacao("O arquivo baixado não é um programa válido. Tente de novo.")


# ── Instalacao ────────────────────────────────────────────────────────────────
def encerrar_agente_rodando():
    """Fecha qualquer AgenteLocal.exe aberto (de qualquer pasta). Sem isso, (a) o Windows nao
    deixa gravar por cima do exe em uso e (b) a copia antiga venceria a eleicao de instancia
    unica do agente e continuaria com a config velha em memoria."""
    if T.get("sem_taskkill"):
        return False
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/IM", EXE_AGENTE], capture_output=True,
                           creationflags=_NO_WINDOW, timeout=20)
        if r.returncode == 0:
            _log("[INSTALAR] agente que estava aberto foi encerrado")
            time.sleep(1.0)
            return True
    except Exception as e:
        _log(f"[INSTALAR] taskkill: {e}")
    return False


def instalar_exe(tmp_exe, destino):
    encerrar_agente_rodando()
    ultimo = None
    for tentativa in range(20):
        try:
            shutil.copy2(tmp_exe, destino)
            return
        except PermissionError as e:
            ultimo = e
            if tentativa == 2:
                encerrar_agente_rodando()
            time.sleep(0.5)
    _log(f"[INSTALAR] nao consegui gravar {destino}: {ultimo}")
    raise ErroInstalacao(f"Não consegui gravar {destino.name}: o agente ainda está aberto. "
                         "Feche-o pela bandeja (ícone azul perto do relógio) e tente de novo.")


def gravar_config(data_dir, cred):
    """config.json COMPLETO na pasta de dados do agente. Mantem o que o lojista ja configurou
    (impressoras, balancas...) quando e a mesma loja; loja diferente = mapeamento zerado."""
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = data_dir / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_bytes().decode("utf-8-sig"))
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
    if cfg.get("restaurant_id") and cfg.get("restaurant_id") != cred["restaurant_id"]:
        cfg["impressoras"] = []
    cfg.update({"token": cred["token"], "restaurant_id": cred["restaurant_id"],
                "restaurant_name": cred.get("restaurant_name", "")})
    cfg.setdefault("poll_interval", 3)
    cfg.setdefault("impressoras", [])
    cfg.pop("modo", None)   # campo do instalador antigo; o agente nunca leu
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_path


def _powershell(script):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   capture_output=True, timeout=20, creationflags=_NO_WINDOW)


def _atalho(lnk, alvo, pasta, descricao):
    _powershell(f'$ws=New-Object -ComObject WScript.Shell;$s=$ws.CreateShortcut("{lnk}");'
                f'$s.TargetPath="{alvo}";$s.WorkingDirectory="{pasta}";$s.Description="{descricao}";$s.Save()')


def criar_atalhos(destino, data_dir):
    if T.get("sem_registro"):
        return
    for d in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"):
        if not d.exists():
            continue
        try:
            _atalho(d / f"{NOME_AGENTE}.lnk", destino, destino.parent, NOME_AGENTE)
            for velho in ("Agente Local MiaCardapio.lnk", "Agente Local MIA.lnk"):
                v = d / velho
                if v.exists():
                    v.unlink()
        except Exception as e:
            _log(f"[ATALHO] {d}: {e}")
    try:
        _atalho(destino.parent / "Dados do agente (config e log).lnk", data_dir, data_dir, "Config e log do agente")
        (destino.parent / "LEIA-ME.txt").write_text(
            f"{NOME_AGENTE}\n\nPrograma: {destino}\nConfig e log: {data_dir}\n"
            "O agente abre sozinho com o Windows e fica na bandeja (icone azul perto do relogio).\n"
            "Para reinstalar ou ligar em outro computador: painel ComprouTai > Integracoes > Impressoras.\n",
            encoding="utf-8")
    except Exception as e:
        _log(f"[ATALHO] pasta: {e}")


def registrar_inicio_automatico(destino):
    if T.get("sem_registro"):
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_VALUE, 0, winreg.REG_SZ, f'"{destino}"')
    except Exception as e:
        _log(f"[REGISTRO] {e}")


def abrir_agente(destino):
    if T.get("sem_abrir"):
        return
    subprocess.Popen([str(destino)], cwd=str(destino.parent),
                     creationflags=_DETACHED | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)


# ── Interface ─────────────────────────────────────────────────────────────────
PASSOS = ["Conectar com a loja", "Baixar o agente", "Instalar", "Abrir o agente"]


class Instalador:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title(f"{PRODUTO} · Instalar agente de impressão")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.update_idletasks()
        try:
            self.root.state("zoomed")   # tela cheia com barra de titulo e taskbar
        except Exception:
            self.root.geometry("900x640")
        # Vem para a FRENTE de verdade: o lojista clicou no arquivo e precisa ver a janela
        # (se abrisse atras do navegador, pareceria que "nao abriu nada").
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(800, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass
        self.saida = 0
        self.loja = ""
        _log(f"=== instalador {INSTALADOR_VERSAO} | exe={Path(sys.executable).name} | pc={socket.gethostname()}")

        origem = self._descobrir_origem()
        if origem:
            self.tela_instalando(origem)
        else:
            self.tela_codigo()

    # origem = (tipo, valor, silenciosa)
    def _descobrir_origem(self):
        c = codigo_do_nome_do_exe()
        if c:
            _log(f"[ORIGEM] codigo no nome do arquivo: {c}")
            return ("codigo", c, False)
        c = codigo_da_linha_de_comando()
        if c:
            _log(f"[ORIGEM] codigo na linha de comando: {c}")
            return ("codigo", c, False)
        cb = credencial_do_clipboard(self.root)
        if cb:
            _log(f"[ORIGEM] {cb[0]} na area de transferencia")
            return (cb[0], cb[1], True)
        _log("[ORIGEM] nenhuma: pedindo o codigo")
        return None

    # helpers de janela
    def _limpar(self):
        for w in self.root.winfo_children():
            w.destroy()

    def _centro(self):
        self._limpar()
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=BG)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        return inner

    def _after(self, *args):
        try:
            if self.root.winfo_exists():
                self.root.after(0, *args)
        except tk.TclError:
            pass

    def _fechar(self, codigo_saida=None):
        if codigo_saida is not None:
            self.saida = codigo_saida
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _wrap(self):
        return int(self.root.winfo_screenwidth() * 0.55)

    def _botao(self, pai, texto, cmd, primario=True):
        return tk.Button(pai, text=texto, command=cmd, bg=ACCENT if primario else BG2,
                         fg="white" if primario else TEXT2, activebackground=ACCENT, activeforeground="white",
                         font=("Segoe UI", 13, "bold" if primario else "normal"), relief="flat", cursor="hand2",
                         padx=22, pady=12, bd=0)

    # ── Tela: digitar o codigo ────────────────────────────────────────────────
    def tela_codigo(self, aviso=None):
        inner = self._centro()
        tk.Label(inner, text="🔑", font=("Segoe UI Emoji", 60), bg=BG, fg=TEXT).pack(pady=(0, 10))
        tk.Label(inner, text="Digite o código de instalação", font=("Segoe UI", 28, "bold"),
                 bg=BG, fg=TEXT).pack()
        tk.Label(inner, text=f"Ele aparece {ONDE_PEGAR_CODIGO}, no passo 1.\n"
                             "Tem o formato CT-XXXX-XXXX. Se você baixou pelo painel, o código já vem no "
                             "nome do arquivo e esta tela não aparece.",
                 font=("Segoe UI", 13), bg=BG, fg=TEXT2, wraplength=self._wrap(), justify="center").pack(pady=(8, 22))
        if aviso:
            tk.Label(inner, text=aviso, font=("Segoe UI", 12), bg=BG, fg=YELLOW,
                     wraplength=self._wrap(), justify="center").pack(pady=(0, 14))
        var = tk.StringVar()
        ent = tk.Entry(inner, textvariable=var, font=("Consolas", 22), width=18, justify="center",
                       bg="#0b1220", fg=TEXT, insertbackground=TEXT, relief="flat",
                       highlightthickness=2, highlightbackground=BG2, highlightcolor=ACCENT)
        ent.pack(ipady=10)
        ent.focus_set()
        erro = tk.StringVar(value="")
        tk.Label(inner, textvariable=erro, font=("Segoe UI", 12), bg=BG, fg=RED,
                 wraplength=self._wrap()).pack(pady=(8, 4))

        def conectar(*_):
            texto = var.get().strip()
            c = normalizar_codigo(texto)
            if c:
                self.tela_instalando(("codigo", c, False))
            elif parece_token(texto):
                self.tela_instalando(("token", texto, False))
            else:
                erro.set("Confira o código: são 2 blocos de 4 letras/números, como CT-7K3M-9Q2F.")
        ent.bind("<Return>", conectar)
        btns = tk.Frame(inner, bg=BG)
        btns.pack(pady=(12, 0))
        self._botao(btns, "Conectar", conectar).pack(side="left", padx=8)
        self._botao(btns, "Fechar", lambda: self._fechar(1), primario=False).pack(side="left", padx=8)
        if T.get("auto_fechar_ms"):
            self.root.after(int(T["auto_fechar_ms"]), lambda: self._fechar(3))

    # ── Tela: instalando (checklist) ──────────────────────────────────────────
    def tela_instalando(self, origem):
        inner = self._centro()
        tk.Label(inner, text="⬇️", font=("Segoe UI Emoji", 56), bg=BG, fg=TEXT).pack(pady=(0, 8))
        self.titulo_var = tk.StringVar(value="Instalando o agente de impressão")
        tk.Label(inner, textvariable=self.titulo_var, font=("Segoe UI", 28, "bold"), bg=BG, fg=TEXT).pack()
        self.sub_var = tk.StringVar(value="Conectando com a loja…")
        tk.Label(inner, textvariable=self.sub_var, font=("Segoe UI", 14), bg=BG, fg=TEXT2,
                 wraplength=self._wrap()).pack(pady=(6, 24))

        lista = tk.Frame(inner, bg=BG)
        lista.pack()
        self.passo_vars = []
        for p in PASSOS:
            row = tk.Frame(lista, bg=BG)
            row.pack(anchor="w", pady=4)
            icone = tk.StringVar(value="○")
            lbl_i = tk.Label(row, textvariable=icone, font=("Segoe UI", 16), bg=BG, fg=TEXT2, width=2)
            lbl_i.pack(side="left")
            lbl_t = tk.Label(row, text=p, font=("Segoe UI", 15), bg=BG, fg=TEXT2, anchor="w")
            lbl_t.pack(side="left", padx=(6, 0))
            self.passo_vars.append((icone, lbl_i, lbl_t))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("ct.Horizontal.TProgressbar", troughcolor=BG2, background=ACCENT, thickness=18)
        self.pbar = ttk.Progressbar(inner, length=min(640, int(self.root.winfo_screenwidth() * 0.4)),
                                    mode="indeterminate", style="ct.Horizontal.TProgressbar")
        self.pbar.pack(pady=(26, 6))
        self.pbar.start(12)
        self.detalhe_var = tk.StringVar(value="")
        tk.Label(inner, textvariable=self.detalhe_var, font=("Segoe UI", 11), bg=BG, fg="#64748b",
                 wraplength=self._wrap()).pack()
        Thread(target=self._instalar, args=(origem,), daemon=True).start()

    def _passo(self, i, estado, detalhe=""):
        def aplicar():
            icone, lbl_i, lbl_t = self.passo_vars[i]
            cor = {"ativo": ACCENT, "ok": GREEN, "erro": RED}.get(estado, TEXT2)
            icone.set({"ativo": "●", "ok": "✓", "erro": "✕"}.get(estado, "○"))
            lbl_i.config(fg=cor)
            lbl_t.config(fg=TEXT if estado in ("ativo", "ok") else (RED if estado == "erro" else TEXT2))
            self.detalhe_var.set(detalhe)
        self._after(aplicar)

    def _progresso(self, pct, total):
        def aplicar():
            if str(self.pbar["mode"]) != "determinate":
                self.pbar.stop()
                self.pbar.config(mode="determinate", maximum=100)
            self.pbar["value"] = pct
            self.detalhe_var.set(f"{pct}% de {total // 1024 // 1024} MB")
        self._after(aplicar)

    def _indeterminado(self):
        def aplicar():
            self.pbar.config(mode="indeterminate")
            self.pbar.start(12)
        self._after(aplicar)

    def _instalar(self, origem):
        tipo, valor, silenciosa = origem
        passo = 0
        try:
            # 1) credencial -> loja
            self._passo(0, "ativo", "código " + valor if tipo == "codigo" else "token de API")
            try:
                if tipo == "codigo":
                    cred = credencial_pendente(valor)
                    if cred:
                        _log(f"[CLAIM] {valor} ja resgatado neste computador; reaproveitando a chave")
                    else:
                        cred = resgatar_codigo(valor)
                        guardar_pendente(valor, cred)
                else:
                    cred = validar_token(valor)
            except ErroInstalacao as e:
                if silenciosa:
                    _log(f"[ORIGEM] credencial da area de transferencia recusada: {e}")
                    self._after(self.tela_codigo, "O que estava na área de transferência não serviu. Digite o código do painel.")
                    return
                raise
            self.loja = cred.get("restaurant_name") or ""
            self._passo(0, "ok")
            self._after(self.sub_var.set, f"para {self.loja}" if self.loja else "Loja conectada")
            self._after(self.titulo_var.set, "Instalando o agente de impressão")

            # 2) download
            passo = 1
            self._passo(1, "ativo", "verificando a versão atual…")
            url, versao = ler_versao_publicada()
            self._passo(1, "ativo", f"baixando v{versao}…")
            tmp_dir = Path(tempfile.mkdtemp(prefix="comproutai_"))
            tmp_exe = tmp_dir / EXE_AGENTE
            baixar_agente(url, tmp_exe, self._progresso)
            self._indeterminado()
            self._passo(1, "ok")

            # 3) instalar
            passo = 2
            pasta = pasta_instalacao()
            self._passo(2, "ativo", str(pasta))
            destino = pasta / EXE_AGENTE
            instalar_exe(tmp_exe, destino)
            data_dir = pasta_dados()
            gravar_config(data_dir, cred)
            apagar_pendente()
            criar_atalhos(destino, data_dir)
            registrar_inicio_automatico(destino)
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self._passo(2, "ok")
            _log(f"[OK] v{versao} em {destino}; config em {data_dir}; loja={self.loja!r}")

            # 4) abrir
            passo = 3
            self._passo(3, "ativo")
            abrir_agente(destino)
            self._passo(3, "ok")
            self._after(self.tela_concluido, versao, pasta)
        except ErroInstalacao as e:
            self._passo(passo, "erro")
            self._after(self.tela_erro, str(e), e.pode_digitar, origem)
        except Exception as e:
            _log(f"[ERRO] inesperado: {e!r}")
            self._passo(passo, "erro")
            self._after(self.tela_erro, f"Erro inesperado: {e}", False, origem)

    # ── Tela: concluido ───────────────────────────────────────────────────────
    def tela_concluido(self, versao, pasta):
        inner = self._centro()
        tk.Label(inner, text="🎉", font=("Segoe UI Emoji", 64), bg=BG, fg=TEXT).pack(pady=(0, 12))
        tk.Label(inner, text="Pronto! Agente instalado e conectado", font=("Segoe UI", 28, "bold"),
                 bg=BG, fg=TEXT).pack()
        loja = f" da {self.loja}" if self.loja else ""
        tk.Label(inner, text=f"O agente de impressão{loja} (v{versao}) está aberto e fica na bandeja, "
                             "perto do relógio.\n\nÚltimo passo, na janela do agente que abriu: "
                             "aba Impressoras, escolha a impressora de cada setor.",
                 font=("Segoe UI", 14), bg=BG, fg=TEXT2, wraplength=self._wrap(), justify="center").pack(pady=(14, 8))
        tk.Label(inner, text=f"Instalado em {pasta}", font=("Segoe UI", 11), bg=BG, fg="#64748b").pack(pady=(0, 20))
        self._botao(inner, "Fechar", lambda: self._fechar(0)).pack()
        self.root.after(int(T.get("auto_fechar_ms", 6000)), lambda: self._fechar(0))

    # ── Tela: erro ────────────────────────────────────────────────────────────
    def tela_erro(self, msg, pode_digitar, origem):
        inner = self._centro()
        tk.Label(inner, text="❌", font=("Segoe UI Emoji", 56), bg=BG, fg=TEXT).pack(pady=(0, 12))
        tk.Label(inner, text="Não deu para concluir", font=("Segoe UI", 26, "bold"), bg=BG, fg=TEXT).pack()
        tk.Label(inner, text=msg, font=("Segoe UI", 14), bg=BG, fg=RED, wraplength=self._wrap(),
                 justify="center").pack(pady=(14, 6))
        tk.Label(inner, text="Nada foi alterado neste computador. Se precisar, chame o suporte e mostre esta tela.",
                 font=("Segoe UI", 12), bg=BG, fg=TEXT2, wraplength=self._wrap()).pack(pady=(0, 20))
        btns = tk.Frame(inner, bg=BG)
        btns.pack()
        if pode_digitar:
            self._botao(btns, "Digitar o código", self.tela_codigo).pack(side="left", padx=8)
            self._botao(btns, "Tentar de novo", lambda: self.tela_instalando(origem), primario=False).pack(side="left", padx=8)
        else:
            self._botao(btns, "Tentar de novo", lambda: self.tela_instalando(origem)).pack(side="left", padx=8)
        self._botao(btns, "Fechar", lambda: self._fechar(2), primario=False).pack(side="left", padx=8)
        if T.get("auto_fechar_ms"):
            self.root.after(int(T["auto_fechar_ms"]), lambda: self._fechar(2))

    def run(self):
        self.root.mainloop()
        return self.saida


if __name__ == "__main__":
    sys.exit(Instalador().run())
