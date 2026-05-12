"""Agente Local v3.4 - GUI na main thread, polling em background"""
import asyncio, json, logging, sys, time, threading, os, subprocess, winreg, queue, hashlib, socket
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

BASE_DIR     = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
# Garante que o log sempre fica na pasta do exe, nao na pasta de trabalho
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
CONFIG_PATH  = BASE_DIR / "config.json"
LOG_PATH     = BASE_DIR / "agente.log"
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

def carregar_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"token":"","anon_key":"","restaurant_id":"","restaurant_name":"","poll_interval":3,
            "impressoras":[],"balancas":[],"ultima_sincronizacao":""}

def salvar_config(c):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)

cfg = carregar_config()

def listar_impressoras_windows():
    if HAS_WIN32:
        try: return [p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        except: pass
    try:
        r = subprocess.run(["powershell","-Command","Get-Printer | Select-Object -ExpandProperty Name"],
                           capture_output=True, text=True, timeout=5)
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
                                           capture_output=True, text=True, timeout=5)
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
                                   capture_output=True, timeout=10)
                    log.info(f"[STARTUP] Atalho criado/corrigido -> {exe}")
        except Exception as e:
            log.warning(f"[STARTUP] Atalho falhou: {e}")

    except Exception as e:
        log.error(f"[STARTUP] Erro: {e}")

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

def ef_poll_jobs():
    global _agents_online, _token_invalido
    imps = cfg.get("impressoras", [])
    # Declara apenas areas de impressoras COM nome_impressora mapeado.
    # Isso garante que o servidor so manda jobs que este agente consegue imprimir.
    # Impressoras sem mapeamento pertencem a outro agente — nao declarar aqui.
    mapa_tipo_area = {"receipt":"caixa","kitchen":"cozinha","bar":"bar","delivery":"delivery","pickup":"balcao"}
    areas_set = set()
    for i in imps:
        if not i.get("nome_impressora","").strip():
            continue  # sem mapeamento Windows — este job e de outro agente
        area = i.get("area","").strip().lower()
        ptype = i.get("printer_type","").strip().lower()
        if area:
            areas_set.add(area)
        elif ptype:
            areas_set.add(mapa_tipo_area.get(ptype, ptype))
    # Se nenhuma impressora tem mapeamento, declara todas as areas para nao perder jobs
    # (modo legado: agente unico sem configuracao multi-agente)
    if not areas_set:
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
        jobs = resp.get("print_jobs") or resp.get("jobs") or []
        if isinstance(resp, list): jobs = resp
        log.info(f"[POLL] OK areas={areas} jobs={len(jobs)} tipos={[j.get('printer_type') for j in jobs]} agentes={[a.get('device_name') for a in _agents_online]}")
        return jobs
    if s == 401:
        if not _token_invalido:
            _token_invalido = True
            log.error(f"[POLL] Token invalido (401) - abrindo configuracoes")
            if _root:
                _root.after(0, abrir_config)
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
                imp["nome"] = ns  # atualiza nome para o do servidor
                imp["area"] = area_servidor
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

def _escpos_font_prefix():
    """Retorna bytes ESC/POS para o tamanho de fonte configurado (0=normal,1=duplo,2=triplo)."""
    n = int(cfg.get("font_size", 0))
    if n <= 0:
        return b"\x1b\x21\x00"  # normal
    if n == 1:
        return b"\x1d\x21\x11"  # largura+altura duplos
    return b"\x1d\x21\x22"      # largura+altura triplos

def _imprimir_raw(nome, conteudo):
    try:
        if HAS_WIN32:
            h=win32print.OpenPrinter(nome)
            try:
                win32print.StartDocPrinter(h,1,("Cupom",None,"RAW"))
                win32print.StartPagePrinter(h)

                # Se for string, codifica com prefixo de tamanho de fonte. Se for bytes (RAW), envia direto.
                if isinstance(conteudo, str):
                    payload = _escpos_font_prefix() + (conteudo+"\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00").encode("cp850","replace")
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
                payload = _escpos_font_prefix() + (conteudo + "\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00").encode("cp850", "replace")
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
    # Fallback: config legada (impressoras na raiz) — ja filtra por area do agente
    nome = _res_imp(pt)
    if nome:
        return {"nome_impressora": nome, "tipo": "comum_win32"}
    return None

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
TL={"counter":"BALCAO","dine_in":"MESA","takeaway":"RETIRADA","delivery":"ENTREGA","pickup":"RETIRADA","table":"MESA","balcao":"BALCAO","mesa":"MESA","retirada":"RETIRADA","entrega":"ENTREGA"}
PL={"cash":"Dinheiro","credit":"Cartao Credito","debit":"Cartao Debito","pix":"PIX","card":"Cartao","money":"Dinheiro","creditcard":"Cartao Credito","debitcard":"Cartao Debito"}

def _li(q,n,p,w=None):
    w=w or W; b=f"[ {q}x ]  {n}"
    total=int(q)*int(p) if p else 0
    if total<=0: return b  # sem preco quando zero (mesa com pagamento no final etc)
    pv=_R(total); e=w-len(b)-len(pv)
    return b+(" "*max(1,e))+pv if e>=1 else f"{b}\n{pv:>{w}}"

