# Simula a impressao: gera os bytes REAIS do agente (proc_job, por impressora) e interpreta o ESC/POS
# como uma termica faria, produzindo o "papel" (texto com atributos). Tambem checa problemas:
# linha maior que o papel, caractere de substituicao, comando desconhecido, acento perdido.
# Uso: venv_build\Scripts\python.exe tests\simular_impressao.py [saida.json]
import importlib.util, sys, io, contextlib, copy, json, re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "agente_local.py"
spec = importlib.util.spec_from_file_location("agente_sim", str(SRC))
A = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(A)

TABELAS = {0: "cp437", 2: "cp850", 3: "cp860", 16: "cp1252", 19: "cp858"}

def interpretar(dados, colunas, modo="normal"):
    """Interpreta bytes ESC/POS. modo: 'normal' (honra ESC t), 'utf8' (impressora em modo UTF-8).
    Devolve (linhas, avisos). Cada linha: dict(texto, negrito, largura, altura, alinhamento, tipo)."""
    linhas, avisos = [], []
    st = dict(tabela=0, negrito=False, lw=1, lh=1, alin=0)
    buf, attrs = [], None
    i, n = 0, len(dados)

    def flush(forcar=False):
        nonlocal buf, attrs
        if buf or forcar:
            txt = "".join(buf)
            a = attrs or dict(st)
            linhas.append(dict(tipo="texto", texto=txt, negrito=a["negrito"], largura=a["lw"], altura=a["lh"], alinhamento=a["alin"]))
        buf, attrs = [], None

    def texto(ch):
        nonlocal attrs
        if attrs is None:
            attrs = dict(st)
        buf.append(ch)

    while i < n:
        b = dados[i]
        if b == 0x0A:
            flush(forcar=True); i += 1; continue
        if b == 0x0D:
            i += 1; continue
        if b == 0x1B and i + 1 < n:
            c = dados[i + 1]
            if c == 0x40:   # ESC @
                flush(); st.update(tabela=0, negrito=False, lw=1, lh=1, alin=0); i += 2; continue
            if c == 0x74:   # ESC t n
                st["tabela"] = dados[i + 2]
                if st["tabela"] not in TABELAS: avisos.append(f"ESC t {st['tabela']} desconhecida")
                i += 3; continue
            if c == 0x45: st["negrito"] = bool(dados[i + 2] & 1); i += 3; continue
            if c == 0x61: st["alin"] = dados[i + 2] % 3 if dados[i + 2] < 48 else dados[i + 2] - 48; i += 3; continue
            if c == 0x21:   # ESC ! n
                m = dados[i + 2]; st["negrito"] = bool(m & 8); st["lh"] = 2 if m & 16 else 1; st["lw"] = 2 if m & 32 else 1; i += 3; continue
            if c == 0x64:   # ESC d n
                flush()
                for _ in range(dados[i + 2]): linhas.append(dict(tipo="avanco"))
                i += 3; continue
            if c == 0x70:   # ESC p (gaveta)
                flush(); linhas.append(dict(tipo="gaveta")); i += 5; continue
            if c == 0x2D: i += 3; continue      # ESC - n (sublinhado)
            if c == 0x42: i += 4; continue      # ESC B n t (beep)
            avisos.append(f"ESC {c:#04x} desconhecido no byte {i}"); i += 2; continue
        if b == 0x1C and i + 1 < n:
            if dados[i + 1] == 0x2E: i += 2; continue    # FS . (modo chines desligado: nada a fazer na simulacao)
            avisos.append(f"FS {dados[i+1]:#04x} desconhecido"); i += 2; continue
        if b == 0x1D and i + 1 < n:
            c = dados[i + 1]
            if c == 0x21:   # GS ! n
                m = dados[i + 2]; st["lw"] = (m >> 4) + 1; st["lh"] = (m & 15) + 1; i += 3; continue
            if c == 0x56:   # GS V
                flush(); linhas.append(dict(tipo="corte")); i += 3 if dados[i + 2] in (0, 1, 48, 49) else 4; continue
            if c in (0x68, 0x77, 0x48, 0x66): i += 3; continue   # GS h/w/H/f (parametros de codigo de barras)
            if c == 0x6B:   # GS k m n d1..dn
                m = dados[i + 2]
                if m >= 65:
                    k = dados[i + 3]; cod = dados[i + 4:i + 4 + k].decode("ascii", "replace")
                    flush(); linhas.append(dict(tipo="barras", texto=f"{'EAN-13' if m == 67 else 'Code128' if m == 73 else m} {cod}", alinhamento=st["alin"]))
                    i += 4 + k; continue
            if c == 0x28 and i + 4 < n and dados[i + 2] == 0x6B:   # GS ( k (QR)
                pl = dados[i + 3] + 256 * dados[i + 4]
                if dados[i + 6] == 0x51: flush(); linhas.append(dict(tipo="qr", alinhamento=st["alin"]))
                i += 5 + pl; continue
            avisos.append(f"GS {c:#04x} desconhecido no byte {i}"); i += 2; continue
        if b < 0x20:
            avisos.append(f"byte de controle {b:#04x} solto no byte {i}"); i += 1; continue
        # texto
        if modo == "utf8" and b >= 0x80:
            L = 2 if b >> 5 == 0b110 else 3 if b >> 4 == 0b1110 else 4 if b >> 3 == 0b11110 else 1
            ch = dados[i:i + L].decode("utf-8", "replace"); texto(ch); i += L; continue
        if b >= 0x80:
            texto(bytes([b]).decode(TABELAS.get(st["tabela"], "cp437"), "replace")); i += 1; continue
        texto(chr(b)); i += 1
    flush()
    # checagens
    for ln in linhas:
        if ln["tipo"] != "texto": continue
        cols = len(ln["texto"]) * ln["largura"]
        if cols > colunas: avisos.append(f"linha com {cols} colunas > papel {colunas}: {ln['texto']!r}")
        if "�" in ln["texto"]: avisos.append(f"caractere invalido na linha: {ln['texto']!r}")
    return linhas, avisos

