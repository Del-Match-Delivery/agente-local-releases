"""Agente Local v3.4 - GUI na main thread, polling em background"""
import asyncio, json, logging, sys, time, threading, os, subprocess, winreg, queue, hashlib, socket, re
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import urllib.request, urllib.error

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    import win32print
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import serial, serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

# Selfcheckout (SCO): modulo OPCIONAL, hoje NAO empacotado. HAS_SCO/mod_sco eram USADOS
# (salvar() da config em ~3084 e o dashboard em ~2889) mas NUNCA definidos -> NameError:
# o salvar() quebrava ANTES de salvar_config() (config local nao persistia) e o dashboard
# quebraria se a aba SCO fosse tocada. Definindo aqui, os 'if HAS_SCO:' viram no-op seguro.
try:
    import selfcheckout as mod_sco
    HAS_SCO = True
except Exception:
    HAS_SCO = False
    mod_sco = None

# Esconde a janela de console dos subprocessos (tasklist/taskkill/powershell). O app e
# windowed (compilado com console=False); SEM esta flag, CADA subprocess.run PISCA um CMD
# na tela do cliente. Como o watchdog da eleicao roda tasklist a cada 15s, dava "o cmd
# fica abrindo toda hora". (subprocess.CREATE_NO_WINDOW = 0x08000000)
_NO_WINDOW = 0x08000000

BASE_DIR     = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
# Garante que o log sempre fica na pasta do exe, nao na pasta de trabalho
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent

# ---------------------------------------------------------------------------
# DATA_DIR: pasta UNICA e ESTAVEL para config/log/estado (independente de onde
# o .exe roda). ANTES o config.json ficava colado ao .exe (BASE_DIR); com varias
# copias do agente em pastas diferentes, cada uma tinha SEU config, e apos um
# update o agente relancava "a copia que sobrou" — o lojista abria e a config
# aparecia trocada/antiga. Fixando o config em %LOCALAPPDATA%\AgenteLocalMIA,
# QUALQUER .exe (de qualquer pasta) le/grava SEMPRE a mesma config. O .exe/.bat
# de update continuam em BASE_DIR (sao artefatos de instalacao, nao dados).
def _resolver_data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "AgenteLocalMIA"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback extremo: se nao der pra criar em LOCALAPPDATA, usa a pasta do exe
        return BASE_DIR
    return d

DATA_DIR     = _resolver_data_dir()
CONFIG_PATH  = DATA_DIR / "config.json"
LOG_PATH     = DATA_DIR / "agente.log"
# Sinal "abrir janela" (v5.78): quando o lojista clica no .exe com o agente JA rodando (na
# bandeja), a copia nova escreve este arquivo e a instancia que ja roda o consome e mostra
# o painel de Status. Antes, o clique nao fazia NADA visivel ("esta no gerenciador de
# tarefas mas nao abre"): a copia nova perdia a eleicao e morria em ~15s, em silencio.
SHOW_FLAG    = DATA_DIR / "abrir_janela.flag"

def _migrar_config_para_data_dir():
    """Na 1a execucao com config em DATA_DIR: se ainda nao existe config la, procura
    o config.json existente mais RECENTE nos locais historicos (pasta do exe atual +
    pastas conhecidas de copias antigas) e copia para DATA_DIR. Assim o lojista NUNCA
    perde a config ao migrar de versao. Nao apaga o original (seguranca)."""
    if CONFIG_PATH.exists():
        return  # ja migrado / ja existe config no local definitivo
    candidatos = []
    # 1) config ao lado do exe atual (o caso mais comum)
    candidatos.append(BASE_DIR / "config.json")
    # 2) pastas historicas onde o agente pode ter rodado antes
    try:
        _home = Path.home()
        for p in (
            _home / "Desktop" / "Agente Local" / "config.json",
            _home / "Desktop" / "Agente Local" / "dist" / "config.json",
            _home / "Desktop" / "Agente Local" / "agente-local-releases" / "config.json",
            _home / "OneDrive" / "Desktop" / "Agente Local" / "config.json",
        ):
            candidatos.append(p)
    except Exception:
        pass
    # Escolhe o config VALIDO (com token/restaurant_id) mais recente
    melhor = None
    melhor_mtime = -1
    for c in candidatos:
        try:
            if not c.exists():
                continue
            dados = json.loads(c.read_text(encoding="utf-8"))
            if not isinstance(dados, dict):
                continue
            # so considera config "de verdade" (nao um esqueleto vazio)
            if not (dados.get("token") or dados.get("restaurant_id")):
                continue
            mt = c.stat().st_mtime
            if mt > melhor_mtime:
                melhor_mtime = mt
                melhor = c
        except Exception:
            continue
    if melhor is not None:
        try:
            CONFIG_PATH.write_text(melhor.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

_migrar_config_para_data_dir()
SUPABASE_URL  = "https://szlyzyflalerxuyxfxzh.supabase.co"
SUPABASE_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN6bHl6eWZsYWxlcnh1eXhmeHpoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMDkyNTQsImV4cCI6MjA4OTU4NTI1NH0.2UewBvzucel7wiuXv14mvgDmi_FmzCc-Zh2CISL9_VI"
DEVICE_NAME        = socket.gethostname()
DEVICE_FINGERPRINT = hashlib.sha256(DEVICE_NAME.encode()).hexdigest()[:32]

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_PATH, encoding="utf-8")])
log = logging.getLogger("agente")

_gui_queue      = queue.Queue()
_root           = None
_tray_icon      = None
status_poll     = "Iniciando..."
_start_time     = time.time()
_stats = {
    "total_impressos": 0,
    "hoje": 0,
    "hoje_data": "",
    "erros": 0,
    "ultimo_job": None,
    "ultimo_erro": None,
    "ultima_impressora": "",
    "historico": [],   # lista dos ultimos 50 jobs impressos com sucesso
    "falhas": [],      # lista das ultimas 100 falhas com causa, tipo, pedido, hora
    "alertas": [],     # alertas ativos (ex: impressora sem mapeamento, jobs stuck)
}

def _registrar_falha(job_id, causa, detalhe, tipo="", pedido="", cliente="", impressora=""):
    """Registra uma falha no historico de diagnostico. Thread-safe via append."""
    entrada = {
        "hora": time.strftime("%H:%M:%S"),
        "data": time.strftime("%d/%m/%Y"),
        "job_id": job_id or "",
        "causa": causa,
        "detalhe": detalhe,
        "tipo": tipo,
        "pedido": pedido,
        "cliente": cliente,
        "impressora": impressora,
    }
    _stats["falhas"].insert(0, entrada)
    if len(_stats["falhas"]) > 100:
        _stats["falhas"] = _stats["falhas"][:100]
    _stats["erros"] += 1
    _stats["ultimo_erro"] = detalhe[:120]
    log.error(f"[FALHA] {causa} | job={job_id} | {detalhe}")

_CONFIG_VAZIA = {"token":"","anon_key":"","restaurant_id":"","restaurant_name":"","poll_interval":3,
                 "impressoras":[],"balancas":[],"ultima_sincronizacao":""}

def carregar_config():
    """Le o config.json. v5.79: TOLERANTE. Ate a v5.78 era json.load em utf-8 estrito, e um
    config.json com BOM (Bloco de Notas antigo grava BOM; PowerShell Set-Content tambem) ou
    salvo em ANSI (acento no nome da loja) derrubava o agente NO BOOT com
    'Unexpected UTF-8 BOM' / UnicodeDecodeError — 'nao abre', sem nada no log. Agora:
    utf-8-sig (aceita BOM) -> cp1252 -> se o JSON continuar ilegivel, guarda o arquivo como
    config.ilegivel.bak, loga e sobe com config vazia (abre as boas-vindas) em vez de morrer."""
    if not CONFIG_PATH.exists():
        return dict(_CONFIG_VAZIA)
    try:
        bruto = CONFIG_PATH.read_bytes()
    except Exception as e:
        log.error(f"[CONFIG] Nao consegui ler {CONFIG_PATH}: {e}; subindo com config vazia")
        return dict(_CONFIG_VAZIA)
    texto = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texto = bruto.decode(enc); break
        except Exception:
            continue
    if texto is not None:
        try:
            dados = json.loads(texto)
            if isinstance(dados, dict):
                return dados
        except Exception as e:
            log.error(f"[CONFIG] config.json invalido ({e})")
    try:
        bak = CONFIG_PATH.with_name("config.ilegivel.bak")
        bak.write_bytes(bruto)
        log.error(f"[CONFIG] config.json ilegivel; copia guardada em {bak}. Subindo com config vazia.")
    except Exception:
        pass
    return dict(_CONFIG_VAZIA)

def salvar_config(c):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)

cfg = carregar_config()

# v5.81: a escala de fonte ganhou o passo "Media" (altura dupla, GS ! 0x01) entre o normal e o
# 2x2. A escala antiga era 0/1/2 = normal/2x2/3x3; a nova e 0/1/2/3 = normal/media/grande/extra.
# Migra o valor salvo UMA vez (flag fonte_v2) para a loja que ja usava "Grande" continuar
# imprimindo no MESMO tamanho de antes (2x2), e nao cair para altura dupla sem ninguem pedir.
if isinstance(cfg, dict) and not cfg.get("fonte_v2"):
    if cfg.get("font_size") in (1, 2, "1", "2"):
        cfg["font_size"] = int(cfg["font_size"]) + 1
    cfg["fonte_v2"] = True
    # So persiste se e uma config de verdade (mesma regra do migrador de pastas): salvar um
    # esqueleto vazio aqui criaria config.json antes das boas-vindas.
    if cfg.get("token") or cfg.get("restaurant_id"):
        try: salvar_config(cfg)
        except Exception: pass