def _fmt(content, jt, pt):
    # Largura do papel: paper_width do content tem prioridade, default 48
    pw = content.get("paper_width")
    w = int(pw) if pw and str(pw).isdigit() else W
    _fs = int(cfg.get("font_size", 0))
    # Para cozinha/bar: nao reduz w — todos os detalhes sempre aparecem.
    # Fonte grande so no nome do item (inline via ESC/POS); addons/obs em normal.
    # Para receipt: reduz w para alinhar colunas de preco com a fonte maior.
    _is_kitchen = jt in ("kitchen","bar") or pt in ("kitchen","bar")
    if not _is_kitchen:
        if _fs == 1:
            w = w // 2
        elif _fs >= 2:
            w = w // 3
    S="-"*w

    # Flags de exibição configuráveis
    show_phone    = content.get("print_customer_info", True)
    show_payment  = content.get("print_payment_method", True)

    tipo=content.get("type",jt); ll=[]
    # Normaliza tipos de salao/mesa para o layout receipt
    if tipo not in ("order","receipt","kitchen","bar","pickup","delivery","command","test_page"):
        tipo = "receipt"
    if tipo in ("order","receipt"):
        ne=content.get("company_name","") or cfg.get("restaurant_name","")
        if ne: ll.append(ne.upper().center(w))
        e=content.get("company_address","")
        if e: ll.append(e.center(w))
        t=content.get("company_phone","")
        if t: ll.append(f"Tel: {t}".center(w))
        ll.append(S)
        n=content.get("order_number","")
        if n: ll.append(f"PEDIDO #{n}".center(w))
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
        ll.append(S)
        DP="·"*w
        for item in content.get("items",[]):
            ll.append(_li(item.get("quantity",1),item.get("name",""),item.get("unit_price_cents",0),w))
            for a in item.get("addons",[]):
                pc=a.get("price_cents",0)
                ll.append(f"  + {a.get('name','')}{f' {_R(pc)}' if pc else ''}")
            obs=item.get("notes","")
            if obs: ll.append(f"  >> {obs}")
            ll.append(DP)
        ll.append(S)
        sub=content.get("subtotal_cents",0); desc=content.get("discount_cents",0)
        ent=content.get("delivery_fee_cents",0); tot=content.get("total_cents",0)
        if sub:
            sv=_R(sub); ll.append(f"{'Subtotal:':<{w-len(sv)}}{sv}")
        if desc and int(desc)>0:
            dv=f"-{_R(desc)}"; ll.append(f"{'Desconto:':<{w-len(dv)}}{dv}")
        if ent and int(ent)>0:
            ev=_R(ent); ll.append(f"{'Taxa entrega:':<{w-len(ev)}}{ev}")
        tv=_R(tot); ll.append(f"{'TOTAL:':<{w-len(tv)}}{tv}")
        pg=content.get("payment_method","")
        if pg and show_payment: ll.append(f"Pagamento: {PL.get(pg.lower(),pg)}")
        cod=content.get("pickup_code","")
        if cod: ll.append("="*w); ll.append(f"RETIRADA: {cod}".center(w)); ll.append("="*w)
        obs2=content.get("notes","")
        if obs2: ll.append(S); ll.append(f"Obs: {obs2}")
        # Endereço de entrega (delivery)
        addr=content.get("delivery_address","") or content.get("delivery_address_street","")
        if addr:
            ll.append(S); ll.append("ENTREGA:".center(w))
            ll.append(addr)
            comp=content.get("delivery_address_complement","")
            if comp: ll.append(comp)
            bairro=content.get("delivery_address_neighborhood","") or content.get("delivery_address_district","")
            if bairro: ll.append(bairro)
            city=content.get("delivery_address_city","")
            ref=content.get("delivery_address_reference","")
            if city: ll.append(city)
            if ref: ll.append(f"Ref: {ref}")
        rod=content.get("footer_message","")
        if rod: ll.append(S); ll.append(rod.center(w))
        ll.append(S)
    elif tipo in ("kitchen","bar"):
        titulo = "COZINHA" if tipo=="kitchen" else "BAR"
        # Cabecalho sempre em fonte normal para caber na largura do papel
        cab = []
        cab+=["*"*w, titulo.center(w), "*"*w]
        n=content.get("order_number","")
        if n: cab.append(f"PEDIDO #{n}".center(w))
        tp=content.get("order_type","")
        if tp: cab.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        m=content.get("table_number","")
        if m: cab.append(f"Mesa: {m}")
        c2=content.get("customer_name","")
        if c2: cab.append(f"Cliente: {c2}")
        if tipo=="kitchen":
            try:
                from datetime import datetime
                dt=content.get("created_at","")
                cab.append(f"Hora: {datetime.fromisoformat(dt.replace('Z','+00:00')).strftime('%H:%M')}")
            except: pass
        cab.append(S)

        if _fs <= 0:
            # Fonte normal: comportamento original — tudo em string
            ll += cab
            DP="·"*w
            for item in content.get("items",[]):
                q=item.get("quantity",item.get("qty",1)); ll.append(f"[ {q}x ]  {item.get('name','')}")
                for a in item.get("addons",[]): ll.append(f"  + {a.get('name','')}")
                obs=item.get("notes","")
                if obs: ll.append(f"  >> {obs}")
                ll.append(DP)
            obs2=content.get("notes","")
            if obs2: ll.append(S); ll.append(f"OBS: {obs2}")
            ll.append(S)
        else:
            # Fonte grande: retorna bytes com comandos ESC/POS inline.
            # Cabecalho e detalhes (addons, obs) em normal; nome do item em grande.
            FNORMAL = b"\x1b\x21\x00"
            FBIG    = _escpos_font_prefix()
            DP_str  = "·"*w
            parts = [FNORMAL]
            enc = lambda s: (s+"\n").encode("cp850","replace")
            for linha in cab:
                parts.append(enc(linha))
            for item in content.get("items",[]):
                q=item.get("quantity",item.get("qty",1))
                nome=item.get("name","")
                parts.append(FBIG)
                parts.append(enc(f"[ {q}x ]  {nome}"))
                parts.append(FNORMAL)
                for a in item.get("addons",[]):
                    parts.append(enc(f"  + {a.get('name','')}"))
                obs=item.get("notes","")
                if obs: parts.append(enc(f"  >> {obs}"))
                parts.append(enc(DP_str))
            obs2=content.get("notes","")
            if obs2:
                parts.append(enc(S))
                parts.append(enc(f"OBS: {obs2}"))
            parts.append(enc(S))
            parts.append(FNORMAL)
            parts.append(b"\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00")  # avanço + corte
            return b"".join(parts)
    elif tipo=="pickup":
        ne=content.get("company_name","") or cfg.get("restaurant_name","")
        if ne: ll.append(ne.upper().center(w))
        e=content.get("company_address","")
        if e: ll.append(e.center(w))
        ll.append(S)
        n=content.get("order_number","")
        if n: ll.append(f"PEDIDO #{n}".center(w))
        tp=content.get("order_type","")
        if tp: ll.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        c2=content.get("customer_name","")
        if c2: ll.append(f"Cliente: {c2}")
        ph=content.get("customer_phone","")
        if ph and show_phone: ll.append(f"Tel: {ph}")
        cod=content.get("pickup_code","")
        if cod: ll.append(f"Codigo: {cod}".center(w))
        ll.append(S)
        DP="·"*w
        for item in content.get("items",[]):
            ll.append(_li(item.get("quantity",1),item.get("name",""),item.get("unit_price_cents",0),w))
            for a in item.get("addons",[]):
                pc=a.get("price_cents",0)
                ll.append(f"  + {a.get('name','')}{f' {_R(pc)}' if pc else ''}")
            obs=item.get("notes","")
            if obs: ll.append(f"  >> {obs}")
            ll.append(DP)
        ll.append(S)
        sub=content.get("subtotal_cents",0); desc=content.get("discount_cents",0)
        tot=content.get("total_cents",0)
        if sub:
            sv=_R(sub); ll.append(f"{'Subtotal:':<{w-len(sv)}}{sv}")
        if desc and int(desc)>0:
            dv=f"-{_R(desc)}"; ll.append(f"{'Desconto:':<{w-len(dv)}}{dv}")
        tv=_R(tot); ll.append(f"{'TOTAL:':<{w-len(tv)}}{tv}")
        pg=content.get("payment_method","")
        if pg and show_payment: ll.append(f"Pagamento: {PL.get(pg.lower(),pg)}")
        obs2=content.get("notes","")
        if obs2: ll.append(S); ll.append(f"Obs: {obs2}")
        ll.append(S)
    elif tipo=="delivery":
        ne=content.get("company_name","") or cfg.get("restaurant_name","")
        if ne: ll.append(ne.upper().center(w))
        e=content.get("company_address","")
        if e: ll.append(e.center(w))
        ll.append(S)
        n=content.get("order_number","")
        if n: ll.append(f"PEDIDO #{n}".center(w))
        tp=content.get("order_type","delivery")
        ll.append(f"** {TL.get(tp,tp.upper())} **".center(w))
        c2=content.get("customer_name","")
        if c2: ll.append(f"Cliente: {c2}")
        t2=content.get("customer_phone","")
        if t2 and show_phone: ll.append(f"Tel: {t2}")
        ll.append(S)
        DP="·"*w
        for item in content.get("items",[]):
            ll.append(_li(item.get("quantity",1),item.get("name",""),item.get("unit_price_cents",0),w))
            for a in item.get("addons",[]):
                pc=a.get("price_cents",0)
                ll.append(f"  + {a.get('name','')}{f' {_R(pc)}' if pc else ''}")
            obs=item.get("notes","")
            if obs: ll.append(f"  >> {obs}")
            ll.append(DP)
        ll.append(S)
        sub=content.get("subtotal_cents",0); desc=content.get("discount_cents",0)
        ent=content.get("delivery_fee_cents",0); tot=content.get("total_cents",0)
        if sub:
            sv=_R(sub); ll.append(f"{'Subtotal:':<{w-len(sv)}}{sv}")
        if desc and int(desc)>0:
            dv=f"-{_R(desc)}"; ll.append(f"{'Desconto:':<{w-len(dv)}}{dv}")
        if ent and int(ent)>0:
            ev=_R(ent); ll.append(f"{'Taxa entrega:':<{w-len(ev)}}{ev}")
        tv=_R(tot); ll.append(f"{'TOTAL:':<{w-len(tv)}}{tv}")
        pg=content.get("payment_method","")
        if pg and show_payment: ll.append(f"Pagamento: {PL.get(pg.lower(),pg)}")
        obs2=content.get("notes","")
        if obs2: ll.append(S); ll.append(f"Obs: {obs2}")
        # Endereço de entrega
        addr=content.get("delivery_address","") or content.get("delivery_address_street","")
        if addr:
            ll.append(S); ll.append("ENDERECO:".center(w)); ll.append(addr)
            comp=content.get("delivery_address_complement","")
            if comp: ll.append(comp)
            bairro=content.get("delivery_address_neighborhood","") or content.get("delivery_address_district","")
            if bairro: ll.append(bairro)
            city=content.get("delivery_address_city","")
            ref=content.get("delivery_address_reference","")
            if city: ll.append(city)
            if ref: ll.append(f"Ref: {ref}")
        ll.append(S)
    elif tipo=="command":
        if content.get("command")=="open_drawer": return "\x1b\x70\x00\x19\xfa"
    elif tipo=="test_page":
        ll+=["="*w,"   AGENTE LOCAL - TESTE OK!   ".center(w),"="*w,
             content.get("title","Teste"),content.get("message",""),
             f"Hora: {time.strftime('%d/%m/%Y %H:%M:%S')}","="*w]
    else:
        ll.append(f"JOB: {tipo}"); ll.append(json.dumps(content,ensure_ascii=False)[:200])
    return "\n".join(ll)

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
    """Retorna lista de areas aceitas para um printer_type. Tipos desconhecidos → receipt/caixa."""
    mapa = {"receipt":["caixa","receipt"],"kitchen":["cozinha","kitchen"],"bar":["bar"],"delivery":["delivery"],"pickup":["balcao","pickup"]}
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
    # Extrai pedido/cliente do content para usar em logs e registros de falha
    _pedido_ref = content.get("order_number","") or content.get("order_id","")[:8] if content else ""
    _cliente_ref = content.get("customer_name","") if content else ""

    # Se agente nao tem impressora mapeada para este tipo, ignora silenciosamente.
    # O servidor so deve mandar este job se este agente declarou a area — mas por seguranca
    # (ex: job chegou antes do poll com areas atualizado), nao marca failed para nao perder o job.
    # O outro agente que tem a impressora mapeada vai processar este job.
    if not _agente_cobre_tipo(pt):
        log.info(f"[PRINT] Job {jid} tipo={pt} — sem impressora mapeada neste agente, ignorando (outro agente processa)")
        return
    log.info(f"[PRINT] Job {jid} tipo={pt}")
    oid=content.get("order_id") or content.get("id","")
    _content_rico = (("items" in content and len(content.get("items") or []) > 0)
                     or "paper_width" in content or "receipt_font_size" in content or "company_name" in content)
    if oid and not _content_rico:
        p=ef_get_order(oid)
        if p:
            content=p
            if "order_items" in content and "items" not in content:
                raw_items = content.get("order_items") or []
                content["items"] = [
                    {
                        "name": it.get("name_snapshot") or it.get("product_name") or it.get("name",""),
                        "quantity": it.get("quantity",1),
                        "unit_price_cents": it.get("price_cents_snapshot") or it.get("unit_price_cents",0),
                        "notes": it.get("notes",""),
                        "addons": it.get("addons_json") or it.get("addons",[]),
                    }
                    for it in raw_items
                ]
            # Atualiza referencia de pedido/cliente apos buscar dados completos
            _pedido_ref = content.get("order_number","") or oid[:8]
            _cliente_ref = content.get("customer_name","") or _cliente_ref
            log.info("[ORDER] OK")
        else:
            log.error(f"[ORDER] Falha ao buscar {oid} — imprimindo com content basico do job")
            _registrar_falha(jid, "falha_buscar_pedido",
                             f"Nao foi possivel buscar dados do pedido {oid} — imprimindo com dados basicos",
                             tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
            # NAO retorna — continua com o content original para garantir que o job sai na impressora

    # Resolve impressora com suporte a multi-rede
    imp = _res_imp_por_rede(pt, printer_id=pid)
    if not imp:
        msg = f"Sem impressora configurada para tipo '{pt}'"
        ef_update_job(jid,"failed", msg)
        _registrar_falha(jid, "impressora_nao_encontrada", msg,
                         tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref)
        return

    nome_imp_local = imp.get("nome_impressora") or imp.get("endereco_ip","")

    # PRIORIDADE: Usa dados formatados do servidor (ESC/POS RAW) se existirem
    escpos_b64 = job.get("escpos_data")
    if escpos_b64:
        import base64
        try:
            dados_brutos = base64.b64decode(escpos_b64)
            log.info(f"[PRINT] Usando layout RAW para Job {jid}")
            for _ in range(copies):
                r = _imprimir_com_roteamento(imp, dados_brutos)
                if not r.get("ok"):
                    ef_update_job(jid, "failed", r.get("erro",""))
                    _registrar_falha(jid, "erro_impressora", r.get("erro",""),
                                     tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref,
                                     impressora=nome_imp_local)
                    return
        except Exception as e:
            log.error(f"[PRINT] Erro ao decodificar ESC/POS: {e} — tentando imprimir como texto")
            escpos_b64 = None  # forca fallback para texto abaixo

    if not escpos_b64:
        texto=_fmt(content,jt,pt)
        for _ in range(copies):
            r=_imprimir_com_roteamento(imp, texto)
            if not r.get("ok"):
                ef_update_job(jid,"failed",r.get("erro",""))
                _registrar_falha(jid, "erro_impressora", r.get("erro",""),
                                 tipo=pt, pedido=_pedido_ref, cliente=_cliente_ref,
                                 impressora=nome_imp_local)
                return
                
    ef_update_job(jid,"printed",pa=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()))
    nome_imp = imp.get("nome_impressora") or imp.get("endereco_ip","")
    pedido_num = (content.get("order_number","") if content else "") or (content.get("order_id","")[:8] if content else "")
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
        "content_ref": (content.get("order_number","") or _oid_real[:8]) if content else "",
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
                try: proc_job(j)
                finally: _jobs_em_proc.discard(j["id"])
            threading.Thread(target=_run, daemon=True).start()
    else: status_poll="Ativo - aguardando"
    _atualizar_icone()