# ------------------------------------------------------------------ pedido de exemplo (ficticio)
PEDIDO = {
    "numero": 2, "order_type": "delivery", "created_at": "2026-09-24T17:05:00.000+00:00",
    "customer_name": "Kethalin Açaí", "customer_phone": "5516999990000",
    "company_name": "Comproutai Lanches",
    "scheduled_label": "AGENDADO: 24/09 AS 19:00", "is_scheduled": True,
    "itens": [
        {"nome": "Ancho Acebolado", "quantidade": 1, "preco_cents": 4290, "barcode": "7891000100103"},
        {"nome": "Ancho DuCheffY", "quantidade": 1, "preco_cents": 4490},
        {"nome": "Bife à Cavalo", "quantidade": 1, "preco_cents": 2390,
         "adicionais": [{"nome": "M (médio)", "preco_cents": 0}], "observacao": "Não por Cheddar/Requeijão"},
    ],
    "subtotal_cents": 11170, "delivery_fee_cents": 800, "total_cents": 11970, "payment_method": "card",
    "notes": "Portão vermelho — tocar a campainha", "delivery_address": "Rua Guido Dalceno, nº 461, Jardim Paraíso, Matão - SP",
}

IMPRESSORAS_CAIXA = [
    {"nome": "Caixa 80mm (48 col, cp850)", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "EPSON", "colunas": 48},
    {"nome": "Caixa 76mm (42 col, cp1252)", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "ELGIN", "colunas": 42, "codepage": "cp1252"},
    {"nome": "Mini 58mm (32 col, ascii)", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "POS58A", "colunas": 32, "codepage": "ascii"},
    {"nome": "Mini 58mm (32 col, utf8)", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "POS58U", "colunas": 32, "codepage": "utf8"},
]
IMPRESSORAS_COZINHA = [
    {"nome": "Cozinha 80mm (48 col)", "area": "cozinha", "printer_type": "kitchen", "tipo": "comum_win32", "nome_impressora": "COZ", "colunas": 48},
]