def listar_impressoras_windows():
    if HAS_WIN32:
        try: return [p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        except: pass
    try:
        r = subprocess.run(["powershell","-Command","Get-Printer | Select-Object -ExpandProperty Name"],
                           capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except: return []

def listar_portas_serial():
    if HAS_SERIAL:
        try: return [p.device for p in serial.tools.list_ports.comports()]
        except: pass
    return ["COM1","COM2","COM3","COM4","COM5","COM6"]

def _criar_icone(cor):
    img = Image.new("RGBA",(64,64),(0,0,0,0))
    dc  = ImageDraw.Draw(img)
    dc.ellipse([4,4,60,60],fill=cor)
    dc.rectangle([20,28,44,36],fill="white")
    dc.rectangle([28,20,36,44],fill="white")
    return img

def _atualizar_icone():
    if _tray_icon and HAS_TRAY:
        cor = (34,197,94) if "Ativo" in status_poll else (239,68,68)
        _tray_icon.icon  = _criar_icone(cor)
        _tray_icon.title = f"Agente Local - {status_poll}"


def _garantir_startup():
    """Garante que registro e atalho de startup sempre apontam para AgenteLocal.exe"""
    try:
        if getattr(sys, 'frozen', False):
            exe = str(BASE_DIR / "AgenteLocal.exe")
            if not Path(exe).exists():
                exe = str(Path(sys.executable).resolve())
        else:
            exe = str((Path(__file__).resolve().parent / "dist" / "AgenteLocal.exe"))

        if not Path(exe).exists():
            log.warning(f"[STARTUP] exe nao encontrado: {exe}")
            return

        # Valor do registro SEMPRE com aspas para suportar caminhos com espacos
        reg_val = f'"{exe}"'

        # Corrige registro HKCU Run
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
            try:
                val, _ = winreg.QueryValueEx(key, "AgenteLocal")
                # Aceita com ou sem aspas no valor existente
                if val.strip('"') != exe:
                    winreg.SetValueEx(key, "AgenteLocal", 0, winreg.REG_SZ, reg_val)
                    log.info(f"[STARTUP] Registro corrigido: {val} -> {reg_val}")
                else:
                    log.info(f"[STARTUP] Registro OK: {reg_val}")
            except FileNotFoundError:
                winreg.SetValueEx(key, "AgenteLocal", 0, winreg.REG_SZ, reg_val)
                log.info(f"[STARTUP] Registro criado: {reg_val}")
            winreg.CloseKey(key)
        except Exception as e:
            log.warning(f"[STARTUP] Registro falhou: {e}")

        # Corrige atalho .lnk na pasta Startup
        try:
            startup_folder = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if startup_folder.exists():
                lnk_path = startup_folder / "AgenteLocal MIA.lnk"

                # Remove atalhos antigos com outros nomes que possam existir
                for lnk_antigo in startup_folder.glob("AgenteLocal*.lnk"):
                    if lnk_antigo != lnk_path:
                        try:
                            lnk_antigo.unlink()
                            log.info(f"[STARTUP] Atalho antigo removido: {lnk_antigo.name}")
                        except Exception:
                            pass

                # Verifica se o atalho correto ja aponta para o exe certo
                precisa_recriar = True
                if lnk_path.exists():
                    try:
                        check_ps = f'$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut("{lnk_path}"); Write-Output $s.TargetPath'
                        r = subprocess.run(["powershell", "-NoProfile", "-Command", check_ps],
                                           capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW)
                        target_atual = r.stdout.strip().strip('"')
                        if target_atual.lower() == exe.lower():
                            precisa_recriar = False
                            log.info(f"[STARTUP] Atalho OK: {lnk_path}")
                    except Exception:
                        pass

                if precisa_recriar:
                    ps = (f'$ws=New-Object -ComObject WScript.Shell;'
                          f'$s=$ws.CreateShortcut("{lnk_path}");'
                          f'$s.TargetPath="{exe}";'
                          f'$s.WorkingDirectory="{Path(exe).parent}";'
                          f'$s.WindowStyle=7;'
                          f'$s.Save()')
                    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                   capture_output=True, timeout=10, creationflags=_NO_WINDOW)
                    log.info(f"[STARTUP] Atalho criado/corrigido -> {exe}")
        except Exception as e:
            log.warning(f"[STARTUP] Atalho falhou: {e}")

    except Exception as e:
        log.error(f"[STARTUP] Erro: {e}")

def _auto_reparo_boot():
    """Auto-reparo executado no boot. Corrige os estados que faziam 'clico e nao abre':
      1. Se estou rodando como AgenteLocal_X.exe (nome versionado, resquicio de update que
         nao renomeou), me copio como AgenteLocal.exe — o nome FIXO que o atalho/registro
         esperam. Assim o proximo clique/boot encontra o arquivo certo.
      2. Remove exes versionados orfaos na pasta (limpeza).
    Nunca lanca excecao (best-effort). So roda em modo frozen (exe real)."""
    if not getattr(sys, 'frozen', False):
        return
    try:
        eu = Path(sys.executable)
        alvo = eu.parent / "AgenteLocal.exe"
        # 0) v5.78: restos de update. update_lock.tmp e a trava do bat ('if exist lock exit');
        #    ate a v5.77 o bat morria no proprio taskkill e deixava a trava PARA SEMPRE => todo
        #    update futuro saia na 1a linha, em silencio. Um bat vivo segura a trava por ~1 min;
        #    com mais de 10 min e lixo. Zera tambem o contador de tentativas quando ele aponta
        #    para a MINHA versao: o update para ela deu certo.
        for _nome in ("update_lock.tmp", "update_apply.bat"):
            try:
                _f = eu.parent / _nome
                if _f.exists() and (time.time() - _f.stat().st_mtime) > 600:
                    _f.unlink(); log.info(f"[REPARO] Removido resto de update antigo: {_nome}")
            except Exception:
                pass
        try:
            _t = DATA_DIR / "update_tentativas.json"
            if _t.exists() and json.loads(_t.read_text(encoding="utf-8")).get("version") == str(CURRENT_VERSION):
                _t.unlink()
        except Exception:
            pass
        # 1) Se meu nome nao e o fixo, garante que existe um AgenteLocal.exe atualizado
        if eu.name.lower() != "agentelocal.exe":
            try:
                precisa = (not alvo.exists()) or (alvo.stat().st_size != eu.stat().st_size)
                if precisa:
                    import shutil
                    shutil.copy2(str(eu), str(alvo))
                    log.info(f"[REPARO] Normalizei o nome do exe: {eu.name} -> AgenteLocal.exe")
            except Exception as e:
                log.warning(f"[REPARO] Nao consegui normalizar nome do exe: {e}")
        # 2) Remove versionados orfaos (mantem o AgenteLocal.exe e a mim mesmo)
        try:
            for f in eu.parent.glob("AgenteLocal_*.exe"):
                if f.resolve() == eu.resolve():
                    continue  # nao apago a mim mesmo enquanto rodo
                try:
                    f.unlink()
                    log.info(f"[REPARO] Removido exe versionado orfao: {f.name}")
                except Exception:
                    pass
        except Exception:
            pass
    except Exception as e:
        log.debug(f"[REPARO] Erro no auto-reparo: {e}")

def iniciar_tray():
    global _tray_icon
    if not HAS_TRAY: return
    menu = pystray.Menu(
        pystray.MenuItem("Status",        lambda _: _gui_queue.put("dashboard"), default=True),
        pystray.MenuItem("Configuracoes", lambda _: _gui_queue.put("config")),
        pystray.MenuItem("Ver Log",       lambda _: _gui_queue.put("log")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Reiniciar",      lambda _: _gui_queue.put("reiniciar")),
        pystray.MenuItem("Sair",          lambda _: _gui_queue.put("sair")),
    )
    _tray_icon = pystray.Icon("AgenteLocal", _criar_icone((239,68,68)), "Agente Local", menu)
    threading.Thread(target=_tray_icon.run, daemon=True).start()

def _ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _post(url, data, token, timeout=30, retries=2):
    body = json.dumps(data).encode()
    headers = {
        "Content-Type": "application/json",
        "x-api-key": token,
        "apikey": SUPABASE_ANON,
        "Authorization": f"Bearer {SUPABASE_ANON}",
    }
    for tentativa in range(retries + 1):
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
                return json.loads(r.read()), r.status
        except urllib.error.HTTPError as e:
            try: return json.loads(e.read()), e.code
            except: return {"error":str(e)}, e.code
        except Exception as e:
            if tentativa < retries:
                log.warning(f"HTTP tentativa {tentativa+1} falhou: {e} - retentando...")
                time.sleep(2)
            else:
                log.error(f"HTTP: {e}")
                return None, 0

_agents_online = []  # Atualizado a cada poll
_token_invalido = False  # Evita abrir configuracoes multiplas vezes
_paper_width_servidor = None  # printer_settings.paper_width do cardapio (32/42/48), vindo do poll (v5.79)
# v5.82: interruptor "Imprimir codigo de barras do produto" da loja (printer_settings.print_barcode),
# vindo do poll. True/False quando a loja tem a linha de ajustes; None quando o servidor nao mandou.
# Serve de fallback para job que chega SEM a chave print_barcode no content (ver _flag_print_barcode).
_print_barcode_servidor = None
_config_auto_ja = False  # ja auto-abrimos a config nesta sessao (NUNCA reabrir sozinho depois)
_janela_config = None    # janela de config unica (singleton: nao empilha nem rouba foco no automatico)
_janela_dashboard = None # janela de status unica (singleton)

def ef_poll_jobs():
    global _agents_online, _token_invalido, _config_auto_ja, _paper_width_servidor, _print_barcode_servidor
    imps = cfg.get("impressoras", [])
    # Declara TODAS as areas cadastradas (com ou sem nome_impressora).
    # O agente recebe todos os jobs das suas areas e processa apenas os que tem impressora mapeada.
    # Jobs sem mapeamento local sao ignorados silenciosamente — o outro agente na mesma area processa.
    mapa_tipo_area = {"receipt":"caixa","kitchen":"cozinha","bar":"bar","delivery":"delivery","pickup":"balcao"}
    areas_set = set()
    for i in imps:
        area = i.get("area","").strip().lower()
        ptype = i.get("printer_type","").strip().lower()
        if area:
            areas_set.add(area)
        elif ptype:
            areas_set.add(mapa_tipo_area.get(ptype, ptype))
    areas = list(areas_set)
    payload = {
        "action": "poll",
        "device_name": DEVICE_NAME,
        "device_fingerprint": DEVICE_FINGERPRINT,
    }
    if areas:
        payload["areas"] = areas
    log.info(f"[POLL] Enviando areas={areas} | token: {cfg.get('token','')[:12]}...")
    resp,s = _post(f"{SUPABASE_URL}/functions/v1/agent-unified-poll", payload, cfg.get("token",""), timeout=45, retries=2)
    if s==200 and resp:
        _token_invalido = False
        _agents_online = resp.get("agents_online", [])
        # v5.79: largura do papel configurada no cardapio (printer_settings.paper_width: 32/42/48).
        # O poll SEMPRE mandou isto em resp.settings e o agente ignorava. Jobs criados pelo
        # trigger nao trazem paper_width no content, entao o cupom saia em 48 colunas numa
        # impressora de 42 (precos quebrando em duas linhas). Guardado aqui, aplicado em proc_job.
        try:
            # v5.82: o agent-unified-poll devolve a linha de printer_settings em resp.config.settings
            # (nao em resp.settings — ate a v5.81 este codigo lia a chave errada e nunca achava nada).
            _st = resp.get("settings")
            if not isinstance(_st, dict): _st = (resp.get("config") or {}).get("settings")
            if not isinstance(_st, dict): _st = {}
            _pw = _colunas_validas(_st.get("paper_width"))
            if _pw and _pw != _paper_width_servidor:
                log.info(f"[POLL] Largura do papel (cardapio): {_pw} colunas")
                _paper_width_servidor = _pw
            # v5.82: interruptor do codigo de barras da loja. Job criado pelo trigger do banco (todo
            # pedido do PDV nasce 'new' e o trigger cria o cupom antes do app), pelo create_public_order
            # ou pelo print-job-create chega SEM a chave print_barcode — so os hooks do app a gravam.
            # Guardado aqui e usado em _fmt quando o content nao decide (ver _flag_print_barcode).
            _pb = _st.get("print_barcode")
            _pb = True if _pb is True else (False if _pb is False else None)
            if _pb != _print_barcode_servidor:
                log.info("[POLL] Codigo de barras no cupom (ajuste da loja): "
                         + ("LIGADO" if _pb else ("desligado" if _pb is False else "sem ajuste no servidor")))
                _print_barcode_servidor = _pb
        except Exception:
            pass
        jobs = resp.get("print_jobs") or resp.get("jobs") or []
        if isinstance(resp, list): jobs = resp
        log.info(f"[POLL] OK areas={areas} jobs={len(jobs)} tipos={[j.get('printer_type') for j in jobs]} agentes={[a.get('device_name') for a in _agents_online]}")
        return jobs
    if s == 401:
        if not _token_invalido:
            _token_invalido = True
            log.error(f"[POLL] Token invalido (401)")
        # Abre a config UMA unica vez por sessao e SEM roubar foco (auto=True). NUNCA
        # reabrir sozinho a cada oscilacao 401<->200 nem empilhar janela — era isso que
        # ficava "abrindo na tela e nao deixava trabalhar".
        # IMPORTANTE: este bloco roda na THREAD DO POLL (nao a da GUI). NAO chamar metodos
        # Tkinter aqui (winfo_exists etc.) — Tkinter nao e thread-safe. A guarda de janela
        # unica fica no proprio abrir_config, que roda na thread da GUI via _root.after.
        if _root and not _config_auto_ja:
            _config_auto_ja = True
            _root.after(0, lambda: abrir_config(auto=True))
        # Nao loga a cada 3s para nao encher o log
    else:
        log.error(f"[POLL] {s}: {resp}")
    return []

def ef_update_job(jid, sv, em=None, pa=None):
    d={"job_id":jid,"status":sv}
    if em: d["error_message"]=em
    if pa: d["printed_at"]=pa
    _,s=_post(f"{SUPABASE_URL}/functions/v1/print-job-status",d,cfg.get("token",""))
    ok = s in (200,204)
    if not ok and sv == "printed":
        # Falha ao marcar como printed: job pode ser reprocessado e impresso de novo
        log.warning(f"[STATUS] Falha ao marcar job {jid} como printed (HTTP {s}) — pode reimprimir!")
        _registrar_falha(jid, "status_update_failed",
                         f"Job impresso localmente mas nao atualizado no servidor (HTTP {s}). Pode ser reimpresso automaticamente.",
                         tipo="status")
    return ok


def autoconfigurar(token):
    resp,s=_post(f"{SUPABASE_URL}/functions/v1/agent-unified-poll",{"action":"poll"},token)
    if s==200 and resp: return {"ok":True,"data":resp}
    err_msg = resp.get("error","Token invalido") if resp else "Sem resposta"
    if resp and "debug" in resp:
        err_msg += f"\nDebug: {resp['debug']}"
    return {"ok":False,"erro":err_msg}

def sincronizar_impressoras():
    """Busca impressoras atualizadas do servidor e atualiza config local.
    NUNCA sobrescreve mapeamento manual (nome_impressora, area, tipo) já feito pelo usuario."""
    token = cfg.get("token","")
    if not token: return
    payload = {"action":"poll","device_name":DEVICE_NAME,"device_fingerprint":DEVICE_FINGERPRINT}
    resp,s = _post(f"{SUPABASE_URL}/functions/v1/agent-unified-poll", payload, token)
    if s==200 and resp:
        printers = resp.get("config",{}).get("printers", resp.get("printers", []))
        if not printers: return
        iw = listar_impressoras_windows()
        # Index case-insensitive para preservar TUDO que o usuario configurou manualmente
        # Indexa por nome E por area/printer_type para achar mesmo se nome mudou no servidor
        imps_atuais_nome = {i.get("nome","").strip().lower(): i for i in cfg.get("impressoras",[])}
        imps_atuais_area = {i.get("area","").strip().lower(): i for i in cfg.get("impressoras",[])}
        imps_atuais_tipo = {i.get("printer_type","").strip().lower(): i for i in cfg.get("impressoras",[])}
        imps_novos = []

        def _auto_match(ns):
            """Tenta match automatico do nome do servidor com impressoras Windows."""
            if ns in iw: return ns
            m = next((x for x in iw if ns.upper() in x.upper() or x.upper() in ns.upper()), "")
            if not m and " " in ns:
                first = ns.split(" ")[0].upper()
                if len(first) > 2:
                    m = next((x for x in iw if first in x.upper()), "")
            return m

        for p in printers:
            ns = p.get("name",""); ts = p.get("printer_type","receipt")
            area_servidor = {"receipt":"caixa","kitchen":"cozinha","bar":"bar"}.get(ts,"caixa")

            # Busca impressora existente: primeiro por nome, depois por area, depois por tipo
            existente = (imps_atuais_nome.get(ns.strip().lower())
                         or imps_atuais_area.get(area_servidor)
                         or imps_atuais_tipo.get(ts))
            if existente:
                imp = dict(existente)
                imp["nome"] = ns  # atualiza nome (label) para o do servidor
                # PRESERVA area/printer_type configurados MANUALMENTE pelo usuario.
                # A sincronizacao NUNCA sobrescreve a area/tipo que o usuario ja salvou —
                # ex: usuario poe a Cozinha como area=caixa de proposito (imprime cupom completo).
                # So usa a sugestao do servidor se o campo estiver vazio na config local.
                if not str(existente.get("area","")).strip():
                    imp["area"] = area_servidor
                if not str(existente.get("printer_type","")).strip():
                    imp["printer_type"] = ts
                # Se nome_impressora estava vazio, tenta match automatico agora
                if not imp.get("nome_impressora"):
                    match = _auto_match(ns)
                    if match:
                        imp["nome_impressora"] = match
                        log.info(f"[SYNC] Auto-mapeou '{ns}' -> '{match}'")
                imps_novos.append(imp)
            else:
                # Nova impressora do servidor - tenta match automatico
                match = _auto_match(ns)
                imps_novos.append({"nome":ns,"area":area_servidor,"printer_type":ts,"nome_impressora":match,"tipo":"comum_win32","modo":"texto"})

        # Seguranca: nunca salvar se alguma impressora nova perdeu nome_impressora que a atual tinha
        imps_atuais_todos = cfg.get("impressoras", [])
        for imp_novo in imps_novos:
            chave = imp_novo.get("nome","").strip().lower()
            atual = imps_atuais_nome.get(chave) or imps_atuais_area.get(imp_novo.get("area","").strip().lower())
            if atual and atual.get("nome_impressora","").strip() and not imp_novo.get("nome_impressora","").strip():
                imp_novo["nome_impressora"] = atual["nome_impressora"]
                log.warning(f"[SYNC] Protegeu nome_impressora='{atual['nome_impressora']}' de '{imp_novo.get('nome','')}' contra sobrescrita")

        if str(imps_novos) != str(imps_atuais_todos):
            cfg["impressoras"] = imps_novos
            salvar_config(cfg)
            log.info(f"[SYNC] Impressoras atualizadas: {[i.get('nome') for i in imps_novos]}")

def ef_get_order(oid):
    resp,s=_post(f"{SUPABASE_URL}/functions/v1/agent-get-order",{"order_id":oid},cfg.get("token",""))
    if s==200 and resp: return resp
    log.error(f"[ORDER] Erro {oid}: {s}"); return None

def ef_enviar_peso(nome_balanca, peso_kg):
    try:
        payload = {
            "action": "scale_reading",
            "restaurant_id": cfg.get("restaurant_id",""),
            "scale_name": nome_balanca,
            "weight_kg": round(peso_kg, 3),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        _post(f"{SUPABASE_URL}/functions/v1/agente-print-jobs", payload, cfg.get("token",""))
    except Exception as e:
        log.debug(f"[PESO] Erro ao enviar: {e}")

_pesos_atuais = {}
_ultimo_envio_peso = {}

def _callback_peso(nome, peso_kg, status):
    global _pesos_atuais, _ultimo_envio_peso
    _pesos_atuais[nome] = {"peso": peso_kg, "status": status, "hora": time.strftime("%H:%M:%S")}
    log.debug(f"[PESO] {nome}: {peso_kg:.3f} kg")
    agora = time.time()
    if agora - _ultimo_envio_peso.get(nome, 0) >= 1.0:
        _ultimo_envio_peso[nome] = agora
        ef_enviar_peso(nome, peso_kg)

import unicodedata

# Tabela de codigo ESC/POS por encoding (ESC t n).
# O texto sempre foi codificado em cp850, mas o comando ESC t nunca era enviado — entao a
# impressora ficava no PC437 de fabrica, onde os bytes de 'ã'(C6) e 'õ'(E4) sao moldura
# (╞, Σ) e todo acento MAIUSCULO tambem quebra. Os minusculos (á é í ó ú â ê ô ç ü) tem o
# mesmo byte nas duas tabelas, por isso so "varios" acentos saiam errados, nao todos.
CP_TAB={"cp850":2,"cp437":0,"cp860":3,"cp858":19,"cp1252":16,"utf8":0,"ascii":0}
# "utf8" (v5.80): para impressora que vem em MODO UTF-8 (varios clones de 58 mm). Nesse modo o
# ESC t e ignorado e NENHUMA tabela de 1 byte funciona: o byte do acento e lido como inicio de
# uma sequencia UTF-8 e engole a letra seguinte ("Nao" -> "N" + lixo). Mandando o texto em UTF-8
# de verdade, sai certo. O ESC t 0 que vai junto e ignorado pela propria impressora.
# "ascii" (v5.80): tira TODOS os acentos (Pao, Maca, Acai). Nenhum byte >= 0x80 vai para a
# impressora, entao sai certo em QUALQUER modelo — inclusive mini impressora de 58 mm que
# ignora o ESC t, numera as tabelas diferente ou fica presa no modo chines.
# Caracteres que nao existem em cp850 e chegam do cadastro (aspas curvas, travessao...)
TRANS={"–":"-","—":"-","‒":"-","―":"-","‘":"'","’":"'",
       "‚":"'","“":'"',"”":'"',"„":'"',"…":"...","•":"*",
       "→":"->"," ":" ","​":"","⁄":"/","−":"-","ʼ":"'"}

# Codepage da impressora da VEZ (v5.80). Ate a v5.79 o codepage era UM so para o agente todo
# (cfg["codepage"], cp850, sem tela para mudar): o agente mandava ESC t 2 (PC850) para toda
# impressora. Mini impressoras de 58 mm (firmware generico) muitas vezes numeram as tabelas de
# outro jeito, ignoram o ESC t ou ficam no modo chines — e cada acento saia como outra letra.
# Agora cada impressora pode ter o seu ("Acentos" na tela Configuracoes). O valor fica num
# threading.local porque a impressao roda na thread do poll e o teste de acentos na da GUI.
_cp_local = threading.local()

def _normaliza_cp(v):
    e = str(v or "").strip().lower().replace("-", "").replace("_", "")
    return e if e in CP_TAB else ""

def _cp():
    """Encoding em uso: o da impressora da vez (_usar_codepage), senao cfg['codepage'], senao cp850."""
    o = getattr(_cp_local, "cp", None)
    if o: return o
    return _normaliza_cp(cfg.get("codepage", "cp850")) or "cp850"

def _cp_da_impressora(imp):
    """Codepage efetivo de uma impressora: imp['codepage'] > cfg['codepage'] > cp850."""
    return (_normaliza_cp((imp or {}).get("codepage"))
            or _normaliza_cp(cfg.get("codepage", "cp850")) or "cp850")

class _usar_codepage:
    """with _usar_codepage("cp860"): ... — formata/imprime com esse codepage nesta thread."""
    def __init__(self, cp): self.cp = _normaliza_cp(cp) or None
    def __enter__(self):
        self.ant = getattr(_cp_local, "cp", None); _cp_local.cp = self.cp; return self
    def __exit__(self, *a):
        _cp_local.cp = self.ant; return False

def _escpos_cp():
    """Comando ESC t que poe a impressora no mesmo codepage em que o texto e codificado.
    Vem precedido de FS . (cancela modo Kanji/duplo-byte): varias impressoras termicas
    clone (chips chineses genericos vendidos com firmware voltado ao mercado chines) ligam
    esse modo por padrao de fabrica. Com ele ativo, QUALQUER byte >=0x80 (acento em cp850,
    ou o separador '·'=0xFA) e pareado de 2 em 2 e desenhado como glifo CJK do proprio
    firmware, ignorando por completo a tabela escolhida pelo ESC t. FS . e no-op inofensivo
    em impressoras que ja nao usam esse modo, entao e seguro mandar sempre."""
    return bytes([0x1c,0x2e]) + bytes([0x1b,0x74,CP_TAB[_cp()]])

def _txt(s):
    """Normaliza texto pra impressao: compoe acentos (NFC) e troca o que nao existe no
    codepage por equivalente ASCII. Precisa rodar ANTES do calculo de colunas, senao
    'a'+acento separados contam 2 caracteres e desalinham a coluna de preco."""
    if not isinstance(s,str) or not s: return s
    s=unicodedata.normalize("NFC",s)
    s="".join(TRANS.get(c,c) for c in s)
    # v5.80: no modo ascii, as trocas que mudam o TAMANHO ('½'->'1/2', '€'->'EUR') acontecem aqui,
    # antes de qualquer medida de coluna; se ficassem so no _enc, a linha de preco passava do papel.
    if _cp() == "ascii":
        s="".join(_ASCII_EXTRA.get(c,c) for c in s)
    return s

def _norm(v):
    """Aplica _txt em todo texto do content, inclusive dentro de listas/dicts."""
    if isinstance(v,str): return _txt(v)
    if isinstance(v,list): return [_norm(x) for x in v]
    if isinstance(v,dict): return {k:_norm(x) for k,x in v.items()}
    return v

# Equivalentes usados SO no modo ascii, para simbolos que nao tem letra-base no NFKD e
# sumiriam do papel (ex.: "1º" viraria "1").
_ASCII_EXTRA = {"·":"-", "°":"o", "º":"o", "ª":"a", "×":"x", "½":"1/2", "¼":"1/4", "¾":"3/4",
                "€":"EUR", "£":"L", "¢":"c", "§":"S", "¿":"?", "¡":"!", "«":'"', "»":'"',
                "±":"+-", "²":"2", "³":"3", "µ":"u", "ß":"ss", "æ":"ae", "Æ":"AE", "ø":"o",
                "Ø":"O", "œ":"oe", "Œ":"OE"}

def _enc(texto):
    """Codifica pro codepage da impressora. Caractere sem equivalente perde o acento em vez
    de virar '?'; se nem assim couber (emoji, simbolo), e descartado. Substitui o antigo
    .encode('cp850','replace'), que enchia o cupom de '?'."""
    enc=_cp(); out=bytearray()
    for c in texto:
        if enc == "ascii" and c in _ASCII_EXTRA:   # v5.80: simbolo sem letra-base (º, ·, €...)
            out += _ASCII_EXTRA[c].encode("ascii"); continue
        try: out+=c.encode(enc); continue
        except UnicodeEncodeError: pass
        base="".join(x for x in unicodedata.normalize("NFKD",c) if not unicodedata.combining(x))
        try: out+=base.encode(enc)
        except UnicodeEncodeError: pass
    return bytes(out)

# 3 linhas curtas: ate o bloco utf8 impresso numa impressora que NAO e UTF-8 (2 bytes por acento
# viram 2 colunas) cabe em 32 colunas, sem a linha quebrar e confundir a leitura dos blocos.
_AMOSTRA_ACENTOS = ("Pão Maçã Açaí Café Avô", "ÁÉÍÓÚ ÂÊÔ ÃÕ Ç", "nº 1º 2ª 30°")
_CP_TESTE = ("cp850", "cp860", "cp1252", "cp858", "cp437", "utf8", "ascii")

def _bytes_teste_acentos():
    """Pagina de teste (v5.80): a MESMA frase com acentos impressa em cada tabela, cada uma com
    o seu ESC t. O lojista olha qual bloco saiu certo e escolhe essa opcao em 'Acentos'. Os
    rotulos sao ASCII puro (saem certos em qualquer tabela). Linhas de ate 30 colunas: cabe em
    58 mm. Comeca com ESC @ (reset) + FS . (sai do modo chines) — nessa ordem, porque o reset
    pode religar o modo chines de fabrica."""
    out = bytearray(b"\x1b\x40\x1c\x2e")
    def L(t): out.extend(t.encode("ascii") + b"\n")
    L("=" * 30); L("TESTE DE ACENTOS".center(30)); L("=" * 30)
    L("Cada bloco usa uma tabela.")
    L("Escolha a que saiu CERTA em")
    L("Configuracoes > Acentos.")
    L("-" * 30)
    for i, nome in enumerate(_CP_TESTE, 1):
        out.extend(bytes([0x1b, 0x74, CP_TAB[nome]]))
        L(f"{i}) {nome}")
        with _usar_codepage(nome):
            for amostra in _AMOSTRA_ACENTOS:
                out.extend(b"   " + _enc(_txt(amostra)) + b"\n")
    out.extend(bytes([0x1b, 0x74, 0]))
    L("-" * 30)
    L("Nenhuma certa? Use ascii:")
    L("tira os acentos e sai certo")
    L("em qualquer impressora.")
    out.extend(b"\n\n\n\n\x1b\x64\x05\x1d\x56\x00")
    return bytes(out)

# ---------------------------------------------------------------------------
# TAMANHO DA LETRA (v5.81). Escala de 4 passos:
#   0=Normal   1=Media (GS ! 0x01: altura 2x, LARGURA normal — nenhuma coluna se perde)
#   2=Grande (GS ! 0x11: 2x2, metade das colunas)   3=Extra (GS ! 0x22: 3x3, um terco)
# Ate a v5.80 o tamanho configurado so valia ate o primeiro destaque: o [[BIG_ORDER_OFF]]
# ZERAVA o GS ! (0x00) em vez de voltar ao tamanho base — por isso "so o cabecalho saia
# grande" (reclamacao de loja, 01/10/2026). Agora todo OFF restaura o tamanho BASE, e a
# fonte pode ser POR IMPRESSORA (imp["font_size"]), com o mesmo padrao threading.local do
# codepage (a impressao roda na thread do poll, o teste na da GUI).
_FONTE_GS    = (0x00, 0x01, 0x11, 0x22)   # GS ! por escala
_FONTE_WMUL  = (1, 1, 2, 3)               # multiplicador de LARGURA por escala
_FONTE_NOMES = ("Normal", "Media", "Grande", "Extra")
_fonte_local = threading.local()

def _fonte_valida(v):
    """Escala de fonte valida (0..3) ou None. Aceita int/str; lixo => None."""
    try:
        n = int(str(v).strip())
        return n if 0 <= n <= 3 else None
    except Exception:
        return None

def _fonte_cfg():
    v = _fonte_valida(cfg.get("font_size"))
    return v if v is not None else 0

def _fonte_da_impressora(imp):
    """Escala efetiva de uma impressora: imp['font_size'] > cfg['font_size'] > normal."""
    v = _fonte_valida((imp or {}).get("font_size"))
    return v if v is not None else _fonte_cfg()

def _fonte_atual():
    """Escala em uso: a da impressora da vez (_usar_fonte), senao cfg['font_size']."""
    o = getattr(_fonte_local, "fs", None)
    return o if o is not None else _fonte_cfg()

class _usar_fonte:
    """with _usar_fonte(2): ... — formata/imprime nessa escala nesta thread."""
    def __init__(self, fs): self.fs = _fonte_valida(fs)
    def __enter__(self):
        self.ant = getattr(_fonte_local, "fs", None); _fonte_local.fs = self.fs; return self
    def __exit__(self, *a):
        _fonte_local.fs = self.ant; return False

def _gs_fonte(n):
    """GS ! da escala n (ja validada ou None=normal)."""
    return bytes([0x1d, 0x21, _FONTE_GS[_fonte_valida(n) or 0]])

def _fonte_secao(nome):
    """Escala configurada para uma secao do cupom (cfg['fonte_secoes']) ou None = herda a base."""
    return _fonte_valida((cfg.get("fonte_secoes") or {}).get(nome))

def _fonte_que_cabe(escala, texto, w_fis):
    """Reduz a escala ate a linha caber na largura FISICA do papel (texto nao quebra no meio
    de um numero de pedido ou de um total). Media (wmul 1) sempre cabe."""
    s = _fonte_valida(escala) or 0
    while s > 0 and len(str(texto)) * _FONTE_WMUL[s] > w_fis:
        s -= 1
    return s

def _escpos_estilo_base():
    """Estilo geral do cupom alem do tamanho (v5.81): negrito em tudo (impressora que sai
    'fraca'), reforco de passada dupla (ESC G, papel apagado) e espaco entre linhas (ESC 3).
    Tudo opt-in: config ausente = bytes vazios = cupom identico ao de antes."""
    p = b""
    if cfg.get("negrito_cupom"): p += b"\x1b\x45\x01"
    if cfg.get("mais_escuro"):   p += b"\x1b\x47\x01"
    el = cfg.get("espaco_linhas")
    if el == 0:   p += b"\x1b\x33\x18"   # compacto (24/180")
    elif el == 2: p += b"\x1b\x33\x28"   # espacado (40/180")
    return p

def _escpos_font_prefix():
    """Bytes ESC/POS do estado BASE do cupom (tamanho da impressora da vez + estilo geral).
    Mandado uma vez no inicio do payload; os marcadores [[FSB]]/OFF voltam para este estado."""
    n = _fonte_atual()
    return b"\x1b\x21\x00" + (_gs_fonte(n) if n > 0 else b"") + _escpos_estilo_base()

def _escpos_sufixo():
    """Avanco + corte do fim do cupom, configuraveis (v5.81). Mantem o formato historico
    '\\n'*n + ESC d n (ha impressora que ignora um dos dois) + GS V. Config ausente = os
    MESMOS bytes de sempre (5 linhas + corte total). Fiscal NAO usa isto: o corte do DANFE
    tem regra propria de zona de silencio do QR."""
    try: n = max(0, min(8, int(cfg.get("avanco_linhas", 5))))
    except Exception: n = 5
    p = b"\n" * n + (bytes([0x1b, 0x64, n]) if n else b"")
    c = str(cfg.get("corte", "total"))
    if c == "parcial": p += b"\x1d\x56\x01"
    elif c != "nao":   p += b"\x1d\x56\x00"
    return p

def _bytes_restaura_base():
    """Fim de um destaque: volta ao estado BASE do cupom — tamanho base (NAO 0x00: era esse
    reset que fazia 'so o cabecalho sair grande' ate a v5.80), negrito conforme o geral, left."""
    neg = b"\x1b\x45\x01" if cfg.get("negrito_cupom") else b"\x1b\x45\x00"
    return _gs_fonte(_fonte_atual()) + neg + b"\x1b\x61\x00"

def _bytes_big_on():
    """Destaque do PEDIDO # (center + bold + fonte da secao 'pedido'). Sem config da secao,
    usa Grande (2x2, o historico) ou a base do cupom se ela for maior — o numero do pedido
    nunca sai MENOR que o corpo."""
    v = _fonte_secao("pedido")
    if v is None: v = max(2, _fonte_atual())
    return b"\x1b\x61\x01\x1b\x45\x01" + _gs_fonte(v)

_ALIASES_CODIGO_ITEM = ("barcode","codigo","codigo_produto","codigo_barras","cod_barras","sku","ean","ean13")

def _codigo_do_item(item):
    """Retorna o codigo do produto (EAN/SKU) cadastrado no item, se houver.
    Aceita varios aliases pois o backend ainda nao fixou um unico nome pra esta chave."""
    if not isinstance(item, dict): return ""
    for k in _ALIASES_CODIGO_ITEM:
        v = item.get(k)
        if v: return str(v).strip()
    return ""

def _valida_ean13(codigo):
    """True se codigo tem exatamente 13 digitos e o digito verificador (padrao EAN-13) bate."""
    if not isinstance(codigo, str) or len(codigo) != 13 or not codigo.isdigit():
        return False
    digitos = [int(c) for c in codigo]
    soma = sum(d if i % 2 == 0 else d * 3 for i, d in enumerate(digitos[:12]))
    dv = (10 - (soma % 10)) % 10
    return dv == digitos[12]

def _escpos_barcode_ean13(codigo, altura=80):
    """Bytes ESC/POS do EAN-13 + o numero em texto logo abaixo.
    So chamar com codigo ja validado por _valida_ean13 (13 digitos, todos ASCII).

    v5.82 — por que mudou (uma semana de cupom saindo SEM nada no lugar do codigo):
      * GS k m=2 (funcao A, dados terminados em NUL) no lugar de m=67 (funcao B, com byte
        de tamanho). A funcao A e a do ESC/POS original e toda termica Epson-compativel
        (Bematech/Elgin/Daruma em emulacao, clones 58/80 mm) aceita; a B falta em firmware
        antigo — e impressora que nao conhece o comando descarta a linha inteira em silencio.
      * HRI da impressora DESLIGADO (GS H 0) e o numero impresso por NOS como texto comum
        ('  Cod: 7899...'), igual a linha que sai com o ajuste desligado. Assim, mesmo que a
        impressora ignore o comando de barras, o codigo do produto NUNCA some do papel.
      * Modulo 3 pontos (GS w 3) e 80 pontos de altura: barra de ~36 mm x 10 mm, que cabe em
        58 mm (384 pontos) e que leitor de balcao le; com modulo 2 (24 mm) leitor barato falha.
    Nao termina em LF: quem monta o cupom ja separa as linhas com '\\n'."""
    dados = codigo.encode("ascii")
    return (
        bytes([0x1d, 0x68, altura]) +           # GS h: altura das barras em pontos
        bytes([0x1d, 0x77, 3]) +                # GS w: largura do modulo (2-6)
        bytes([0x1d, 0x48, 0]) +                # GS H 0: sem HRI da impressora (texto e nosso, abaixo)
        bytes([0x1d, 0x6b, 2]) + dados + b"\x00" +   # GS k m=2: EAN-13 (funcao A), terminado em NUL
        b"\n" +
        _enc(_txt(f"  Cod: {codigo}"))         # numero legivel SEMPRE, no codepage da impressora
    )

def _linha_codigo_item(item, imprime_barcode=False):
    """Linha a imprimir com o codigo do item: marcador de barcode (vira bytes EAN-13 reais
    em _substituir_marcadores_escpos) quando 'imprime_barcode' esta ligado E o codigo
    cadastrado e um EAN-13 valido, ou texto 'Cod: X' nos demais casos. Retorna '' se o item
    nao tem codigo cadastrado.

    'imprime_barcode' vem de content.get("print_barcode") — interruptor POR LOJA que o
    backend controla (printer_settings.print_barcode, default false). Sem checar isso aqui,
    qualquer loja com EAN-13 valido ja cadastrado passaria a imprimir barcode sem ter pedido,
    quebrando o isolamento do piloto (contrato confirmado no repo do ComprouTai:
    supabase/migrations/20260925120000_print_barcode_por_loja.sql)."""
    codigo = _codigo_do_item(item)
    if not codigo: return ""
    if imprime_barcode and _valida_ean13(codigo): return f"[[EAN13:{codigo}]]"
    return f"  Cod: {codigo}"

def _flag_print_barcode(content):
    """Decide se o cupom deste job sai com barras EAN-13 (v5.82).

    1. O job TRAZ a chave print_barcode (hooks do app gravam true/false): vale o que veio.
    2. O job NAO traz a chave: vale o ajuste da loja recebido no poll (_print_barcode_servidor).
       E o caso de TODO pedido do PDV: ele nasce com status 'new', o trigger do banco cria o
       cupom na hora (sem a chave e sem nada de printer_settings) e o app, ao ver que ja existe
       cupom, nao cria o dele. Ate a v5.81 isso significava: ajuste LIGADO na tela da loja e
       cupom saindo sem barras, sem mensagem nenhuma. Mesma coisa para create_public_order
       (cardapio publico) e print-job-create (pagamento online / reimpressao).
    3. Sem nenhum dos dois: desligado (comportamento de sempre; nenhuma loja muda sem pedir)."""
    if not isinstance(content, dict): return False
    if "print_barcode" in content:
        return content.get("print_barcode") is True
    return _print_barcode_servidor is True

_MARCADOR_RE = re.compile(r"\[\[(BIG_ORDER_ON|BIG_ORDER_OFF|NEG_ON|NEG_OFF|ALTO_ON|ALTO_OFF|EAN13:\d{13})\]\]")

def _tem_marcador(texto):
    """True se a linha tem marcador ESC/POS que precisa virar bytes crus."""
    return isinstance(texto, str) and bool(_MARCADOR_RE.search(texto))

def _substituir_marcadores_escpos(texto):
    """Substitui marcadores '[[BIG_ORDER_ON/OFF]]', '[[NEG_ON/OFF]]', '[[ALTO_ON/OFF]]',
    '[[FS0..FS3]]'/'[[FSB]]' (tamanho por secao, v5.81) e '[[EAN13:codigo]]' por bytes
    ESC/POS reais. Percorre o texto em blocos: trechos comuns sao normalizados e
    codificados no codepage da impressora (_enc), marcadores viram bytes crus diretamente —
    o EAN-13 tem conteudo variavel (o codigo), entao nao da pra usar placeholder fixo como
    os outros. Se nao houver marcador, retorna direto o encode (caminho rapido).
    Os OFF sao calculados NA HORA (e nao constantes) porque restauram o estado BASE do
    cupom, que depende da fonte da impressora da vez e do negrito geral."""
    if not isinstance(texto, str):
        # Blindagem: se por algum motivo veio None/bytes/outro tipo, converte
        texto = str(texto) if texto is not None else ""
    if not _tem_marcador(texto):
        return _enc(_txt(texto))
    partes = []
    pos = 0
    for m in _MARCADOR_RE.finditer(texto):
        if m.start() > pos:
            partes.append(_enc(_txt(texto[pos:m.start()])))
        tag = m.group(1)
        if tag == "BIG_ORDER_ON": partes.append(_bytes_big_on())
        elif tag == "BIG_ORDER_OFF": partes.append(_bytes_restaura_base())
        elif tag == "NEG_ON": partes.append(b"\x1b\x61\x01\x1b\x45\x01")   # center + bold, tamanho intacto
        elif tag == "NEG_OFF":
            partes.append((b"\x1b\x45\x01" if cfg.get("negrito_cupom") else b"\x1b\x45\x00") + b"\x1b\x61\x00")
        # AGENDADO (v5.79): SEMPRE altura dupla/largura normal — e o unico destaque garantido
        # de caber em qualquer papel de 24+ colunas sem quebrar o horario no meio.
        elif tag == "ALTO_ON": partes.append(b"\x1b\x61\x01\x1b\x45\x01\x1d\x21\x01")
        elif tag == "ALTO_OFF": partes.append(_bytes_restaura_base())
        elif tag == "FSB": partes.append(_bytes_restaura_base())
        elif tag.startswith("FS"): partes.append(_gs_fonte(int(tag[2])))   # tamanho por secao (v5.81)
        elif tag.startswith("EAN13:"): partes.append(_escpos_barcode_ean13(tag.split(":",1)[1]))
        pos = m.end()
    if pos < len(texto):
        partes.append(_enc(_txt(texto[pos:])))
    return b"".join(partes)

def _imprimir_raw(nome, conteudo):
    try:
        if HAS_WIN32:
            h=win32print.OpenPrinter(nome)
            try:
                win32print.StartDocPrinter(h,1,("Cupom",None,"RAW"))
                win32print.StartPagePrinter(h)

                # Se for string, codifica com prefixo de tamanho de fonte. Se for bytes (RAW), envia direto.
                if isinstance(conteudo, str):
                    corpo = _substituir_marcadores_escpos(conteudo) + _escpos_sufixo()
                    # ESC t antes do texto: sem isso a impressora fica no PC437 e os acentos quebram
                    payload = _escpos_cp() + _escpos_font_prefix() + corpo
                    win32print.WritePrinter(h, payload)
                else:
                    win32print.WritePrinter(h, conteudo)

                win32print.EndPagePrinter(h)
                win32print.EndDocPrinter(h)
            finally: win32print.ClosePrinter(h)
            return {"ok":True}
        return {"ok":False,"erro":"win32print indisponivel"}
    except Exception as e: return {"ok":False,"erro":str(e)}

def _imprimir_tcp(endereco, conteudo):
    """Imprime via socket TCP — para impressoras de rede sem driver Windows."""
    import socket
    try:
        if ":" in endereco:
            host, porta_str = endereco.rsplit(":", 1)
            porta = int(porta_str)
        else:
            host, porta = endereco, 9100
        with socket.create_connection((host, porta), timeout=10) as s:
            if isinstance(conteudo, str):
                corpo = _substituir_marcadores_escpos(conteudo) + _escpos_sufixo()
                # ESC t antes do texto: sem isso a impressora fica no PC437 e os acentos quebram
                payload = _escpos_cp() + _escpos_font_prefix() + corpo
            else:
                payload = conteudo
            s.sendall(payload)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

def _res_imp_por_rede(pt, printer_id=None):
    """Resolve impressora considerando multi-rede e fallback para config legada."""
    areas_pt = _areas_para_tipo(pt)
    redes = cfg.get("redes", [])
    # Busca nas redes configuradas
    for rede in redes:
        for imp in rede.get("impressoras", []):
            if printer_id and (imp.get("id") == printer_id or imp.get("nome") == printer_id):
                return imp
            if not printer_id and imp.get("printer_type") == pt:
                # Verifica se esta impressora pertence a area deste agente
                area_imp = imp.get("area", "").strip().lower()
                if not area_imp or area_imp in areas_pt:
                    return imp
    # Fallback: config legada (impressoras na raiz do cfg)
    imps_legado = cfg.get("impressoras", [])
    # Se veio printer_id, tenta casar com a impressora especifica ANTES de cair na 1a da area.
    # Isso corrige o roteamento quando ha varias impressoras na mesma area (ex: 2x caixa):
    # o printer_id era ignorado e tudo caia sempre na primeira.
    if printer_id:
        pid_norm = str(printer_id).strip().lower()
        for i in imps_legado:
            if (str(i.get("id","")).strip().lower() == pid_norm
                    or str(i.get("nome","")).strip().lower() == pid_norm
                    or str(i.get("nome_impressora","")).strip().lower() == pid_norm):
                if i.get("nome_impressora","") or i.get("endereco_ip",""):
                    return dict(i) if "tipo" in i else {**i, "tipo": i.get("tipo","comum_win32")}
    # Sem printer_id (ou nao encontrado): 1a impressora da area do agente. v5.80: devolve o dict
    # COMPLETO da config, nao so o nome. So com o nome, o job com printer_id (UUID do servidor, que
    # a config local nao guarda) perdia 'codepage' e 'colunas' da impressora: uma mini em modo
    # UTF-8 configurada como 'utf8' voltava a receber cp850 em 48 colunas.
    for i in imps_legado:
        if (str(i.get("area","")).strip().lower() in areas_pt
                or str(i.get("printer_type","")).strip() == pt) and i.get("nome_impressora",""):
            return {**i, "tipo": i.get("tipo") or "comum_win32"}
    nome = _res_imp(pt)
    if nome:
        return {"nome_impressora": nome, "tipo": "comum_win32"}
    return None

def _chave_imp(imp):
    """Chave unica de uma impressora fisica (para nao imprimir 2x na mesma).
    Usa nome_impressora (driver Windows) ou endereco_ip (rede)."""
    return (str(imp.get("nome_impressora","")).strip().lower()
            or str(imp.get("endereco_ip","")).strip().lower())

def _res_todas_imp_por_tipo(pt):
    """Retorna TODAS as impressoras ativas cuja area/tipo casa com o printer_type pt.
    Usado para espalhar um job em todas as impressoras da mesma funcao (ex: 2x caixa).
    Deduplica por impressora fisica (nome/ip) — a mesma impressora nunca entra 2x.
    So inclui impressoras com destino real (nome_impressora ou endereco_ip preenchido)."""
    areas_pt = _areas_para_tipo(pt)
    resultado = []
    vistos = set()

    def _considerar(imp):
        # Casa por area (caixa/cozinha/...) OU por printer_type exato
        area_imp = str(imp.get("area","")).strip().lower()
        ptype = str(imp.get("printer_type","")).strip()
        if not (area_imp in areas_pt or ptype == pt):
            return
        if not (imp.get("nome_impressora","") or imp.get("endereco_ip","")):
            return  # sem destino fisico mapeado — ignora
        chave = _chave_imp(imp)
        if not chave or chave in vistos:
            return
        vistos.add(chave)
        # Normaliza 'tipo' (comum_win32/rede) para o roteamento
        imp_norm = dict(imp) if "tipo" in imp else {**imp, "tipo": imp.get("tipo","comum_win32")}
        resultado.append(imp_norm)

    # Impressoras em redes configuradas
    for rede in cfg.get("redes", []):
        for imp in rede.get("impressoras", []):
            _considerar(imp)
    # Impressoras na config legada (raiz)
    for imp in cfg.get("impressoras", []):
        _considerar(imp)
    return resultado

def _imprimir_com_roteamento(imp, conteudo):
    """Roteia impressão: driver Windows (comum_win32) ou TCP direto (rede)."""
    tipo = imp.get("tipo", "comum_win32")
    if tipo == "rede":
        return _imprimir_tcp(imp.get("endereco_ip", ""), conteudo)
    else:
        return _imprimir_raw(imp.get("nome_impressora", ""), conteudo)

def _R(v):
    try: return f"R$ {int(v)/100:.2f}"
    except: return "R$ 0,00"

W=48

def _colunas_validas(v):
    """Largura de papel valida em colunas (24..64) ou None. Aceita int/str; lixo => None."""
    try:
        n = int(str(v).strip())
        return n if 24 <= n <= 64 else None
    except Exception:
        return None

def _largura_efetiva(imp, content, servidor):
    """Largura do papel em COLUNAS para formatar o cupom. Precedencia (v5.79 + COMP-46), do
    mais especifico/manual para o mais generico:
      1) 'colunas' da impressora na config local (campo 'Colunas do papel', por impressora);
      2) cfg['paper_width_cols'] — ajuste local GERAL (botoes 58/76/80 mm da tela, COMP-46):
         feito in loco por quem conhece a bobina; o servidor manda o mesmo valor pra loja toda
         e nao pode sobrescrever uma correcao explicita da instalacao;
      3) paper_width que veio no content do job (print-job-create manda);
      4) printer_settings.paper_width do cardapio (chega em resp.settings do poll) — o caso
         dos jobs do trigger, que nao trazem paper_width no content;
      5) W (48).
    Sem isto, impressora de 42 colunas recebia cupom formatado em 48 e cada linha de preco
    quebrava em duas ("R$ 1" / "11.70"), visto em loja em 24/09/2026."""
    for v in ((imp or {}).get("colunas"), cfg.get("paper_width_cols"),
              (content or {}).get("paper_width"), servidor):
        c = _colunas_validas(v)
        if c: return c
    return W

TL={"counter":"BALCAO","dine_in":"MESA","takeaway":"RETIRADA","delivery":"ENTREGA","pickup":"RETIRADA","table":"MESA","balcao":"BALCAO","mesa":"MESA","retirada":"RETIRADA","entrega":"ENTREGA"}
PL={"cash":"Dinheiro","credit":"Cartao Credito","debit":"Cartao Debito","pix":"PIX","card":"Cartao","money":"Dinheiro","creditcard":"Cartao Credito","debitcard":"Cartao Debito"}

def _pedido_do_content(content):
    """Retorna o objeto 'pedido' quando o servidor manda content aninhado.
    Se o servidor manda content plano (formato antigo/achatado), retorna o proprio content."""
    p = content.get("pedido")
    return p if isinstance(p, dict) else content

def _itens_do_content(content):
    """Retorna lista de itens do content.
    Aceita, em ordem:
      1. content.pedido.itens / content.pedido.items (novo formato aninhado)
      2. content.itens (formato novo achatado)
      3. content.items (formato legado)
    Retorna [] se content for None/tipo errado ou nao houver itens.
    """
    if not isinstance(content, dict): return []
    pedido = content.get("pedido")
    if isinstance(pedido, dict):
        itens = pedido.get("itens") or pedido.get("items")
        if isinstance(itens, list):
            return itens
    itens = content.get("itens") or content.get("items")
    if isinstance(itens, list):
        return itens
    # 4. content.order.items (shape do print-agent-poll). ULTIMO recurso de proposito: so entra
    #    quando nada acima casou, ou seja, em payload que hoje imprimiria ZERO item. Nenhum job
    #    que ja funciona muda de fonte de itens por causa disto.
    order = content.get("order")
    if isinstance(order, dict):
        itens = order.get("items") or order.get("itens")
        if isinstance(itens, list):
            return itens
    return []

def _adicionais_do_item(item):
    """Retorna lista normalizada de adicionais/escolhas/customizacoes do item.
    UNIAO das 5 chaves, na ordem:
      adicionais -> addons -> addons_json -> selections_json -> customizations_json
    Isso cobre combos/wizard onde o item traz adicionais em addons_json E escolhas em
    selections_json ao mesmo tempo — antes o fallback perdia uma das listas.

    Aliases de NOME aceitos: nome, name, addon_name, option_name, label, title
    Aliases de PRECO aceitos: preco_cents, priceCents, price_cents, unit_price_cents
    Aliases de ID   aceitos: addonId, addon_id, id

    DEDUP (contrato do servidor, confirmado 2026-07-12):
      - Se o adicional tem ID (addonId/addon_id/id): dedup por ESSE ID. Assim a MESMA
        escolha que chega em 2 chaves (ex: addons E addons_json) colapsa em 1 (mesmo id),
        MAS escolhas de GRUPOS DIFERENTES com o mesmo texto (ex: dois "Nao quero
        Acompanhamento" num combo Subway, cada um com addonId proprio) NAO colapsam —
        as duas saem no cupom. Antes o dedup era por (nome+preco) e perdia uma delas.
      - Se o adicional NAO tem ID: cai para dedup por (nome_lower, preco) — comportamento
        historico, evita duplicar o mesmo adicional vindo em varias chaves.
    Nota: adicional NUNCA tem quantidade propria (0 ocorrencias em 60 dias no banco);
    a quantidade relevante e a do ITEM (_qtd_do_item). Cada escolha e uma entrada.
    Retorna [] em qualquer situacao inesperada — nunca lanca excecao.
    """
    if not isinstance(item, dict): return []

    def _nome_adicional(a):
        if not isinstance(a, dict): return ""
        for k in ("nome", "name", "addon_name", "option_name", "label", "title"):
            v = a.get(k)
            if v: return str(v)
        return ""

    def _preco_adicional(a):
        if not isinstance(a, dict): return 0
        for k in ("preco_cents", "priceCents", "price_cents", "unit_price_cents"):
            v = a.get(k)
            if v:
                try: return int(v)
                except (TypeError, ValueError): return 0
        return 0

    def _id_adicional(a):
        if not isinstance(a, dict): return ""
        for k in ("addonId", "addon_id", "id"):
            v = a.get(k)
            if v not in (None, "", 0): return str(v).strip()
        return ""

    def _rotas_adicional(a):
        """routed_to: setores para onde este adicional foi roteado (contrato de 2026-07-27).
        Ausente = adicional nao roteado (comportamento historico)."""
        if not isinstance(a, dict): return []
        rt = a.get("routed_to")
        if isinstance(rt, str): rt = [rt]
        if not isinstance(rt, list): return []
        return [str(s) for s in rt if s]

    resultado = []
    vistos = {}  # dedup: por id quando existe; senao por (nome_lower, preco)
    for chave in ("adicionais", "addons", "addons_json", "selections_json", "customizations_json"):
        ads = item.get(chave)
        if not isinstance(ads, list) or not ads:
            continue
        for a in ads:
            if not isinstance(a, dict): continue
            nome = _nome_adicional(a)
            if not nome: continue
            preco = _preco_adicional(a)
            aid = _id_adicional(a)
            rotas = _rotas_adicional(a)
            # Prioriza o ID (distingue escolhas de grupos distintos com texto igual);
            # sem ID, mantem o dedup historico por (nome+preco).
            chave_dedup = ("id", aid) if aid else (nome.strip().lower(), preco)
            if chave_dedup in vistos:
                # Mesma escolha vinda de outra chave: aproveita o routed_to se o 1o nao trouxe,
                # senao a rota se perderia dependendo da ordem em que as chaves foram lidas.
                if rotas and not vistos[chave_dedup].get("routed_to"):
                    vistos[chave_dedup]["routed_to"] = rotas
                continue
            entrada = {"nome": nome, "preco_cents": preco, "routed_to": rotas}
            vistos[chave_dedup] = entrada
            resultado.append(entrada)
    return resultado

def _obs_do_item(item):
    """Retorna observacao do item. Aceita 'obs' (novo servidor) ou 'notes' (legado)."""
    if not isinstance(item, dict): return ""
    return item.get("obs") or item.get("notes") or ""

def _qtd_do_item(item):
    """Retorna quantidade do item. Aceita 'qtd' (novo servidor), 'quantity' ou 'qty' (legado)."""
    if not isinstance(item, dict): return 1
    return item.get("qtd") or item.get("quantity") or item.get("qty") or 1

def _nome_do_item(item):
    """Retorna nome do item. Aceita 'nome' (novo servidor) ou 'name' (legado)."""
    if not isinstance(item, dict): return ""
    return item.get("nome") or item.get("name") or ""

def _size_do_item(item):
    """Retorna o tamanho/variacao do item (ex.: 'Copo 770ml'). Aceita 'size_name'
    (novo servidor) ou 'tamanho' como alias. '' quando nao houver."""
    if not isinstance(item, dict): return ""
    v = item.get("size_name") or item.get("tamanho") or ""
    return str(v).strip()

def _nome_com_tamanho(item):
    """Nome do item com o tamanho/variacao entre parenteses, quando houver.
    Ex.: 'Monte seu Copo (Copo 770ml)'. Sem tamanho, retorna so o nome.
    Produtos que 'substituem preco base' (Monte seu Copo, sabor do Subway) usam
    o tamanho para definir o preco — precisa sair proeminente no cabecalho."""
    nome = _nome_do_item(item)
    size = _size_do_item(item)
    return f"{nome} ({size})" if size else nome

def _preco_do_item(item):
    """Retorna preco unitario em centavos.
    Aceita 'preco_cents' (novo), 'priceCents' (camelCase cru) ou 'unit_price_cents' (legado)."""
    if not isinstance(item, dict): return 0
    return item.get("preco_cents") or item.get("priceCents") or item.get("unit_price_cents") or 0

# ─────────────────────────────────────────────────────────────────────────────
# AGRUPAMENTO POR CATEGORIA (contrato do servidor de 2026-07-31)
#
# Existe loja que guarda o TAMANHO da marmita na CATEGORIA do produto, nao no nome: o catalogo
# tem "Marmitex Pequena", "Marmitex Media", "Marmitex Executiva" — e produtos de nome IDENTICO
# em mais de uma ("Boi e Queijo" em Media E em Pequena). O cupom saia "[ 1x ] Boi e Queijo" e
# nem a cozinha nem o balcao tinham como saber qual tamanho montar.
# ─────────────────────────────────────────────────────────────────────────────

def _categoria_do_item(item):
    """Categoria do produto (ex.: 'Marmitex Media'). Aceita 'category_name' (contrato do
    servidor) ou 'categoria' (mesmo dado em pt-BR, endpoint agent-jobs). '' quando nao houver.

    null/ausente e LEGITIMO e comum: item sem produto vinculado, linha que e um adicional
    roteado para o setor (nao e produto do catalogo), ou caminho em que so o nome chega e o
    servidor prefere mandar null a mandar a categoria errada. Nesses casos o item cai no grupo
    sem cabecalho — nunca sai 'null'/'None' impresso no papel.
    """
    if not isinstance(item, dict): return ""
    v = item.get("category_name")
    if v in (None, ""): v = item.get("categoria")
    if v in (None, ""): return ""
    s = str(v).strip()
    # Blindagem: servidor mandando a string "null"/"None" nao pode virar cabecalho
    return "" if s.lower() in ("null", "none", "undefined", "nan") else s

def _agrupar_por_categoria(content):
    """True quando o pedido pede os itens agrupados por categoria, com cabecalho.

    REGRA DURA: a decisao e do SERVIDOR (flag print_item_category, opt-in por loja em
    Configuracoes > Impressao), NUNCA do agente. Nao se infere nada da presenca de
    category_name nos itens: sem a flag em true o cupom tem que sair byte a byte igual ao de
    antes, porque as outras lojas nao pediram essa mudanca e nao podem ver o cupom mudar.

    E FLAG POR JOB, NAO CONFIGURACAO DO AGENTE — nunca cacheie em cfg. Numa rede a matriz
    concentra os jobs das filiais, e o servidor resolve a flag pela LOJA QUE FEZ O PEDIDO:
    dois jobs da MESMA leva podem vir com flags diferentes e cada cupom tem que respeitar a
    sua. Por isso e lida do content a cada _fmt, e de lugar nenhum mais.

    Nivel do pedido/content, nos 3 shapes que o servidor usa (contrato de 31/07):
      content.print_item_category ....... agent-unified-poll / agent-get-order (raiz)
      content.pedido.print_item_category  agent-jobs
      content.order.print_item_category . print-agent-poll
    """
    if not isinstance(content, dict): return False
    v = content.get("print_item_category")
    if v is None:
        for _k in ("pedido", "order"):
            _sub = content.get(_k)
            if isinstance(_sub, dict) and _sub.get("print_item_category") is not None:
                v = _sub.get("print_item_category"); break
    if isinstance(v, bool): return v
    # Tolera a flag chegando como texto/numero (JSON de terceiros). Cuidado deliberado: so
    # liga em valores explicitamente verdadeiros — "false"/"0"/lixo desliga.
    if isinstance(v, str): return v.strip().lower() in ("true", "1", "t", "sim", "yes", "y")
    if isinstance(v, (int, float)): return int(v) == 1
    return False

# ---------------------------------------------------------------------------
# Rede multi-loja: de QUAL LOJA e este job
# ---------------------------------------------------------------------------
# Numa rede a matriz concentra a impressao das filiais (print_target_restaurant_id).
# O job sempre soube a origem (print_jobs.restaurant_id), mas o polling nunca devolveu
# essa coluna — o agente so recebe o content. Por isso o servidor carimba a origem
# DENTRO do content, na trigger set_print_job_target (contrato de 01/08/2026):
#
#   store_name ......... nome da loja que FEZ o pedido
#   print_store_label .. true quando a origem NAO e quem imprime (filial no concentrador)
#
# Antes disso o cabecalho caia em cfg["restaurant_name"] — o nome da MATRIZ — e o
# cupom da filial saia com a loja errada; a comanda nao dizia nada sobre a loja.

def _loja_do_job(content):
    """Nome da loja que FEZ o pedido. '' quando o servidor nao carimbou (job antigo).

    Lido do CONTENT a cada impressao e de lugar nenhum mais. Nunca de cfg: cfg guarda a
    loja PAREADA com o agente (a matriz), e numa mesma leva chegam jobs de lojas
    diferentes — cachear aqui carimbaria a loja errada no cupom da filial seguinte.

    Nos 3 shapes que o servidor usa, igual ao print_item_category:
      content.store_name .......... agent-unified-poll / agent-get-order (raiz)
      content.pedido.store_name ... agent-jobs
      content.order.store_name .... print-agent-poll
    """
    if not isinstance(content, dict): return ""
    v = content.get("store_name")
    if v in (None, ""):
        for _k in ("pedido", "order"):
            _sub = content.get(_k)
            if isinstance(_sub, dict) and _sub.get("store_name"):
                v = _sub.get("store_name"); break
    if v in (None, ""): return ""
    s = str(v).strip()
    # Blindagem: servidor mandando "null"/"None" nao pode virar nome de loja no papel
    return "" if s.lower() in ("null", "none", "undefined", "nan") else s

def _selo_loja_ativo(content):
    """True quando o cupom/comanda deve estampar 'LOJA: X'.

    REGRA DURA: a decisao e do SERVIDOR (print_store_label), NUNCA do agente. Nao se
    infere nada da presenca de store_name: loja unica recebe o campo tambem e o cupom
    dela tem que sair byte a byte igual ao de antes. So filial imprimindo no
    concentrador ganha o selo. Mesmos 3 shapes e mesma tolerancia de tipo do
    print_item_category (JSON de terceiros manda bool como texto)."""
    if not isinstance(content, dict): return False
    v = content.get("print_store_label")
    if v is None:
        for _k in ("pedido", "order"):
            _sub = content.get(_k)
            if isinstance(_sub, dict) and _sub.get("print_store_label") is not None:
                v = _sub.get("print_store_label"); break
    if isinstance(v, bool): return v
    if isinstance(v, str): return v.strip().lower() in ("true", "1", "t", "sim", "yes", "y")
    if isinstance(v, (int, float)): return int(v) == 1
    return False

def _linhas_selo_loja(content, w):
    """Linhas do selo 'LOJA: X' em negrito centralizado, ou [] quando nao se aplica.

    Negrito de tamanho NORMAL ([[NEG_ON]]), nao [[BIG_ORDER_ON]]: este ultimo e 2x2 e
    estouraria o papel de 58 mm — e nome de loja e bem mais longo que um numero de pedido.
    Nome comprido QUEBRA em varias linhas em vez de truncar: em 'Droga Ven LJ24 - AV.
    MARIA ANTONIA CAMARGO DE OLIVEIRA' (80+ colunas) o corte esconderia justamente o
    trecho que diferencia uma loja da outra, que e o motivo do selo existir."""
    if not _selo_loja_ativo(content): return []
    nome = _loja_do_job(content)
    if not nome: return []
    return [f"[[NEG_ON]]{linha}[[NEG_OFF]]" for linha in _wrap_linhas(f"LOJA: {nome.upper()}", w)]

# ---------------------------------------------------------------------------
# Pedido AGENDADO: contrato do print_jobs.content (migration 20260805130000)
# ---------------------------------------------------------------------------
# O trigger trg_print_content_schedule (BEFORE INSERT OR UPDATE OF content em print_jobs)
# roda enrich_print_content_with_schedule() e, SO quando orders.scheduled_for esta
# preenchido, mescla no content:
#   is_scheduled ..... true
#   scheduled_for .... "2026-09-09T15:00:00+00:00"  (UTC CRU — nao usar sem converter)
#   scheduled_date ... "09/09/2026"                 (ja em America/Sao_Paulo)
#   scheduled_time ... "12:00"                      (ja no relogio da loja)
#   scheduled_label .. "AGENDADO: 09/09 AS 12:00"   (linha PRONTA para a impressora)
# Pedido nao agendado = campos AUSENTES (nao vem false/null). Regras duras:
#   - imprimir scheduled_label COMO VEIO: sem acento de proposito ("AS", nao "ÀS"), porque
#     CP850/latin1 corrompe acento em varias termicas; nao re-formatar nem "corrigir";
#   - NUNCA converter fuso de novo: scheduled_date/time ja estao no relogio da loja (somar
#     ou subtrair 3h foi o bug que derrubou o agendamento em 30/07); so scheduled_for e UTC;
#   - funil unico: o trigger cobre todas as origens (cardapio, PDV, KDS, reimpressao) — o
#     agente NAO busca orders.scheduled_for por conta propria;
#   - is_scheduled nao decide nada aqui: o criterio e scheduled_label existir.
# Referencia de renderizacao: agente Tauri, agent-source/src-tauri/src/escpos/receipt.rs.

def _rotulo_agendado(content):
    """scheduled_label do content, ou '' quando o pedido nao e agendado / campo ausente.
    Mesmos 3 shapes do print_item_category (raiz, pedido, order); a raiz vence porque e la
    que o trigger grava."""
    if not isinstance(content, dict): return ""
    v = content.get("scheduled_label")
    if v in (None, ""):
        for _k in ("pedido", "order"):
            _sub = content.get(_k)
            if isinstance(_sub, dict) and _sub.get("scheduled_label"):
                v = _sub.get("scheduled_label"); break
    if v in (None, ""): return ""
    s = str(v).strip()
    # Blindagem: servidor mandando "null"/"None" nao pode virar linha de destaque no papel
    return "" if s.lower() in ("null", "none", "undefined", "nan") else s

def _linhas_agendado(content, w_fis):
    """Linhas do destaque de agendamento em negrito + ALTURA dupla centralizado ([[ALTO_ON]]),
    ou [] quando o pedido nao e agendado.
    v5.78 usava 2x2 ([[BIG_ORDER_ON]], como o agente Tauri). Em 2x2 cada caractere ocupa 2
    colunas: "AGENDADO: 24/09 AS 19:00" (24 chars) so cabia em 48 colunas — em impressora de 42
    (muito comum) a PROPRIA impressora quebrava a linha no meio do horario ("...AS 19" / ":00"),
    visto em loja em 24/09/2026. Altura dupla (largura normal) mantem o destaque e a linha
    inteira cabe em qualquer papel de 24+ colunas. w_fis = largura FISICA do papel; a quebra
    por espaco so acontece em papel mais estreito que a label (nunca no meio do horario)."""
    rotulo = _rotulo_agendado(content)
    if not rotulo: return []
    return [f"[[ALTO_ON]]{linha}[[ALTO_OFF]]"
            for linha in _wrap_linhas(rotulo, max(8, int(w_fis)))]

# ---------------------------------------------------------------------------
# Data/hora do pedido no RELOGIO DA LOJA (v5.78)
# ---------------------------------------------------------------------------
# O trigger create_print_jobs_on_new_order grava content.created_at = now() em UTC, e o
# agent-unified-poll (o endpoint deste agente) NAO acrescenta created_at_brt/hora_brt — so o
# agent-jobs (usado pelo agente Tauri antigo) fazia isso. Resultado ate a v5.77: o cupom saia
# SEM linha 'Data:' e a comanda imprimia 'Hora:' com a hora UTC crua, 3h adiantada. Aqui
# convertemos para Brasilia como o agente Tauri (receipt.rs: FixedOffset -3). UTC-3 fixo:
# o Brasil nao tem horario de verao desde 2019 e todo o sistema (trigger de agendamento,
# edge functions) padroniza America/Sao_Paulo. zoneinfo nao serve: o build nao carrega tzdata.
from datetime import datetime as _DT, timezone as _TZ, timedelta as _TD
_BRT = _TZ(_TD(hours=-3))

def _dt_brt(iso):
    """ISO do banco -> datetime em Brasilia; None se ilegivel. Sem fuso => assume UTC (e como o
    banco grava). Aceita 'Z', '+00:00', '+00', fracao de segundos, e 'T' ou espaco."""
    if not iso or not isinstance(iso, str): return None
    s = iso.strip().replace("Z", "+00:00")
    try:
        dt = _DT.fromisoformat(s)
    except Exception:
        try: dt = _DT.fromisoformat(s[:19])
        except Exception: return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=_TZ.utc)
    return dt.astimezone(_BRT)

def _data_hora_brt(content):
    """('DD/MM/AAAA HH:MM', 'HH:MM') do pedido no relogio da loja. Prioridade: created_at_brt e
    hora_brt PRONTOS do servidor (shape agent-jobs) — nunca reconverte o que ja veio em BRT.
    Senao converte content.created_at (UTC) para Brasilia. NUNCA imprime a hora UTC crua."""
    if not isinstance(content, dict): return "", ""
    data = str(content.get("created_at_brt") or "").strip()
    hora = str(content.get("hora_brt") or "").strip()
    if not data or not hora:
        dt = _dt_brt(content.get("created_at"))
        if dt is not None:
            data = data or dt.strftime("%d/%m/%Y %H:%M")
            hora = hora or dt.strftime("%H:%M")
    return data, hora

def _grupos_por_categoria(itens, ativo):
    """Agrupa os itens por categoria e devolve [(nome_categoria, [itens]), ...].
    Categoria '' = grupo SEM cabecalho.

    - ativo=False (flag desligada): um unico grupo '' com TODOS os itens na ordem original —
      o cupom sai identico ao formato atual.
    - Ordem dos grupos = PRIMEIRA APARICAO da categoria no pedido, nao alfabetica: o lojista
      espera o cupom na ordem em que os itens entraram.
    - Itens sem categoria formam o grupo '' na posicao em que apareceram — nao vao para o fim
      e nao inventam um "OUTROS".
    - Se NENHUM item tem categoria, sai um unico grupo '' e nenhum cabecalho e impresso.
    """
    itens = itens if isinstance(itens, list) else []
    if not ativo:
        return [("", itens)]
    grupos = {}   # chave normalizada -> [itens]
    nomes  = {}   # chave normalizada -> nome como apareceu 1a vez (o que vai no papel)
    ordem  = []   # chaves na ordem de primeira aparicao
    for item in itens:
        cat = _categoria_do_item(item)
        # Chave em minusculas: 'Bebidas' e 'bebidas' sao a MESMA categoria e nao podem gerar
        # dois cabecalhos. O nome impresso e o da primeira aparicao.
        k = cat.lower()
        if k not in grupos:
            grupos[k] = []; nomes[k] = cat; ordem.append(k)
        grupos[k].append(item)
    return [(nomes[k], grupos[k]) for k in ordem]

def _cab_categoria(cat, w):
    """Linhas do cabecalho de um grupo: nome da categoria em CAIXA ALTA, centralizado na
    largura do papel, em negrito, entre tracinhos:
        ------------- MARMITEX MEDIA -------------
    Categoria '' devolve [] — grupo sem categoria sai sem cabecalho.

    O NOME VENCE OS TRACINHOS: se nao couber com eles, os tracinhos saem de cena; se nem
    assim couber, o nome quebra em varias linhas. Cortar o nome derrota o proposito do
    cabecalho (era exatamente o nome que faltava para desambiguar o produto).

    Negrito por ESC/POS INLINE (ESC E 1 / ESC E 0), o mesmo jeito que _linha_pai usa para a
    fonte B: nao mexe no alinhamento (a linha ja tem a largura exata w e nao pode ser
    recentralizada pela impressora) e nao depende do sistema de marcadores [[...]] — assim
    este cabecalho funciona nos dois caminhos de _fmt (string e bytes) sem caso especial.
    """
    nome = _txt(str(cat or "")).strip().upper()
    if not nome:
        return []
    folga = w - len(nome) - 2  # 2 = os espacos que separam o nome dos tracinhos
    if folga >= 2:
        esq = folga // 2
        linhas = ["-" * esq + " " + nome + " " + "-" * (folga - esq)]
    else:
        # Nome grande demais para os tracinhos: imprime so o nome, quebrado em PALAVRAS e
        # centralizado por espacos. Quebra propria (nao _wrap_linhas) para este cabecalho nao
        # depender do modulo do cupom fiscal: e uma linha curta, sem \n, entao o caso e simples.
        linhas, atual = [], ""
        for palavra in nome.split():
            while len(palavra) > w:                     # palavra maior que o papel: fatia
                if atual: linhas.append(atual); atual = ""
                linhas.append(palavra[:w]); palavra = palavra[w:]
            cand = palavra if not atual else atual + " " + palavra
            if len(cand) <= w: atual = cand
            else: linhas.append(atual); atual = palavra
        if atual: linhas.append(atual)
        linhas = [l.center(w) for l in (linhas or [nome])]
    return [f"\x1b\x45\x01{l}\x1b\x45\x00" for l in linhas]

# Rotulos dos setores no sufixo de roteamento dos adicionais
SL={"bar":"Bar","kitchen":"Cozinha","cozinha":"Cozinha","copa":"Copa","receipt":"Caixa","caixa":"Caixa"}

def _rota_sufixo(a):
    """Sufixo ' -> Bar' no adicional que foi roteado para outro setor (contrato 2026-07-27).
    Usa '->' e nao a seta unicode: '→' nao existe em cp850 e sairia como '?' no papel."""
    rt = a.get("routed_to") if isinstance(a, dict) else None
    if not rt: return ""
    return " -> " + ", ".join(SL.get(str(s).lower(), str(s).title()) for s in rt)

def _linha_pai(item, ind="  "):
    """Linha de origem de um adicional roteado: o item e um adicional que caiu na comanda
    deste setor, entao mostra de qual produto ele veio. Fonte B (menor) via ESC/POS.
    Sem is_addon_line/parent_name retorna [] — payload antigo nao muda."""
    if not isinstance(item, dict) or not item.get("is_addon_line"): return []
    pai = item.get("parent_name") or ""
    return [f"{ind}\x1b\x4d\x01({pai})\x1b\x4d\x00"] if pai else []

def _li(q,n,p,w=None):
    w=w or W; b=f"[ {q}x ]  {n}"
    total=int(q)*int(p) if p else 0
    if total<=0: return b  # sem preco quando zero (mesa com pagamento no final etc)
    pv=_R(total); e=w-len(b)-len(pv)
    return b+(" "*max(1,e))+pv if e>=1 else f"{b}\n{pv:>{w}}"

def _campo(content, *nomes):
    """Retorna o primeiro campo nao-vazio dentre varios nomes possiveis. Blindado contra None."""
    if not isinstance(content, dict): return ""
    for n in nomes:
        v = content.get(n)
        if v not in (None, "", [], {}):
            return str(v).strip()
    return ""

def _bloco_endereco(content, w, titulo="ENTREGA:"):
    """Monta o bloco de endereco de entrega a partir de QUALQUER nome de campo provavel.
    Cobre 3 situacoes:
      1. String pronta em delivery_address (ou variantes de nome).
      2. Endereco aninhado como objeto: delivery_address = {street, number, ...}.
      3. Campos separados no nivel do content, com ou sem prefixo (delivery_address_* / delivery_*).
    Retorna [] se realmente nao houver nenhum dado de endereco.
    Nunca lanca excecao — na duvida, retorna o que conseguiu montar.
    """
    if not isinstance(content, dict): return []
    S = "-"*w
    linhas = []

    # 1) String pronta (varios nomes possiveis que o servidor pode usar)
    addr = _campo(content, "delivery_address", "delivery_address_street",
                  "delivery_street", "address", "endereco", "endereco_entrega",
                  "delivery_address_line1", "delivery_address_full")
    # Se delivery_address veio como objeto/dict (endereco aninhado), trata como campos separados
    _addr_obj = content.get("delivery_address")
    if isinstance(_addr_obj, dict):
        addr = ""  # ignora a string; vamos montar a partir do objeto abaixo
        src = _addr_obj
    else:
        src = content

    def g(*nomes):
        return _campo(src, *nomes)

    # Numero da casa — pode vir separado mesmo quando ha string pronta.
    # v5.80: 'numero' SO vale dentro do OBJETO de endereco. No content plano, 'numero' e o numero do
    # PEDIDO (e assim que o cupom inteiro o le): pedido sem campo de numero da casa saia com
    # "Numero: <numero do pedido>" no bloco de entrega, como se fosse o numero da casa.
    _nomes_num = ["delivery_number", "delivery_address_number", "number", "numero_casa", "house_number"]
    if src is not content:
        _nomes_num.insert(3, "numero")
    num = g(*_nomes_num)
    if addr:
        linhas.append(addr)
        # Se a string pronta NAO contem o numero da casa, imprime o numero em linha propria.
        # Corrige jobs que saiam sem o numero quando delivery_address vinha sem numero mas
        # delivery_number estava preenchido em campo separado. Linha rotulada = sempre legivel,
        # independente do formato da string.
        import re as _re
        _tem_num = bool(num) and _re.search(r'\b' + _re.escape(str(num)) + r'\b', addr)
        if num and not _tem_num:
            linhas.append(f"Numero: {num}")
    else:
        # 2/3) Monta rua + numero a partir de campos separados (com e sem prefixo)
        rua = g("delivery_address_street", "delivery_street", "street", "logradouro", "rua",
                "delivery_address_line1", "line1")
        if rua:
            linhas.append(f"{rua}, {num}" if num else rua)

    # Complemento
    comp = g("delivery_address_complement", "delivery_complement", "complement",
             "complemento", "delivery_address_line2", "line2")
    if comp: linhas.append(comp)
    # Bairro
    bairro = g("delivery_address_neighborhood", "delivery_neighborhood", "delivery_address_district",
               "neighborhood", "bairro", "district")
    if bairro: linhas.append(bairro)
    # Cidade + estado
    city = g("delivery_address_city", "delivery_city", "city", "cidade")
    uf = g("delivery_address_state", "delivery_state", "state", "estado", "uf")
    if city:
        linhas.append(f"{city} - {uf}" if uf else city)
    elif uf:
        linhas.append(uf)
    # CEP
    cep = g("delivery_address_postal_code", "delivery_postal_code", "postal_code",
            "cep", "zipcode", "zip")
    if cep: linhas.append(f"CEP: {cep}")
    # Referencia
    ref = g("delivery_address_reference", "delivery_reference", "reference", "referencia", "ponto_referencia")
    if ref: linhas.append(f"Ref: {ref}")
    # Observacao para o entregador (nivel do content, nao do objeto de endereco)
    dnotes = _campo(content, "delivery_notes", "delivery_note", "delivery_instructions",
                    "obs_entrega", "observacao_entrega")
    if dnotes: linhas.append(f"Obs entrega: {dnotes}")

    if not linhas:
        return []
    return [S, titulo.center(w)] + linhas

# ═══════════════════════════════════════════════════════════════════════════════
# NFC-e — DANFE (Documento Auxiliar da NFC-e)
#
# O cupom fiscal e um DOCUMENTO DIFERENTE do cupom do pedido, e tudo o que sai nele vem do
# XML AUTORIZADO PELA SEFAZ (bloco content.fiscal) — nada do pedido, nada de printer_settings.
# Se essa regra for violada, o lojista entrega um documento invalido ao cliente.
#
# Sem XML nao existe DANFE: o bloco NUNCA e montado a partir do pedido, em nenhuma hipotese.
# ═══════════════════════════════════════════════════════════════════════════════

# Tipos de job que sao CUPOM DE CLIENTE (cabecalho + rodape do lojista), nunca comanda de
# setor. Lista EXPLICITA de proposito: classificar por exclusao ("se nao e order nem receipt,
# entao e setor") fazia o cupom fiscal sair como ticket de cozinha, sem o bloco fiscal.
# AO CRIAR UM job_type NOVO QUE SEJA CUPOM DE CLIENTE, ELE PRECISA ENTRAR AQUI.
TIPOS_CUPOM_CLIENTE = {"order", "receipt", "fiscal", "danfce", "pickup", "delivery"}
TIPOS_CUPOM_FISCAL  = {"fiscal", "danfce"}

# Ajuste SINIEF 32/24, em vigor desde 01/02/2025: todo impresso entregue ao consumidor que
# NAO seja documento fiscal precisa dizer isso. Sem esse aviso o cliente leva um papel com
# itens e total achando que recebeu nota.
# FS0/FSB: o aviso fica em tamanho NORMAL mesmo com o cupom em fonte grande (23 caracteres
# em 2x nao cabem no papel de 58 mm) — e exigencia de layout, nao estetica (SINIEF 32/24).
AVISO_NAO_FISCAL = "[[NEG_ON]][[FS0]]NÃO É DOCUMENTO FISCAL[[FSB]][[NEG_OFF]]"

def _escpos_qr(dados, modulo=5):
    """QR Code pelo comando NATIVO da impressora (GS ( k) — nao bitmap: imprime mais rapido e
    sai nitido em qualquer resolucao. Nivel de correcao de erro M (0x32), exigido pela
    especificacao da NFC-e (em L o leitor falha em papel manchado/desbotado).

    O conteudo vai em bytes crus, SEM passar pelo codepage da impressora: o payload do QR nao
    e texto para imprimir, e dado do simbolo. Modelos antigos sem QR nativo ignoram o comando
    e imprimem o resto do cupom — a chave de acesso continua legivel em texto (Divisao IV).
    """
    d = str(dados or "").encode("utf-8", "ignore")
    if not d:
        return b""
    if len(d) > 7089:  # capacidade maxima do modelo 2
        d = d[:7089]
    n = len(d) + 3
    return (b"\x1d\x28\x6b\x04\x00\x31\x41\x32\x00"                                  # modelo 2
            + b"\x1d\x28\x6b\x03\x00\x31\x43" + bytes([max(1, min(16, int(modulo)))])  # tamanho do modulo
            + b"\x1d\x28\x6b\x03\x00\x31\x45\x32"                                    # correcao de erro M
            + b"\x1d\x28\x6b" + bytes([n & 0xff, (n >> 8) & 0xff]) + b"\x31\x50\x30" + d  # dados
            + b"\x1d\x28\x6b\x03\x00\x31\x51\x30")                                   # imprime

def _fv(v):
    """Valor que veio do XML: usa como esta. Ja chega em pt-BR e com documento mascarado —
    o agente NAO formata e NAO recalcula nada. None/ausente vira '' para ser OMITIDO."""
    if v is None:
        return ""
    return str(v).strip()

def _wrap_linhas(texto, w, indent=""):
    """Quebra texto em linhas de no maximo w colunas sem cortar palavra no meio. Palavra maior
    que a largura e fatiada (URL de consulta, descricao de produto sem espaco).
    Truncar nao e opcao aqui: campo do DANFE cortado no meio e documento errado.
    Piso 4 (era 8): com fonte Extra em papel de 58 mm sobram 10 colunas logicas, e o piso 8
    somado ao recuo de continuacao (4) gerava linha de 12 — estourava a largura fisica."""
    if texto is None:
        return []
    largura = max(4, w - len(indent))
    out = []
    for bruto in str(texto).split("\n"):
        palavras = bruto.split()
        if not palavras:
            continue
        atual = ""
        for palavra in palavras:
            while len(palavra) > largura:
                if atual:
                    out.append(indent + atual); atual = ""
                out.append(indent + palavra[:largura]); palavra = palavra[largura:]
            cand = palavra if not atual else atual + " " + palavra
            if len(cand) <= largura:
                atual = cand
            else:
                out.append(indent + atual); atual = palavra
        if atual:
            out.append(indent + atual)
    return out

def _quebrar_linhas_longas(linhas, w):
    """Quebra, na palavra, as linhas de texto maiores que o papel (v5.80). Antes a propria
    impressora quebrava onde a coluna acabava, no meio da palavra: o endereco de entrega saia
    "...Monte Alto" / ", SP, CEP" e a observacao do cliente idem. Linhas que ja cabem (e as com
    marcador [[...]]) saem EXATAMENTE iguais. Continuacao de "+ adicional" e ">> obs" fica
    recuada sob o texto. Aceita elementos com '\\n' dentro (observacao em varias linhas)."""
    out = []
    for item in linhas:
        if not isinstance(item, str):
            out.append(item); continue
        for l in item.split("\n"):
            if len(l) <= w or "[[" in l:
                out.append(l); continue
            corpo = l.lstrip(" ")
            lead = " " * (len(l) - len(corpo))
            cont = lead + ("  " if corpo.startswith(("+ ", ">> ")) else "")
            partes = _wrap_linhas(corpo, w - len(lead))
            if not partes:
                out.append(l); continue
            out.append(lead + partes[0])
            resto = " ".join(partes[1:])
            if resto:
                out.extend(_wrap_linhas(resto, w, cont))
    return out

def _par(esq, dir_, w):
    """Rotulo a esquerda, valor alinhado a direita, dentro de w colunas. Se nao couber na mesma
    linha, o valor desce para a linha seguinte alinhado a direita — nunca abrevia o rotulo
    (os rotulos da Divisao III sao literais e nao podem ser encurtados)."""
    esq = str(esq); dir_ = str(dir_)
    espaco = w - len(esq) - len(dir_)
    if espaco < 1:
        return [esq, dir_.rjust(w)]
    return [esq + " " * espaco + dir_]

def _cupom_fiscal_bytes(content, fiscal, w):
    """Desenha o cupom fiscal (DANFE) nas divisoes I a IX, na ordem NORMATIVA, e devolve os
    bytes ESC/POS prontos para a impressora.

    REGRAS QUE NAO SAO ESTETICA:
      - Rotulos sao LITERAIS: "Qtde. Total de Itens", "Valor Total R$", "Valor a Pagar R$",
        "Forma de Pagamento", "Valor Pago", "Troco" e a frase de consulta nao se abreviam
        nem se traduzem.
      - Campo obrigatorio ausente e OMITIDO, nunca inventado: serie nula nao vira "001", a
        linha sai fora. Cupom declarando serie diferente da transmitida e documento errado.
      - Contingencia imprime o aviso em DOIS lugares (apos a Divisao I e apos a VII) e
        SUPRIME o protocolo — a nota ainda nao foi autorizada.
      - Troco, forma de pagamento e valor pago sao obrigatorios na Divisao III: NENHUM toggle
        de configuracao pode remover.
      - Em delivery, nome do consumidor e endereco de entrega sao obrigatorios e NAO dependem
        de print_customer_info.
      - Fonte NORMAL sempre: o font_size que o lojista escolheu no agente nao vale aqui, senao
        as colunas do documento fiscal desalinham.
      - O corte nunca acontece antes do fim da Divisao IX nem sobre a zona de silencio do QR.
    """
    NORMAL  = b"\x1b\x21\x00"
    CENTRO  = b"\x1b\x61\x01"
    ESQUERDA = b"\x1b\x61\x00"
    NEG_ON  = b"\x1b\x45\x01"
    NEG_OFF = b"\x1b\x45\x00"
    S = "-" * w

    # ESC t primeiro: este caminho devolve bytes e vai direto pra impressora, sem passar pelo
    # prefixo montado em _imprimir_raw/_imprimir_tcp. Sem isso "Consumidor Eletronica",
    # "autorizacao" e "HOMOLOGACAO" saem com caractere de moldura no lugar do acento.
    p = [_escpos_cp(), ESQUERDA, NORMAL]
    ENC = lambda s: _enc(_txt(str(s)) + "\n")

    def esq(linhas):
        for l in linhas or []:
            p.append(ENC(l))

    def centro(linhas, negrito=False):
        if not linhas:
            return
        p.append(CENTRO)
        if negrito: p.append(NEG_ON)
        for l in linhas:
            p.append(ENC(l))
        if negrito: p.append(NEG_OFF)
        p.append(ESQUERDA)

    # Aviso de contingencia — montado aqui porque sai em DOIS pontos (apos I e apos VII).
    _cont = _fv(fiscal.get("contingencia")).lower()
    if _cont == "offline":
        aviso_cont = ["EMITIDA EM CONTINGÊNCIA OFF-LINE", "Pendente de autorização"]
    elif _cont == "epec":
        aviso_cont = ["EMITIDA EM CONTINGÊNCIA VIA EPEC", "Pendente de autorização"]
    elif _cont:
        aviso_cont = ["EMITIDA EM CONTINGÊNCIA", "Pendente de autorização"]
    else:
        aviso_cont = []

    # ── Divisao I — emitente (do XML, NAO de printer_settings) ───────────────
    centro(_wrap_linhas(_fv(fiscal.get("emitente_nome")), w), negrito=True)
    doc = _fv(fiscal.get("emitente_documento"))
    if doc:
        centro([f"{'CNPJ' if '/' in doc else 'CPF'}: {doc}"])
    ie = _fv(fiscal.get("emitente_ie"))
    if ie:
        centro([f"IE: {ie}"])
    for linha in (fiscal.get("emitente_endereco") or []):
        centro(_wrap_linhas(_fv(linha), w))
    p.append(ENC(S))
    centro(_wrap_linhas("Documento Auxiliar da Nota Fiscal de Consumidor Eletrônica", w))
    if aviso_cont:
        centro(aviso_cont, negrito=True)

    # ── Divisao II — itens: os 6 campos sao obrigatorios e nunca somem ───────
    p.append(ENC(S))
    esq(["CÓDIGO  DESCRIÇÃO"])
    esq(_par("QTDE UN  VL UNIT R$", "VL TOTAL R$", w))
    p.append(ENC(S))
    for it in (fiscal.get("itens") or []):
        if not isinstance(it, dict):
            continue
        cod  = _fv(it.get("codigo"))
        desc = _fv(it.get("descricao"))
        esq(_wrap_linhas(f"{cod} {desc}".strip(), w))
        qtd = _fv(it.get("quantidade"))
        un  = _fv(it.get("unidade"))
        vu  = _fv(it.get("valor_unitario"))
        vt  = _fv(it.get("valor_total"))
        esq(["  " + l for l in _par(f"{qtd} {un} x {vu}".strip(), vt, w - 2)])
        vtrib = _fv(it.get("valor_tributos"))   # opcional
        if vtrib:
            esq(["  " + l for l in _par("Valor Tributos R$", vtrib, w - 2)])

    # ── Divisao III — totais e pagamento ────────────────────────────────────
    p.append(ENC(S))
    qi = _fv(fiscal.get("qtde_itens"))          # itens DISTINTOS, nao a soma das quantidades
    if qi: esq(_par("Qtde. Total de Itens", qi, w))
    vtot = _fv(fiscal.get("valor_total"))
    if vtot: esq(_par("Valor Total R$", vtot, w))
    acr = _fv(fiscal.get("acrescimos_desconto"))  # null = omitir a linha
    if acr: esq(_par("Acréscimos/Desconto R$", acr, w))
    vpag = _fv(fiscal.get("valor_a_pagar"))
    if vpag: esq(_par("Valor a Pagar R$", vpag, w))
    pagamentos = [x for x in (fiscal.get("pagamentos") or []) if isinstance(x, dict)]
    if pagamentos:
        esq(_par("Forma de Pagamento", "Valor Pago R$", w))
        for pg in pagamentos:
            esq(_par(_fv(pg.get("forma")), _fv(pg.get("valor")), w))
    troco = _fv(fiscal.get("troco"))            # obrigatorio quando existe
    if troco: esq(_par("Troco R$", troco, w))

    # ── Divisao IV — consulta pela chave de acesso ──────────────────────────
    p.append(ENC(S))
    centro(_wrap_linhas("Consulte pela Chave de Acesso em", w))
    url = _fv(fiscal.get("url_chave"))
    if url: centro(_wrap_linhas(url, w))
    # chave_formatada vem em 11 blocos de 4: quebra nos espacos, sem partir bloco no meio
    chave = _fv(fiscal.get("chave_formatada")) or _fv(fiscal.get("chave_acesso"))
    if chave: centro(_wrap_linhas(chave, w))

    # ── Divisao V — QR Code (centralizado) ──────────────────────────────────
    qr = _fv(fiscal.get("qr_code_url"))
    if qr:
        p.append(b"\n")
        p.append(CENTRO)
        # Modulo 5 em bobina 80 mm, 4 em 58 mm — em 58 mm o modulo 5 corta o simbolo
        p.append(_escpos_qr(qr, 4 if w <= 34 else 5))
        p.append(b"\n")
        p.append(ESQUERDA)

    # ── Divisao VI — consumidor ─────────────────────────────────────────────
    p.append(ENC(S))
    cons = fiscal.get("consumidor") if isinstance(fiscal.get("consumidor"), dict) else {}
    tipo_c = _fv(cons.get("tipo")).lower()
    doc_c  = _fv(cons.get("documento"))
    if tipo_c == "cpf" and doc_c:
        esq(_wrap_linhas(f"CONSUMIDOR CPF: {doc_c}", w))
    elif tipo_c == "cnpj" and doc_c:
        esq(_wrap_linhas(f"CONSUMIDOR CNPJ: {doc_c}", w))
    elif tipo_c in ("estrangeiro", "id_estrangeiro") and doc_c:
        esq(_wrap_linhas(f"CONSUMIDOR Id. Estrangeiro: {doc_c}", w))
    else:
        esq(["CONSUMIDOR NÃO IDENTIFICADO"])
    nome_c = _fv(cons.get("nome"))
    if nome_c:
        esq(_wrap_linhas(nome_c, w))
    # Endereco de entrega: null fora de delivery. Em delivery e obrigatorio e NAO depende de
    # print_customer_info — por isso nao ha nenhum toggle consultado aqui.
    for l in (fiscal.get("entrega_endereco") or []):
        esq(_wrap_linhas(_fv(l), w))

    # ── Divisao VII — identificacao da nota ─────────────────────────────────
    p.append(ENC(S))
    ident = []
    num = _fv(fiscal.get("numero"))
    ser = _fv(fiscal.get("serie"))
    if num: ident.append(f"NFC-e nº {num}")
    if ser: ident.append(f"Série {ser}")   # serie nula: a linha sai fora, nao vira "001"
    if ident:
        centro(_wrap_linhas(" ".join(ident), w))
    de = _fv(fiscal.get("data_emissao"))   # dhEmi da NOTA, em Brasilia
    if de:
        centro(_wrap_linhas(f"Emissão: {de}", w))
    if aviso_cont:
        # Contingencia SUPRIME o protocolo: a nota ainda nao foi autorizada
        centro(aviso_cont, negrito=True)
    else:
        prot = _fv(fiscal.get("protocolo"))
        if prot:
            centro(_wrap_linhas(f"Protocolo de Autorização: {prot}", w))
        da = _fv(fiscal.get("data_autorizacao"))
        if da:
            centro(_wrap_linhas(da, w))

    # ── Divisao VIII — mensagem fiscal ──────────────────────────────────────
    # homologacao vem do tpAmb do XML, SEM default: so avisa quando e explicitamente True.
    # Em producao esse aviso NAO pode aparecer.
    if fiscal.get("homologacao") is True:
        p.append(ENC(S))
        centro(["EMITIDA EM AMBIENTE DE HOMOLOGAÇÃO", "SEM VALOR FISCAL"], negrito=True)
    iaf = _fv(fiscal.get("inf_ad_fisco"))
    if iaf:
        p.append(ENC(S))
        esq(_wrap_linhas(iaf, w))

    # ── Divisao IX — informacoes complementares ─────────────────────────────
    icpl = _fv(fiscal.get("inf_cpl"))
    vat  = _fv(fiscal.get("valor_aproximado_tributos"))
    if icpl or vat:
        p.append(ENC(S))
        if icpl: esq(_wrap_linhas(icpl, w))
        if vat:  esq(_par("Valor aproximado dos tributos R$", vat, w))

    # ── Rodape do lojista — SO depois do fim da Divisao IX, nunca antes ─────
    # A mensagem institucional ("Obrigado pela preferencia") e permitida, mas FORA das
    # divisoes: dentro delas seria texto livre no meio do documento fiscal.
    # E nesta area pos-DANFE que cabem, no futuro, QR proprio de avaliacao, saldo de
    # fidelidade e cupom promocional.
    rod = _fv(content.get("footer_message"))
    if rod:
        p.append(ENC(S))
        centro(_wrap_linhas(rod, w))

    p.append(NORMAL)
    p.append(b"\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00")  # avanco + corte (bem depois da Divisao IX)
    return b"".join(p)

def _fmt(content, jt, pt, imp=None):
    # Se o servidor mandar content aninhado ({pedido: {...}}), desembrulha campos do pedido
    # para que o resto do codigo continue lendo do 'content' plano.
    # Campos de nivel do content (auto_print, paper_width, company_name, etc.) tem prioridade
    # sobre os campos de dentro do pedido (para nao sobrescrever config do restaurante).
    # Acentos compostos (NFC) antes de qualquer medicao de coluna: se o nome vier decomposto
    # ('a'+acento separados), len() conta 2 e a coluna de preco desalinha.
    content = _norm(content)
    # Desembrulha o pedido aninhado qualquer que seja o nome da chave: 'pedido' (agent-jobs) ou
    # 'order' (print-agent-poll). Sem isto o shape do print-agent-poll sairia com os itens mas
    # sem numero do pedido, cliente e totais — todos eles estao um nivel abaixo.
    for _chave_ninho in ("pedido", "order"):
        _ninho = content.get(_chave_ninho)
        if isinstance(_ninho, dict):
            _merged = dict(_ninho)
            _merged.update(content)  # content por cima — mantem tudo que ja veio no nivel de fora
            content = _merged
    # Largura do papel: precedencia unica em _largura_efetiva (colunas da impressora > ajuste
    # local geral > paper_width do job > largura do cardapio via poll > 48). 'imp' e a
    # impressora resolvida em proc_job; chamadas sem impressora (teste/reimpressao) pulam o 1.
    w = _largura_efetiva(imp, content, _paper_width_servidor)
    w_fis = w   # largura FISICA do papel; w pode ser reduzido abaixo por font_size (so receipt)
    # v5.81: fonte POR IMPRESSORA (imp['font_size'] > cfg['font_size']), escala 0..3.
    _fs = _fonte_da_impressora(imp)
    # Para cozinha/bar: nao reduz w — todos os detalhes sempre aparecem.
    # Fonte grande so no nome do item (inline via ESC/POS); addons/obs em normal.
    # Para receipt: reduz w para alinhar colunas de preco com a fonte maior.
    # 'Media' (altura dupla) tem wmul 1: a letra cresce e NENHUMA coluna se perde.
    _is_kitchen = jt in ("kitchen","bar") or pt in ("kitchen","bar")
    if not _is_kitchen:
        w = max(8, w // _FONTE_WMUL[_fs])
    S="-"*w
    # v5.82: interruptor do codigo de barras decidido UMA vez por cupom (content > ajuste da loja via
    # poll > desligado) e registrado no log — para nunca mais depurar isto no escuro.
    _barcode_on = _flag_print_barcode(content)
    if not _is_kitchen:
        try:
            _its_log = _itens_do_content(content)
            _ncod = sum(1 for _i in _its_log if _codigo_do_item(_i))
            _nean = sum(1 for _i in _its_log if _valida_ean13(_codigo_do_item(_i)))
            _orig = ("chave print_barcode do job" if "print_barcode" in content
                     else ("ajuste da loja (poll)" if _print_barcode_servidor is not None else "nenhum: job sem a chave e servidor sem ajuste"))
            log.info(f"[CUPOM] codigo de barras: {'LIGADO' if _barcode_on else 'desligado'} | origem: {_orig} | "
                     f"itens com codigo: {_ncod}/{len(_its_log)} (EAN-13 validos: {_nean})")
        except Exception:
            pass

    # Flags de exibição configuráveis
    show_phone    = content.get("print_customer_info", True)
    show_payment  = content.get("print_payment_method", True)
    # Agrupar itens por categoria com cabecalho — opt-in por loja, decidido pelo SERVIDOR.
    # Desligado (o normal) = cupom identico ao de antes.
    _agrupar_cat  = _agrupar_por_categoria(content)

    tipo=content.get("type",jt); ll=[]
    # Normaliza tipos de salao/mesa para o layout receipt
    if tipo not in ("order","receipt","kitchen","bar","pickup","delivery","command","test_page",
                    "fiscal","danfce"):
        tipo = "receipt"

    # CUPOM FISCAL (NFC-e): documento DIFERENTE do cupom do pedido. Sai por modulo proprio,
    # justamente para nao se misturar com o cupom operacional, e tudo vem do XML autorizado.
    # Basta o bloco fiscal chegar: um job com content.fiscal E cupom fiscal, qualquer que seja
    # o 'type' que o servidor carimbou.
    _fiscal = content.get("fiscal")
    if isinstance(_fiscal, dict) and _fiscal:
        return _cupom_fiscal_bytes(content, _fiscal, w)

    if tipo in ("order","receipt","fiscal","danfce"):
        # Ajuste SINIEF 32/24: cupom do pedido SEM NFC-e autorizada precisa avisar que nao e
        # documento fiscal. Quando ha bloco fiscal o codigo nem chega aqui (retornou acima) —
        # e la o aviso NAO aparece, porque ali o documento e fiscal.
        ll.append(AVISO_NAO_FISCAL)
        # store_name antes do cfg: numa rede, cfg e a MATRIZ, e o cupom da filial tem que
        # sair com o nome de quem vendeu. company_name (razao social) segue tendo prioridade.
        ne=content.get("company_name","") or _loja_do_job(content) or cfg.get("restaurant_name","")
        if ne: ll += _lin_centro(ne.upper(), "loja")
        e=content.get("company_address","")
        if e: ll.append(e.center(w))
        t=content.get("company_phone","")
        if t: ll.append(f"Tel: {t}".center(w))
        ll.append(S)
        # Selo da loja: so em filial impressa no concentrador (servidor decide).
        _selo=_linhas_selo_loja(content, w)
        if _selo: ll += _selo + [S]
        n=content.get("numero","") or content.get("order_number","")
        if n: ll.append(_lin_pedido(n))
        data_brt,_=_data_hora_brt(content)   # BRT: created_at_brt do servidor ou created_at (UTC) convertido
        if data_brt: ll.append(f"Data: {data_brt}")
        tp=content.get("order_type","")
        if tp: ll.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        c2=content.get("customer_name","")
        if c2: ll.append(f"Cliente: {c2}")
        # Mesa: só mostra se for diferente do tipo de pedido (evita "Mesa: COMER AQUI")
        m=content.get("table_number","")
        tipos_pedido = list(TL.keys()) + list(TL.values()) + ["pickup","counter","dine_in","takeaway","delivery","retirada","mesa","balcao","comer aqui"]
        if m and str(m).lower() not in [x.lower() for x in tipos_pedido]:
            ll.append(f"Mesa: {m}")
        # Telefone do cliente (controlado por print_customer_info)
        ph=content.get("customer_phone","")
        if ph and show_phone: ll.append(f"Tel: {ph}")
        # AGENDADO: logo apos Cliente/Mesa/Tel e ANTES dos itens, com separador + negrito 2x2
        # (contrato do print_jobs.content; espelha o agente Tauri). Ausente => cupom igual.
        _ag=_linhas_agendado(content, w_fis)
        if _ag: ll.append(S); ll += _ag
        ll.append(S)
        DP="."*w
        for _cat, _itens_cat in _grupos_por_categoria(_itens_do_content(content), _agrupar_cat):
            ll += _cab_categoria(_cat, w)
            for item in _itens_cat:
                size=_size_do_item(item)
                ll += _lin_item(item)
                ll += _linha_pai(item)
                _cod_ln=_linha_codigo_item(item, _barcode_on)
                if _cod_ln: ll.append(_cod_ln)
                for a in _adicionais_do_item(item):
                    if size and a.get('nome','').strip()==size: continue  # ja saiu no cabecalho
                    pc=a.get("preco_cents",0)
                    # Sem sufixo de setor aqui: cupom do cliente nao mostra roteamento interno
                    ll.append(f"  + {a.get('nome','')}{f' {_R(pc)}' if pc else ''}")
                obs=_obs_do_item(item)
                if obs: ll.append(f"  >> {obs}")
                ll.append(DP)
        ll.append(S)
        sub=content.get("subtotal_cents",0); desc=content.get("discount_cents",0)
        ent=content.get("delivery_fee_cents",0); tot=content.get("total_cents",0)
        # v5.81: _par empilha o valor quando nao cabe ao lado do rotulo (fonte Extra em 58 mm
        # deixa 10 colunas; antes o f-string gerava linha maior que o papel e quebrava no meio).
        if sub: ll += _par("Subtotal:", _R(sub), w)
        if desc and int(desc)>0: ll += _par("Desconto:", f"-{_R(desc)}", w)
        if ent and int(ent)>0: ll += _par("Taxa entrega:", _R(ent), w)
        ll += _lin_valor("TOTAL:", _R(tot), "total")
        pg=content.get("payment_method","")
        if pg and show_payment: ll.append(f"Pagamento: {PL.get(pg.lower(),pg)}")
        cod=content.get("pickup_code","")
        if cod: ll.append("="*w); ll.append(f"RETIRADA: {cod}".center(w)); ll.append("="*w)
        obs2=content.get("notes","")
        if obs2: ll.append(S); ll.append(f"Obs: {obs2}")
        # Endereço de entrega (delivery) — bloco robusto que aceita varios nomes de campo
        ll += _bloco_endereco(content, w, titulo="ENTREGA:")
        rod=content.get("footer_message","")
        if rod: ll.append(S); ll += _lin_centro(rod, "rodape")
        ll.append(S)
    elif tipo in ("kitchen","bar"):
        titulo = "COZINHA" if tipo=="kitchen" else "BAR"
        # Cabecalho sempre em fonte normal para caber na largura do papel
        cab = []
        # Ajuste SINIEF 32/24: comanda de setor NUNCA e documento fiscal — aviso no TOPO.
        cab.append(AVISO_NAO_FISCAL)
        cab+=["*"*w, titulo.center(w), "*"*w]
        # Selo da loja LOGO ABAIXO do titulo, antes do numero do pedido: na matriz que
        # concentra as filiais, produzir o pedido da loja errada e o erro mais caro que
        # existe aqui — quem esta na chapa tem que ler a loja antes de qualquer outra coisa.
        cab += _linhas_selo_loja(content, w)
        n=content.get("numero","") or content.get("order_number","")
        if n: cab.append(_lin_pedido(n))
        # AGENDADO dentro do bloco grande (2x2), junto do numero/tipo/mesa: e o dado que define
        # se o item entra em producao AGORA ou depois — quem esta na chapa le antes dos itens.
        cab += _linhas_agendado(content, w_fis)
        tp=content.get("order_type","")
        if tp: cab.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        m=content.get("table_number","")
        if m: cab.append(f"Mesa: {m}")
        c2=content.get("customer_name","")
        if c2: cab.append(f"Cliente: {c2}")
        if tipo=="kitchen":
            # v5.78: hora em BRT. O fallback antigo fazia fromisoformat(created_at).strftime —
            # imprimia a hora UTC crua (3h adiantada) em todo job vindo do agent-unified-poll.
            _,hora=_data_hora_brt(content)
            if hora: cab.append(f"Hora: {hora}")
        cab.append(S)

        if _fs <= 0:
            # Fonte normal: comportamento original — tudo em string
            ll += cab
            DP="."*w
            itens=_itens_do_content(content)
            # Na comanda o tamanho errado vira PRATO errado: o cabecalho de categoria e ainda
            # mais critico aqui do que no cupom do caixa.
            for _cat, _itens_cat in _grupos_por_categoria(itens, _agrupar_cat):
                ll += _cab_categoria(_cat, w)
                for item in _itens_cat:
                    size=_size_do_item(item)
                    q=_qtd_do_item(item); ll.append(f"[ {q}x ]  {_nome_com_tamanho(item)}")
                    ll += _linha_pai(item)
                    _cod_ln=_linha_codigo_item(item, _barcode_on)
                    if _cod_ln: ll.append(_cod_ln)
                    for a in _adicionais_do_item(item):
                        if size and a.get('nome','').strip()==size: continue  # ja saiu no cabecalho
                        ll.append(f"  + {a.get('nome','')}{_rota_sufixo(a)}")
                    obs=_obs_do_item(item)
                    if obs: ll.append(f"  >> {obs}")
                    ll.append(DP)
            # Um setor pode receber comanda sem item proprio (ex: bar so com bebida de combo)
            if not itens: ll.append("  (sem itens para este setor)"); ll.append(DP)
            obs2=content.get("notes","")
            if obs2: ll.append(S); ll.append(f"OBS: {obs2}")
            ll.append(S)
        else:
            # Fonte grande: retorna bytes com comandos ESC/POS inline.
            # Cabecalho e detalhes (addons, obs) em normal; nome do item em grande.
            FNORMAL = b"\x1b\x21\x00"
            FBIG    = _gs_fonte(_fs)
            DP_str  = "."*w
            # ESC t primeiro: este caminho devolve bytes e vai direto pra impressora,
            # sem passar pelo prefixo montado em _imprimir_raw/_imprimir_tcp.
            # Estilo geral (negrito/escuro/espaco) tambem entra aqui pelo mesmo motivo.
            parts = [_escpos_cp(), FNORMAL, _escpos_estilo_base()]
            enc = lambda s: _enc(_txt(s)+"\n")
            encq = lambda s: b"".join(enc(x) for x in _quebrar_linhas_longas([s], w))   # v5.80: quebra na palavra
            for linha in cab:
                if _tem_marcador(linha):
                    # Substitui marcadores por bytes ESC/POS reais. FNORMAL em seguida: na
                    # comanda a fonte base vale SO para o nome do item; o [[FSB]] do marcador
                    # restaura a base da impressora e sem este reset o resto do cabecalho
                    # sairia grande e estouraria a largura (formatado em w cheio).
                    parts.append(_substituir_marcadores_escpos(linha + "\n"))
                    parts.append(FNORMAL)
                else:
                    parts.append(encq(linha))
            itens=_itens_do_content(content)
            # Cabecalho de categoria em fonte NORMAL (como o resto do cab): em fonte grande a
            # linha de largura w estouraria o papel e quebraria em duas.
            for _cat, _itens_cat in _grupos_por_categoria(itens, _agrupar_cat):
                for linha in _cab_categoria(_cat, w):
                    parts.append(enc(linha))   # negrito ja vem inline na string
                for item in _itens_cat:
                    q=_qtd_do_item(item)
                    size=_size_do_item(item)
                    nome=_nome_com_tamanho(item)
                    parts.append(FBIG)
                    # Quebra NA PALAVRA na largura logica da fonte (v5.81): em 2x2 cada letra
                    # ocupa 2 colunas e um nome comprido estourava o papel — a impressora
                    # quebrava no meio da palavra ("Calabre"/"sa").
                    for _lx in _quebrar_linhas_longas([f"[ {q}x ]  {nome}"], max(8, w // _FONTE_WMUL[_fs])):
                        parts.append(enc(_lx))
                    parts.append(FNORMAL)
                    for linha in _linha_pai(item): parts.append(enc(linha))
                    _cod_ln=_linha_codigo_item(item, _barcode_on)
                    if _cod_ln: parts.append(_substituir_marcadores_escpos(_cod_ln + "\n"))
                    for a in _adicionais_do_item(item):
                        if size and a.get('nome','').strip()==size: continue  # ja saiu no cabecalho
                        parts.append(encq(f"  + {a.get('nome','')}{_rota_sufixo(a)}"))
                    obs=_obs_do_item(item)
                    if obs: parts.append(encq(f"  >> {obs}"))
                    parts.append(enc(DP_str))
            # Um setor pode receber comanda sem item proprio (ex: bar so com bebida de combo)
            if not itens:
                parts.append(enc("  (sem itens para este setor)")); parts.append(enc(DP_str))
            obs2=content.get("notes","")
            if obs2:
                parts.append(enc(S))
                parts.append(encq(f"OBS: {obs2}"))
            parts.append(enc(S))
            parts.append(FNORMAL)
            parts.append(_escpos_sufixo())  # avanço + corte (configuraveis desde a v5.81)
            return b"".join(parts)
    elif tipo=="pickup":
        # Ajuste SINIEF 32/24: cupom sem NFC-e autorizada avisa que nao e documento fiscal
        ll.append(AVISO_NAO_FISCAL)
        # store_name antes do cfg: numa rede, cfg e a MATRIZ, e o cupom da filial tem que
        # sair com o nome de quem vendeu. company_name (razao social) segue tendo prioridade.
        ne=content.get("company_name","") or _loja_do_job(content) or cfg.get("restaurant_name","")
        if ne: ll += _lin_centro(ne.upper(), "loja")
        e=content.get("company_address","")
        if e: ll.append(e.center(w))
        ll.append(S)
        # Selo da loja: so em filial impressa no concentrador (servidor decide).
        _selo=_linhas_selo_loja(content, w)
        if _selo: ll += _selo + [S]
        n=content.get("numero","") or content.get("order_number","")
        if n: ll.append(_lin_pedido(n))
        data_brt,_=_data_hora_brt(content)   # BRT: created_at_brt do servidor ou created_at (UTC) convertido
        if data_brt: ll.append(f"Data: {data_brt}")
        tp=content.get("order_type","")
        if tp: ll.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        c2=content.get("customer_name","")
        if c2: ll.append(f"Cliente: {c2}")
        ph=content.get("customer_phone","")
        if ph and show_phone: ll.append(f"Tel: {ph}")
        cod=content.get("pickup_code","")
        if cod: ll.append(f"Codigo: {cod}".center(w))
        # AGENDADO antes dos itens (mesma regra do cupom): quem retira precisa ver o horario
        _ag=_linhas_agendado(content, w_fis)
        if _ag: ll.append(S); ll += _ag
        ll.append(S)
        DP="."*w
        for _cat, _itens_cat in _grupos_por_categoria(_itens_do_content(content), _agrupar_cat):
            ll += _cab_categoria(_cat, w)
            for item in _itens_cat:
                size=_size_do_item(item)
                ll += _lin_item(item)
                ll += _linha_pai(item)
                _cod_ln=_linha_codigo_item(item, _barcode_on)
                if _cod_ln: ll.append(_cod_ln)
                for a in _adicionais_do_item(item):
                    if size and a.get('nome','').strip()==size: continue  # ja saiu no cabecalho
                    pc=a.get("preco_cents",0)
                    # Sem sufixo de setor aqui: cupom do cliente nao mostra roteamento interno
                    ll.append(f"  + {a.get('nome','')}{f' {_R(pc)}' if pc else ''}")
                obs=_obs_do_item(item)
                if obs: ll.append(f"  >> {obs}")
                ll.append(DP)
        ll.append(S)
        sub=content.get("subtotal_cents",0); desc=content.get("discount_cents",0)
        tot=content.get("total_cents",0)
        if sub: ll += _par("Subtotal:", _R(sub), w)
        if desc and int(desc)>0: ll += _par("Desconto:", f"-{_R(desc)}", w)
        ll += _lin_valor("TOTAL:", _R(tot), "total")
        pg=content.get("payment_method","")
        if pg and show_payment: ll.append(f"Pagamento: {PL.get(pg.lower(),pg)}")
        obs2=content.get("notes","")
        if obs2: ll.append(S); ll.append(f"Obs: {obs2}")
        ll.append(S)
    elif tipo=="delivery":
        # Ajuste SINIEF 32/24: cupom sem NFC-e autorizada avisa que nao e documento fiscal
        ll.append(AVISO_NAO_FISCAL)
        # store_name antes do cfg: numa rede, cfg e a MATRIZ, e o cupom da filial tem que
        # sair com o nome de quem vendeu. company_name (razao social) segue tendo prioridade.
        ne=content.get("company_name","") or _loja_do_job(content) or cfg.get("restaurant_name","")
        if ne: ll += _lin_centro(ne.upper(), "loja")
        e=content.get("company_address","")
        if e: ll.append(e.center(w))
        ll.append(S)
        # Selo da loja: so em filial impressa no concentrador (servidor decide).
        _selo=_linhas_selo_loja(content, w)
        if _selo: ll += _selo + [S]
        n=content.get("numero","") or content.get("order_number","")
        if n: ll.append(_lin_pedido(n))
        data_brt,_=_data_hora_brt(content)   # BRT: created_at_brt do servidor ou created_at (UTC) convertido
        if data_brt: ll.append(f"Data: {data_brt}")
        tp=content.get("order_type","delivery")
        ll.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        c2=content.get("customer_name","")
        if c2: ll.append(f"Cliente: {c2}")
        t2=content.get("customer_phone","")
        if t2 and show_phone: ll.append(f"Tel: {t2}")
        # AGENDADO antes dos itens (mesma regra do cupom): entrega agendada sai com o horario
        _ag=_linhas_agendado(content, w_fis)
        if _ag: ll.append(S); ll += _ag
        ll.append(S)
        DP="."*w
        for _cat, _itens_cat in _grupos_por_categoria(_itens_do_content(content), _agrupar_cat):
            ll += _cab_categoria(_cat, w)
            for item in _itens_cat:
                size=_size_do_item(item)
                ll += _lin_item(item)
                ll += _linha_pai(item)
                _cod_ln=_linha_codigo_item(item, _barcode_on)
                if _cod_ln: ll.append(_cod_ln)
                for a in _adicionais_do_item(item):
                    if size and a.get('nome','').strip()==size: continue  # ja saiu no cabecalho
                    pc=a.get("preco_cents",0)
                    # Sem sufixo de setor aqui: cupom do cliente nao mostra roteamento interno
                    ll.append(f"  + {a.get('nome','')}{f' {_R(pc)}' if pc else ''}")
                obs=_obs_do_item(item)
                if obs: ll.append(f"  >> {obs}")
                ll.append(DP)
        ll.append(S)
        sub=content.get("subtotal_cents",0); desc=content.get("discount_cents",0)
        ent=content.get("delivery_fee_cents",0); tot=content.get("total_cents",0)
        if sub: ll += _par("Subtotal:", _R(sub), w)
        if desc and int(desc)>0: ll += _par("Desconto:", f"-{_R(desc)}", w)
        if ent and int(ent)>0: ll += _par("Taxa entrega:", _R(ent), w)
        ll += _lin_valor("TOTAL:", _R(tot), "total")
        pg=content.get("payment_method","")
        if pg and show_payment: ll.append(f"Pagamento: {PL.get(pg.lower(),pg)}")
        obs2=content.get("notes","")
        if obs2: ll.append(S); ll.append(f"Obs: {obs2}")
        # Endereço de entrega — bloco robusto que aceita varios nomes de campo
        ll += _bloco_endereco(content, w, titulo="ENDERECO:")
        ll.append(S)
    elif tipo=="command":
        # v5.80: BYTES, nao texto. Como texto, o '\xfa' (pulso da gaveta) passava pelo codepage:
        # em cp850 virava 0xA3 e em utf8 virava 2 bytes (C3 BA), deixando um byte solto no papel.
        # Como bytes vai cru, exatamente 1B 70 00 19 FA, sem alimentar/cortar papel a toa.
        if content.get("command")=="open_drawer": return b"\x1b\x70\x00\x19\xfa"
    elif tipo=="test_page":
        ll+=["="*w,"   AGENTE LOCAL - TESTE OK!   ".center(w),"="*w,
             content.get("title","Teste"),content.get("message",""),
             f"Hora: {time.strftime('%d/%m/%Y %H:%M:%S')}","="*w]
    else:
        ll.append(f"JOB: {tipo}"); ll.append(json.dumps(content,ensure_ascii=False)[:200])
    return "\n".join(_quebrar_linhas_longas(ll, w))   # v5.80: linha maior que o papel quebra na palavra

def _res_imp(pt):
    imps=cfg.get("impressoras",[])
    areas=_areas_para_tipo(pt)
    for i in imps:
        if i.get("area","").strip().lower() in areas or i.get("printer_type","").strip()==pt:
            n=i.get("nome_impressora","")
            if n: return n
    return ""

_TIPOS_KITCHEN = {"kitchen","bar"}

def _areas_para_tipo(pt):
    """Retorna lista de areas aceitas para um printer_type. Tipos desconhecidos → receipt/caixa.
    fiscal/danfce entram EXPLICITAMENTE apontando para a caixa: cupom fiscal e cupom de cliente
    e vai para a impressora do CAIXA, nunca para um setor."""
    mapa = {"receipt":["caixa","receipt"],"kitchen":["cozinha","kitchen"],"bar":["bar"],"delivery":["delivery"],"pickup":["balcao","pickup"],
            "fiscal":["caixa","receipt"],"danfce":["caixa","receipt"]}
    if pt in mapa:
        return mapa[pt]
    return ["cozinha","kitchen","bar"] if pt in _TIPOS_KITCHEN else ["caixa","receipt"]

def _agente_cobre_tipo(pt):
    """Retorna True se este agente tem impressora configurada para o printer_type pt."""
    imps = cfg.get("impressoras", [])
    if not imps:
        return True  # sem config, aceita tudo (modo legado)
    areas_pt = _areas_para_tipo(pt)
    for i in imps:
        area = i.get("area","").strip().lower()
        ptype = i.get("printer_type","").strip()
        if area in areas_pt or ptype == pt:
            if i.get("nome_impressora",""):
                return True
            else:
                log.warning(f"[PRINT] Impressora '{i.get('nome','')}' tipo={pt} area={area} nao tem nome_impressora mapeado!")
    return False

def proc_job(job):
    jid=job.get("id"); pt=job.get("printer_type","receipt")
    pid=job.get("printer_id")
    content=job.get("content",{}); copies=int(job.get("copies",1)); jt=job.get("job_type","order")
    # v5.78: garante content.created_at (UTC, do proprio print_job) como fonte do 'Data:'/'Hora:'
    # em BRT quando o content nao traz created_at nem created_at_brt. Content existente por cima.
    if isinstance(content, dict) and not content.get("created_at") and job.get("created_at"):
        content = dict(content); content["created_at"] = job["created_at"]

    # NORMALIZACAO — o servidor pode mandar o job em 3 formatos:
    #   A) agent-unified-poll (legado): job.content.items[] com addons_json/price_cents (ingles cru)
    #   B) novo achatado:                job.content.itens[] com adicionais/preco_cents
    #   C) novo aninhado (agent-jobs):   job.pedido.itens[] direto no raiz, SEM job.content
    # Se detectar formato C (pedido no raiz do job), embrulha em content para o resto funcionar.
    if isinstance(job.get("pedido"), dict) and not (content.get("items") or content.get("itens")):
        # Formato C: pedido esta no raiz do job. Injetamos como content.pedido.
        content = dict(content) if isinstance(content, dict) else {}
        content["pedido"] = job["pedido"]

    # Se content vier aninhado (pedido dentro), le tambem do pedido interno para logs
    _pedido_obj = content.get("pedido") if isinstance(content.get("pedido"), dict) else {}
    # Extrai pedido/cliente do content para usar em logs e registros de falha
    _pedido_ref = (content.get("numero","") or _pedido_obj.get("numero","")
                   or content.get("order_number","") or _pedido_obj.get("order_number","")
                   or content.get("order_id","")[:8]) if content else ""
    _cliente_ref = (content.get("customer_name","") or _pedido_obj.get("customer_name","")) if content else ""

    # CUPOM FISCAL: sem o bloco fiscal nao existe DANFE, e ele NUNCA e montado a partir do
    # pedido. Se o servidor carimbou job_type fiscal e content.fiscal nao chegou (whitelist do
    # endpoint de poll, tipico), o cupom fiscal NAO sai — mas tambem nao pode sair um segundo
    # cupom comum, que o cliente levaria achando que e nota. Registra falha VISIVEL: esta
    # classe de bug ja quebrou a impressao "sem erro em lugar nenhum".
    if jt in TIPOS_CUPOM_FISCAL and not isinstance(content.get("fiscal"), dict):
        msg = ("Job fiscal chegou SEM content.fiscal — bloco perdido no caminho "
               "(whitelist do agent-unified-poll?). Cupom fiscal NAO impresso.")
        log.error(f"[FISCAL] Job {jid}: {msg}")
        ef_update_job(jid, "failed", msg)
        _registrar_falha(jid, "fiscal_sem_bloco", msg,
                         tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
        return

    # CONTORNO (v5.59): impressora de caixa "absorve" jobs de cozinha/bar como CUPOM.
    # Caso de uso: o lojista quer que a impressora do caixa imprima TUDO como cupom completo
    # (precos/total/endereco), inclusive itens que o servidor carimbou como kitchen/bar
    # (destino do produto no cardapio). Se o agente TEM impressora de caixa/receipt mas NAO
    # tem impressora para o tipo original (kitchen/bar), reescreve o job para receipt/order.
    # Assim o job nao e ignorado, e roteado para a caixa e sai como cupom (nao comanda).
    # NAO dispara se o agente tiver impressora propria de cozinha/bar — nesse caso o job vai
    # normalmente para ela como comanda.
    # Cupom fiscal NUNCA e absorvido: ele ja vem para a caixa e tem layout proprio.
    _forcar_cupom = False
    if (pt in ("kitchen","bar") and jt not in TIPOS_CUPOM_FISCAL
            and not _agente_cobre_tipo(pt) and _agente_cobre_tipo("receipt")):
        log.info(f"[PRINT] Job {jid} veio como '{pt}' e o agente so tem impressora de caixa "
                 f"— absorvendo como CUPOM (receipt)")
        pt = "receipt"
        jt = "order"
        _forcar_cupom = True
        if isinstance(content, dict):
            content = dict(content)
            content["type"] = "order"  # controla o layout em _fmt (cupom completo)

    # Se agente nao tem impressora mapeada para este tipo, ignora silenciosamente.
    # O servidor so deve mandar este job se este agente declarou a area — mas por seguranca
    # (ex: job chegou antes do poll com areas atualizado), nao marca failed para nao perder o job.
    # O outro agente que tem a impressora mapeada vai processar este job.
    if not _agente_cobre_tipo(pt):
        log.info(f"[PRINT] Job {jid} tipo={pt} — sem impressora mapeada neste agente, ignorando (outro agente processa)")
        return
    log.info(f"[PRINT] Job {jid} tipo={pt} job_type={jt}"
             + (" FISCAL" if isinstance(content.get("fiscal"), dict) else ""))
    oid=content.get("order_id") or content.get("id","") or _pedido_obj.get("order_id","") or _pedido_obj.get("id","")
    _content_rico = (
        ("items" in content and len(content.get("items") or []) > 0)
        or ("itens" in content and len(content.get("itens") or []) > 0)
        or (isinstance(content.get("pedido"), dict) and len(_itens_do_content(content)) > 0)
        or "paper_width" in content or "receipt_font_size" in content or "company_name" in content
        # Bloco fiscal ja e conteudo suficiente: o DANFE nao precisa de nada do pedido, e um
        # refetch aqui TROCA o content inteiro e levaria o bloco embora.
        or isinstance(content.get("fiscal"), dict)
    )
    if oid and not _content_rico:
        p=ef_get_order(oid)
        if p:
            # Preserva o bloco fiscal: ef_get_order devolve o PEDIDO, que nao tem dado do XML.
            # Sem isto o refetch apagaria o cupom fiscal e ele sairia como cupom comum.
            _fiscal_orig = content.get("fiscal")
            # Mesma armadilha com a flag de agrupar por categoria: o refetch TROCA o content
            # inteiro, e se o pedido buscado nao trouxer a flag o cupom da loja que a ligou
            # perderia os cabecalhos. Preserva o que o job trouxe.
            _agrup_orig = _agrupar_por_categoria(content)
            content=p
            if isinstance(_fiscal_orig, dict):
                content["fiscal"] = _fiscal_orig
            if _agrup_orig and not _agrupar_por_categoria(content):
                content["print_item_category"] = True
            if "order_items" in content and "items" not in content:
                raw_items = content.get("order_items") or []
                content["items"] = [
                    {
                        "name": it.get("name_snapshot") or it.get("product_name") or it.get("name",""),
                        "size_name": it.get("size_name","") or it.get("tamanho",""),
                        "quantity": it.get("quantity",1),
                        "unit_price_cents": it.get("price_cents_snapshot") or it.get("unit_price_cents",0),
                        "notes": it.get("notes",""),
                        "addons": it.get("addons_json") or it.get("addons",[]),
                        # Sem isto o remapeamento descartaria a categoria e o cabecalho nao sairia
                        "category_name": it.get("category_name") or it.get("categoria") or "",
                    }
                    for it in raw_items
                ]
            # Atualiza referencia de pedido/cliente apos buscar dados completos
            _pedido_ref = content.get("numero","") or content.get("order_number","") or oid[:8]
            _cliente_ref = content.get("customer_name","") or _cliente_ref
            log.info("[ORDER] OK")
        else:
            log.error(f"[ORDER] Falha ao buscar {oid} — imprimindo com content basico do job")
            _registrar_falha(jid, "falha_buscar_pedido",
                             f"Nao foi possivel buscar dados do pedido {oid} — imprimindo com dados basicos",
                             tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
            # NAO retorna — continua com o content original para garantir que o job sai na impressora

    # Resolve as impressoras-alvo:
    #  - printer_id PREENCHIDO -> alvo explicito, imprime SO naquela impressora (nao espalha).
    #  - printer_id NULL       -> espalha: imprime em TODAS as impressoras da funcao (ex: 2x caixa).
    #    Se nenhuma casar a funcao, cai no fallback _res_imp_por_rede (comportamento historico,
    #    ex: job orfao vai pra 1a impressora / padrao).
    if pid:
        _uma = _res_imp_por_rede(pt, printer_id=pid)
        imps_alvo = [_uma] if _uma else []
    else:
        imps_alvo = _res_todas_imp_por_tipo(pt)
        if not imps_alvo:
            _uma = _res_imp_por_rede(pt, printer_id=None)
            imps_alvo = [_uma] if _uma else []

    if not imps_alvo:
        msg = f"Sem impressora configurada para tipo '{pt}'"
        ef_update_job(jid,"failed", msg)
        _registrar_falha(jid, "impressora_nao_encontrada", msg,
                         tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
        return

    if len(imps_alvo) > 1:
        log.info(f"[PRINT] Job {jid} tipo={pt} sera impresso em {len(imps_alvo)} impressoras: "
                 f"{[ (i.get('nome_impressora') or i.get('endereco_ip','')) for i in imps_alvo ]}")

    # Primeira impressora usada para logs/registro de falha (compat com codigo existente)
    imp = imps_alvo[0]
    nome_imp_local = imp.get("nome_impressora") or imp.get("endereco_ip","")

    # Prepara o CONTEUDO a imprimir UMA vez (mesmo conteudo vai para todas as impressoras da funcao).
    # PRIORIDADE: dados formatados do servidor (ESC/POS RAW) se existirem.
    # Se o job foi absorvido como cupom (_forcar_cupom), IGNORA o escpos_data (seria a comanda ja
    # renderizada) e reformatamos via _fmt para sair como cupom.
    # Cupom fiscal tambem IGNORA o escpos_data: quem garante as divisoes I a IX (rotulos
    # literais, QR nativo, omissao de campo ausente) e o renderizador local. Se o servidor
    # mandar RAW de cupom comum junto do bloco fiscal, o RAW venceria e o DANFE se perderia
    # em silencio — o cliente levaria um cupom comum achando que era a nota.
    #
    # AGRUPAMENTO POR CATEGORIA — NUNCA NOS DOIS: o escpos_data ja vem agrupado pelo servidor.
    # Os dois caminhos abaixo sao MUTUAMENTE EXCLUSIVOS (RAW vence; _fmt so roda se dados is
    # None), e e _fmt quem agrupa. Ao mexer aqui, mantenha a exclusao: se um dia o RAW virar
    # base para o _fmt complementar, o cabecalho de categoria sairia DUAS vezes no mesmo papel.
    _tem_fiscal = isinstance(content.get("fiscal"), dict) and bool(content.get("fiscal"))
    escpos_b64 = None if (_forcar_cupom or _tem_fiscal) else job.get("escpos_data")
    dados = None  # conteudo final (bytes RAW ou str de texto)
    if escpos_b64:
        import base64
        try:
            dados = base64.b64decode(escpos_b64)
            log.info(f"[PRINT] Usando layout RAW para Job {jid}")
        except Exception as e:
            log.error(f"[PRINT] Erro ao decodificar ESC/POS: {e} — tentando imprimir como texto")
            dados = None
    _fmt_ok = False   # v5.80: dados vieram do _fmt (e podem ser refeitos para outra impressora)
    if dados is None:
        # Formatacao NUNCA pode impedir a impressao. Se _fmt levantar excecao, imprime um cupom minimo.
        try:
            # fonte junto do codepage (v5.81): o prefixo/os marcadores lidos na impressao
            # usam a fonte DESTA impressora, nao a global.
            with _usar_codepage(_cp_da_impressora(imp)), _usar_fonte(_fonte_da_impressora(imp)):
                dados=_fmt(content,jt,pt,imp)   # imp: largura/codepage/fonte por impressora
            _fmt_ok = True
        except Exception as e:
            # EXCECAO da regra acima: cupom fiscal nao tem "cupom minimo". Um papel com
            # "PEDIDO #99" no lugar do DANFE seria entregue ao cliente como se fosse a nota.
            # Melhor nao sair nada e a falha aparecer na aba Falhas do que sair documento errado.
            if _tem_fiscal:
                msg = f"Erro ao montar o DANFE: {str(e)[:200]}"
                log.error(f"[FISCAL] Job {jid}: {msg} — cupom fiscal NAO impresso", exc_info=True)
                ef_update_job(jid, "failed", msg)
                _registrar_falha(jid, "erro_formatacao_fiscal", msg,
                                 tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
                return
            log.error(f"[PRINT] Erro em _fmt para job {jid}: {e} — imprimindo cupom minimo", exc_info=True)
            _num = (content.get("numero","") if isinstance(content, dict) else "") or _pedido_ref or "?"
            _cli = _cliente_ref or ""
            dados = (
                f"{'='*W}\n"
                f"PEDIDO #{_num}\n"
                f"{('Cliente: '+_cli) if _cli else ''}\n"
                f"Tipo: {pt}\n"
                f"{'='*W}\n"
                f"(Erro na formatacao — verifique o log)\n"
                f"{'='*W}"
            )
            try:
                _registrar_falha(jid, "erro_formatacao", str(e)[:300],
                                 tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
            except Exception: pass

    # IMPRIME em CADA impressora-alvo (espalhamento por funcao). copies = vias na MESMA impressora.
    # Politica de erro: registra falha da impressora que falhou, mas CONTINUA nas outras — nao perde
    # as que funcionam. Marca o job 'failed' no servidor SO se NENHUMA impressora imprimiu.
    sucessos = 0
    falhas_imp = []
    for _imp in imps_alvo:
        _nome = _imp.get("nome_impressora") or _imp.get("endereco_ip","")
        ok_imp = True
        # v5.80: cada impressora com o SEU codepage e a SUA largura; v5.81 idem para a FONTE.
        # Se diferem da primeira (usada no _fmt acima), formata de novo so para esta — senao
        # a mini de 58 mm herdaria a tabela/largura/fonte da impressora do caixa.
        _cp_imp = _cp_da_impressora(_imp)
        _fs_imp = _fonte_da_impressora(_imp)
        _dados_imp = dados
        if _fmt_ok and _imp is not imp and (
                _cp_imp != _cp_da_impressora(imp) or
                _fs_imp != _fonte_da_impressora(imp) or
                _largura_efetiva(_imp, content, _paper_width_servidor) != _largura_efetiva(imp, content, _paper_width_servidor)):
            try:
                with _usar_codepage(_cp_imp), _usar_fonte(_fs_imp):
                    _dados_imp = _fmt(content,jt,pt,_imp)
            except Exception as e:
                log.warning(f"[PRINT] Job {jid}: reformatar para '{_nome}' falhou ({e}); usando o da 1a impressora")
        # v5.81: vias LOCAIS do cupom do cliente ('vias' da impressora > 'vias_cupom' geral).
        # So para cupom: comanda de setor e cupom fiscal saem SEMPRE em 1 via (documento e um).
        # max(copies, local): o servidor pode pedir mais vias que a config local, nunca menos.
        _vias = copies
        if pt not in ("kitchen","bar") and jt not in TIPOS_CUPOM_FISCAL and not _tem_fiscal:
            try:
                _vias = max(copies, max(1, min(3, int(_imp.get("vias") or cfg.get("vias_cupom") or 1))))
            except Exception: pass
        for _ in range(_vias):
            with _usar_codepage(_cp_imp), _usar_fonte(_fs_imp):
                r = _imprimir_com_roteamento(_imp, _dados_imp)
            if not r.get("ok"):
                ok_imp = False
                falhas_imp.append((_nome, r.get("erro","")))
                _registrar_falha(jid, "erro_impressora", r.get("erro",""),
                                 tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref,
                                 impressora=_nome)
                break  # nao insiste nas outras vias desta impressora
        if ok_imp:
            sucessos += 1

    if sucessos == 0:
        # Nenhuma impressora imprimiu — job falhou de verdade.
        _err = falhas_imp[0][1] if falhas_imp else "falha desconhecida na impressao"
        ef_update_job(jid,"failed", _err)
        return

    # Pelo menos uma via saiu. Confirma o job UMA VEZ (o job e 1, foram N vias fisicas).
    if falhas_imp:
        log.warning(f"[PRINT] Job {jid} impresso em {sucessos}/{len(imps_alvo)} impressoras — "
                    f"falharam: {[f[0] for f in falhas_imp]}")
    ef_update_job(jid,"printed",pa=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()))
    nome_imp = imp.get("nome_impressora") or imp.get("endereco_ip","")
    pedido_num = (content.get("numero","") or content.get("order_number","") if content else "") or (content.get("order_id","")[:8] if content else "")
    cliente = (content.get("customer_name","") if content else "") or ""
    log.info(f"[PRINT] Job {jid} OK | tipo={pt} | pedido={pedido_num} | cliente={cliente} | impressora='{nome_imp}'")
    _stats["total_impressos"] += 1
    _stats["ultimo_job"] = time.strftime("%H:%M:%S")
    _stats["ultima_impressora"] = nome_imp
    # Contador diario - reseta meia-noite
    hoje = time.strftime("%d/%m/%Y")
    if _stats["hoje_data"] != hoje:
        _stats["hoje"] = 0
        _stats["hoje_data"] = hoje
    _stats["hoje"] += 1
    # Historico dos ultimos 50 jobs
    # Usa oid (salvo antes de sobrescrever content) para garantir que order_id é o UUID correto
    _oid_real = oid or (content.get("order_id","") if content else "") or (content.get("id","") if content else "")
    entrada = {
        "hora": time.strftime("%H:%M:%S"),
        "data": hoje,
        "impressora": nome_imp,
        "tipo": pt,
        "job_id": jid,
        "order_id": _oid_real,
        "content_ref": (content.get("numero","") or content.get("order_number","") or _oid_real[:8]) if content else "",
        "cliente": cliente,
    }
    _stats["historico"].insert(0, entrada)
    if len(_stats["historico"]) > 50:
        _stats["historico"] = _stats["historico"][:50]
    log.info(f"[HIST] Salvo job_id='{jid}' order_id='{_oid_real}' tipo='{pt}' impressora='{nome_imp}'")

_jobs_em_proc = set()  # Evita processar o mesmo job duas vezes

def poll():
    global status_poll
    jobs=ef_poll_jobs()
    novos = [j for j in jobs if j.get("id") not in _jobs_em_proc]
    if novos:
        status_poll=f"Ativo - {len(novos)} job(s)"
        log.info(f"[POLL] {len(novos)} job(s)")
        for job in novos:
            jid = job["id"]
            _jobs_em_proc.add(jid)
            def _run(j=job):
                _jid = j.get("id","?")
                try:
                    proc_job(j)
                except Exception as e:
                    # Excecao inesperada em proc_job — NUNCA deve deixar o job em 'sent' silenciosamente.
                    # Marca failed no servidor para que possa ser reprocessado / visto pelo operador.
                    log.error(f"[PRINT] Excecao ao processar job {_jid}: {e}", exc_info=True)
                    try:
                        ef_update_job(_jid, "failed", f"excecao_proc_job: {str(e)[:200]}")
                    except Exception as e2:
                        log.error(f"[PRINT] Nao conseguiu marcar job {_jid} como failed: {e2}")
                    try:
                        _registrar_falha(_jid, "excecao_proc_job", str(e)[:300], tipo=j.get("printer_type","?"))
                    except Exception: pass
                finally:
                    _jobs_em_proc.discard(_jid)
            threading.Thread(target=_run, daemon=True).start()
    else: status_poll="Ativo - aguardando"
    _atualizar_icone()

CURRENT_VERSION = "5.82"
VERSION_URL = "https://raw.githubusercontent.com/delmatch-user/agente-local-releases/main/version.json"

_update_em_andamento = False  # evita multiplos downloads simultaneos

def _popen_bat_orfao(bat):
    """Roda um .bat FORA da arvore de processos do agente, sem janela.

    POR QUE (v5.78): os bats de update/reparo/reinicio comecam com
    'taskkill /F /FI "IMAGENAME eq AgenteLocal*" /T'. O /T mata a arvore inteira de cada
    AgenteLocal — e, lancado como filho direto (Popen(["cmd","/c",bat])), o cmd do proprio bat
    ESTAVA nessa arvore. Resultado: o bat morria no passo 1, sem copiar o exe novo e sem reabrir
    o agente; ficavam update_apply.bat + update_lock.tmp na pasta e a loja SEM agente ate o
    proximo login (reproduzido em 08/09/2026 com uma arvore falsa: filho direto = morre;
    orfao = sobrevive). Isso tambem explica parte do "abre e fecha sozinho apos atualizar".

    COMO: um cmd intermediario faz 'start "" /b cmd /c bat' e termina na hora. O cmd que roda o
    bat fica orfao (pai ja morto) => fora de qualquer arvore que o taskkill enxergue.
    So CREATE_NO_WINDOW (sem DETACHED_PROCESS): assim existe um console OCULTO herdado pelo bat,
    que o 'timeout /t' precisa para esperar de verdade (sem console ele falha na hora e os
    loops de retry/heartbeat do bat de update rodariam sem pausa)."""
    return subprocess.Popen(
        ["cmd", "/c", "start", "", "/b", "cmd", "/c", str(bat)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _bat_update(exe_novo: Path, exe_destino: Path, del_extra: str = "") -> str:
    """Gera o bat de update BLINDADO. Garantias:
      - NUNCA deixa a loja sem AgenteLocal.exe: faz BACKUP do atual e usa COPY (nao move
        destrutivo). Se a copia falhar, restaura o backup e abre a versao antiga.
      - Mata processos por CURINGA (AgenteLocal*), pegando nomes versionados travados.
      - Sempre relanca com o nome FIXO AgenteLocal.exe (nunca versionado).
      - ROLLBACK automatico: apos abrir o novo, espera um HEARTBEAT (.boot_ok). Se o novo
        exe nao subir em ~30s, restaura o backup e abre a versao anterior — a loja volta
        a funcionar na versao que funcionava, em vez de ficar travada.
    """
    lock      = exe_novo.parent / "update_lock.tmp"
    backup    = exe_novo.parent / "AgenteLocal.bak.exe"
    heartbeat = exe_novo.parent / ".boot_ok"
    d = str(exe_destino)
    return (
        "@echo off\r\n"
        f'if exist "{lock}" exit /b 0\r\n'
        f'echo 1>"{lock}"\r\n'
        # 1) Mata TODAS as instancias AgenteLocal* (inclui nomes versionados travados)
        '  taskkill /F /FI "IMAGENAME eq AgenteLocal*" /T >nul 2>&1\r\n'
        # 'ping -n N' = sleep de N-1 s que funciona SEM console. 'timeout /t' falha na hora quando
        # o bat roda oculto (sem stdin de console) e os loops de retry/heartbeat abaixo rodariam
        # sem pausa => rollback antes do exe novo subir. (Medido em 08/09/2026.)
        "ping -n 6 127.0.0.1 >nul\r\n"
        # 2) Backup do exe atual (se existir) — rede de seguranca para rollback
        f'if exist "{d}" copy /y "{d}" "{backup}" >nul 2>&1\r\n'
        # 3) Limpa heartbeat antigo e aplica o novo por COPY com retry (max 10 = ~30s)
        f'del /f /q "{heartbeat}" >nul 2>&1\r\n'
        "set /a TRIES=0\r\n"
        ":retry\r\n"
        f'copy /y "{exe_novo}" "{d}" >nul 2>&1\r\n'
        "if errorlevel 1 (\r\n"
        "  set /a TRIES+=1\r\n"
        "  if %TRIES% GEQ 10 goto :rollback\r\n"
        "  ping -n 4 127.0.0.1 >nul\r\n"
        "  goto retry\r\n"
        ")\r\n"
        + del_extra +
        # 4) Abre a versao nova (nome fixo) e espera o heartbeat de boot
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{d}\'"\r\n'
        "set /a WAIT=0\r\n"
        ":waitboot\r\n"
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'if exist "{heartbeat}" goto :ok\r\n'
        "set /a WAIT+=1\r\n"
        "if %WAIT% GEQ 10 goto :rollback\r\n"
        "goto waitboot\r\n"
        # 5) OK: novo exe subiu. Remove o exe versionado baixado e o backup.
        ":ok\r\n"
        f'if /I not "{str(exe_novo)}"=="{d}" del /f /q "{exe_novo}" >nul 2>&1\r\n'
        f'del /f /q "{backup}" >nul 2>&1\r\n'
        "goto :end\r\n"
        # 6) ROLLBACK: novo nao subiu / copia falhou. Restaura backup e abre a versao antiga.
        ":rollback\r\n"
        f'taskkill /F /FI "IMAGENAME eq AgenteLocal*" /T >nul 2>&1\r\n'
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'if exist "{backup}" copy /y "{backup}" "{d}" >nul 2>&1\r\n'
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{d}\'"\r\n'
        ":end\r\n"
        f'del /f /q "{lock}" >nul 2>&1\r\n'
        'del "%~f0"\r\n'
    )

_TAMANHO_MIN_EXE = 3 * 1024 * 1024  # 3 MB: piso de sanidade (o exe real tem ~20 MB;
                                    # um HTML de erro/404 ou download truncado e pequeno)

def _baixar_e_aplicar_update(nova, url_nova):
    """Roda em thread separada: baixa o exe novo, VERIFICA INTEGRIDADE e aplica via bat
    blindado (backup + copy + heartbeat + rollback). Se o download vier corrompido/curto,
    ABORTA sem aplicar — nunca substitui o exe bom por um quebrado (que causaria 'nao abre').
    Em qualquer falha, libera o lock para tentar de novo no proximo ciclo (nao trava)."""
    global _update_em_andamento
    try:
        exe_tmp = BASE_DIR / "AgenteLocal_update.tmp"
        exe_novo = BASE_DIR / f"AgenteLocal_{nova}.exe"
        log.info(f"[UPDATE] Baixando v{nova}...")
        req = urllib.request.Request(url_nova)
        esperado = None
        baixado = 0
        with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as r, open(exe_tmp, "wb") as f:
            try:
                esperado = int(r.headers.get("Content-Length") or 0) or None
            except Exception:
                esperado = None
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                baixado += len(chunk)

        # --- INTEGRIDADE (blindagem 6) ---
        # (a) tamanho minimo plausivel
        if baixado < _TAMANHO_MIN_EXE:
            raise ValueError(f"download muito pequeno ({baixado} bytes) — provavel erro/404, abortando")
        # (b) bate com Content-Length quando o servidor informa
        if esperado and baixado != esperado:
            raise ValueError(f"download incompleto ({baixado}/{esperado} bytes), abortando")
        # (c) assinatura de executavel Windows (PE começa com 'MZ')
        try:
            with open(exe_tmp, "rb") as _fp:
                if _fp.read(2) != b"MZ":
                    raise ValueError("arquivo baixado nao e um .exe valido (sem cabecalho MZ), abortando")
        except ValueError:
            raise
        except Exception:
            pass  # se nao conseguir ler o cabecalho, os checks (a)/(b) ja protegem

        # Renomeia para .exe so APOS validar
        if exe_novo.exists():
            try: exe_novo.unlink()
            except Exception: pass
        exe_tmp.rename(exe_novo)
        log.info(f"[UPDATE] Download OK e validado ({baixado} bytes): {exe_novo}")

        exe_destino = BASE_DIR / "AgenteLocal.exe"
        # del_extra removido: o bat blindado ja limpa versionados no :ok de forma segura.
        # Apagar o exe atual em execucao aqui era arriscado (podia deixar a loja sem exe).
        bat = BASE_DIR / "update_apply.bat"
        bat.write_text(_bat_update(exe_novo, exe_destino, ""), encoding="utf-8")
        # Orfao (fora da arvore do agente): ver _popen_bat_orfao — como filho direto o bat
        # morria no proprio taskkill /T e o update NUNCA era aplicado.
        _popen_bat_orfao(bat)
        log.info("[UPDATE] Aplicando atualizacao (bat blindado com rollback)...")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        log.warning(f"[UPDATE] Falha/abortado no update: {e}")
        # Limpa o tmp corrompido e libera o lock para retry no proximo ciclo (blindagem 5)
        try:
            _t = BASE_DIR / "AgenteLocal_update.tmp"
            if _t.exists(): _t.unlink()
        except Exception:
            pass
        try:
            _lock = BASE_DIR / ".update_attempt.lock"
            if _lock.exists(): _lock.unlink()
        except Exception:
            pass
        _update_em_andamento = False

def _alvo_update(info):
    """(versao, url) que este agente deve considerar como 'publicada'.
    v5.78: le latest_version/latest_url ANTES de version/url. MOTIVO: ate a v5.77 o updater de
    dentro do agente e quebrado (o bat morre no proprio taskkill /T) — QUALQUER mudanca em
    'version' faz o agente <=5.77 se matar uma vez (loja sem agente) e depois ficar travado na
    versao antiga (update_lock.tmp orfao). Por isso 'version'/'url' ficam CONGELADOS em 5.77
    no version.json: os legados leem so eles, veem 'igual a minha' e nao fazem nada. Versoes
    novas sao publicadas em latest_version/latest_url, que so >=5.78 entende. Clientes em
    <=5.77 migram uma unica vez pelo ATUALIZAR_E_ABRIR_AGENTE.bat (que tambem le latest_*)."""
    if not isinstance(info, dict): return "", ""
    nova = str(info.get("latest_version") or info.get("version") or "").strip()
    url  = str(info.get("latest_url") or info.get("url") or "").strip()
    return nova, url

_UPDATE_MAX_TENTATIVAS_24H = 2
_update_bloqueado_avisado = False

def _update_pode_tentar(nova):
    """Teto do update automatico: no maximo 2 tentativas por versao-alvo a cada 24h. Contador
    em DATA_DIR/update_tentativas.json (sobrevive a reinicios; zerado no boot da versao-alvo,
    ver _auto_reparo_boot). Sem isto, um update que falha e faz rollback (antivirus segurando
    o exe, disco cheio, exe corrompido no download) reiniciaria a loja a cada 5 min PARA SEMPRE
    ("fica fechando e abrindo toda hora"). Retorna True e CONSOME uma tentativa; False = pare."""
    p = DATA_DIR / "update_tentativas.json"
    agora = time.time()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict): d = {}
    except Exception:
        d = {}
    if d.get("version") != nova or (agora - float(d.get("first", 0) or 0)) > 86400:
        d = {"version": nova, "first": agora, "count": 0}
    if int(d.get("count", 0) or 0) >= _UPDATE_MAX_TENTATIVAS_24H:
        return False
    d["count"] = int(d.get("count", 0) or 0) + 1
    try: p.write_text(json.dumps(d), encoding="utf-8")
    except Exception: pass
    return True

async def checar_atualizacao():
    """Verifica nova versao silenciosamente; download em thread para nao travar o poll."""
    global _update_em_andamento, _update_bloqueado_avisado
    if not getattr(sys, "frozen", False):
        return
    if _update_em_andamento:
        return
    # Lock file persistente — se update foi tentado nos ultimos 5min, nao tenta de novo
    # Isso impede loop infinito quando o update falha em substituir o exe
    lock_file = BASE_DIR / ".update_attempt.lock"
    if lock_file.exists():
        try:
            idade = time.time() - lock_file.stat().st_mtime
            if idade < 300:  # 5 minutos
                return
        except Exception:
            pass
    try:
        req = urllib.request.Request(VERSION_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as r:
            info = json.loads(r.read())
        nova, url_nova = _alvo_update(info)   # v5.78: latest_* antes de version/url
        if not nova or not url_nova or nova == CURRENT_VERSION:
            return
        # v5.78: NUNCA faz DOWNGRADE sozinho. Antes bastava 'nova != CURRENT_VERSION': um exe
        # mais novo instalado a mao numa loja (ou em teste, antes de publicar o version.json)
        # se "atualizava" para a versao publicada MAIS ANTIGA — e o bat de update derruba
        # todo AgenteLocal* no processo. Rollback deliberado continua possivel: basta o
        # version.json publicado trazer "allow_downgrade": true.
        if _ver_tuple(nova) < _ver_tuple(CURRENT_VERSION) and not info.get("allow_downgrade"):
            log.debug(f"[UPDATE] Publicada v{nova} e mais antiga que a minha v{CURRENT_VERSION}; "
                      f"ignorando (sem allow_downgrade)")
            return
        # v5.78: teto de tentativas (2 por versao-alvo / 24h). Bloqueado => segura o lock de
        # 5 min (senao voltaria aqui a cada poll) e avisa UMA vez por processo.
        if not _update_pode_tentar(nova):
            try: lock_file.write_text(str(time.time()))
            except Exception: pass
            if not _update_bloqueado_avisado:
                _update_bloqueado_avisado = True
                log.warning(f"[UPDATE] v{nova}: limite de {_UPDATE_MAX_TENTATIVAS_24H} tentativas em 24h "
                            f"atingido; nao tento de novo ate amanha (evita ciclo fecha/abre). "
                            f"Para forcar, use o ATUALIZAR_E_ABRIR_AGENTE.bat.")
            return
        # Marca tentativa de update no lock file
        try:
            lock_file.write_text(str(time.time()))
        except Exception:
            pass
        _update_em_andamento = True
        log.info(f"[UPDATE] Nova versao {nova} disponivel. Baixando...")
        threading.Thread(target=_baixar_e_aplicar_update, args=(nova, url_nova), daemon=True).start()
    except Exception as e:
        log.debug(f"[UPDATE] {e}")

def _reset_jobs_failed_servidor():
    """Reseta jobs failed recentes (ultimas 2h) de volta para pending no servidor.
    Cobre qualquer tipo de erro, nao so 'sem mapeamento Windows'.
    Roda a cada 10 minutos para recuperar automaticamente jobs perdidos."""
    imps = cfg.get("impressoras", [])
    areas_set = set()
    for i in imps:
        if not i.get("nome_impressora","").strip():
            continue
        area = i.get("area","").strip().lower()
        ptype = i.get("printer_type","").strip().lower()
        if area:
            areas_set.add(area)
        elif ptype:
            mapa_tipo_area = {"receipt":"caixa","kitchen":"cozinha","bar":"bar","delivery":"delivery","pickup":"balcao"}
            areas_set.add(mapa_tipo_area.get(ptype, ptype))
    if not areas_set:
        return
    token = cfg.get("token","")
    if not token:
        return
    try:
        payload = {
            "action": "reset_failed",
            "areas": list(areas_set),
            "device_fingerprint": DEVICE_FINGERPRINT,
            "minutes": 120,
        }
        resp, s = _post(f"{SUPABASE_URL}/functions/v1/agent-unified-poll", payload, token, timeout=15)
        if s == 200 and resp:
            n = resp.get("reset_count", 0)
            if n:
                log.info(f"[RESET] {n} job(s) failed resetados para pending automaticamente")
                _stats["alertas"] = [a for a in _stats["alertas"] if a.get("tipo") != "jobs_stuck"]
    except Exception as e:
        log.debug(f"[RESET] {e}")

async def loop_poll():
    iv = max(1, int(cfg.get("poll_interval", 3)))
    log.info(f"[POLL] Iniciando a cada {iv}s")
    ciclos = 0
    ultimo_update_check = 0
    ultimo_reset_failed = 0
    await checar_atualizacao()
    while True:
        try: poll()
        except Exception as e: log.error(f"[POLL] {e}")
        ciclos += 1
        # Re-sincroniza impressoras do servidor a cada 5 minutos
        if ciclos % max(1, int(300 / iv)) == 0:
            try: sincronizar_impressoras()
            except Exception as e: log.error(f"[SYNC] {e}")
        agora = time.time()
        # Verifica atualizacao a cada 1 minuto
        if agora - ultimo_update_check >= 60:
            ultimo_update_check = agora
            try: await checar_atualizacao()
            except Exception as e: log.debug(f"[UPDATE] {e}")
        # Reseta jobs failed a cada 10 minutos
        if agora - ultimo_reset_failed >= 600:
            ultimo_reset_failed = agora
            threading.Thread(target=_reset_jobs_failed_servidor, daemon=True).start()
        await asyncio.sleep(iv)


def abrir_boasvindas():
    """Tela de boas-vindas para primeira configuracao"""
    global cfg
    w = tk.Toplevel(_root)
    w.title("Concentrador de Impressoes e Dispositivos")
    w.geometry("460x620")
    w.configure(bg="#1a1a2e")
    w.resizable(False, False)
    w.lift(); w.focus_force()

    # Header
    hf = tk.Frame(w, bg="#1a1a2e"); hf.pack(pady=(32,0))
    icon_canvas = tk.Canvas(hf, width=56, height=56, bg="#5b8dee", highlightthickness=0)
    icon_canvas.configure(bg="#5b8dee")
    icon_frame = tk.Frame(hf, bg="#5b8dee", width=56, height=56)
    icon_frame.pack()
    icon_frame.pack_propagate(False)
    tk.Label(icon_frame, text="[I]", bg="#5b8dee", fg="#1a1a2e",
             font=("Segoe UI", 20, "bold")).pack(expand=True)

    tk.Label(w, text="Concentrador de Impressoes", bg="#1a1a2e", fg="#cdd6f4",
             font=("Segoe UI", 15, "bold")).pack(pady=(10,0))
    tk.Label(w, text="e Dispositivos", bg="#1a1a2e", fg="#cdd6f4",
             font=("Segoe UI", 15, "bold")).pack()
    tk.Label(w, text="DELMATCH", bg="#1a1a2e", fg="#6c7086",
             font=("Segoe UI", 8)).pack(pady=(2,16))

    # Card descricao
    cf = tk.Frame(w, bg="#25253a", padx=20, pady=14); cf.pack(fill="x", padx=24, pady=(0,12))
    tk.Label(cf, text="Bem-vindo ao Concentrador", bg="#25253a", fg="#cdd6f4",
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    tk.Label(cf, text="Conecte suas impressoras e balancas ao sistema MIA em 3 passos simples.",
             bg="#25253a", fg="#6c7086", font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=(4,0))

    # Passos
    sf = tk.Frame(w, bg="#1a1a2e"); sf.pack(fill="x", padx=24, pady=(0,16))
    passos = [
        ("1", "Cole o Token de API", "gerado no painel MIA do restaurante"),
        ("2", "Conecte ao sistema",  "busca impressoras automaticamente"),
        ("3", "Mapeie as impressoras","clique duplo para configurar cada uma"),
    ]
    for num, titulo, desc in passos:
        row = tk.Frame(sf, bg="#1a1a2e"); row.pack(fill="x", pady=4)
        nb = tk.Frame(row, bg="#5b8dee", width=24, height=24)
        nb.pack(side="left", padx=(0,10)); nb.pack_propagate(False)
        tk.Label(nb, text=num, bg="#5b8dee", fg="#1a1a2e",
                 font=("Segoe UI", 10, "bold")).pack(expand=True)
        tf = tk.Frame(row, bg="#1a1a2e"); tf.pack(side="left", fill="x", expand=True)
        tk.Label(tf, text=titulo, bg="#1a1a2e", fg="#cdd6f4",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(tf, text=desc, bg="#1a1a2e", fg="#6c7086",
                 font=("Segoe UI", 9)).pack(anchor="w")

    # Input token
    tk.Label(w, text="Token de API", bg="#1a1a2e", fg="#a6adc8",
             font=("Segoe UI", 10)).pack(anchor="w", padx=24)
    token_var = tk.StringVar()
    te = tk.Entry(w, textvariable=token_var, show="*", bg="#0d0d1a", fg="#cdd6f4",
                  insertbackground="#cdd6f4", font=("Segoe UI", 11),
                  relief="flat", highlightthickness=1, highlightbackground="#313244",
                  highlightcolor="#5b8dee")
    te.pack(fill="x", padx=24, pady=(6,16), ipady=8)

    status_var = tk.StringVar(value="")
    status_lbl = tk.Label(w, textvariable=status_var, bg="#1a1a2e", fg="#f38ba8",
                          font=("Segoe UI", 9), wraplength=380)
    status_lbl.pack(pady=(0,4))

    def conectar():
        token = token_var.get().strip()
        if not token:
            status_var.set("Cole o Token de API para continuar.")
            return
        status_var.set("Conectando ao sistema...")
        status_lbl.config(fg="#f9e2af"); w.update()
        r = autoconfigurar(token)
        if r.get("ok"):
            d = r["data"]
            cfg.update({"token": token,
                        "restaurant_id": d.get("restaurant_id",""),
                        "restaurant_name": d.get("restaurant_name",""),
                        "ultima_sincronizacao": time.strftime("%d/%m/%Y %H:%M:%S")})
            printers = d.get("config",{}).get("printers", d.get("printers", []))
            iw = listar_impressoras_windows()
            imps_existentes2 = {i.get("nome","").strip().lower():i for i in cfg.get("impressoras",[])}
            imps = []
            for p in printers:
                ns = p.get("name",""); ts = p.get("printer_type","receipt")
                area = {"receipt":"caixa","kitchen":"cozinha","bar":"bar"}.get(ts,"caixa")
                existente2 = imps_existentes2.get(ns.strip().lower())
                match = existente2.get("nome_impressora","") if existente2 else ""
                if not match:
                    match = next((x for x in iw if ns.upper()[:5] in x.upper() or x.upper()[:5] in ns.upper()),"")
                imps.append({"nome":ns,"area":area,"printer_type":ts,"nome_impressora":match,"tipo":"comum_win32","modo":"texto"})
            cfg["impressoras"] = imps
            salvar_config(cfg)
            status_var.set(f"Conectado: {d.get('restaurant_name','')}!")
            status_lbl.config(fg="#a6e3a1")
            w.after(1500, lambda: (w.destroy(), abrir_config()))
        else:
            status_var.set(f"Erro: {r.get('erro','Token invalido')}")
            status_lbl.config(fg="#f38ba8")

    btn = tk.Button(w, text="Conectar ao Sistema", command=conectar,
                    bg="#5b8dee", fg="#1a1a2e", font=("Segoe UI", 11, "bold"),
                    relief="flat", cursor="hand2", padx=20, pady=10)
    btn.pack(fill="x", padx=24, pady=(0,8))
    te.bind("<Return>", lambda e: conectar())

    tk.Label(w, text=f"Concentrador de Impressoes e Dispositivos  .  Delmatch  .  v{CURRENT_VERSION}",
             bg="#1a1a2e", fg="#45475a", font=("Segoe UI", 8)).pack(pady=(4,16))



def _dashboard_aberto():
    try:
        return _janela_dashboard is not None and _janela_dashboard.winfo_exists()
    except Exception:
        return False

def _trazer_para_frente(w):
    """Traz a janela pra frente DE VERDADE no Windows. deiconify+lift+focus_force nao bastam
    quando outro app (PDV, navegador) tem o foco: o Windows ignora o pedido e a janela fica
    atras — pro lojista, 'nao abriu'. Piscar -topmost e desligar em seguida resolve."""
    try:
        w.deiconify(); w.lift(); w.focus_force()
        w.attributes("-topmost", True)
        w.after(300, lambda: w.attributes("-topmost", False))
    except Exception:
        pass

def abrir_dashboard():
    global _janela_dashboard
    # JANELA UNICA: se ja esta aberta, so traz pra frente em vez de empilhar outra.
    if _dashboard_aberto():
        _trazer_para_frente(_janela_dashboard)
        return
    w = tk.Toplevel(_root)
    w.title("Status - Concentrador")
    w.geometry("820x620")
    w.configure(bg="#1a1a2e")
    w.resizable(True, True)
    w.lift(); w.focus_force()
    _janela_dashboard = w
    _trazer_para_frente(w)
    w.bind("<Destroy>", lambda e: (globals().__setitem__('_janela_dashboard', None) if e.widget is w else None))

    tk.Label(w, text="Concentrador de Impressoes e Dispositivos",
             bg="#1a1a2e", fg="#cdd6f4", font=("Segoe UI",13,"bold")).pack(pady=(14,2))

    # Barra de agentes online
    agentes_bar = tk.Frame(w, bg="#25253a"); agentes_bar.pack(fill="x", padx=20, pady=(0,4))
    agentes_var = tk.StringVar(value="Carregando agentes...")
    agentes_lbl = tk.Label(agentes_bar, textvariable=agentes_var,
                           bg="#25253a", fg="#a6e3a1", font=("Segoe UI",8), anchor="w", padx=8, pady=4)
    agentes_lbl.pack(side="left", fill="x", expand=True)

    # Referência para _popular_tree_ag definida depois — preenchida quando a aba for criada
    _popular_tree_ag_ref = [None]

    def _atualizar_barra_agentes():
        if not w.winfo_exists(): return
        def _fetch():
            try:
                imps = cfg.get("impressoras", [])
                areas = list(set([
                    i.get("area","").strip().lower() for i in imps
                    if i.get("area","").strip() and i.get("nome_impressora","").strip()
                ]))
                payload = {"device_name": DEVICE_NAME, "device_fingerprint": DEVICE_FINGERPRINT}
                if areas: payload["areas"] = areas
                resp, s = _post(f"{SUPABASE_URL}/functions/v1/agent-unified-poll", payload, cfg.get("token",""))
                if s == 200 and resp:
                    lista = resp.get("agents_online", [])
                    import datetime
                    agora = time.time()
                    partes = []
                    for ag in lista:
                        nome = ag.get("device_name") or "Agente"
                        areas_ag = ", ".join(ag.get("covered_areas") or []) or "?"
                        hb = ag.get("last_heartbeat_at","")
                        try:
                            ts = datetime.datetime.fromisoformat(hb.replace("Z","+00:00"))
                            diff = agora - ts.timestamp()
                            online = diff < 35
                        except Exception:
                            online = False
                        marcador = "●" if online else "○"
                        este = " (este)" if ag.get("device_name","") == DEVICE_NAME else ""
                        partes.append(f"{marcador} {nome}{este} [{areas_ag}]")
                    texto = "  ".join(partes) if partes else "Nenhum agente online"
                    if not w.winfo_exists(): return
                    _root.after(0, lambda: agentes_var.set(texto) if w.winfo_exists() else None)
                    # Atualiza _agents_online global e tabela da aba Agentes
                    global _agents_online
                    _agents_online = lista
                    if _popular_tree_ag_ref[0]:
                        _root.after(0, lambda: _popular_tree_ag_ref[0](lista) if w.winfo_exists() else None)
            except Exception:
                pass
        threading.Thread(target=_fetch, daemon=True).start()

    def _loop_barra_agentes():
        if not w.winfo_exists(): return
        _atualizar_barra_agentes()
        w.after(15000, _loop_barra_agentes)

    # Popula imediatamente com cache e agenda refresh
    if _agents_online:
        import datetime as _dt
        agora = time.time()
        partes = []
        for ag in _agents_online:
            nome = ag.get("device_name") or "Agente"
            areas_ag = ", ".join(ag.get("covered_areas") or []) or "?"
            hb = ag.get("last_heartbeat_at","")
            try:
                ts = _dt.datetime.fromisoformat(hb.replace("Z","+00:00"))
                online = (agora - ts.timestamp()) < 35
            except Exception:
                online = False
            marcador = "●" if online else "○"
            este = " (este)" if ag.get("device_name","") == DEVICE_NAME else ""
            partes.append(f"{marcador} {nome}{este} [{areas_ag}]")
        agentes_var.set("  ".join(partes) if partes else "Aguardando dados...")
    w.after(500, _loop_barra_agentes)

    # Barra de botoes no topo
    bf = tk.Frame(w, bg="#1a1a2e"); bf.pack(fill="x", padx=20, pady=(0,6))
    tk.Button(bf, text="Configuracoes", command=abrir_config,
              bg="#313244", fg="#cdd6f4", font=("Segoe UI",9,"bold"),
              relief="flat", padx=12, pady=5, cursor="hand2").pack(side="left", padx=4)
    tk.Button(bf, text="Ver Log", command=abrir_log,
              bg="#313244", fg="#cdd6f4", font=("Segoe UI",9,"bold"),
              relief="flat", padx=12, pady=5, cursor="hand2").pack(side="left", padx=4)
    def _reparar_confirm():
        if messagebox.askyesno("Reparar",
                "Reparar o agente agora?\n\nIsso fecha e reabre o agente, corrigindo problemas "
                "de inicializacao (ex: 'nao abre' apos atualizar). Suas configuracoes sao mantidas.",
                parent=w):
            reparar_agente()
    tk.Button(bf, text="Reparar", command=_reparar_confirm,
              bg="#f9e2af", fg="#1e1e2e", font=("Segoe UI",9,"bold"),
              relief="flat", padx=12, pady=5, cursor="hand2").pack(side="left", padx=4)
    btn_upd = tk.Button(bf, text="Atualizar Sistema",
                        bg="#a6e3a1", fg="#1e1e2e", font=("Segoe UI",9,"bold"),
                        relief="flat", padx=12, pady=5, cursor="hand2")
    btn_upd.pack(side="left", padx=4)
    tk.Button(bf, text="Fechar", command=w.destroy,
              bg="#313244", fg="#cdd6f4", font=("Segoe UI",9,"bold"),
              relief="flat", padx=12, pady=5, cursor="hand2").pack(side="right", padx=4)

    def verificar_atualizacao_manual():
        btn_upd.config(text="Verificando...", state="disabled", bg="#89b4fa")
        def _run():
            try:
                req = urllib.request.Request(VERSION_URL, headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as r:
                    info = json.loads(r.read())
                nova, url_nova = _alvo_update(info)   # v5.78: latest_* antes de version/url
                if not nova or not url_nova:
                    w.after(0, lambda: (btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"),
                                        messagebox.showwarning("Aviso","Nao foi possivel verificar atualizacao.",parent=w))); return
                # v5.78: publicada mais ANTIGA que a minha nao e "nova versao" (sem allow_downgrade)
                if nova == CURRENT_VERSION or (_ver_tuple(nova) < _ver_tuple(CURRENT_VERSION)
                                               and not info.get("allow_downgrade")):
                    w.after(0, lambda: (btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"),
                                        messagebox.showinfo("Atualizado",f"Voce ja esta na versao mais recente (v{CURRENT_VERSION}).",parent=w))); return
                def _confirmar():
                    if not messagebox.askyesno("Atualizar",f"Nova versao v{nova} disponivel!\nDeseja atualizar agora?",parent=w):
                        btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"); return
                    btn_upd.config(text="Baixando...", bg="#f9e2af")
                    def _baixar():
                        try:
                            exe_tmp = BASE_DIR / "AgenteLocal_update.tmp"
                            exe_novo = BASE_DIR / f"AgenteLocal_{nova}.exe"
                            # Baixa validando integridade (mesma blindagem do update automatico)
                            req2 = urllib.request.Request(url_nova)
                            baixado = 0; esperado = None
                            with urllib.request.urlopen(req2, timeout=120, context=_ssl_ctx()) as rr, open(exe_tmp,"wb") as ff:
                                try: esperado = int(rr.headers.get("Content-Length") or 0) or None
                                except Exception: esperado = None
                                while True:
                                    ch = rr.read(65536)
                                    if not ch: break
                                    ff.write(ch); baixado += len(ch)
                            if baixado < _TAMANHO_MIN_EXE:
                                raise ValueError(f"download muito pequeno ({baixado} bytes) — abortado")
                            if esperado and baixado != esperado:
                                raise ValueError(f"download incompleto ({baixado}/{esperado})")
                            with open(exe_tmp,"rb") as _fp:
                                if _fp.read(2) != b"MZ":
                                    raise ValueError("arquivo baixado nao e um .exe valido")
                            if exe_novo.exists():
                                try: exe_novo.unlink()
                                except Exception: pass
                            exe_tmp.rename(exe_novo)
                            exe_destino = BASE_DIR / "AgenteLocal.exe"
                            # del_extra removido: o bat blindado ja limpa versionados com seguranca.
                            bat = BASE_DIR / "update_apply.bat"
                            bat.write_text(_bat_update(exe_novo, exe_destino, ""), encoding="utf-8")
                            _popen_bat_orfao(bat)   # fora da arvore do agente (ver _popen_bat_orfao)
                            log.info(f"[UPDATE] Atualizando para v{nova} via botao manual (validado {baixado} bytes)")
                            w.after(0, lambda: messagebox.showinfo("Atualizando",f"Atualizando para v{nova}...\nO agente vai reiniciar automaticamente.",parent=w))
                            w.after(500, sys.exit)
                        except Exception as e:
                            try:
                                _t = BASE_DIR / "AgenteLocal_update.tmp"
                                if _t.exists(): _t.unlink()
                            except Exception: pass
                            w.after(0, lambda: (btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"),
                                                messagebox.showerror("Erro",f"Falha ao baixar:\n{e}",parent=w)))
                    threading.Thread(target=_baixar, daemon=True).start()
                w.after(0, _confirmar)
            except Exception as e:
                w.after(0, lambda: (btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"),
                                    messagebox.showerror("Erro",f"Falha ao verificar:\n{e}",parent=w)))
        threading.Thread(target=_run, daemon=True).start()
    btn_upd.config(command=verificar_atualizacao_manual)

    # Notebook com abas
    nb = ttk.Notebook(w)
    nb.pack(fill="both", expand=True, padx=12, pady=(0,12))

    # ── ABA 1: STATUS ──────────────────────────────────────────────
    tab_status = tk.Frame(nb, bg="#1a1a2e"); nb.add(tab_status, text="  Status  ")

    cards_frame = tk.Frame(tab_status, bg="#1a1a2e"); cards_frame.pack(fill="x", padx=16, pady=12)
    cards_frame.columnconfigure(0,weight=1); cards_frame.columnconfigure(1,weight=1)
    cards_frame.columnconfigure(2,weight=1); cards_frame.columnconfigure(3,weight=1)

    def make_card(parent, col, label, value_var, cor):
        f = tk.Frame(parent, bg="#25253a", padx=12, pady=10)
        f.grid(row=0, column=col, padx=5, pady=5, sticky="nsew")
        tk.Label(f, text=label, bg="#25253a", fg="#6c7086", font=("Segoe UI",9)).pack(anchor="w")
        tk.Label(f, textvariable=value_var, bg="#25253a", fg=cor, font=("Segoe UI",22,"bold")).pack(anchor="w")

    v_total = tk.StringVar(value="0"); v_hoje = tk.StringVar(value="0")
    v_erros = tk.StringVar(value="0"); v_uptime = tk.StringVar(value="0m")
    make_card(cards_frame, 0, "Total impressos", v_total,  "#a6e3a1")
    make_card(cards_frame, 1, "Hoje",            v_hoje,   "#89b4fa")
    make_card(cards_frame, 2, "Falhas",          v_erros,  "#f38ba8")
    make_card(cards_frame, 3, "Uptime",          v_uptime, "#cba6f7")

    # Ultimo job / ultimo erro
    info_f = tk.Frame(tab_status, bg="#1a1a2e"); info_f.pack(fill="x", padx=16, pady=(0,8))
    info_f.columnconfigure(0,weight=1); info_f.columnconfigure(1,weight=1)
    uf = tk.Frame(info_f, bg="#25253a", padx=14, pady=10); uf.grid(row=0,column=0,padx=(0,4),sticky="nsew")
    tk.Label(uf, text="Ultimo job impresso", bg="#25253a", fg="#6c7086", font=("Segoe UI",9)).pack(anchor="w")
    v_ujob = tk.StringVar(value="Nenhum ainda"); v_uimp = tk.StringVar(value="")
    tk.Label(uf, textvariable=v_ujob, bg="#25253a", fg="#cdd6f4", font=("Segoe UI",10)).pack(anchor="w")
    tk.Label(uf, textvariable=v_uimp, bg="#25253a", fg="#6c7086", font=("Segoe UI",9)).pack(anchor="w")
    ef2 = tk.Frame(info_f, bg="#25253a", padx=14, pady=10); ef2.grid(row=0,column=1,padx=(4,0),sticky="nsew")
    tk.Label(ef2, text="Ultimo erro", bg="#25253a", fg="#6c7086", font=("Segoe UI",9)).pack(anchor="w")
    v_uerr = tk.StringVar(value="Nenhum")
    tk.Label(ef2, textvariable=v_uerr, bg="#25253a", fg="#f38ba8",
             font=("Segoe UI",9), wraplength=340, justify="left").pack(anchor="w")

    # Balancas
    pf = tk.Frame(tab_status, bg="#25253a", padx=14, pady=8); pf.pack(fill="x", padx=16, pady=(0,8))
    tk.Label(pf, text="Balancas em tempo real", bg="#25253a", fg="#6c7086", font=("Segoe UI",9)).pack(anchor="w")
    pesos_frame = tk.Frame(pf, bg="#25253a"); pesos_frame.pack(fill="x", pady=(4,0))
    def atualizar_pesos():
        if not w.winfo_exists(): return
        for wid in pesos_frame.winfo_children(): wid.destroy()
        if not _pesos_atuais:
            tk.Label(pesos_frame, text="Nenhuma balanca conectada", bg="#25253a", fg="#45475a", font=("Segoe UI",9)).pack(anchor="w")
        else:
            for nome_b, info_b in _pesos_atuais.items():
                row2 = tk.Frame(pesos_frame, bg="#25253a"); row2.pack(fill="x", pady=1)
                cor = "#a6e3a1" if info_b["status"]=="ok" else "#f38ba8"
                tk.Label(row2, text=f"{nome_b}:", bg="#25253a", fg="#cdd6f4", font=("Segoe UI",9,"bold"), width=18, anchor="w").pack(side="left")
                tk.Label(row2, text=f"{info_b['peso']:.3f} kg", bg="#25253a", fg=cor, font=("Segoe UI",13,"bold")).pack(side="left", padx=6)
                tk.Label(row2, text=info_b["hora"], bg="#25253a", fg="#45475a", font=("Segoe UI",8)).pack(side="left")
        w.after(500, atualizar_pesos)
    atualizar_pesos()

    # ── ABA 2: IMPRESSOES (historico de sucesso) ──────────────────
    tab_hist = tk.Frame(nb, bg="#1a1a2e"); nb.add(tab_hist, text="  Impressoes  ")

    hdr_f = tk.Frame(tab_hist, bg="#1a1a2e"); hdr_f.pack(fill="x", padx=12, pady=(10,4))
    tk.Label(hdr_f, text="Ultimas 50 impressoes com sucesso",
             bg="#1a1a2e", fg="#6c7086", font=("Segoe UI",9)).pack(side="left")

    hist_frame = tk.Frame(tab_hist, bg="#1a1a2e"); hist_frame.pack(fill="both", expand=True, padx=12, pady=(0,4))
    cols_h = ("hora","tipo","impressora","pedido","cliente")
    tree_h = ttk.Treeview(hist_frame, columns=cols_h, show="headings", height=16)
    tree_h.heading("hora",      text="Hora");       tree_h.column("hora",       width=65,  minwidth=55)
    tree_h.heading("tipo",      text="Tipo");       tree_h.column("tipo",       width=75,  minwidth=60)
    tree_h.heading("impressora",text="Impressora"); tree_h.column("impressora", width=180, minwidth=100)
    tree_h.heading("pedido",    text="Pedido");     tree_h.column("pedido",     width=80,  minwidth=60)
    tree_h.heading("cliente",   text="Cliente");    tree_h.column("cliente",    width=160, minwidth=80)
    sb_h = ttk.Scrollbar(hist_frame, orient="vertical", command=tree_h.yview)
    tree_h.configure(yscrollcommand=sb_h.set)
    tree_h.pack(side="left", fill="both", expand=True)
    sb_h.pack(side="right", fill="y")

    def reimprimir():
        sel = tree_h.selection()
        if not sel: messagebox.showwarning("Aviso","Selecione um job na lista!",parent=w); return
        idx = tree_h.index(sel[0])
        if idx >= len(_stats["historico"]): return
        job_info = _stats["historico"][idx]
        jid = job_info.get("job_id",""); nome_imp_hist = job_info.get("impressora","")
        def _do_reimp(jid=jid, job_info=job_info, nome_imp_hist=nome_imp_hist):
            try:
                oid_hist = job_info.get("order_id","") or ""
                pt_orig = job_info.get("tipo","receipt")
                log.info(f"[REIMP] Iniciando job_id='{jid}' order_id='{oid_hist}' tipo='{pt_orig}' impressora_hist='{nome_imp_hist}'")
                resp = None
                if oid_hist and len(oid_hist) >= 36:
                    r1, s1 = _post(f"{SUPABASE_URL}/functions/v1/agent-get-order", {"order_id": oid_hist}, cfg.get("token",""))
                    log.info(f"[REIMP] Busca via order_id: HTTP {s1} | tem_resp={bool(r1)}")
                    if s1 == 200 and r1 and not r1.get("error"): resp = r1
                if not resp and jid and len(jid) >= 36:
                    r2, s2 = _post(f"{SUPABASE_URL}/functions/v1/agent-get-order", {"job_id": jid}, cfg.get("token",""))
                    log.info(f"[REIMP] Busca via job_id: HTTP {s2} | tem_resp={bool(r2)}")
                    if s2 == 200 and r2 and not r2.get("error"): resp = r2
                if not resp:
                    log.error(f"[REIMP] Pedido nao encontrado. order_id='{oid_hist}' job_id='{jid}'")
                    w.after(0, lambda: messagebox.showerror("Erro","Nao foi possivel buscar o pedido.\nVerifique o log para detalhes.",parent=w)); return
                log.info(f"[REIMP] Pedido encontrado: order_number={resp.get('order_number','?')} items={len(resp.get('items') or resp.get('order_items') or [])}")
                if "order_items" in resp and "items" not in resp:
                    raw_items = resp.get("order_items") or []
                    resp["items"] = [{"name": it.get("name_snapshot") or it.get("product_name") or it.get("name",""),
                                      "size_name": it.get("size_name","") or it.get("tamanho",""),
                                      "quantity": it.get("quantity",1), "unit_price_cents": it.get("price_cents_snapshot") or it.get("unit_price_cents",0),
                                      "notes": it.get("notes",""), "addons": it.get("addons_json") or it.get("addons",[]),
                                      # Categoria tambem na reimpressao: o papel tem que sair igual ao original
                                      "category_name": it.get("category_name") or it.get("categoria") or ""} for it in raw_items]
                imp = _res_imp_por_rede(pt_orig); pt_uso = pt_orig
                log.info(f"[REIMP] Impressora para tipo '{pt_orig}': {imp}")
                if not imp:
                    imps_locais = [i for i in cfg.get("impressoras",[]) if i.get("nome_impressora","").strip()]
                    log.info(f"[REIMP] Fallback impressoras locais: {[i.get('nome_impressora') for i in imps_locais]}")
                    if imps_locais:
                        i0 = imps_locais[0]; imp = i0
                        pt_uso = i0.get("printer_type","").strip() or i0.get("area","").strip() or pt_orig
                if not imp and nome_imp_hist:
                    # v5.80: reaproveita a entrada da config (mantem codepage/colunas da impressora)
                    imp = next(({**i, "tipo": i.get("tipo") or "comum_win32"} for i in cfg.get("impressoras",[])
                                if i.get("nome_impressora","") == nome_imp_hist),
                               {"nome_impressora": nome_imp_hist, "tipo": "comum_win32"})
                    log.info(f"[REIMP] Usando impressora do historico: '{nome_imp_hist}'")
                if not imp:
                    log.error("[REIMP] Nenhuma impressora disponivel neste PC")
                    w.after(0, lambda: messagebox.showerror("Erro","Nenhuma impressora configurada neste PC",parent=w)); return
                nome_real = imp.get("nome_impressora") or imp.get("endereco_ip","")
                log.info(f"[REIMP] Imprimindo em '{nome_real}' pt_uso='{pt_uso}'")
                # v5.80: reimpressao com a tabela de acentos e a largura DA impressora (antes saia
                # sempre em cp850/48, e uma mini configurada como utf8 reimprimia com letra errada).
                # v5.81: fonte da impressora idem.
                with _usar_codepage(_cp_da_impressora(imp)), _usar_fonte(_fonte_da_impressora(imp)):
                    texto = _fmt(resp, pt_uso, pt_uso, imp)
                    r = _imprimir_com_roteamento(imp, texto)
                if r.get("ok"):
                    log.info(f"[REIMP] OK em '{nome_real}'")
                    w.after(0, lambda: messagebox.showinfo("OK",f"Reimpresso em:\n{nome_real}",parent=w))
                else:
                    log.error(f"[REIMP] Falha na impressora '{nome_real}': {r.get('erro','')}")
                    w.after(0, lambda: messagebox.showerror("Erro",f"Impressora: {nome_real}\n\n{r.get('erro','')}",parent=w))
            except Exception as e:
                log.error(f"[REIMP] Excecao: {e}", exc_info=True)
                w.after(0, lambda: messagebox.showerror("Erro",str(e),parent=w))
        threading.Thread(target=_do_reimp, daemon=True).start()

    tk.Button(tab_hist, text="Reimprimir selecionado", command=reimprimir,
              bg="#cba6f7", fg="#1e1e2e", font=("Segoe UI",9,"bold"),
              relief="flat", padx=12, pady=5, cursor="hand2").pack(anchor="w", padx=12, pady=(0,8))

    # ── ABA 3: FALHAS (diagnostico) ───────────────────────────────
    tab_falhas = tk.Frame(nb, bg="#1a1a2e"); nb.add(tab_falhas, text="  Falhas  ")

    falha_hdr = tk.Frame(tab_falhas, bg="#1a1a2e"); falha_hdr.pack(fill="x", padx=12, pady=(10,4))
    tk.Label(falha_hdr, text="Historico de falhas — ultimas 100 ocorrencias",
             bg="#1a1a2e", fg="#6c7086", font=("Segoe UI",9)).pack(side="left")
    def limpar_falhas():
        _stats["falhas"].clear(); _stats["erros"] = 0
        log.info("[DIAG] Historico de falhas limpo pelo operador")
    tk.Button(falha_hdr, text="Limpar", command=limpar_falhas,
              bg="#45475a", fg="#cdd6f4", font=("Segoe UI",8),
              relief="flat", padx=8, pady=3, cursor="hand2").pack(side="right")

    falha_frame = tk.Frame(tab_falhas, bg="#1a1a2e"); falha_frame.pack(fill="both", expand=True, padx=12, pady=(0,4))
    cols_f = ("hora","causa","pedido","cliente","tipo","impressora","detalhe")
    tree_f = ttk.Treeview(falha_frame, columns=cols_f, show="headings", height=14)
    tree_f.heading("hora",      text="Hora");       tree_f.column("hora",       width=65,  minwidth=55)
    tree_f.heading("causa",     text="Causa");      tree_f.column("causa",      width=160, minwidth=100)
    tree_f.heading("pedido",    text="Pedido");     tree_f.column("pedido",     width=75,  minwidth=55)
    tree_f.heading("cliente",   text="Cliente");    tree_f.column("cliente",    width=120, minwidth=80)
    tree_f.heading("tipo",      text="Tipo");       tree_f.column("tipo",       width=70,  minwidth=55)
    tree_f.heading("impressora",text="Impressora"); tree_f.column("impressora", width=130, minwidth=80)
    tree_f.heading("detalhe",   text="Detalhe");    tree_f.column("detalhe",    width=280, minwidth=120)
    sb_f = ttk.Scrollbar(falha_frame, orient="vertical", command=tree_f.yview)
    tree_f.configure(yscrollcommand=sb_f.set)
    tree_f.pack(side="left", fill="both", expand=True)
    sb_f.pack(side="right", fill="y")

    # Detalhe completo ao selecionar linha
    detalhe_f = tk.Frame(tab_falhas, bg="#25253a", padx=12, pady=8)
    detalhe_f.pack(fill="x", padx=12, pady=(0,8))
    tk.Label(detalhe_f, text="Detalhe completo:", bg="#25253a", fg="#6c7086", font=("Segoe UI",9)).pack(anchor="w")
    v_detalhe = tk.StringVar(value="Selecione uma linha para ver o detalhe completo")
    tk.Label(detalhe_f, textvariable=v_detalhe, bg="#25253a", fg="#f38ba8",
             font=("Segoe UI",9), wraplength=760, justify="left").pack(anchor="w")

    def on_falha_select(event):
        sel = tree_f.selection()
        if not sel: return
        idx = tree_f.index(sel[0])
        if idx < len(_stats["falhas"]):
            f = _stats["falhas"][idx]
            v_detalhe.set(f"{f.get('data','')} {f.get('hora','')} | {f.get('causa','')} | {f.get('detalhe','')}")
    tree_f.bind("<<TreeviewSelect>>", on_falha_select)

    # Rotulos de causas traduzidos para exibicao
    _causas_pt = {
        "sem_mapeamento_windows":  "Sem mapeamento Windows",
        "impressora_nao_encontrada": "Impressora nao encontrada",
        "erro_impressora":         "Erro na impressora",
        "falha_buscar_pedido":     "Falha ao buscar pedido",
        "status_update_failed":    "Falha ao atualizar status",
    }

    # ── ABA 4: AGENTES ────────────────────────────────────────────
    tab_ag = tk.Frame(nb, bg="#1a1a2e"); nb.add(tab_ag, text="  Agentes  ")

    tk.Label(tab_ag, text="Agentes conectados ao mesmo restaurante",
             bg="#1a1a2e", fg="#cdd6f4", font=("Segoe UI",9,"bold"),
             anchor="w", padx=8, pady=4).pack(fill="x", padx=10, pady=(10,2))

    cols_ag = ("maquina","areas","status","ultimo")
    tree_ag = ttk.Treeview(tab_ag, columns=cols_ag, show="headings", height=10)
    for col, lbl, cw in [("maquina","Maquina",200),("areas","Area(s)",200),("status","Status",90),("ultimo","Ultimo heartbeat",160)]:
        tree_ag.heading(col, text=lbl); tree_ag.column(col, width=cw, anchor="w")
    tree_ag.tag_configure("online",  foreground="#a6e3a1")
    tree_ag.tag_configure("recente", foreground="#f9e2af")
    tree_ag.tag_configure("offline", foreground="#f38ba8")
    sb_ag = ttk.Scrollbar(tab_ag, orient="vertical", command=tree_ag.yview)
    tree_ag.configure(yscrollcommand=sb_ag.set)
    ag_frame = tk.Frame(tab_ag, bg="#1a1a2e"); ag_frame.pack(fill="both", expand=True, padx=10, pady=4)
    tree_ag.pack(in_=ag_frame, side="left", fill="both", expand=True)
    sb_ag.pack(in_=ag_frame, side="right", fill="y")

    ag_status_var = tk.StringVar(value="")
    tk.Label(tab_ag, textvariable=ag_status_var, bg="#1a1a2e", fg="#6c7086",
             font=("Segoe UI",8)).pack(anchor="w", padx=12)
    tk.Button(tab_ag, text="Atualizar agora", command=lambda: _atualizar_barra_agentes(),
              bg="#89b4fa", fg="#1e1e2e", font=("Segoe UI",9,"bold"),
              relief="flat", padx=12, pady=4, cursor="hand2").pack(pady=4)

    def _popular_tree_ag(lista):
        if not w.winfo_exists(): return
        tree_ag.delete(*tree_ag.get_children())
        import datetime
        agora = time.time()
        for ag in lista:
            nome = ag.get("device_name") or "Agente"
            areas_ag = ", ".join(ag.get("covered_areas") or []) or "todas"
            hb = ag.get("last_heartbeat_at","")
            try:
                ts = datetime.datetime.fromisoformat(hb.replace("Z","+00:00"))
                diff = agora - ts.timestamp()
                if diff < 35:
                    status_txt = "Online"; tag = "online"
                elif diff < 120:
                    status_txt = "Recente"; tag = "recente"
                else:
                    status_txt = "Offline"; tag = "offline"
                hb_fmt = time.strftime("%H:%M:%S", time.localtime(ts.timestamp()))
            except Exception:
                status_txt = "?"; tag = "recente"; hb_fmt = hb[:19]
            if ag.get("device_name","") == DEVICE_NAME:
                nome = nome + " (este)"
            tree_ag.insert("", "end", values=(nome, areas_ag, status_txt, hb_fmt), tags=(tag,))
        ag_status_var.set(f"Atualizado: {time.strftime('%H:%M:%S')}  |  {len(lista)} agente(s)")

    # Registra referência para uso em _atualizar_barra_agentes
    _popular_tree_ag_ref[0] = _popular_tree_ag

    # Popula imediatamente com cache
    _popular_tree_ag(_agents_online)

    # ── LOOP DE ATUALIZACAO ───────────────────────────────────────
    def atualizar():
        if not w.winfo_exists(): return
        v_total.set(str(_stats["total_impressos"]))
        v_hoje.set(str(_stats["hoje"]))
        v_erros.set(str(_stats["erros"]))
        mins = int((time.time() - _start_time) / 60)
        v_uptime.set(f"{mins}m" if mins < 60 else f"{mins//60}h {mins%60}m")
        if _stats["ultimo_job"]:
            v_ujob.set(f"Impresso as {_stats['ultimo_job']}")
            v_uimp.set(f"Impressora: {_stats['ultima_impressora']}")
        if _stats["ultimo_erro"]:
            v_uerr.set(_stats["ultimo_erro"][:120])

        # Aba titulo com contador de falhas
        n_falhas = len(_stats["falhas"])
        nb.tab(tab_falhas, text=f"  Falhas ({n_falhas})  " if n_falhas else "  Falhas  ")

        # Historico de impressoes
        tree_h.delete(*tree_h.get_children())
        for h in _stats["historico"]:
            tree_h.insert("", tk.END, values=(
                h.get("hora",""), h.get("tipo",""),
                h.get("impressora","")[:28],
                h.get("content_ref",""),
                h.get("cliente","")[:22],
            ))

        # Historico de falhas
        tree_f.delete(*tree_f.get_children())
        for f in _stats["falhas"]:
            causa_label = _causas_pt.get(f.get("causa",""), f.get("causa",""))
            tree_f.insert("", tk.END, values=(
                f.get("hora",""),
                causa_label,
                f.get("pedido",""),
                f.get("cliente","")[:18],
                f.get("tipo",""),
                f.get("impressora","")[:20],
                f.get("detalhe","")[:60],
            ), tags=("falha",))
        tree_f.tag_configure("falha", foreground="#f38ba8")

        # Atualiza tabela de agentes com dados do cache global
        _popular_tree_ag(_agents_online)

        w.after(2000, atualizar)

    atualizar()


def abrir_log():
    w=tk.Toplevel(_root); w.title("Log"); w.geometry("820x500"); w.configure(bg="#1e1e2e")
    txt=scrolledtext.ScrolledText(w,bg="#1e1e2e",fg="#a6e3a1",font=("Consolas",9),state="disabled")
    txt.pack(fill="both",expand=True,padx=10,pady=10)
    def upd():
        if LOG_PATH.exists():
            ll=LOG_PATH.read_text(encoding="utf-8",errors="replace").splitlines()
            txt.config(state="normal"); txt.delete("1.0","end")
            txt.insert("end","\n".join(ll[-300:])); txt.see("end"); txt.config(state="disabled")
        w.after(2000,upd)
    def clr():
        if messagebox.askyesno("Limpar","Deseja limpar?",parent=w):
            LOG_PATH.write_text("",encoding="utf-8"); upd()
    row=tk.Frame(w,bg="#1e1e2e"); row.pack(fill="x",padx=10,pady=5)
    for tb,cb,cor in [("Atualizar",upd,"#89b4fa"),("Limpar",clr,"#f38ba8"),
                       ("Abrir",lambda:os.startfile(str(LOG_PATH)),"#a6e3a1")]:
        tk.Button(row,text=tb,command=cb,bg=cor,fg="#1e1e2e",font=("Segoe UI",9,"bold"),
                  relief="flat",padx=10,pady=5).pack(side="left",padx=4)
    upd()

def _config_aberta():
    try:
        return _janela_config is not None and _janela_config.winfo_exists()
    except Exception:
        return False

def abrir_config(auto=False):
    global cfg, _janela_config
    # JANELA UNICA: se ja esta aberta, nao empilha outra. No modo AUTOMATICO (poll/boot)
    # NAO rouba o foco (nao chama focus_force) — era isso que "nao deixava a cliente trabalhar".
    if _config_aberta():
        if not auto:
            try: _janela_config.deiconify(); _janela_config.lift(); _janela_config.focus_force()
            except Exception: pass
        return
    cfg=carregar_config(); iw=listar_impressoras_windows(); ps=listar_portas_serial()
    w=tk.Toplevel(_root); w.title("Concentrador de Impressoes e Dispositivos")
    w.geometry("820x700"); w.configure(bg="#1e1e2e")
    if not auto:
        w.lift(); w.focus_force()   # so traz pra frente/rouba foco quando o USUARIO pediu (tray/botao)
    _janela_config = w
    # limpa a referencia quando a janela e destruida (por qualquer caminho), p/ permitir reabrir depois
    w.bind("<Destroy>", lambda e: (globals().__setitem__('_janela_config', None) if e.widget is w else None))

    sty=ttk.Style(w); sty.theme_use("clam")
    sty.configure("TNotebook",background="#1e1e2e",borderwidth=0)
    sty.configure("TNotebook.Tab",background="#313244",foreground="white",padding=[12,6])
    sty.map("TNotebook.Tab",background=[("selected","#89b4fa")])
    sty.configure("TFrame",background="#1e1e2e")
    sty.configure("TLabel",background="#1e1e2e",foreground="#cdd6f4")
    sty.configure("TEntry",fieldbackground="#313244",foreground="white",insertcolor="white")
    sty.configure("TCombobox",fieldbackground="#313244",foreground="white")
    sty.configure("Treeview",background="#313244",foreground="white",fieldbackground="#313244",rowheight=28)
    sty.configure("Treeview.Heading",background="#45475a",foreground="white",font=("Segoe UI",9,"bold"))
    sty.map("Treeview",background=[("selected","#89b4fa")])

    # IMPORTANTE: empacota o rod (botoes inferiores) ANTES do notebook
    # para que ele sempre fique visivel na parte de baixo, independente do tamanho da janela
    rod = tk.Frame(w, bg="#181825")
    rod.pack(fill="x", side="bottom")

    nb=ttk.Notebook(w); nb.pack(fill="both",expand=True,padx=10,pady=10)

    # CONEXAO
    f1=ttk.Frame(nb); nb.add(f1,text="Conexao")
    inf=tk.Frame(f1,bg="#313244"); inf.grid(row=0,column=0,padx=15,pady=15,sticky="ew")
    tk.Label(inf,text="Cole o Token de API gerado no sistema MIA.\nO agente se configurara automaticamente.",
             bg="#313244",fg="#a6c8e0",font=("Segoe UI",9),pady=8,justify="center").pack()
    ttk.Label(f1,text="Token de API:").grid(row=1,column=0,sticky="w",padx=15,pady=4)
    tv=tk.StringVar(value=cfg.get("token","")); te=ttk.Entry(f1,textvariable=tv,width=65,show="*")
    te.grid(row=2,column=0,padx=15,sticky="ew"); sv2=tk.StringVar(value="")

    def conectar():
        token=tv.get().strip()
        if not token: messagebox.showwarning("Aviso","Cole o Token!",parent=w); return
        sv2.set("Conectando..."); w.update()
        r=autoconfigurar(token)
        if r.get("ok"):
            d=r["data"]
            cfg.update({"token":token,"restaurant_id":d.get("restaurant_id",""),
                        "restaurant_name":d.get("restaurant_name",""),
                        "ultima_sincronizacao":time.strftime("%d/%m/%Y %H:%M:%S")})
            printers=d.get("config",{}).get("printers", d.get("printers",[])); icfg=[]
            imps_existentes={i.get("nome","").strip().lower():i for i in cfg.get("impressoras",[])}
            for p in printers:
                ns=p.get("name",""); ts=p.get("printer_type","receipt")
                area={"receipt":"caixa","kitchen":"cozinha","bar":"bar"}.get(ts,"caixa")
                existente=imps_existentes.get(ns.strip().lower())
                match=existente.get("nome_impressora","") if existente else ""
                if not match:
                    match=next((x for x in iw if ns.upper()[:5] in x.upper() or x.upper()[:5] in ns.upper()),"")
                _nova={"nome":ns,"area":area,"printer_type":ts,"nome_impressora":match,"tipo":"comum_win32","modo":"texto"}
                # v5.80: ajustes feitos NA LOJA (colunas do papel, acentos) sobrevivem ao re-conectar
                for _k in ("colunas","codepage"):
                    if existente and existente.get(_k): _nova[_k]=existente[_k]
                icfg.append(_nova)
            cfg["impressoras"]=icfg; salvar_config(cfg)
            sv2.set(f"Conectado: {d.get('restaurant_name','')}")
            for item in ti.get_children(): ti.delete(item)
            for imp in icfg:
                tag="" if imp.get("nome_impressora") else "sem_map"
                ti.insert("",tk.END,values=(imp["nome"],imp["area"],imp["nome_impressora"],imp["tipo"],
                                            str(imp.get("colunas") or ""), str(imp.get("codepage") or "")),tags=(tag,))
            messagebox.showinfo("OK",f"Restaurante: {d.get('restaurant_name','')}\nImpressoras: {len(printers)}\n\nClique DUPLO para mapear.",parent=w)
        else:
            sv2.set(f"Erro: {r.get('erro','')}"); messagebox.showerror("Erro",r.get("erro","Token invalido"),parent=w)

    tk.Button(f1,text="Conectar ao Sistema",command=conectar,bg="#a6e3a1",fg="#1e1e2e",
              font=("Segoe UI",11,"bold"),relief="flat",padx=20,pady=10,cursor="hand2").grid(row=5,column=0,pady=10)
    tk.Label(f1,textvariable=sv2,bg="#1e1e2e",fg="#89b4fa",font=("Segoe UI",10,"bold")).grid(row=6,column=0)
    rf=tk.Frame(f1,bg="#313244"); rf.grid(row=7,column=0,padx=15,pady=8,sticky="ew")
    tk.Label(rf,text=f"Restaurante: {cfg.get('restaurant_name','Nao configurado')}",
             bg="#313244",fg="#cdd6f4",font=("Segoe UI",10,"bold"),pady=4).pack()
    if cfg.get("restaurant_id"):
        tk.Label(rf,text=f"ID: {cfg.get('restaurant_id','')}",bg="#313244",fg="#6c7086",font=("Segoe UI",8)).pack()
    tk.Label(rf,text=f"Ultima sincronizacao: {cfg.get('ultima_sincronizacao','Nunca')}",
             bg="#313244",fg="#6c7086",font=("Segoe UI",8),pady=4).pack()
    sf=tk.Frame(f1,bg="#313244"); sf.grid(row=8,column=0,padx=15,pady=5,sticky="ew")
    sv_status=tk.StringVar(value=f"Status: {status_poll}")
    cs="#a6e3a1" if "Ativo" in status_poll else "#f38ba8"
    lbl_status=tk.Label(sf,textvariable=sv_status,bg="#313244",fg=cs,font=("Segoe UI",10,"bold"),pady=8)
    lbl_status.pack()
    def _atualizar_status_config():
        sv_status.set(f"Status: {status_poll}")
        cor="#a6e3a1" if "Ativo" in status_poll else "#f38ba8"
        lbl_status.config(fg=cor)
        if sf.winfo_exists(): sf.after(1000, _atualizar_status_config)
    _atualizar_status_config()
    ttk.Label(f1,text="Intervalo polling (s):").grid(row=9,column=0,sticky="w",padx=15,pady=8)
    pv=tk.StringVar(value=str(cfg.get("poll_interval",3))); ttk.Entry(f1,textvariable=pv,width=8).grid(row=10,column=0,sticky="w",padx=15)
    f1.columnconfigure(0,weight=1)

    # IMPRESSORAS
    f2=ttk.Frame(nb); nb.add(f2,text="Impressoras")
    inf2=tk.Frame(f2,bg="#313244"); inf2.grid(row=0,column=0,columnspan=6,padx=10,pady=6,sticky="ew")
    tk.Label(inf2,text="DUPLO CLIQUE em uma linha para editar a Impressora Windows.\nVermelho = sem mapeamento.  caixa=receipt | cozinha=kitchen | bar=bar",
             bg="#313244",fg="#a6c8e0",font=("Segoe UI",9),pady=6,wraplength=750,justify="left").pack()

    cols=("nome","area","impressora_windows","tipo","colunas","acentos","fonte")   # v5.79: colunas; v5.80: acentos; v5.81: fonte
    ti=ttk.Treeview(f2,columns=cols,show="headings",height=9)
    for col,lbl,cw in [("nome","Nome Sistema",140),("area","Area",80),
                        ("impressora_windows","Impressora Windows",250),("tipo","Tipo",90),
                        ("colunas","Colunas",65),("acentos","Acentos",70),("fonte","Fonte",70)]:
        ti.heading(col,text=lbl); ti.column(col,width=cw)
    sbi=ttk.Scrollbar(f2,orient="vertical",command=ti.yview); ti.configure(yscrollcommand=sbi.set)
    ti.grid(row=1,column=0,columnspan=5,padx=10,pady=5,sticky="nsew"); sbi.grid(row=1,column=5,pady=5,sticky="ns")
    ti.tag_configure("sem_map", foreground="#f38ba8")
    ti.tag_configure("outro_agente", foreground="#89b4fa")

    # Áreas cobertas por outros agentes online
    _areas_outros = set()
    for ag in _agents_online:
        if ag.get("device_name","") != DEVICE_NAME:
            for a in (ag.get("covered_areas") or []):
                _areas_outros.add(a.lower())

    _mapa_tipo_area = {"receipt":"caixa","kitchen":"cozinha","bar":"bar","delivery":"delivery","pickup":"balcao"}

    def _tag_impressora(imp):
        if imp.get("nome_impressora"):
            return ""
        area = imp.get("area","").strip().lower()
        ptype = imp.get("printer_type","").strip().lower()
        area_do_tipo = _mapa_tipo_area.get(ptype, ptype)
        if area in _areas_outros or area_do_tipo in _areas_outros:
            return "outro_agente"
        return "sem_map"

    def _fonte_rotulo(imp):
        """Nome da fonte por impressora para a grade ('' = herda o geral)."""
        v = _fonte_valida(imp.get("font_size"))
        return _FONTE_NOMES[v].lower() if v is not None else ""

    for imp in cfg.get("impressoras",[]):
        tag = _tag_impressora(imp)
        nome_w = imp.get("nome_impressora","") or ("(outro agente)" if tag == "outro_agente" else "")
        ti.insert("",tk.END,values=(imp.get("nome",""),imp.get("area",""),
                                    nome_w, imp.get("tipo","comum_win32"),
                                    str(imp.get("colunas") or ""), str(imp.get("codepage") or ""),
                                    _fonte_rotulo(imp)),tags=(tag,))

    ef2=tk.Frame(f2,bg="#2a2a3e",relief="ridge",bd=1); ef2.grid(row=2,column=0,columnspan=6,padx=10,pady=4,sticky="ew")
    tk.Label(ef2,text="Area:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=0,column=0,padx=(10,4),pady=10)
    earea=ttk.Combobox(ef2,values=["caixa","cozinha","bar","delivery","balcao"],width=10); earea.grid(row=0,column=1,padx=4,pady=10)
    tk.Label(ef2,text="Impressora Windows:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=0,column=2,padx=4,pady=10)
    eiw=ttk.Combobox(ef2,values=iw,width=34); eiw.grid(row=0,column=3,padx=8,pady=10)
    lbe=tk.Label(ef2,text="<< Clique DUPLO em uma linha",bg="#2a2a3e",fg="#6c7086",font=("Segoe UI",8)); lbe.grid(row=0,column=4,padx=8)
    # v5.79: colunas do papel POR IMPRESSORA (vazio = automatico: usa o que o cardapio configurou
    # em 'Largura do Papel'; senao 48). Serve para corrigir na loja uma impressora de 42 colunas
    # sem depender do servidor: com 48 o preco quebrava em duas linhas ("R$ 1" / "11.70").
    tk.Label(ef2,text="Colunas do papel:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=1,column=0,columnspan=2,padx=(10,4),pady=(0,8),sticky="e")
    ecol=ttk.Combobox(ef2,values=["","32","42","48"],width=6); ecol.grid(row=1,column=2,padx=4,pady=(0,8),sticky="w")
    tk.Label(ef2,text="vazio = automatico   32 = 58 mm   42 = 76 mm   48 = 80 mm",
             bg="#2a2a3e",fg="#6c7086",font=("Segoe UI",8)).grid(row=1,column=3,padx=4,pady=(0,8),sticky="w")
    # v5.80: tabela de acentos POR IMPRESSORA (vazio = padrao do agente, cp850). Letra errada no
    # papel (comum em mini impressora de 58 mm) => 'Testar acentos' e escolher o bloco certo.
    tk.Label(ef2,text="Acentos:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=1,column=4,padx=4,pady=(0,8),sticky="e")
    eacc=ttk.Combobox(ef2,values=[""]+list(_CP_TESTE),width=8); eacc.grid(row=1,column=5,padx=4,pady=(0,8),sticky="w")
    # v5.81: fonte POR IMPRESSORA (vazio = herda o tamanho geral da aba Impressao). Deixa a
    # comanda da cozinha grande sem mexer no cupom do caixa, e vice-versa.
    _FONTES_COMBO=[""]+[n.lower() for n in _FONTE_NOMES]
    tk.Label(ef2,text="Fonte:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=1,column=6,padx=4,pady=(0,8),sticky="e")
    efnt=ttk.Combobox(ef2,values=_FONTES_COMBO,width=8); efnt.grid(row=1,column=7,padx=(4,10),pady=(0,8),sticky="w")
    tk.Label(ef2,text="Letra errada no papel? Clique 'Testar acentos', veja qual bloco saiu certo e escolha esse em Acentos. "
                      "'ascii' tira os acentos e funciona em qualquer impressora.",
             bg="#2a2a3e",fg="#6c7086",font=("Segoe UI",8),wraplength=760,justify="left").grid(row=2,column=0,columnspan=6,padx=10,pady=(0,8),sticky="w")

    def duplo(e):
        sel=ti.selection()
        if not sel: return
        vals=ti.item(sel[0],"values"); lbe.config(text=f"Editando: {vals[0]}",fg="#89b4fa")
        earea.set(vals[1] if len(vals)>1 else "")
        ecol.set(vals[4] if len(vals)>4 else "")
        eacc.set(vals[5] if len(vals)>5 else "")
        efnt.set(vals[6] if len(vals)>6 else "")
        eiw.set(vals[2] if len(vals)>2 else ""); eiw.focus()

    def aplicar():
        sel=ti.selection()
        if not sel: messagebox.showwarning("Aviso","Clique duplo em uma linha!",parent=w); return
        nova=eiw.get().strip(); nova_area=earea.get().strip()
        if not nova: messagebox.showwarning("Aviso","Selecione a Impressora Windows!",parent=w); return
        col_txt=ecol.get().strip()
        if col_txt and not _colunas_validas(col_txt):
            messagebox.showwarning("Aviso","Colunas do papel: use 32, 42 ou 48 (ou deixe vazio).",parent=w); return
        acc_txt=eacc.get().strip().lower()
        if acc_txt and not _normaliza_cp(acc_txt):
            messagebox.showwarning("Aviso",f"Acentos: use uma destas opcoes: {', '.join(_CP_TESTE)} (ou deixe vazio).",parent=w); return
        acc_txt=_normaliza_cp(acc_txt)
        fnt_txt=efnt.get().strip().lower()
        if fnt_txt and fnt_txt not in _FONTES_COMBO:
            messagebox.showwarning("Aviso",f"Fonte: use uma destas opcoes: {', '.join(n for n in _FONTES_COMBO if n)} (ou deixe vazio = herda o geral).",parent=w); return
        fnt_val=_FONTES_COMBO.index(fnt_txt)-1 if fnt_txt else None   # "normal"=0 ... "extra"=3
        vals=ti.item(sel[0],"values")
        area_final = nova_area or vals[1]
        ti.item(sel[0],values=(vals[0],area_final,nova,vals[3],col_txt,acc_txt,fnt_txt),tags=("",))
        lbe.config(text=f"OK: {vals[0]} -> {nova}",fg="#a6e3a1"); eiw.set(""); earea.set(""); ecol.set(""); eacc.set(""); efnt.set("")
        # Salva imediatamente no cfg e no disco
        nome_sistema = vals[0]
        for imp in cfg.get("impressoras",[]):
            if imp.get("nome") == nome_sistema:
                imp["nome_impressora"] = nova
                if nova_area: imp["area"] = nova_area
                if col_txt: imp["colunas"] = _colunas_validas(col_txt)
                else: imp.pop("colunas", None)
                if acc_txt: imp["codepage"] = acc_txt
                else: imp.pop("codepage", None)
                if fnt_val is not None: imp["font_size"] = fnt_val
                else: imp.pop("font_size", None)
                break
        salvar_config(cfg)
        log.info(f"[CONFIG] Impressora '{nome_sistema}' area={area_final} -> '{nova}' colunas={col_txt or 'auto'} acentos={acc_txt or 'padrao'} fonte={fnt_txt or 'herda'}")

    ti.bind("<Double-1>",duplo)
    tk.Button(ef2,text="Aplicar",command=aplicar,bg="#89b4fa",fg="#1e1e2e",
              font=("Segoe UI",9,"bold"),relief="flat",padx=14,pady=6,cursor="hand2").grid(row=0,column=5,padx=8)

    fi2=ttk.Frame(f2); fi2.grid(row=3,column=0,columnspan=6,padx=10,pady=4,sticky="ew")   # (ef2 cresceu por dentro; grid externo inalterado)
    ttk.Label(fi2,text="Novo:").grid(row=0,column=0,padx=4,pady=6)
    en=ttk.Entry(fi2,width=14); en.grid(row=0,column=1,padx=4)
    ttk.Label(fi2,text="Area:").grid(row=0,column=2,padx=4)
    ea=ttk.Combobox(fi2,values=["caixa","cozinha","bar","delivery","balcao"],width=9); ea.grid(row=0,column=3,padx=4)
    ttk.Label(fi2,text="Impressora:").grid(row=0,column=4,padx=4)
    ead=ttk.Combobox(fi2,values=iw,width=26); ead.grid(row=0,column=5,padx=4)

    def add_i():
        n=en.get().strip(); ww=ead.get().strip()
        if not n or not ww: messagebox.showwarning("Aviso","Preencha Nome e Impressora!",parent=w); return
        ti.insert("",tk.END,values=(n,ea.get().strip(),ww,"comum_win32","",""))
        en.delete(0,tk.END); ead.set("")

    def rem_i():
        sel=ti.selection()
        if sel: ti.delete(sel[0])

    def tst_i():
        sel=ti.selection()
        if not sel: messagebox.showwarning("Aviso","Selecione uma impressora!",parent=w); return
        vals=ti.item(sel[0],"values")
        nw=vals[2] if len(vals)>2 else ""
        if not nw: messagebox.showwarning("Aviso","Mapeie a Impressora Windows!\nClique DUPLO na linha.",parent=w); return
        txt2=("="*W+"\n"+f"  {cfg.get('restaurant_name','AGENTE LOCAL')}  ".center(W)+"\n"+
              "  TESTE DE IMPRESSAO OK!  ".center(W)+"\n"+"="*W+"\n"+
              f"Impressora: {nw}\n"+f"Hora: {time.strftime('%d/%m/%Y %H:%M:%S')}\n"+"="*W+"\n")
        # v5.80: com a tabela de acentos DA LINHA (a mesma que os pedidos usam nessa impressora).
        # Fonte NORMAL de proposito: este teste valida o MAPEAMENTO (o texto e formatado em W
        # colunas fixas); o teste de fonte e o "Imprimir cupom de teste" da aba Impressao.
        with _usar_codepage(_cp_da_impressora({"codepage": vals[5] if len(vals)>5 else ""})), _usar_fonte(0):
            r=_imprimir_raw(nw,txt2)
        if r.get("ok"): messagebox.showinfo("OK",f"Teste enviado:\n{nw}",parent=w)
        else: messagebox.showerror("Erro",r.get("erro",""),parent=w)

    def tst_acentos():
        """v5.80: imprime a pagina de teste de acentos na impressora selecionada."""
        sel=ti.selection()
        if not sel: messagebox.showwarning("Aviso","Selecione uma impressora!",parent=w); return
        vals=ti.item(sel[0],"values")
        alvo=next((dict(i) for i in cfg.get("impressoras",[]) if i.get("nome")==vals[0]), None) \
             or {"nome":vals[0],"nome_impressora":vals[2],"tipo":vals[3] if len(vals)>3 else "comum_win32"}
        if not (alvo.get("nome_impressora") or alvo.get("endereco_ip")):
            messagebox.showwarning("Aviso","Mapeie a Impressora Windows!\nClique DUPLO na linha.",parent=w); return
        r=_imprimir_com_roteamento(alvo,_bytes_teste_acentos())
        if r.get("ok"):
            messagebox.showinfo("Teste de acentos",
                "Teste enviado.\n\nVeja no papel qual bloco (1 a 6) saiu com os acentos CERTOS, "
                "de clique DUPLO nesta impressora e escolha essa opcao em 'Acentos'. Depois clique Aplicar.\n\n"
                "Se nenhum saiu certo, escolha 'ascii'.",parent=w)
        else: messagebox.showerror("Erro",r.get("erro",""),parent=w)

    def sync_e_recarregar():
        def _do():
            sincronizar_impressoras()
            def _ui():
                ti.delete(*ti.get_children())
                for imp in cfg.get("impressoras",[]):
                    tag="" if imp.get("nome_impressora") else "sem_map"
                    ti.insert("",tk.END,values=(imp.get("nome",""),imp.get("area",""),
                                                imp.get("nome_impressora",""),imp.get("tipo","comum_win32"),
                                                str(imp.get("colunas") or ""), str(imp.get("codepage") or ""),
                                                _fonte_rotulo(imp)),tags=(tag,))
                messagebox.showinfo("Sincronizado","Impressoras atualizadas do servidor!",parent=w)
            w.after(0,_ui)
        threading.Thread(target=_do, daemon=True).start()

    bi2=tk.Frame(f2,bg="#1e1e2e"); bi2.grid(row=4,column=0,columnspan=6,padx=10,pady=6,sticky="w")
    for tb,cb,cor in [("+ Adicionar",add_i,"#a6e3a1"),("Remover",rem_i,"#f38ba8"),
                       ("Testar Impressao",tst_i,"#cba6f7"),("Testar acentos",tst_acentos,"#94e2d5"),
                       ("Sincronizar",sync_e_recarregar,"#fab387"),
                       ("Ver Log",abrir_log,"#6c7086")]:
        tk.Button(bi2,text=tb,command=cb,bg=cor,fg="#1e1e2e",font=("Segoe UI",9,"bold"),
                  relief="flat",padx=10,pady=5,cursor="hand2").pack(side="left",padx=4)

    # (v5.81: os controles 'Fonte' e 'Papel' que ficavam aqui viraram a aba 'Impressao',
    #  junto com os ajustes novos — destaques por secao, estilo, corte, avanco e vias.)
    f2.columnconfigure(0,weight=1); f2.rowconfigure(1,weight=1)

    # ── ABA IMPRESSAO (v5.81): padrao de impressao do cupom. Cada controle salva NA HORA
    # (mesmo comportamento dos campos da aba Impressoras) e vale para o proximo pedido;
    # nenhum deles mexe no layout fiscal (DANFE), que tem regras proprias. ──
    fIm=ttk.Frame(nb); nb.add(fIm,text="Impressao")
    imroot=tk.Frame(fIm,bg="#1e1e2e"); imroot.pack(fill="both",expand=True)
    imroot.columnconfigure(0,weight=1); imroot.columnconfigure(1,weight=1)

    def _salva_im(chave, valor):
        """Salva um ajuste de impressao; None remove a chave (= comportamento padrao)."""
        if valor is None: cfg.pop(chave, None)
        else: cfg[chave] = valor
        salvar_config(cfg)
        log.info(f"[CONFIG] Impressao: {chave}={'padrao' if valor is None else valor}")

    def _grupo_im(titulo, dica, col, row, colspan=1):
        g=tk.LabelFrame(imroot,text=f" {titulo} ",bg="#1e1e2e",fg="#f9e2af",
                        font=("Segoe UI",9,"bold"),bd=1,relief="groove")
        g.grid(row=row,column=col,columnspan=colspan,padx=8,pady=5,sticky="nsew")
        if dica:
            tk.Label(g,text=dica,bg="#1e1e2e",fg="#6c7086",font=("Segoe UI",8),
                     wraplength=720 if colspan>1 else 350,justify="left").pack(anchor="w",padx=8,pady=(2,2))
        return g

    def _botoes_escolha(parent, opcoes, idx_atual, ao_escolher):
        """Fileira de botoes onde um fica aceso (selecionado)."""
        fr=tk.Frame(parent,bg="#1e1e2e"); fr.pack(anchor="w",padx=8,pady=(2,8))
        bts=[]
        def _pinta(i_sel):
            for i,b in enumerate(bts):
                b.config(bg="#89b4fa" if i==i_sel else "#45475a",
                         fg="#1e1e2e" if i==i_sel else "#cdd6f4")
        for i,nome in enumerate(opcoes):
            b=tk.Button(fr,text=nome,command=lambda i=i:(_pinta(i),ao_escolher(i)),
                        bg="#45475a",fg="#cdd6f4",font=("Segoe UI",9,"bold"),
                        relief="flat",padx=10,pady=4,cursor="hand2")
            b.pack(side="left",padx=2); bts.append(b)
        _pinta(idx_atual)

    # Tamanho base do cupom inteiro
    g1=_grupo_im("Tamanho da letra — cupom inteiro",
        "Vale do cabecalho ao rodape, em todas as impressoras (por impressora: aba Impressoras, campo Fonte). "
        "Media deixa a letra 2x mais ALTA sem perder colunas (opcao segura). Grande/Extra tambem alargam: "
        "sobra metade / um terco das colunas e nomes compridos quebram em mais linhas.", 0, 0, 2)
    _botoes_escolha(g1, _FONTE_NOMES, _fonte_cfg(), lambda i:_salva_im("font_size", i))

    # Destaques por secao
    g2=_grupo_im("Destaques por secao",
        "'Herda' segue o tamanho do cupom. No Nº do pedido, 'Auto' = Grande (como sempre foi).", 0, 1)
    for _k,_rot,_her in [("loja","Nome da loja","Herda"),("pedido","N. do pedido","Auto (Grande)"),
                         ("itens","Nome dos itens","Herda"),("total","TOTAL","Herda"),
                         ("rodape","Rodape","Herda")]:
        _frs=tk.Frame(g2,bg="#1e1e2e"); _frs.pack(anchor="w",padx=8,pady=1,fill="x")
        tk.Label(_frs,text=_rot,bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",9),width=14,anchor="w").pack(side="left")
        _cbx=ttk.Combobox(_frs,values=[_her]+list(_FONTE_NOMES),width=13,state="readonly")
        _vsec=_fonte_secao(_k)
        _cbx.set(_FONTE_NOMES[_vsec] if _vsec is not None else _her)
        def _muda_sec(e,k=_k,cb=None,her=""):
            sel=cb.get(); fs=dict(cfg.get("fonte_secoes") or {})
            if sel==her: fs.pop(k,None)
            else: fs[k]=list(_FONTE_NOMES).index(sel)
            _salva_im("fonte_secoes", fs or None)
        _cbx.bind("<<ComboboxSelected>>",lambda e,k=_k,cb=_cbx,her=_her:_muda_sec(e,k,cb,her))
        _cbx.pack(side="left",padx=4)
    tk.Frame(g2,bg="#1e1e2e",height=4).pack()

    # Estilo
    g3=_grupo_im("Estilo","",1,1)
    _neg_var=tk.BooleanVar(value=bool(cfg.get("negrito_cupom")))
    _escu_var=tk.BooleanVar(value=bool(cfg.get("mais_escuro")))
    for _var,_txt_cb,_chv in [(_neg_var,"Negrito no cupom inteiro (impressora que sai 'fraca')","negrito_cupom"),
                              (_escu_var,"Impressao mais escura (passada dupla)","mais_escuro")]:
        tk.Checkbutton(g3,text=_txt_cb,variable=_var,
            command=lambda v=_var,c=_chv:_salva_im(c, True if v.get() else None),
            bg="#1e1e2e",fg="#cdd6f4",selectcolor="#313244",activebackground="#1e1e2e",
            activeforeground="#cdd6f4",font=("Segoe UI",9)).pack(anchor="w",padx=8,pady=1)
    tk.Label(g3,text="Espaco entre linhas:",bg="#1e1e2e",fg="#cdd6f4",
             font=("Segoe UI",9)).pack(anchor="w",padx=8,pady=(6,0))
    _esp=cfg.get("espaco_linhas"); _esp_idx=_esp if _esp in (0,2) else 1
    _botoes_escolha(g3,["Compacto","Normal","Espacado"],_esp_idx,
                    lambda i:_salva_im("espaco_linhas", None if i==1 else i))

    # Papel, corte e vias
    g4=_grupo_im("Papel e corte",
        "Largura: quando o servidor nao manda a largura no job, o cupom cai em 48 colunas; loja com bobina "
        "de 58 mm ajusta aqui (ou por impressora, campo Colunas). Vias: so o cupom do cliente — comanda de "
        "setor e cupom fiscal saem sempre em 1 via.", 0, 2, 2)
    _fr_pap=tk.Frame(g4,bg="#1e1e2e"); _fr_pap.pack(anchor="w",fill="x")
    _LARGURAS_PAPEL=[("Auto",None),("58mm",32),("76mm",42),("80mm",48)]
    _pw_cfg=_colunas_validas(cfg.get("paper_width_cols"))
    _pw_idx=next((i for i,(_n,_c) in enumerate(_LARGURAS_PAPEL) if _c==_pw_cfg),0)
    tk.Label(_fr_pap,text="Largura da bobina",bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",9),
             width=18,anchor="w").pack(side="left",padx=(8,0))
    _botoes_escolha(_fr_pap,[n for n,_ in _LARGURAS_PAPEL],_pw_idx,
                    lambda i:_salva_im("paper_width_cols", _LARGURAS_PAPEL[i][1]))
    _fr_cor=tk.Frame(g4,bg="#1e1e2e"); _fr_cor.pack(anchor="w",fill="x")
    _CORTES=[("Corte total","total"),("Parcial (preso)","parcial"),("Sem corte","nao")]
    _c_cfg=str(cfg.get("corte","total"))
    _c_idx=next((i for i,(_n,_v) in enumerate(_CORTES) if _v==_c_cfg),0)
    tk.Label(_fr_cor,text="Corte do papel",bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",9),
             width=18,anchor="w").pack(side="left",padx=(8,0))
    _botoes_escolha(_fr_cor,[n for n,_ in _CORTES],_c_idx,
                    lambda i:_salva_im("corte", None if _CORTES[i][1]=="total" else _CORTES[i][1]))
    _fr_via=tk.Frame(g4,bg="#1e1e2e"); _fr_via.pack(anchor="w",fill="x")
    try: _v_cfg=max(1,min(3,int(cfg.get("vias_cupom") or 1)))
    except Exception: _v_cfg=1
    tk.Label(_fr_via,text="Vias do cupom",bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",9),
             width=18,anchor="w").pack(side="left",padx=(8,0))
    _botoes_escolha(_fr_via,["1 via","2 vias","3 vias"],_v_cfg-1,
                    lambda i:_salva_im("vias_cupom", None if i==0 else i+1))
    _fr_av=tk.Frame(g4,bg="#1e1e2e"); _fr_av.pack(anchor="w",fill="x",pady=(0,6))
    tk.Label(_fr_av,text="Avanco antes do corte",bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",9),
             width=18,anchor="w").pack(side="left",padx=(8,0))
    try: _av_cfg=max(0,min(8,int(cfg.get("avanco_linhas",5))))
    except Exception: _av_cfg=5
    _av_cbx=ttk.Combobox(_fr_av,values=[str(i) for i in range(9)],width=4,state="readonly")
    _av_cbx.set(str(_av_cfg)); _av_cbx.pack(side="left",padx=8)
    _av_cbx.bind("<<ComboboxSelected>>",
                 lambda e:_salva_im("avanco_linhas", None if _av_cbx.get()=="5" else int(_av_cbx.get())))
    tk.Label(_fr_av,text="linhas (5 = padrao)",bg="#1e1e2e",fg="#6c7086",font=("Segoe UI",8)).pack(side="left")

    # Cupom de teste com os ajustes atuais
    def imprimir_cupom_teste():
        """Imprime um pedido de EXEMPLO com os ajustes acima, na impressora do caixa
        (ou na 1a impressora mapeada). E o jeito de ver o resultado sem esperar pedido real."""
        _impt=_res_imp_por_rede("receipt") \
             or next((i for i in cfg.get("impressoras",[]) if i.get("nome_impressora") or i.get("endereco_ip")),None)
        if not _impt:
            messagebox.showwarning("Aviso","Nenhuma impressora mapeada!\nMapeie na aba Impressoras.",parent=w); return
        _ct={"type":"order","numero":"123","order_type":"delivery","customer_name":"Maria Souza",
             "company_name":(cfg.get("restaurant_name","") or "CUPOM DE TESTE").upper(),
             "created_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
             "itens":[{"nome":"Pizza Calabresa","tamanho":"G","qtd":1,"preco_cents":4500,
                       "adicionais":[{"nome":"Borda catupiry","preco_cents":800}],"notes":"sem cebola"},
                      {"nome":"Coca-Cola Lata","qtd":2,"preco_cents":600}],
             "subtotal_cents":5700,"delivery_fee_cents":700,"total_cents":6400,
             "payment_method":"pix","footer_message":"Obrigado pela preferencia!"}
        def _do_t():
            try:
                with _usar_codepage(_cp_da_impressora(_impt)), _usar_fonte(_fonte_da_impressora(_impt)):
                    _tx=_fmt(_ct,"order","receipt",_impt)
                    _r=_imprimir_com_roteamento(_impt,_tx)
                _nm=_impt.get("nome_impressora") or _impt.get("endereco_ip","")
                if _r.get("ok"):
                    w.after(0,lambda:messagebox.showinfo("OK",f"Cupom de teste enviado:\n{_nm}",parent=w))
                else:
                    w.after(0,lambda:messagebox.showerror("Erro",_r.get("erro",""),parent=w))
            except Exception as _e:
                log.error(f"[CONFIG] Cupom de teste falhou: {_e}", exc_info=True)
                w.after(0,lambda:messagebox.showerror("Erro",str(_e),parent=w))
        threading.Thread(target=_do_t,daemon=True).start()

    _fr_bt=tk.Frame(imroot,bg="#1e1e2e"); _fr_bt.grid(row=3,column=0,columnspan=2,sticky="w",padx=8,pady=(2,8))
    tk.Button(_fr_bt,text="Imprimir cupom de teste",command=imprimir_cupom_teste,bg="#cba6f7",fg="#1e1e2e",
              font=("Segoe UI",9,"bold"),relief="flat",padx=14,pady=6,cursor="hand2").pack(side="left",padx=4)
    tk.Label(_fr_bt,text="Sai na impressora do caixa, com os ajustes desta aba e da impressora.",
             bg="#1e1e2e",fg="#6c7086",font=("Segoe UI",8)).pack(side="left",padx=6)

    # BALANCAS
    f3=ttk.Frame(nb); nb.add(f3,text="Balancas")
    cob=("nome","tipo","conexao","baud"); tb2=ttk.Treeview(f3,columns=cob,show="headings",height=8)
    for col,lbl,cw in [("nome","Nome",100),("tipo","Tipo",90),("conexao","Porta/IP",260),("baud","Baud",80)]:
        tb2.heading(col,text=lbl); tb2.column(col,width=cw)
    sbb2=ttk.Scrollbar(f3,orient="vertical",command=tb2.yview); tb2.configure(yscrollcommand=sbb2.set)
    tb2.grid(row=0,column=0,columnspan=5,padx=10,pady=10,sticky="nsew"); sbb2.grid(row=0,column=5,pady=10,sticky="ns")
    for b in cfg.get("balancas",[]):
        con=f"{b.get('host','')}:{b.get('porta',8008)}" if b.get("tipo")=="tcp" else b.get("porta_com","")
        tb2.insert("",tk.END,values=(b.get("nome",""),b.get("tipo","serial"),con,b.get("baud",9600)))
    fb3=ttk.Frame(f3); fb3.grid(row=1,column=0,columnspan=6,padx=10,sticky="ew")
    ttk.Label(fb3,text="Nome:").grid(row=0,column=0,padx=4,pady=6)
    ebn=ttk.Entry(fb3,width=10); ebn.grid(row=0,column=1,padx=4)
    ttk.Label(fb3,text="Tipo:").grid(row=0,column=2,padx=4)
    ebt=ttk.Combobox(fb3,values=["serial","tcp","auto"],width=8); ebt.set("serial"); ebt.grid(row=0,column=3,padx=4)
    ttk.Label(fb3,text="Porta/IP:").grid(row=0,column=4,padx=4)
    ebc=ttk.Combobox(fb3,values=ps,width=20); ebc.grid(row=0,column=5,padx=4)
    ttk.Label(fb3,text="Baud:").grid(row=0,column=6,padx=4)
    ebb=ttk.Combobox(fb3,values=["4800","9600","19200","38400","115200"],width=8); ebb.set("4800"); ebb.grid(row=0,column=7,padx=4)
    def add_b():
        n=ebn.get().strip(); c=ebc.get().strip()
        if not n or not c: messagebox.showwarning("Aviso","Preencha Nome e Porta/IP!",parent=w); return
        tb2.insert("",tk.END,values=(n,ebt.get().strip(),c,ebb.get().strip()))
        ebn.delete(0,tk.END); ebc.set("")
    def rem_b():
        sel=tb2.selection()
        if sel: tb2.delete(sel[0])
    bb3=tk.Frame(f3,bg="#1e1e2e"); bb3.grid(row=2,column=0,columnspan=6,padx=10,pady=6,sticky="w")
    for tb,cb,cor in [("+ Adicionar",add_b,"#a6e3a1"),("Remover",rem_b,"#f38ba8")]:
        tk.Button(bb3,text=tb,command=cb,bg=cor,fg="#1e1e2e",font=("Segoe UI",9,"bold"),
                  relief="flat",padx=10,pady=5,cursor="hand2").pack(side="left",padx=4)

    # Painel de teste de balanca
    tf3=tk.Frame(f3,bg="#25253a",relief="ridge",bd=1)
    tf3.grid(row=3,column=0,columnspan=6,padx=10,pady=(4,0),sticky="ew")
    tk.Label(tf3,text="Teste de Balanca",bg="#25253a",fg="#cdd6f4",
             font=("Segoe UI",9,"bold")).grid(row=0,column=0,padx=12,pady=(8,4),sticky="w")

    # Linha de controles
    ctrl=tk.Frame(tf3,bg="#25253a"); ctrl.grid(row=1,column=0,columnspan=6,padx=8,pady=4,sticky="ew")
    tk.Label(ctrl,text="Porta:",bg="#25253a",fg="#a6adc8",font=("Segoe UI",9)).pack(side="left",padx=(4,2))
    porta_test=ttk.Combobox(ctrl,values=ps+["COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9"],width=8)
    if ps: porta_test.set(ps[0])
    else:  porta_test.set("COM8")
    porta_test.pack(side="left",padx=2)
    tk.Label(ctrl,text="Baud:",bg="#25253a",fg="#a6adc8",font=("Segoe UI",9)).pack(side="left",padx=(8,2))
    baud_test=ttk.Combobox(ctrl,values=["4800","9600","19200","2400","38400"],width=7)
    baud_test.set("4800"); baud_test.pack(side="left",padx=2)
    tk.Label(ctrl,text="Modo:",bg="#25253a",fg="#a6adc8",font=("Segoe UI",9)).pack(side="left",padx=(8,2))
    modo_test=ttk.Combobox(ctrl,values=["Auto","ASCII 8N1","7E1"],width=8)
    modo_test.set("Auto"); modo_test.pack(side="left",padx=2)

    # Display do peso
    peso_frame=tk.Frame(tf3,bg="#1a1a2e",relief="sunken",bd=2)
    peso_frame.grid(row=2,column=0,columnspan=6,padx=12,pady=6,sticky="ew")
    peso_var=tk.StringVar(value="--- kg")
    tk.Label(peso_frame,textvariable=peso_var,bg="#1a1a2e",fg="#a6e3a1",
             font=("Segoe UI",22,"bold")).pack(side="left",padx=16,pady=8)
    status_var2=tk.StringVar(value="Aguardando...")
    status_lbl2=tk.Label(peso_frame,textvariable=status_var2,bg="#1a1a2e",fg="#6c7086",
                          font=("Segoe UI",9))
    status_lbl2.pack(side="left",padx=8)
    leituras_var=tk.StringVar(value="Leituras: 0")
    tk.Label(peso_frame,textvariable=leituras_var,bg="#1a1a2e",fg="#45475a",
             font=("Segoe UI",8)).pack(side="right",padx=12)

    # Log de leituras
    log_frame=tk.Frame(tf3,bg="#0d0d1a"); log_frame.grid(row=3,column=0,columnspan=6,padx=12,pady=(0,8),sticky="ew")
    log_b=tk.Text(log_frame,bg="#0d0d1a",fg="#a6e3a1",font=("Consolas",8),
                  height=4,relief="flat",state="disabled",wrap="word")
    log_b.pack(fill="x",padx=2,pady=2)

    _teste_ativo=[False]
    _serial_obj=[None]
    _leituras=[0]

    def log_b_add(msg, cor="#a6e3a1"):
        log_b.config(state="normal")
        log_b.insert("end",f"{msg}\n")
        log_b.see("end")
        log_b.config(state="disabled")

    def iniciar_teste():
        import serial, re, threading
        porta=porta_test.get().strip()
        baud=int(baud_test.get().strip())
        modo=modo_test.get()
        if not porta:
            messagebox.showwarning("Aviso","Selecione a porta!",parent=w); return
        if _teste_ativo[0]:
            _teste_ativo[0]=False
            if _serial_obj[0]:
                try: _serial_obj[0].close()
                except: pass
            btn_teste.config(text="Iniciar Teste",bg="#5b8dee")
            status_var2.set("Parado")
            return

        _teste_ativo[0]=True
        _leituras[0]=0
        btn_teste.config(text="Parar Teste",bg="#f38ba8")
        log_b_add(f"Conectando {porta} @ {baud} baud modo={modo}...")

        def _run():
            import re
            modos_tentar=[]
            if modo=="Auto":
                modos_tentar=[
                    (4800,8,"N",1,"ascii"),
                    (9600,8,"N",1,"ascii"),
                    (9600,7,"E",1,"7e1"),
                    (4800,7,"E",1,"7e1"),
                ]
            elif modo=="ASCII 8N1":
                modos_tentar=[(baud,8,"N",1,"ascii")]
            else:
                modos_tentar=[(baud,7,"E",1,"7e1")]

            s=None
            for bd,bs,par,sb,tipo in modos_tentar:
                try:
                    s=serial.Serial(porta,baudrate=bd,bytesize=bs,
                                    parity=par,stopbits=sb,timeout=1)
                    import time; time.sleep(0.3); s.flushInput()
                    dados=s.read(32)
                    if dados:
                        _serial_obj[0]=s
                        w.after(0,lambda bd=bd,tipo=tipo: (
                            log_b_add(f"Conectado! {bd} baud {tipo}"),
                            status_var2.set(f"Conectado {bd}b")
                        ))
                        break
                    s.close(); s=None
                except Exception as e:
                    w.after(0,lambda e=e: log_b_add(f"Erro: {e}","#f38ba8"))
                    if s:
                        try: s.close()
                        except: pass
                    s=None

            if not s:
                w.after(0,lambda: (
                    log_b_add("Nao foi possivel conectar!","#f38ba8"),
                    status_var2.set("Erro de conexao"),
                    btn_teste.config(text="Iniciar Teste",bg="#5b8dee")
                ))
                _teste_ativo[0]=False
                return

            buf=b""
            import time
            while _teste_ativo[0]:
                try:
                    chunk=s.read(32)
                    if not chunk: continue
                    buf+=chunk
                    if len(buf)>512: buf=buf[-256:]
                    # Tenta ler peso
                    texto=buf.decode("ascii",errors="ignore")
                    matches=re.findall(r"([0-9 ]{2}[.,][0-9]{3})",texto)
                    if not matches:
                        matches=re.findall(r"(\d{1,3}[.,]\d{3})",texto)
                    if matches:
                        peso_str=matches[-1].strip().replace(",",".")
                        try:
                            peso=float(peso_str)
                            if 0<=peso<=500:
                                _leituras[0]+=1
                                n=_leituras[0]
                                w.after(0,lambda p=peso,n=n: (
                                    peso_var.set(f"{p:.3f} kg"),
                                    leituras_var.set(f"Leituras: {n}"),
                                    status_var2.set("Lendo..."),
                                    status_lbl2.config(fg="#a6e3a1")
                                ))
                                buf=b""
                        except: pass
                except Exception as e:
                    if _teste_ativo[0]:
                        w.after(0,lambda e=e: (
                            log_b_add(f"Erro leitura: {e}","#f38ba8"),
                            status_var2.set("Erro")
                        ))
                    break

            try: s.close()
            except: pass
            _serial_obj[0]=None

        threading.Thread(target=_run,daemon=True).start()

    def escanear_auto():
        import serial.tools.list_ports, threading
        log_b_add("Escaneando portas COM...")
        def _scan():
            portas_encontradas=[]
            try:
                for p in serial.tools.list_ports.comports():
                    portas_encontradas.append(p.device)
            except: pass
            if portas_encontradas:
                w.after(0,lambda: (
                    porta_test.config(values=portas_encontradas),
                    porta_test.set(portas_encontradas[0]),
                    log_b_add(f"Portas: {portas_encontradas}"),
                ))
            else:
                w.after(0,lambda: log_b_add("Nenhuma porta COM encontrada","#f38ba8"))
        threading.Thread(target=_scan,daemon=True).start()

    # Botoes de teste
    bb_test=tk.Frame(tf3,bg="#25253a"); bb_test.grid(row=4,column=0,columnspan=6,padx=8,pady=(0,8),sticky="w")
    btn_teste=tk.Button(bb_test,text="Iniciar Teste",command=iniciar_teste,
                        bg="#5b8dee",fg="#1e1e2e",font=("Segoe UI",9,"bold"),
                        relief="flat",padx=12,pady=6,cursor="hand2")
    btn_teste.pack(side="left",padx=4)
    tk.Button(bb_test,text="Escanear Portas",command=escanear_auto,
              bg="#313244",fg="#cdd6f4",font=("Segoe UI",9,"bold"),
              relief="flat",padx=10,pady=6,cursor="hand2").pack(side="left",padx=4)
    tk.Button(bb_test,text="Usar esta config",
              command=lambda: (
                  ebc.set(porta_test.get()),
                  ebb.set(baud_test.get()),
                  log_b_add(f"Config aplicada: {porta_test.get()} @ {baud_test.get()}")
              ),
              bg="#a6e3a1",fg="#1e1e2e",font=("Segoe UI",9,"bold"),
              relief="flat",padx=10,pady=6,cursor="hand2").pack(side="left",padx=4)

    f3.columnconfigure(0,weight=1); f3.rowconfigure(0,weight=1)

    # SELFCHECKOUT
    f_sco=ttk.Frame(nb); nb.add(f_sco,text="Selfcheckout")

    tk.Label(f_sco,text="Configuracao do Selfcheckout por Balanca",
             font=("Segoe UI",10,"bold"),bg="#1e1e2e",fg="#5b8dee").grid(
             row=0,column=0,columnspan=4,padx=12,pady=(12,4),sticky="w")

    tk.Label(f_sco,text="Ativa automaticamente a impressao quando peso estavel detectado.",
             font=("Segoe UI",8),bg="#1e1e2e",fg="#6c7086").grid(
             row=1,column=0,columnspan=4,padx=12,pady=(0,8),sticky="w")

    # Campos de config
    campos_sco=[
        ("Porta COM:",    "sco_porta",    "COM8",  14),
        ("Baud Rate:",    "sco_baud",     "4800",  10),
        ("Tara (kg):",    "sco_tara",     "0.000", 10),
        ("Peso minimo (kg):","sco_min",   "0.050", 10),
        ("Estabilidade (s):","sco_estab", "1.5",   10),
        ("Cooldown (s):", "sco_cool",     "3.0",   10),
        ("Impressora:",   "sco_imp",      "",      20),
    ]
    sco_vars={}
    for row,(label,key,default,width) in enumerate(campos_sco):
        tk.Label(f_sco,text=label,bg="#1e1e2e",fg="#a6adc8",
                 font=("Segoe UI",9)).grid(row=row+2,column=0,padx=(12,4),pady=3,sticky="e")
        val=cfg.get("selfcheckout",{}).get(key,default)
        var=tk.StringVar(value=str(val))
        sco_vars[key]=var
        if key=="sco_imp":
            cb=ttk.Combobox(f_sco,textvariable=var,
                           values=[i.get("nome_impressora","") for i in cfg.get("impressoras",[]) if i.get("nome_impressora")],
                           width=width)
            cb.grid(row=row+2,column=1,padx=4,pady=3,sticky="w")
        elif key=="sco_porta":
            cb=ttk.Combobox(f_sco,textvariable=var,
                           values=ps+["COM8","COM9","COM1","COM2","COM3"],width=width)
            cb.grid(row=row+2,column=1,padx=4,pady=3,sticky="w")
        else:
            ttk.Entry(f_sco,textvariable=var,width=width).grid(
                row=row+2,column=1,padx=4,pady=3,sticky="w")

    # Toggle ativo
    sco_ativo_var=tk.BooleanVar(value=cfg.get("selfcheckout",{}).get("ativo",False))
    tk.Checkbutton(f_sco,text="Selfcheckout ATIVO",variable=sco_ativo_var,
                   bg="#1e1e2e",fg="#cdd6f4",selectcolor="#313244",
                   font=("Segoe UI",10,"bold"),activebackground="#1e1e2e").grid(
                   row=9,column=0,columnspan=2,padx=12,pady=8,sticky="w")

    # Status em tempo real
    sco_status_frame=tk.Frame(f_sco,bg="#25253a"); sco_status_frame.grid(
        row=10,column=0,columnspan=4,padx=12,pady=4,sticky="ew")
    sco_peso_var=tk.StringVar(value="--- kg")
    sco_estado_var=tk.StringVar(value="Parado")
    sco_total_var=tk.StringVar(value="0 impressoes")
    tk.Label(sco_status_frame,textvariable=sco_peso_var,bg="#25253a",fg="#a6e3a1",
             font=("Segoe UI",18,"bold")).pack(side="left",padx=12,pady=8)
    tk.Label(sco_status_frame,textvariable=sco_estado_var,bg="#25253a",fg="#f9e2af",
             font=("Segoe UI",10)).pack(side="left",padx=8)
    tk.Label(sco_status_frame,textvariable=sco_total_var,bg="#25253a",fg="#45475a",
             font=("Segoe UI",9)).pack(side="right",padx=12)

    def _sco_status_cb(estado,peso,msg):
        estados={
            "aguardando": "Aguardando prato...",
            "pesando":    "Pesando...",
            "estavel":    "Peso estavel!",
            "imprimindo": "Imprimindo...",
            "cooldown":   "Retire o prato",
            "reconectando":"Reconectando...",
            "erro":       "Erro de conexao",
        }
        cores={
            "aguardando": "#6c7086",
            "pesando":    "#f9e2af",
            "estavel":    "#a6e3a1",
            "imprimindo": "#5b8dee",
            "cooldown":   "#fab387",
            "reconectando":"#f9e2af",
            "erro":       "#f38ba8",
        }
        try:
            sco_peso_var.set(f"{peso:.3f} kg")
            sco_estado_var.set(estados.get(estado,estado))
            if HAS_SCO and mod_sco.get_selfcheckout():
                sco_total_var.set(f"{mod_sco.get_selfcheckout().total_impressos} impressoes")
        except: pass

    def btn_tarar():
        if HAS_SCO and mod_sco.get_selfcheckout():
            tara=mod_sco.get_selfcheckout().tarar_agora()
            sco_vars["sco_tara"].set(f"{tara:.3f}")
            messagebox.showinfo("Tara",f"Tara definida: {tara:.3f} kg",parent=w)
        else:
            messagebox.showwarning("Aviso","Selfcheckout nao esta ativo!",parent=w)

    bb_sco=tk.Frame(f_sco,bg="#1e1e2e"); bb_sco.grid(
        row=11,column=0,columnspan=4,padx=12,pady=6,sticky="w")
    tk.Button(bb_sco,text="Tarar agora (peso atual = tara)",command=btn_tarar,
              bg="#f9e2af",fg="#1e1e2e",font=("Segoe UI",9,"bold"),
              relief="flat",padx=12,pady=6,cursor="hand2").pack(side="left",padx=4)

    f_sco.columnconfigure(1,weight=1)

    # AGENTES ONLINE
    f_ag=ttk.Frame(nb); nb.add(f_ag,text="Agentes")
    tk.Label(f_ag,text="Agentes conectados ao mesmo restaurante",
             bg="#313244",fg="#cdd6f4",font=("Segoe UI",9,"bold"),anchor="w",padx=8,pady=4
             ).pack(fill="x",padx=10,pady=(10,2))
    cols_ag=("maquina","areas","status","ultimo")
    tag_ag=ttk.Treeview(f_ag,columns=cols_ag,show="headings",height=8)
    for col,lbl,cw in [("maquina","Maquina",180),("areas","Area(s)",160),("status","Status",80),("ultimo","Ultimo heartbeat",160)]:
        tag_ag.heading(col,text=lbl); tag_ag.column(col,width=cw,anchor="w")
    tag_ag.tag_configure("online",  foreground="#a6e3a1")
    tag_ag.tag_configure("recente", foreground="#f9e2af")
    tag_ag.tag_configure("offline", foreground="#f38ba8")
    tag_ag.pack(fill="both",expand=True,padx=10,pady=4)

    status_ag_var = tk.StringVar(value="Carregando...")
    tk.Label(f_ag, textvariable=status_ag_var, bg="#1e1e2e", fg="#6c7086",
             font=("Segoe UI",8)).pack(anchor="w", padx=12)

    def _popular_tabela(lista):
        tag_ag.delete(*tag_ag.get_children())
        agora = time.time()
        import datetime
        for ag in lista:
            nome = ag.get("device_name") or "Agente"
            areas_ag = ", ".join(ag.get("covered_areas") or []) or "todas"
            hb = ag.get("last_heartbeat_at","")
            try:
                ts = datetime.datetime.fromisoformat(hb.replace("Z","+00:00"))
                diff = agora - ts.timestamp()
                if diff < 35:
                    status_txt = "Online"; tag = "online"
                elif diff < 120:
                    status_txt = "Recente"; tag = "recente"
                else:
                    status_txt = "Offline"; tag = "offline"
                hb_fmt = time.strftime("%H:%M:%S", time.localtime(ts.timestamp()))
            except Exception:
                status_txt = "?"; tag = "recente"; hb_fmt = hb[:19]
            if ag.get("device_name","") == DEVICE_NAME:
                nome = nome + " (este)"
            tag_ag.insert("","end",values=(nome, areas_ag, status_txt, hb_fmt),tags=(tag,))
        status_ag_var.set(f"Ultima atualizacao: {time.strftime('%H:%M:%S')}  |  {len(lista)} agente(s)")

    def _atualizar_agentes():
        if not w.winfo_exists(): return
        status_ag_var.set("Buscando...")
        def _fetch():
            try:
                imps = cfg.get("impressoras",[])
                areas = list(set([i.get("area","").strip().lower() for i in imps if i.get("area","").strip() and i.get("nome_impressora")]))
                payload = {"action":"poll","device_name":DEVICE_NAME,"device_fingerprint":DEVICE_FINGERPRINT}
                if areas: payload["areas"] = areas
                resp, s = _post(f"{SUPABASE_URL}/functions/v1/agent-unified-poll", payload, cfg.get("token",""))
                if s == 200 and resp:
                    lista = resp.get("agents_online", [])
                    if not w.winfo_exists(): return
                    _root.after(0, lambda: _popular_tabela(lista))
                else:
                    erro = resp.get("error","") if isinstance(resp,dict) else str(resp)[:80]
                    if not w.winfo_exists(): return
                    _root.after(0, lambda: status_ag_var.set(f"Erro {s}: {erro}"))
            except Exception as ex:
                if not w.winfo_exists(): return
                _root.after(0, lambda: status_ag_var.set(f"Excecao: {ex}"))
        threading.Thread(target=_fetch, daemon=True).start()

    def _auto_refresh_agentes():
        if not w.winfo_exists(): return
        _atualizar_agentes()
        w.after(15000, _auto_refresh_agentes)

    # Popula imediatamente com dados já em memória, depois inicia refresh automático
    _popular_tabela(_agents_online)
    w.after(100, _auto_refresh_agentes)

    tk.Button(f_ag,text="Atualizar",command=_atualizar_agentes,
              bg="#89b4fa",fg="#1e1e2e",font=("Segoe UI",9,"bold"),
              relief="flat",padx=12,pady=4,cursor="hand2").pack(pady=4)

    # INICIALIZACAO
    f4=ttk.Frame(nb); nb.add(f4,text="Inicializacao")
    def esta_st():
        try:
            k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_READ)
            winreg.QueryValueEx(k,"AgenteLocal"); winreg.CloseKey(k); return True
        except: return False
    def tog():
        try:
            k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE)
            if esta_st():
                winreg.DeleteValue(k,"AgenteLocal"); stb.config(text="Ativar inicio automatico")
                messagebox.showinfo("OK","Removido!",parent=w)
            else:
                exe=(str(Path(sys.executable).parent/"AgenteLocal.exe") if getattr(sys,'frozen',False) else f'"{sys.executable}" "{__file__}"')
                winreg.SetValueEx(k,"AgenteLocal",0,winreg.REG_SZ,exe); stb.config(text="Desativar inicio automatico")
                messagebox.showinfo("OK","Iniciara com o Windows!",parent=w)
            winreg.CloseKey(k)
        except Exception as e: messagebox.showerror("Erro",str(e),parent=w)
    def atl():
        try:
            # Tenta desktop local e OneDrive
            desktops = [
                Path.home()/"Desktop",
                Path.home()/"OneDrive"/"Desktop",
                Path(os.environ.get("USERPROFILE",""))/"Desktop",
                Path(os.environ.get("USERPROFILE",""))/"OneDrive"/"Desktop",
            ]
            d = next((p for p in desktops if p.exists()), Path.home()/"Desktop")
            script_dir = Path(__file__).resolve().parent if not getattr(sys,"frozen",False) else Path(sys.executable).parent
            if getattr(sys,"frozen",False):
                exe = str(Path(sys.executable).resolve())
            else:
                possivel = [script_dir / "dist" / "AgenteLocal.exe", script_dir / "AgenteLocal.exe"]
                exe_path = next((p for p in possivel if p.exists()), None)
                if exe_path:
                    exe = str(exe_path)
                else:
                    messagebox.showerror("Erro", f"AgenteLocal.exe nao encontrado!\nGere o executavel primeiro.", parent=w)
                    return
            # Prefere OneDrive Desktop se existir
            onedrive_desk = Path(os.environ.get("USERPROFILE","")) / "OneDrive" / "Desktop"
            d = onedrive_desk if onedrive_desk.exists() else d
            atalho = str(d / "Agente Local.lnk")
            ps = f'''$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut("{atalho}"); $s.TargetPath="{exe}"; $s.WorkingDirectory="{Path(exe).parent}"; $s.Description="Agente Local MIA"; $s.Save()'''
            r = subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command", ps],
                               capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
            if r.returncode == 0 and Path(atalho).exists():
                messagebox.showinfo("OK", f"Atalho criado em:\n{atalho}", parent=w)
            else:
                messagebox.showerror("Erro", f"Nao foi possivel criar o atalho.\n{r.stderr}", parent=w)
        except Exception as e: messagebox.showerror("Erro", str(e), parent=w)
    tk.Label(f4,text="Inicializacao do Windows",bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",13,"bold")).pack(pady=30)
    ts="Desativar inicio automatico" if esta_st() else "Ativar inicio automatico"
    stb=tk.Button(f4,text=ts,command=tog,bg="#89b4fa",fg="#1e1e2e",font=("Segoe UI",11,"bold"),
                  relief="flat",padx=20,pady=10,cursor="hand2",width=30); stb.pack(pady=8)
    tk.Button(f4,text="Criar atalho na Area de Trabalho",command=atl,
              bg="#a6e3a1",fg="#1e1e2e",font=("Segoe UI",10,"bold"),relief="flat",padx=15,pady=8,cursor="hand2",width=30).pack(pady=8)
    tk.Button(f4,text="Abrir Log",command=abrir_log,
              bg="#fab387",fg="#1e1e2e",font=("Segoe UI",10,"bold"),relief="flat",padx=15,pady=8,cursor="hand2",width=30).pack(pady=8)

    # RODAPE
    def salvar(silencioso=False):
        global cfg
        cfg["token"]=tv.get().strip(); cfg["poll_interval"]=int(pv.get().strip() or "3")
        # Index das impressoras atuais para preservar printer_type
        imps_orig = {i.get("nome","").strip().lower(): i for i in cfg.get("impressoras",[])}
        imps=[]
        for item in ti.get_children():
            v=ti.item(item,"values")
            nome=v[0]; area=v[1]; nome_win=v[2]; tipo=v[3]
            colunas=_colunas_validas(v[4]) if len(v)>4 else None   # v5.79: largura do papel por impressora
            codepage=_normaliza_cp(v[5]) if len(v)>5 else ""        # v5.80: tabela de acentos por impressora
            orig = imps_orig.get(nome.strip().lower(), {})
            # printer_type vem do servidor (via config original), area e derivada dele
            printer_type = orig.get("printer_type") or {"caixa":"receipt","cozinha":"kitchen","bar":"bar"}.get(area.strip().lower(), "receipt")
            area_correta = {"receipt":"caixa","kitchen":"cozinha","bar":"bar"}.get(printer_type, area)
            _imp_novo={"nome":nome,"area":area_correta,"nome_impressora":nome_win,"tipo":tipo,"modo":"texto","printer_type":printer_type}
            if colunas: _imp_novo["colunas"]=colunas
            if codepage: _imp_novo["codepage"]=codepage
            imps.append(_imp_novo)
        cfg["impressoras"]=imps; bals=[]
        for item in tb2.get_children():
            v=tb2.item(item,"values"); n2,t2,c3,b2=v[0],v[1],v[2],v[3]
            if t2=="tcp" and ":" in c3:
                h2,p2=c3.split(":",1); bals.append({"nome":n2,"tipo":"tcp","host":h2,"porta":int(p2)})
            else: bals.append({"nome":n2,"tipo":t2,"porta_com":c3,"baud":int(b2)})
        cfg["balancas"]=bals
        # Salva config do selfcheckout
        sco_cfg={
            "ativo":  sco_ativo_var.get(),
            "sco_porta":  sco_vars["sco_porta"].get(),
            "sco_baud":   sco_vars["sco_baud"].get(),
            "sco_tara":   sco_vars["sco_tara"].get(),
            "sco_min":    sco_vars["sco_min"].get(),
            "sco_estab":  sco_vars["sco_estab"].get(),
            "sco_cool":   sco_vars["sco_cool"].get(),
            "sco_imp":    sco_vars["sco_imp"].get(),
        }
        cfg["selfcheckout"]=sco_cfg
        # Reinicia selfcheckout se ativo
        if HAS_SCO:
            mod_sco.parar_selfcheckout()
            if sco_cfg["ativo"]:
                cfg_bal={
                    "porta":          sco_cfg["sco_porta"],
                    "baudrate":       int(sco_cfg["sco_baud"] or 4800),
                    "bytesize":       8,"parity":"N","stopbits":1,
                    "tara_kg":        float(sco_cfg["sco_tara"] or 0),
                    "peso_minimo_kg": float(sco_cfg["sco_min"] or 0.05),
                    "estabilidade_s": float(sco_cfg["sco_estab"] or 1.5),
                    "cooldown_s":     float(sco_cfg["sco_cool"] or 3.0),
                    "nome":           "Selfcheckout",
                }
                mod_sco.iniciar_selfcheckout(
                    cfg_bal, SUPABASE_URL,
                    cfg.get("token",""), cfg.get("restaurant_id",""),
                    sco_cfg["sco_imp"], _sco_status_cb
                )
                log.info("[SCO] Selfcheckout iniciado apos salvar config")
        salvar_config(cfg)
        if not silencioso:
            messagebox.showinfo("Salvo!","Configuracoes salvas!\nReinicie o agente para aplicar.",parent=w)
        w.destroy()

    # Botoes do rodape — rod ja foi criado e empacotado no topo da funcao
    tk.Button(rod,text="Salvar Configuracoes",command=salvar,bg="#89b4fa",fg="#1e1e2e",
              font=("Segoe UI",11,"bold"),relief="flat",padx=20,pady=12,cursor="hand2").pack(side="right",padx=10,pady=8)
    tk.Button(rod,text="Cancelar",command=w.destroy,bg="#45475a",fg="white",
              font=("Segoe UI",10),relief="flat",padx=15,pady=12,cursor="hand2").pack(side="right",pady=8)
    tk.Button(rod,text="Log em tempo real",command=abrir_log,bg="#fab387",fg="#1e1e2e",
              font=("Segoe UI",10,"bold"),relief="flat",padx=15,pady=12,cursor="hand2").pack(side="left",padx=10,pady=8)
    tk.Button(rod,text="Status / Impressoes / Falhas",command=abrir_dashboard,bg="#a6e3a1",fg="#1e1e2e",
              font=("Segoe UI",10,"bold"),relief="flat",padx=15,pady=12,cursor="hand2").pack(side="left",padx=4,pady=8)

    # AUTO-SAVE: salva configuracoes automaticamente quando o usuario fecha a janela (X no canto)
    def _on_close_window():
        try:
            salvar(silencioso=True)
            log.info("[CONFIG] Configuracoes salvas automaticamente ao fechar")
        except Exception as e:
            log.warning(f"[CONFIG] Erro ao salvar ao fechar: {e}")
            w.destroy()
    w.protocol("WM_DELETE_WINDOW", _on_close_window)

def reiniciar_app():
    log.info("Reiniciando agente...")
    # Sempre usa AgenteLocal.exe na pasta do executavel, nunca sys.executable
    # (sys.executable no PyInstaller aponta para pasta temp _MEH* que some apos exit)
    if getattr(sys, 'frozen', False):
        exe = str(BASE_DIR / "AgenteLocal.exe")
    else:
        exe = sys.executable
    bat = BASE_DIR / "restart.bat"
    bat.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{exe}\'"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8"
    )
    _popen_bat_orfao(bat)   # fora da arvore do agente: o bat sobrevive ao proprio taskkill /T
    os._exit(0)


def reparar_agente():
    """Botao 'Reparar' da GUI. Faz o mesmo que o CORRIGIR_AGENTE.bat, de dentro do app:
    normaliza o nome do exe, limpa versionados orfaos, mata TODAS as instancias travadas
    (por curinga) e reabre uma unica pelo nome fixo. Resolve 'clico e nao abre' sem CMD."""
    log.info("[REPARO] Reparo manual solicitado pela GUI")
    try:
        _auto_reparo_boot()  # normaliza nome + remove versionados orfaos
    except Exception as e:
        log.warning(f"[REPARO] auto_reparo falhou: {e}")
    if getattr(sys, 'frozen', False):
        exe = str(BASE_DIR / "AgenteLocal.exe")
    else:
        exe = sys.executable
    bat = BASE_DIR / "reparo.bat"
    # Mata tudo por curinga (pega versionados travados), espera e reabre pelo nome fixo.
    bat.write_text(
        "@echo off\r\n"
        'taskkill /F /FI "IMAGENAME eq AgenteLocal*" /T >nul 2>&1\r\n'
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{exe}\'"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8"
    )
    _popen_bat_orfao(bat)   # fora da arvore do agente: o bat sobrevive ao proprio taskkill /T
    os._exit(0)


def verificar_atualizacao():
    # Deprecated: replaced by checar_atualizacao() inside loop_poll()
    return
    try:
        import urllib.request, json, os, sys, tempfile
        # Tenta pegar version.json
        url = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/version.json"
        req = urllib.request.Request(url)
        if GITHUB_TOKEN:
            req.add_header("Authorization", f"token {GITHUB_TOKEN}")
        
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as r:
            data = json.loads(r.read())

        nova = data.get("version","")
        if nova and nova != VERSION:
            log.info(f"[UPDATE] Nova versao disponivel: {nova} (atual: {VERSION})")
            exe_url = data.get("url","")
            if exe_url:
                log.info(f"[UPDATE] Baixando {exe_url}...")
                
                # Handler customizado para remover Authorization em caso de redirecionamento (S3/GitHub Assets)
                class RedirectHandler(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
                        if "github" not in newurl.lower():
                            if "Authorization" in new_req.headers:
                                del new_req.headers["Authorization"]
                        return new_req

                opener = urllib.request.build_opener(RedirectHandler)
                req_exe = urllib.request.Request(exe_url)
                if GITHUB_TOKEN and "github.com" in exe_url:
                    req_exe.add_header("Authorization", f"token {GITHUB_TOKEN}")
                
                tmp = tempfile.mktemp(suffix=".exe")
                with opener.open(req_exe, timeout=60) as r2:
                    with open(tmp, "wb") as f2:
                        f2.write(r2.read())
                
                # Script de substituicao e reinicio
                bat = tempfile.mktemp(suffix=".bat")
                exe_name = "AgenteLocal.exe"
                if getattr(sys, "frozen", False):
                   exe_atual = os.path.join(os.path.dirname(sys.executable), exe_name)
                else:
                   exe_atual = os.path.join(os.getcwd(), "dist", exe_name)

                with open(bat, "w") as fb:
                    fb.write(f"@echo off\ntimeout /t 2 /nobreak >nul\nmove /y \"{tmp}\" \"{exe_atual}\"\nstart \"\" \"{exe_atual}\"\ndel \"%~f0\"\n")
                
                import subprocess
                subprocess.Popen(["cmd","/c",bat], creationflags=0x08000000)
                log.info("[UPDATE] Atualizacao aplicada! Reiniciando...")
                os._exit(0)
        else:
            log.info(f"[UPDATE] Versao atual {VERSION} ja e a mais recente")
    except Exception as e:
        log.error(f"[UPDATE] Erro ao verificar atualizacao: {e}")

async def loop_update():
    await asyncio.sleep(30)  # aguarda 30s antes da primeira verificacao
    while True:
        try: verificar_atualizacao()
        except Exception as e: log.error(f"[UPDATE] {e}")
        await asyncio.sleep(6 * 3600)  # verifica a cada 6 horas





import pystray
from PIL import Image as PILImage

def _criar_icone(icon=None):
    img = PILImage.new("RGBA", (64,64), (0,0,0,0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.ellipse([4,4,60,60], fill="#5b8dee")
    d.rectangle([20,18,44,46], fill="white")
    d.rectangle([20,18,44,26], fill="#1a1a2e")
    return img

def iniciar_tray():
    global _tray_icon
    try:
        img = _criar_icone()
    except:
        img = PILImage.new("RGBA", (64,64), "#5b8dee")

    menu = pystray.Menu(
        pystray.MenuItem(
            "Status",
            lambda icon, item: _gui_queue.put("dashboard"),
            default=True
        ),
        pystray.MenuItem(
            "Configuracoes",
            lambda icon, item: _gui_queue.put("config")
        ),
        pystray.MenuItem(
            "Ver Log",
            lambda icon, item: _gui_queue.put("log")
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Reiniciar",
            lambda icon, item: _gui_queue.put("reiniciar")
        ),
        pystray.MenuItem(
            "Sair",
            lambda icon, item: _gui_queue.put("sair")
        ),
    )

    rest = cfg.get("restaurant_name","Agente Local")
    _tray_icon = pystray.Icon(
        "AgenteLocal",
        img,
        f"Concentrador MIA - {rest}",
        menu
    )
    _tray_icon.run()

def _check():
    # v5.78: pedido "abrir janela" de uma copia nova (lojista clicou no .exe com o agente ja
    # rodando na bandeja). Consumir = apagar o arquivo (a copia nova espera isso p/ encerrar).
    try:
        if SHOW_FLAG.exists():
            try: SHOW_FLAG.unlink()
            except Exception: pass
            log.info("[GUI] Pedido de abrir janela recebido (clique no .exe); mostrando painel de Status")
            abrir_dashboard()
    except Exception as e:
        log.debug(f"[GUI] SHOW_FLAG: {e}")
    try:
        cmd = _gui_queue.get_nowait()
        try:
            if   cmd == "config":    abrir_config()
            elif cmd == "dashboard": abrir_dashboard()
            elif cmd == "log":       abrir_log()
            elif cmd == "reiniciar": reiniciar_app()
            elif cmd == "sair":
                try:
                    if _meu_registro_path and _meu_registro_path.exists():
                        _meu_registro_path.unlink()  # remove meu registro de instancia
                except Exception:
                    pass
                os._exit(0)
        except Exception as e:
            log.error(f"[GUI] Erro ao abrir '{cmd}': {e}", exc_info=True)
    except queue.Empty:
        pass
    _root.after(300, _check)

def _matar_outras_instancias():
    """Mata TODAS as outras instancias AgenteLocal* (inclui nomes versionados travados,
    ex: AgenteLocal_5.54.exe), EXCETO o processo atual. Usa taskkill por CURINGA no filtro
    /FI (WMIC foi descontinuado e retorna 'Consulta invalida' no Windows novo do cliente).
    Retorna quantos matou (aproximado)."""
    meu_pid = os.getpid()
    mortos = 0
    try:
        # Lista PIDs de processos AgenteLocal* via tasklist CSV (robusto, sem WMIC)
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq AgenteLocal*", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=6, creationflags=_NO_WINDOW
        )
        for linha in (r.stdout or "").splitlines():
            partes = [p.strip().strip('"') for p in linha.split('","')]
            partes = [p.strip('"') for p in partes]
            if len(partes) >= 2:
                try:
                    pid_outro = int(partes[1])
                except ValueError:
                    continue
                if pid_outro != meu_pid and pid_outro > 0:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid_outro), "/T"],
                                   capture_output=True, timeout=5, creationflags=_NO_WINDOW)
                    mortos += 1
                    log.info(f"[STARTUP] Matou instancia paralela PID={pid_outro} ({partes[0]})")
    except Exception as e:
        log.debug(f"[STARTUP] Erro ao listar/matar instancias: {e}")
    return mortos


def _escrever_heartbeat():
    """Escreve o arquivo .boot_ok que o bat de update usa para confirmar que o exe novo
    subiu (senao faz rollback). Escrito cedo no boot, antes de qualquer coisa que possa
    demorar/falhar (GUI, sync)."""
    try:
        (BASE_DIR / ".boot_ok").write_text(str(CURRENT_VERSION), encoding="utf-8")
    except Exception:
        pass


# ===========================================================================
# INSTANCIA UNICA POR ELEICAO  (v5.68)
# ---------------------------------------------------------------------------
# PROBLEMA que isto resolve: apos um update, a loja ficava com VARIAS instancias
# do AgenteLocal abertas ao mesmo tempo. Causas somadas:
#   1) Auto-start duplicado (chave Run + atalho .lnk) -> 2 lancamentos por login.
#   2) v5.67 passou a "NUNCA sair pelo mutex" (p/ acabar com o 'clico e nao abre').
#      Efeito colateral: quando o kill do fantasma nao funcionava (antivirus/timing),
#      a 2a instancia seguia rodando -> duas coexistindo.
#   3) O startup usava um "kill-guerra" SIMETRICO (cada uma matava a outra), sem
#      vencedor deterministico -> ora sobrava 1, ora 2, ora zero.
#   4) Com >=2 rodando, cada uma poll+checa update; no update ambas relancavam exes,
#      e o antivirus escaneando o exe novo estourava o timeout do heartbeat -> mais
#      relancamentos. As instancias se acumulavam.
#
# SOLUCAO: eleicao DETERMINISTICA. Cada instancia publica (pid, versao, heartbeat)
# em DATA_DIR/instances. Vence SEMPRE a MAIOR versao (empate: menor pid) -> "fica so
# a mais atualizada". Quem vence mata as outras; quem perde SO encerra apos confirmar
# que a vencedora esta VIVA e SAUDAVEL (nunca deixa a loja sem agente; nunca sai cego).
# Um watchdog roda a eleicao a cada ~15s, entao duplicatas que surjam (ex.: durante um
# update) colapsam sozinhas para UMA — sem CMD, sem intervencao do lojista.
# ===========================================================================
INSTANCES_DIR = DATA_DIR / "instances"
_ELEICAO_HB_STALE_S = 45   # registro sem heartbeat ha mais que isso => instancia pendurada
_ELEICAO_INTERVALO_S = 15  # de quanto em quanto o watchdog re-roda a eleicao
_meu_registro_path = None
_eleicao_lock = threading.Lock()  # serializa eleicao (boot + watchdog nao se atropelam)


def _ver_tuple(s):
    """'5.68' -> (5, 68). Converte versao em tupla comparavel. Versao ilegivel vira (0,)
    (perde a eleicao, o que e o correto: instancia sa/atual deve vencer uma corrompida)."""
    try:
        return tuple(int(x) for x in str(s).strip().split("."))
    except Exception:
        return (0,)


def _escrever_registro_instancia():
    """Publica/atualiza o registro DESTA instancia (pid, versao, exe, heartbeat) em
    DATA_DIR/instances/<pid>.json. E o que permite a eleicao saber a VERSAO de cada
    processo AgenteLocal vivo. Best-effort, nunca lanca."""
    global _meu_registro_path
    try:
        INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        _meu_registro_path = INSTANCES_DIR / f"{os.getpid()}.json"
        _meu_registro_path.write_text(json.dumps({
            "pid": os.getpid(),
            "version": str(CURRENT_VERSION),
            "exe": str(sys.executable),
            "hb": time.time(),
            "since": _start_time,   # v5.78: quando esta instancia subiu (desempate + deteccao de clique manual)
        }), encoding="utf-8")
    except Exception:
        pass


def _listar_pids_agente():
    """PIDs vivos de processos AgenteLocal* (via tasklist CSV; WMIC foi descontinuado)."""
    pids = []
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq AgenteLocal*", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=6, creationflags=_NO_WINDOW)
        for linha in (r.stdout or "").splitlines():
            partes = [p.strip('"') for p in linha.split('","')]
            if len(partes) >= 2:
                try:
                    pids.append(int(partes[1]))
                except ValueError:
                    continue
    except Exception as e:
        log.debug(f"[ELEICAO] tasklist falhou: {e}")
    return pids


def _matar_pid(pid):
    """Mata um PID (arvore inteira). Retorna True se o comando rodou."""
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                       capture_output=True, timeout=5, creationflags=_NO_WINDOW)
        return True
    except Exception:
        return False


def _since_registro(rec):
    """'since' do registro como float; ausente/ilegivel => +inf (conta como a MAIS NOVA,
    ou seja, perde o desempate — nunca derruba uma instancia que se declarou mais antiga)."""
    try:
        s = float((rec or {}).get("since") or 0)
        return s if s > 0 else float("inf")
    except Exception:
        return float("inf")


def _decidir_vencedor(registros):
    """Funcao PURA (testavel): dado {pid: {"version": ..., "since": ...}}, devolve o pid
    vencedor. Regra: MAIOR versao vence; empate => instancia MAIS ANTIGA (menor 'since',
    v5.78+); ultimo desempate => MENOR pid. Como todas as instancias olham os mesmos
    registros, todas chegam ao MESMO vencedor -> exatamente uma sobrevive.
    POR QUE 'mais antiga' no empate (e nao so menor pid): o Windows REUSA pids, entao com
    'menor pid' a copia NOVA (clique do lojista no .exe) vencia ~metade das vezes e MATAVA o
    agente que estava trabalhando, reiniciando-o a toa. Com 'mais antiga vence', o clique
    nunca derruba o agente em producao: a copia nova so pede pra ele mostrar a janela.
    Compatibilidade: registros sem 'since' (<=v5.77) so empatam entre si (mesma versao),
    e ai caem no criterio antigo (menor pid) — comportamento identico ao de antes."""
    vencedor = None
    for p in registros:
        if vencedor is None:
            vencedor = p
            continue
        rp, rw = registros[p], registros[vencedor]
        vp, vw = _ver_tuple(rp.get("version")), _ver_tuple(rw.get("version"))
        if vp > vw:
            vencedor = p
        elif vp == vw:
            sp, sw = _since_registro(rp), _since_registro(rw)
            if sp < sw or (sp == sw and p < vencedor):
                vencedor = p
    return vencedor


_MANUAL_MIN_IDADE_S = 60      # instancia rodando ha mais que isso ja estava "em producao"
_abrir_janela_no_boot = False  # setado pela eleicao; o main abre o painel ao terminar de subir


def _idade_instancia(pid, rec):
    """Ha quantos segundos a instancia 'pid' esta rodando. Usa o 'since' do registro
    (v5.78+); p/ registro de versao antiga (sem 'since') usa a data de CRIACAO do arquivo
    de registro (no Windows st_ctime = criacao; o heartbeat so reescreve, nao recria).
    Desconhecido => 0 (trata como recem-nascida: nunca dispara janela por engano)."""
    try:
        s = float((rec or {}).get("since") or 0)
        if s > 0:
            return max(0.0, time.time() - s)
    except Exception:
        pass
    try:
        return max(0.0, time.time() - (INSTANCES_DIR / f"{pid}.json").stat().st_ctime)
    except Exception:
        return 0.0


def _pedir_janela_e_esperar(pid_vencedor, timeout_s=4.0):
    """Escreve o SHOW_FLAG pedindo a instancia vencedora que mostre o painel e espera ela
    CONSUMIR o pedido (apagar o arquivo). Consumir prova que ela esta viva E com a GUI
    respondendo. Retorna True se consumiu. Se nao consumiu, limpa o pedido (um flag orfao
    faria o painel abrir sozinho num boot futuro) e retorna False."""
    try:
        SHOW_FLAG.write_text(json.dumps({"de": os.getpid(), "para": pid_vencedor,
                                         "ts": time.time()}), encoding="utf-8")
    except Exception as e:
        log.debug(f"[ELEICAO] nao consegui escrever SHOW_FLAG: {e}")
        return False
    fim = time.time() + timeout_s
    while time.time() < fim:
        time.sleep(0.2)
        if not SHOW_FLAG.exists():
            return True
    try: SHOW_FLAG.unlink()
    except Exception: pass
    return False


def _eleger_instancia_unica(motivo="boot"):
    """Eleicao DETERMINISTICA de instancia unica.
    Vencedora = MAIOR versao; empate => MENOR pid (todas as instancias, olhando os mesmos
    registros, calculam o MESMO vencedor).
      - Se EU venco: mato as outras instancias AgenteLocal vivas e sigo rodando.
      - Se NAO venco: eu NUNCA me auto-encerro (sigo rodando); a vencedora e quem me encerra.
        So mato/assumo se a 'vencedora' estiver morta/pendurada. Assim ZERO instancias e
        impossivel (fim do 'nao abre') e as duplicatas colapsam mesmo assim.
    Retorna True se esta instancia deve continuar. So age em modo frozen (.exe real)."""
    global _abrir_janela_no_boot
    if not getattr(sys, "frozen", False):
        return True
    if not _eleicao_lock.acquire(blocking=False):
        return True  # ja tem uma eleicao rodando; nao empilha
    meu_pid = os.getpid()
    try:
        _escrever_registro_instancia()
        # Grace: se ha PIDs vivos que ainda nao publicaram registro (pode ser uma versao MAIS
        # NOVA subindo), espera e re-scaneia antes de decidir — no boot 3 passadas, no watchdog
        # 2 (evita que uma instancia velha mate a nova que ainda esta escrevendo o registro).
        tentativas = 3 if motivo == "boot" else 2
        for _t in range(tentativas):
            vivos = set(_listar_pids_agente())
            vivos.add(meu_pid)  # eu SEMPRE conto como vivo (mesmo se o tasklist falhar)
            registros = {}
            try:
                arquivos = list(INSTANCES_DIR.glob("*.json"))
            except Exception:
                arquivos = []
            for f in arquivos:
                try:
                    pid = int(f.stem)
                except ValueError:
                    try: f.unlink()
                    except Exception: pass
                    continue
                if pid not in vivos:
                    try: f.unlink()   # registro orfao: processo ja morreu
                    except Exception: pass
                    continue
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    rec = None
                if isinstance(rec, dict):
                    registros[pid] = rec
            # garante o meu proprio registro no mapa
            registros.setdefault(meu_pid, {"pid": meu_pid, "version": str(CURRENT_VERSION),
                                           "hb": time.time(), "since": _start_time})
            # PIDs vivos que ainda nao publicaram registro (recem-lancados). No boot,
            # espera e re-scaneia — pode ser uma versao MAIS NOVA subindo.
            desconhecidos = [p for p in vivos if p not in registros]
            if desconhecidos and _t < tentativas - 1:
                time.sleep(2)
                _escrever_registro_instancia()
                continue

            # ---- decide o vencedor: maior versao, empate menor pid ----
            vencedor = _decidir_vencedor(registros)

            if vencedor == meu_pid:
                # EU venco: encerro as outras. CRITICO: mato SO instancias que PUBLICARAM
                # registro (apps reais de eleicao) — NUNCA PIDs sem registro. O PyInstaller
                # onefile roda como DOIS 'AgenteLocal.exe': o bootloader-PAI + o app-FILHO
                # (este, que roda o Python e publica o registro). O pai NUNCA publica registro.
                # Ate a v5.69 matavamos "todo AgenteLocal != eu", o que incluia o NOSSO
                # bootloader-pai; taskkill /T no pai derrubava o filho junto => SUICIDIO =>
                # 'nao abre'. Matando so registrados, o proprio pai (e o pai das outras) fica
                # de fora, mas some sozinho: matar o app-filho faz o bootloader-pai encerrar.
                outras = [p for p in registros
                          if p != meu_pid and p != os.getppid()]
                # v5.78: se estou derrubando uma instancia que ja rodava ha um tempo (>60s),
                # isto NAO e o auto-start duplicado do login (as duas sobem juntas, com
                # segundos de diferenca): alguem abriu o .exe de novo (ex.: um exe mais novo
                # por cima do antigo). Quem clicou espera VER algo -> abro o painel ao
                # terminar de subir. (Avaliado ANTES de apagar os registros: a idade pode vir
                # da data de criacao do arquivo.)
                if motivo == "boot" and any(_idade_instancia(p, registros[p]) > _MANUAL_MIN_IDADE_S
                                            for p in outras):
                    _abrir_janela_no_boot = True
                for p in outras:
                    _matar_pid(p)
                    try: (INSTANCES_DIR / f"{p}.json").unlink()
                    except Exception: pass
                if outras:
                    log.info(f"[ELEICAO/{motivo}] Instancia vencedora v{CURRENT_VERSION} "
                             f"pid={meu_pid}; encerrei {len(outras)} extra: {outras}")
                return True

            # NAO venco. REGRA DE OURO: eu NUNCA me auto-encerro (os._exit). Encerrar a si
            # mesmo causava o bug "nao abre": quando o 'vencedor' era um processo que JA tinha
            # morrido mas ainda aparecia no tasklist (o taskkill /F nao some com ele na hora) e
            # o arquivo de registro dele ainda estava fresco, a instancia VIVA cedia a um morto
            # -> ZERO instancias. Quem colapsa duplicatas e SEMPRE a vencedora, matando as
            # outras. Como a vencedora nunca se mata e mata-mutua e impossivel (decisao
            # deterministica), zero-instancias fica IMPOSSIVEL.
            rec = registros.get(vencedor, {})
            hb = rec.get("hb", 0)
            saudavel = (vencedor in vivos) and ((time.time() - hb) < _ELEICAO_HB_STALE_S)
            if saudavel:
                # Existe uma vencedora real e saudavel: eu SIGO RODANDO; ela vai me encerrar
                # no ciclo de eleicao dela (boot dela ou watchdog em ate ~15s). Melhor uma
                # duplicata por instantes do que a loja sem agente.
                log.info(f"[ELEICAO/{motivo}] Existe instancia vencedora pid={vencedor} "
                         f"v{rec.get('version')}; sigo rodando (ela colapsa duplicatas). "
                         f"eu=pid{meu_pid} v{CURRENT_VERSION}")
                # v5.78: a vencedora ja roda ha >60s => isto e o lojista clicando no .exe com
                # o agente na bandeja ("esta no gerenciador de tarefas mas nao abre"). Peco a
                # ela que MOSTRE o painel. Se ela consumir o pedido (prova de que esta viva E
                # com a GUI respondendo), encerro esta copia em silencio: nao ha razao pra
                # ficar 15s duplicado (2o icone na bandeja) ate o watchdog dela me matar.
                # Se NAO consumir em 4s, vale a regra de ouro: sigo rodando.
                if motivo == "boot" and _idade_instancia(vencedor, rec) > _MANUAL_MIN_IDADE_S:
                    if _pedir_janela_e_esperar(vencedor):
                        log.info(f"[ELEICAO/boot] Vencedora pid={vencedor} mostrou o painel; "
                                 f"encerro esta copia extra (pid={meu_pid}).")
                        try:
                            if _meu_registro_path and _meu_registro_path.exists():
                                _meu_registro_path.unlink()
                        except Exception:
                            pass
                        logging.shutdown()
                        os._exit(0)
                    log.info(f"[ELEICAO/boot] Vencedora pid={vencedor} nao respondeu ao pedido "
                             f"de janela em 4s; sigo rodando (eleicao decide).")
                return True
            else:
                # Vencedora aparente NAO esta saudavel (morta/pendurada): assumo o posto.
                log.warning(f"[ELEICAO/{motivo}] Vencedora aparente pid={vencedor} nao esta "
                            f"saudavel; matando-a e assumindo (nunca deixa a loja sem agente).")
                _matar_pid(vencedor)
                try: (INSTANCES_DIR / f"{vencedor}.json").unlink()
                except Exception: pass
                # deixa o watchdog re-eleger no proximo ciclo p/ limpar o resto
                return True
        return True
    except Exception as e:
        log.warning(f"[ELEICAO/{motivo}] Falha na eleicao ({e}); seguindo rodando por seguranca.")
        return True
    finally:
        try: _eleicao_lock.release()
        except Exception: pass


def _watchdog_instancia():
    """Roda a eleicao periodicamente. E o que faz duplicatas que surjam DEPOIS do boot
    (tipicamente durante um update) colapsarem sozinhas para UMA instancia, sem CMD."""
    while True:
        try:
            time.sleep(_ELEICAO_INTERVALO_S)
            _escrever_registro_instancia()   # mantem meu heartbeat fresco p/ as outras me verem vivo
            _eleger_instancia_unica("watchdog")
        except Exception as e:
            log.debug(f"[ELEICAO/watchdog] {e}")


if __name__ == "__main__":
    if getattr(sys, 'frozen', False):
        # 1) Publica meu registro (pid + versao) o QUANTO ANTES, p/ que a eleicao de
        #    qualquer instancia (a minha e a das outras) enxergue a MINHA versao.
        _escrever_registro_instancia()

        # 2) Heartbeat de update (.boot_ok): sinaliza que este exe subiu (o bat de update
        #    aguarda isso p/ rollback). Escrito cedo, antes de qualquer coisa lenta.
        _escrever_heartbeat()

        # 3) ELEICAO de instancia unica (substitui o antigo kill-guerra simetrico + mutex,
        #    que ora deixava 2, ora zero). Mantem SEMPRE a instancia da MAIOR versao
        #    (empate: menor pid). Se eu venco, encerro as outras; se NAO venco eu NUNCA me
        #    auto-encerro (sigo rodando) — a vencedora e quem colapsa as duplicatas. Assim
        #    ZERO instancias e impossivel (fim do 'clico e nao abre' E do 'abre varias').
        _eleger_instancia_unica("boot")

        # 4) Auto-reparo: normaliza nome versionado -> AgenteLocal.exe e limpa orfaos
        _auto_reparo_boot()

        # 5) Watchdog continuo: se em qualquer momento surgirem instancias duplicadas
        #    (tipicamente durante um update), a eleicao roda de novo e colapsa p/ UMA
        #    (a mais nova) em ~15s, sozinho, sem CMD/intervencao do lojista.
        threading.Thread(target=_watchdog_instancia, daemon=True).start()

    log.info(f"=== Concentrador de Impressoes e Dispositivos v{CURRENT_VERSION} iniciando ===")

    # Remove exes versionados antigos em background (pode estar em uso logo apos update)
    if getattr(sys, 'frozen', False):
        def _cleanup_old_exes():
            time.sleep(10)  # Aguarda bat de update terminar de mover o arquivo
            for f in BASE_DIR.glob("AgenteLocal_*.exe"):
                # Extrai versao do nome: AgenteLocal_5.23.exe -> "5.23"
                try:
                    ver_str = f.stem.replace("AgenteLocal_", "")
                    ver_parts = [int(x) for x in ver_str.split(".")]
                    cur_parts = [int(x) for x in CURRENT_VERSION.split(".")]
                    # So apaga se for versao MENOR ou IGUAL a atual (nunca a que esta sendo instalada)
                    if ver_parts > cur_parts:
                        log.info(f"[CLEANUP] Ignorando {f.name} (versao futura, update em andamento)")
                        continue
                except Exception:
                    pass  # nome estranho: tenta apagar mesmo assim
                for _ in range(3):
                    try:
                        f.unlink()
                        log.info(f"[CLEANUP] Removido exe antigo: {f.name}")
                        break
                    except Exception:
                        time.sleep(3)
            # "Sumir com os dados antigos": apos a config definitiva em DATA_DIR existir e
            # ser valida, apaga config.json ORFAOS deixados em pastas antigas — eram eles que,
            # apos um update, faziam o agente reabrir com a config trocada. So remove se o
            # config bom ja esta salvo no local definitivo (nunca apaga o unico config valido).
            try:
                if CONFIG_PATH.exists():
                    _def = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                    _def_ok = isinstance(_def, dict) and (_def.get("token") or _def.get("restaurant_id"))
                    if _def_ok:
                        _home = Path.home()
                        _orfaos = [
                            BASE_DIR / "config.json",  # config colado ao exe (legado)
                            _home / "Desktop" / "Agente Local" / "config.json",
                            _home / "Desktop" / "Agente Local" / "dist" / "config.json",
                            _home / "Desktop" / "Agente Local" / "agente-local-releases" / "config.json",
                            _home / "OneDrive" / "Desktop" / "Agente Local" / "config.json",
                        ]
                        for c in _orfaos:
                            try:
                                # nunca apaga o proprio arquivo definitivo
                                if c.resolve() == CONFIG_PATH.resolve():
                                    continue
                                if c.exists():
                                    # backup leve antes de remover (por seguranca), depois apaga
                                    try:
                                        (c.parent / "config.orfao.bak").write_text(
                                            c.read_text(encoding="utf-8"), encoding="utf-8")
                                    except Exception:
                                        pass
                                    c.unlink()
                                    log.info(f"[CLEANUP] Removido config orfao: {c}")
                            except Exception:
                                continue
            except Exception as e:
                log.debug(f"[CLEANUP] Erro ao limpar configs orfaos: {e}")
        threading.Thread(target=_cleanup_old_exes, daemon=True).start()

    # Garante startup no Windows
    _garantir_startup()

    _root = tk.Tk()
    _root.withdraw()
    _root.title("Agente Local")

    # Primeira execucao - abre boas-vindas
    if not cfg.get("token") or not cfg.get("restaurant_id"):
        log.info("Primeira execucao - abrindo boas-vindas")
        abrir_boasvindas()
        _root.mainloop()
        _root = tk.Tk()
        _root.withdraw()
        cfg = carregar_config()

    if not cfg.get("restaurant_id"):
        log.error("restaurant_id nao configurado.")
        import sys
        sys.exit(1)

    # Sincroniza impressoras do servidor ao iniciar
    log.info("[SYNC] Sincronizando impressoras do servidor ao iniciar...")
    try:
        sincronizar_impressoras()
        cfg = carregar_config()  # Recarrega apos sincronizacao
    except Exception as e:
        log.warning(f"[SYNC] Falha na sincronizacao inicial: {e}")

    # Abre config automaticamente se nao tiver impressoras mapeadas
    imps_mapeadas = [i for i in cfg.get("impressoras",[]) if i.get("nome_impressora")]
    if not imps_mapeadas:
        log.info("[APP] Sem impressoras mapeadas - abrindo configuracoes")
        _root.after(1500, lambda: abrir_config(auto=True))

    log.info(f"Restaurante: {cfg.get('restaurant_name','?')}")
    log.info(f"Impressoras: {[i.get('nome') for i in cfg.get('impressoras',[])]}")

    # Inicia polling em background
    import asyncio, threading

    def _run_polling_safe():
        while True:
            try:
                asyncio.run(loop_poll())
            except Exception as e:
                log.error(f"[POLL] Crash: {e} - reiniciando em 5s")
                import time as _t; _t.sleep(5)

    threading.Thread(target=_run_polling_safe, daemon=True).start()

    # Fecha janela = minimiza para bandeja
    def _on_close():
        _root.withdraw()
    _root.protocol("WM_DELETE_WINDOW", _on_close)

    # v5.78: um SHOW_FLAG que sobrou de antes (a vencedora morreu sem consumir) e lixo — se
    # ficasse, o painel abriria sozinho no proximo login. Um pedido legitimo so e escrito
    # para uma instancia que JA passou deste ponto, entao apagar aqui nunca perde pedido.
    try:
        if SHOW_FLAG.exists(): SHOW_FLAG.unlink()
    except Exception:
        pass
    _root.after(300, _check)
    if _abrir_janela_no_boot:
        # Substitui uma instancia que ja rodava (alguem abriu o .exe de novo): mostra o painel.
        log.info("[GUI] Substitui uma instancia que ja rodava (clique no .exe); abrindo painel de Status")
        _root.after(1500, abrir_dashboard)

    # Inicia systray em thread separada
    threading.Thread(target=iniciar_tray, daemon=True).start()

    # Loop principal com crash recovery
    while True:
        try:
            _root.mainloop()
            break
        except Exception as e:
            log.error(f"[GUI] Erro mainloop: {e} - reiniciando")
            import time as _t; _t.sleep(1)