CURRENT_VERSION = "5.46"
VERSION_URL = "https://raw.githubusercontent.com/delmatch-user/agente-local-releases/main/version.json"

_update_em_andamento = False  # evita multiplos downloads simultaneos

def _bat_update(exe_novo: Path, exe_destino: Path, del_extra: str = "") -> str:
    """Gera conteudo do bat de update. Mata processos, move com retry, lanca nova versao."""
    exe_destino_nome = exe_destino.name
    lock = exe_novo.parent / "update_lock.tmp"
    return (
        "@echo off\r\n"
        # Sai imediatamente se outro bat de update ja esta rodando
        f'if exist "{lock}" exit /b 0\r\n'
        f'echo 1>"{lock}"\r\n'
        # Mata todos os processos AgenteLocal pelo nome exato e versoes antigas via WMIC
        f'taskkill /F /IM "{exe_destino_nome}" >nul 2>&1\r\n'
        "for /f \"skip=1 tokens=2 delims=,\" %%P in ('wmic process where \"name like 'AgenteLocal%%'\" get processid /format:csv 2^>nul') do taskkill /F /PID %%P >nul 2>&1\r\n"
        # Espera processo liberar o arquivo
        "timeout /t 5 /nobreak >nul\r\n"
        # Move com retry (max 10 tentativas = 30s)
        "set /a TRIES=0\r\n"
        ":retry\r\n"
        f'move /y "{exe_novo}" "{exe_destino}" >nul 2>&1\r\n'
        "if errorlevel 1 (\r\n"
        "  set /a TRIES+=1\r\n"
        "  if %TRIES% GEQ 10 goto :fail\r\n"
        "  timeout /t 3 /nobreak >nul\r\n"
        "  goto retry\r\n"
        ")\r\n"
        + del_extra +
        # Lanca nova versao
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{exe_destino}\'"\r\n'
        "goto :end\r\n"
        ":fail\r\n"
        # Move falhou: relanca o exe destino atual sem update
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{exe_destino}\'"\r\n'
        ":end\r\n"
        f'del /f /q "{lock}" >nul 2>&1\r\n'
        'del "%~f0"\r\n'
    )