def capturar(job, impressoras, font_size=0):
    enviados = []
    def fake(imp, dados):
        if isinstance(dados, str):
            corpo = A._substituir_marcadores_escpos(dados + "\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00")
            payload = A._escpos_cp() + A._escpos_font_prefix() + corpo      # = _imprimir_raw
        else:
            payload = dados
        enviados.append((imp, A._cp(), payload))
        return {"ok": True}
    A._imprimir_com_roteamento = fake
    A.ef_update_job = lambda *a, **k: True
    A.ef_get_order = lambda *a, **k: None
    A._post = lambda *a, **k: ({}, 200)
    A.cfg["impressoras"] = impressoras
    A.cfg["font_size"] = font_size
    A.cfg.pop("paper_width_cols", None); A.cfg.pop("codepage", None)
    with contextlib.redirect_stdout(io.StringIO()):
        A.proc_job(job)
    return enviados

def main():
    saida = []
    casos = [
        ("Cupom do caixa", {"id": "sim-1", "printer_type": "receipt", "job_type": "order", "copies": 1,
                            "created_at": PEDIDO["created_at"], "content": dict(copy.deepcopy(PEDIDO), print_barcode=True)}, IMPRESSORAS_CAIXA, 0),
        ("Comanda da cozinha", {"id": "sim-2", "printer_type": "kitchen", "job_type": "kitchen", "copies": 1,
                                "created_at": PEDIDO["created_at"], "content": copy.deepcopy(PEDIDO)}, IMPRESSORAS_COZINHA, 0),
        ("Comanda da cozinha (fonte grande)", {"id": "sim-3", "printer_type": "kitchen", "job_type": "kitchen", "copies": 1,
                                "created_at": PEDIDO["created_at"], "content": copy.deepcopy(PEDIDO)}, IMPRESSORAS_COZINHA, 1),
    ]
    for titulo, job, imps, fs in casos:
        for imp, cp, payload in capturar(job, imps, fs):
            cols = int(imp.get("colunas") or 48)
            linhas, avisos = interpretar(payload, cols, "utf8" if cp == "utf8" else "normal")
            saida.append(dict(caso=titulo, impressora=imp["nome"], codepage=cp, colunas=cols, bytes=len(payload), linhas=linhas, avisos=avisos))
    with A._usar_codepage("cp850"):
        pg = A._bytes_teste_acentos()
    linhas, avisos = interpretar(pg, 32, "normal")
    saida.append(dict(caso="Testar acentos (impressora que honra todas as tabelas)", impressora="Mini 58mm", codepage="varias", colunas=32, bytes=len(pg), linhas=linhas, avisos=avisos))
    alvo = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if alvo: alvo.write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    total = 0
    for s in saida:
        print(f"\n=== {s['caso']} | {s['impressora']} | acentos={s['codepage']} | {s['bytes']} bytes")
        for ln in s["linhas"]:
            if ln["tipo"] == "texto":
                t = ln["texto"]
                if ln["alinhamento"] == 1: t = t.center(s["colunas"] // ln["largura"])
                marca = ("*" if ln["negrito"] else " ") + (f"{ln['largura']}x{ln['altura']}" if (ln["largura"], ln["altura"]) != (1, 1) else "   ")
                print(f"  {marca} |{t}")
            elif ln["tipo"] == "barras": print(f"       [codigo de barras {ln['texto']}]")
            elif ln["tipo"] == "qr": print("       [QR code]")
            elif ln["tipo"] == "corte": print("       ----- corte -----")
        for a in s["avisos"]: print("  AVISO:", a)
        total += len(s["avisos"])
    print(f"\nTOTAL DE AVISOS: {total}")
    return total

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