def _baixar_e_aplicar_update(nova, url_nova):
    """Roda em thread separada: baixa o exe novo e aplica sem travar o poll."""
    global _update_em_andamento
    try:
        # Baixa como .tmp para evitar que o cleanup de versoes antigas apague antes do bat mover
        exe_tmp = BASE_DIR / f"AgenteLocal_update.tmp"
        exe_novo = BASE_DIR / f"AgenteLocal_{nova}.exe"
        log.info(f"[UPDATE] Baixando v{nova}...")
        req = urllib.request.Request(url_nova)
        with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as r, open(exe_tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        # Renomeia para .exe so apos download completo
        if exe_novo.exists():
            exe_novo.unlink()
        exe_tmp.rename(exe_novo)
        log.info(f"[UPDATE] Download concluido: {exe_novo}")
        exe_destino = BASE_DIR / "AgenteLocal.exe"
        exe_atual = Path(sys.executable)
        del_extra = f'del /f /q "{exe_atual}" >nul 2>&1\r\n' if exe_atual.name.lower() != "agentelocal.exe" else ""
        bat = BASE_DIR / "update_apply.bat"
        bat.write_text(_bat_update(exe_novo, exe_destino, del_extra), encoding="utf-8")
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        )
        log.info("[UPDATE] Reiniciando para aplicar atualizacao...")
        sys.exit(0)
    except Exception as e:
        log.warning(f"[UPDATE] Falha no download: {e}")
        _update_em_andamento = False

async def checar_atualizacao():
    """Verifica nova versao silenciosamente; download em thread para nao travar o poll."""
    global _update_em_andamento
    if not getattr(sys, "frozen", False):
        return
    if _update_em_andamento:
        return
    try:
        req = urllib.request.Request(VERSION_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as r:
            info = json.loads(r.read())
        nova = info.get("version", "")
        url_nova = info.get("url", "")
        if not nova or not url_nova or nova == CURRENT_VERSION:
            return
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



def abrir_dashboard():
    w = tk.Toplevel(_root)
    w.title("Status - Concentrador")
    w.geometry("820x620")
    w.configure(bg="#1a1a2e")
    w.resizable(True, True)
    w.lift(); w.focus_force()

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
                nova = info.get("version",""); url_nova = info.get("url","")
                if not nova or not url_nova:
                    w.after(0, lambda: (btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"),
                                        messagebox.showwarning("Aviso","Nao foi possivel verificar atualizacao.",parent=w))); return
                if nova == CURRENT_VERSION:
                    w.after(0, lambda: (btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"),
                                        messagebox.showinfo("Atualizado",f"Voce ja esta na versao mais recente (v{CURRENT_VERSION}).",parent=w))); return
                def _confirmar():
                    if not messagebox.askyesno("Atualizar",f"Nova versao v{nova} disponivel!\nDeseja atualizar agora?",parent=w):
                        btn_upd.config(text="Atualizar Sistema", state="normal", bg="#a6e3a1"); return
                    btn_upd.config(text="Baixando...", bg="#f9e2af")
                    def _baixar():
                        try:
                            exe_novo = BASE_DIR / f"AgenteLocal_{nova}.exe"
                            urllib.request.urlretrieve(url_nova, exe_novo)
                            exe_destino = BASE_DIR / "AgenteLocal.exe"
                            exe_atual = Path(sys.executable)
                            del_extra2 = f'del /f /q "{exe_atual}" >nul 2>&1\r\n' if exe_atual.name.lower() != "agentelocal.exe" else ""
                            bat = BASE_DIR / "update_apply.bat"
                            bat.write_text(_bat_update(exe_novo, exe_destino, del_extra2), encoding="utf-8")
                            subprocess.Popen(["cmd","/c",str(bat)], creationflags=subprocess.CREATE_NO_WINDOW)
                            log.info(f"[UPDATE] Atualizando para v{nova} via botao manual")
                            w.after(0, lambda: messagebox.showinfo("Atualizando",f"Atualizando para v{nova}...\nO agente vai reiniciar automaticamente.",parent=w))
                            w.after(500, sys.exit)
                        except Exception as e:
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
                                      "quantity": it.get("quantity",1), "unit_price_cents": it.get("price_cents_snapshot") or it.get("unit_price_cents",0),
                                      "notes": it.get("notes",""), "addons": it.get("addons_json") or it.get("addons",[])} for it in raw_items]
                imp = _res_imp_por_rede(pt_orig); pt_uso = pt_orig
                log.info(f"[REIMP] Impressora para tipo '{pt_orig}': {imp}")
                if not imp:
                    imps_locais = [i for i in cfg.get("impressoras",[]) if i.get("nome_impressora","").strip()]
                    log.info(f"[REIMP] Fallback impressoras locais: {[i.get('nome_impressora') for i in imps_locais]}")
                    if imps_locais:
                        i0 = imps_locais[0]; imp = i0
                        pt_uso = i0.get("printer_type","").strip() or i0.get("area","").strip() or pt_orig
                if not imp and nome_imp_hist:
                    imp = {"nome_impressora": nome_imp_hist, "tipo": "comum_win32"}
                    log.info(f"[REIMP] Usando impressora do historico: '{nome_imp_hist}'")
                if not imp:
                    log.error("[REIMP] Nenhuma impressora disponivel neste PC")
                    w.after(0, lambda: messagebox.showerror("Erro","Nenhuma impressora configurada neste PC",parent=w)); return
                nome_real = imp.get("nome_impressora") or imp.get("endereco_ip","")
                log.info(f"[REIMP] Imprimindo em '{nome_real}' pt_uso='{pt_uso}'")
                texto = _fmt(resp, pt_uso, pt_uso)
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

def abrir_config():
    global cfg
    cfg=carregar_config(); iw=listar_impressoras_windows(); ps=listar_portas_serial()
    w=tk.Toplevel(_root); w.title("Concentrador de Impressoes e Dispositivos")
    w.geometry("820x700"); w.configure(bg="#1e1e2e"); w.lift(); w.focus_force()

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
                icfg.append({"nome":ns,"area":area,"printer_type":ts,"nome_impressora":match,"tipo":"comum_win32","modo":"texto"})
            cfg["impressoras"]=icfg; salvar_config(cfg)
            sv2.set(f"Conectado: {d.get('restaurant_name','')}")
            for item in ti.get_children(): ti.delete(item)
            for imp in icfg:
                tag="" if imp.get("nome_impressora") else "sem_map"
                ti.insert("",tk.END,values=(imp["nome"],imp["area"],imp["nome_impressora"],imp["tipo"]),tags=(tag,))
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

    cols=("nome","area","impressora_windows","tipo")
    ti=ttk.Treeview(f2,columns=cols,show="headings",height=9)
    for col,lbl,cw in [("nome","Nome Sistema",140),("area","Area",80),
                        ("impressora_windows","Impressora Windows",300),("tipo","Tipo",100)]:
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

    for imp in cfg.get("impressoras",[]):
        tag = _tag_impressora(imp)
        nome_w = imp.get("nome_impressora","") or ("(outro agente)" if tag == "outro_agente" else "")
        ti.insert("",tk.END,values=(imp.get("nome",""),imp.get("area",""),
                                    nome_w, imp.get("tipo","comum_win32")),tags=(tag,))

    ef2=tk.Frame(f2,bg="#2a2a3e",relief="ridge",bd=1); ef2.grid(row=2,column=0,columnspan=6,padx=10,pady=4,sticky="ew")
    tk.Label(ef2,text="Area:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=0,column=0,padx=(10,4),pady=10)
    earea=ttk.Combobox(ef2,values=["caixa","cozinha","bar","delivery","balcao"],width=10); earea.grid(row=0,column=1,padx=4,pady=10)
    tk.Label(ef2,text="Impressora Windows:",bg="#2a2a3e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).grid(row=0,column=2,padx=4,pady=10)
    eiw=ttk.Combobox(ef2,values=iw,width=34); eiw.grid(row=0,column=3,padx=8,pady=10)
    lbe=tk.Label(ef2,text="<< Clique DUPLO em uma linha",bg="#2a2a3e",fg="#6c7086",font=("Segoe UI",8)); lbe.grid(row=0,column=4,padx=8)

    def duplo(e):
        sel=ti.selection()
        if not sel: return
        vals=ti.item(sel[0],"values"); lbe.config(text=f"Editando: {vals[0]}",fg="#89b4fa")
        earea.set(vals[1] if len(vals)>1 else "")
        eiw.set(vals[2] if len(vals)>2 else ""); eiw.focus()

    def aplicar():
        sel=ti.selection()
        if not sel: messagebox.showwarning("Aviso","Clique duplo em uma linha!",parent=w); return
        nova=eiw.get().strip(); nova_area=earea.get().strip()
        if not nova: messagebox.showwarning("Aviso","Selecione a Impressora Windows!",parent=w); return
        vals=ti.item(sel[0],"values")
        area_final = nova_area or vals[1]
        ti.item(sel[0],values=(vals[0],area_final,nova,vals[3]),tags=("",))
        lbe.config(text=f"OK: {vals[0]} -> {nova}",fg="#a6e3a1"); eiw.set(""); earea.set("")
        # Salva imediatamente no cfg e no disco
        nome_sistema = vals[0]
        for imp in cfg.get("impressoras",[]):
            if imp.get("nome") == nome_sistema:
                imp["nome_impressora"] = nova
                if nova_area: imp["area"] = nova_area
                break
        salvar_config(cfg)
        log.info(f"[CONFIG] Impressora '{nome_sistema}' area={area_final} -> '{nova}'")

    ti.bind("<Double-1>",duplo)
    tk.Button(ef2,text="Aplicar",command=aplicar,bg="#89b4fa",fg="#1e1e2e",
              font=("Segoe UI",9,"bold"),relief="flat",padx=14,pady=6,cursor="hand2").grid(row=0,column=5,padx=8)

    fi2=ttk.Frame(f2); fi2.grid(row=3,column=0,columnspan=6,padx=10,pady=4,sticky="ew")
    ttk.Label(fi2,text="Novo:").grid(row=0,column=0,padx=4,pady=6)
    en=ttk.Entry(fi2,width=14); en.grid(row=0,column=1,padx=4)
    ttk.Label(fi2,text="Area:").grid(row=0,column=2,padx=4)
    ea=ttk.Combobox(fi2,values=["caixa","cozinha","bar","delivery","balcao"],width=9); ea.grid(row=0,column=3,padx=4)
    ttk.Label(fi2,text="Impressora:").grid(row=0,column=4,padx=4)
    ead=ttk.Combobox(fi2,values=iw,width=26); ead.grid(row=0,column=5,padx=4)

    def add_i():
        n=en.get().strip(); ww=ead.get().strip()
        if not n or not ww: messagebox.showwarning("Aviso","Preencha Nome e Impressora!",parent=w); return
        ti.insert("",tk.END,values=(n,ea.get().strip(),ww,"comum_win32"))
        en.delete(0,tk.END); ead.set("")

    def rem_i():
        sel=ti.selection()
        if sel: ti.delete(sel[0])

    def tst_i():
        sel=ti.selection()
        if not sel: messagebox.showwarning("Aviso","Selecione uma impressora!",parent=w); return
        nw=ti.item(sel[0],"values")[2]
        if not nw: messagebox.showwarning("Aviso","Mapeie a Impressora Windows!\nClique DUPLO na linha.",parent=w); return
        txt2=("="*W+"\n"+f"  {cfg.get('restaurant_name','AGENTE LOCAL')}  ".center(W)+"\n"+
              "  TESTE DE IMPRESSAO OK!  ".center(W)+"\n"+"="*W+"\n"+
              f"Impressora: {nw}\n"+f"Hora: {time.strftime('%d/%m/%Y %H:%M:%S')}\n"+"="*W+"\n")
        r=_imprimir_raw(nw,txt2)
        if r.get("ok"): messagebox.showinfo("OK",f"Teste enviado:\n{nw}",parent=w)
        else: messagebox.showerror("Erro",r.get("erro",""),parent=w)

    def sync_e_recarregar():
        def _do():
            sincronizar_impressoras()
            def _ui():
                ti.delete(*ti.get_children())
                for imp in cfg.get("impressoras",[]):
                    tag="" if imp.get("nome_impressora") else "sem_map"
                    ti.insert("",tk.END,values=(imp.get("nome",""),imp.get("area",""),
                                                imp.get("nome_impressora",""),imp.get("tipo","comum_win32")),tags=(tag,))
                messagebox.showinfo("Sincronizado","Impressoras atualizadas do servidor!",parent=w)
            w.after(0,_ui)
        threading.Thread(target=_do, daemon=True).start()

    bi2=tk.Frame(f2,bg="#1e1e2e"); bi2.grid(row=4,column=0,columnspan=6,padx=10,pady=6,sticky="w")
    for tb,cb,cor in [("+ Adicionar",add_i,"#a6e3a1"),("Remover",rem_i,"#f38ba8"),
                       ("Testar Impressao",tst_i,"#cba6f7"),("Sincronizar",sync_e_recarregar,"#fab387"),
                       ("Ver Log",abrir_log,"#6c7086")]:
        tk.Button(bi2,text=tb,command=cb,bg=cor,fg="#1e1e2e",font=("Segoe UI",9,"bold"),
                  relief="flat",padx=10,pady=5,cursor="hand2").pack(side="left",padx=4)

    # Controle de tamanho de fonte ESC/POS
    _LABELS_FONTE = ["Normal", "Grande", "Extra Grande"]
    tk.Frame(bi2,bg="#1e1e2e",width=20).pack(side="left")
    tk.Label(bi2,text="Fonte:",bg="#1e1e2e",fg="#cdd6f4",font=("Segoe UI",9,"bold")).pack(side="left",padx=(0,4))
    _fs_var = tk.IntVar(value=int(cfg.get("font_size",0)))
    lbl_fs = tk.Label(bi2,text=_LABELS_FONTE[_fs_var.get()],bg="#313244",fg="#f9e2af",
                      font=("Segoe UI",9,"bold"),padx=10,pady=5,width=12)
    lbl_fs.pack(side="left",padx=2)
    def _set_font_size(delta):
        v = max(0, min(2, _fs_var.get() + delta))
        _fs_var.set(v)
        cfg["font_size"] = v
        salvar_config(cfg)
        lbl_fs.config(text=_LABELS_FONTE[v])
    tk.Button(bi2,text="A-",command=lambda:_set_font_size(-1),bg="#45475a",fg="#cdd6f4",
              font=("Segoe UI",9,"bold"),relief="flat",padx=8,pady=5,cursor="hand2").pack(side="left",padx=2)
    tk.Button(bi2,text="A+",command=lambda:_set_font_size(1),bg="#45475a",fg="#cdd6f4",
              font=("Segoe UI",9,"bold"),relief="flat",padx=8,pady=5,cursor="hand2").pack(side="left",padx=2)

    f2.columnconfigure(0,weight=1); f2.rowconfigure(1,weight=1)

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
                               capture_output=True, text=True, timeout=10)
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
    def salvar():
        global cfg
        cfg["token"]=tv.get().strip(); cfg["poll_interval"]=int(pv.get().strip() or "3")
        # Index das impressoras atuais para preservar printer_type
        imps_orig = {i.get("nome","").strip().lower(): i for i in cfg.get("impressoras",[])}
        imps=[]
        for item in ti.get_children():
            v=ti.item(item,"values")
            nome=v[0]; area=v[1]; nome_win=v[2]; tipo=v[3]
            orig = imps_orig.get(nome.strip().lower(), {})
            # printer_type vem do servidor (via config original), area e derivada dele
            printer_type = orig.get("printer_type") or {"caixa":"receipt","cozinha":"kitchen","bar":"bar"}.get(area.strip().lower(), "receipt")
            area_correta = {"receipt":"caixa","kitchen":"cozinha","bar":"bar"}.get(printer_type, area)
            imps.append({"nome":nome,"area":area_correta,"nome_impressora":nome_win,"tipo":tipo,"modo":"texto","printer_type":printer_type})
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
        messagebox.showinfo("Salvo!","Configuracoes salvas!\nReinicie o agente para aplicar.",parent=w)
        w.destroy()

    rod=tk.Frame(w,bg="#181825"); rod.pack(fill="x",side="bottom")
    tk.Button(rod,text="Salvar Configuracoes",command=salvar,bg="#89b4fa",fg="#1e1e2e",
              font=("Segoe UI",11,"bold"),relief="flat",padx=20,pady=12,cursor="hand2").pack(side="right",padx=10,pady=8)
    tk.Button(rod,text="Cancelar",command=w.destroy,bg="#45475a",fg="white",
              font=("Segoe UI",10),relief="flat",padx=15,pady=12,cursor="hand2").pack(side="right",pady=8)
    tk.Button(rod,text="Log em tempo real",command=abrir_log,bg="#fab387",fg="#1e1e2e",
              font=("Segoe UI",10,"bold"),relief="flat",padx=15,pady=12,cursor="hand2").pack(side="left",padx=10,pady=8)
    tk.Button(rod,text="Status / Impressoes / Falhas",command=abrir_dashboard,bg="#a6e3a1",fg="#1e1e2e",
              font=("Segoe UI",10,"bold"),relief="flat",padx=15,pady=12,cursor="hand2").pack(side="left",padx=4,pady=8)

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
        "timeout /t 2 /nobreak >nul\r\n"
        f'powershell -WindowStyle Hidden -Command "Start-Process -FilePath \'{exe}\'"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8"
    )
    subprocess.Popen(["cmd", "/c", str(bat)],
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
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
    try:
        cmd = _gui_queue.get_nowait()
        try:
            if   cmd == "config":    abrir_config()
            elif cmd == "dashboard": abrir_dashboard()
            elif cmd == "log":       abrir_log()
            elif cmd == "reiniciar": reiniciar_app()
            elif cmd == "sair":      os._exit(0)
        except Exception as e:
            log.error(f"[GUI] Erro ao abrir '{cmd}': {e}", exc_info=True)
    except queue.Empty:
        pass
    _root.after(300, _check)

if __name__ == "__main__":
    if getattr(sys, 'frozen', False):
        import ctypes

        meu_pid = os.getpid()

        # Mutex global — verifica ANTES de matar qualquer processo
        # Evita que multiplas instancias passem ao mesmo tempo quando uma mata a outra
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, "AgenteLocalMIA_SingleInstance")
        _mutex_err = ctypes.windll.kernel32.GetLastError()
        if _mutex_err == 183:  # ERROR_ALREADY_EXISTS — ja tem uma instancia com o mutex
            # Verifica se o processo que tem o mutex ainda esta vivo
            # Se o outro processo for uma versao antiga (versionada), mata e assume
            time.sleep(1)
            _mutex_err2 = ctypes.windll.kernel32.GetLastError()
            # Tenta matar processos versionados antigos (AgenteLocal_X.Y.exe) mas nao AgenteLocal.exe
            try:
                r = subprocess.run(
                    ["wmic", "process", "where", "name like 'AgenteLocal_%'",
                     "get", "ProcessId", r"\format:csv"],
                    capture_output=True, text=True, timeout=5
                )
                for linha in r.stdout.splitlines():
                    partes = [p.strip() for p in linha.split(",")]
                    if len(partes) >= 2:
                        try:
                            pid_outro = int(partes[-1])
                            if pid_outro != meu_pid and pid_outro > 0:
                                subprocess.run(["taskkill", "/F", "/PID", str(pid_outro)],
                                               capture_output=True, timeout=4)
                        except ValueError:
                            continue
            except Exception:
                pass
            log.warning("[STARTUP] Outra instancia ja esta rodando. Encerrando.")
            sys.exit(0)

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
        _root.after(1500, abrir_config)

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

    _root.after(300, _check)

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
