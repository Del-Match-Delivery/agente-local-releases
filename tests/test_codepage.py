# Tabela de acentos (codepage) POR IMPRESSORA, modo ascii e pagina de teste (v5.80). Autocontido.
# Uso: venv_build\Scripts\python.exe tests\test_codepage.py [caminho\agente_local.py]
import importlib.util, sys, io, contextlib, copy, threading, re
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
spec = importlib.util.spec_from_file_location("agente_sob_teste_cp", str(SRC))
A = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(A)
A.cfg["font_size"] = 0
A.cfg.pop("codepage", None)
A.cfg.pop("paper_width_cols", None)

# Tira os comandos ESC/POS para medir so o texto: ESC @ e FS . tem 2 bytes; ESC t/E/a/d e
# GS !/V tem 3 (comando + 1 argumento).
_CMD = re.compile(rb"\x1b\x40|\x1c\x2e|[\x1b\x1d][\x00-\xff][\x00-\xff]")
def visivel(b):
    return _CMD.sub(b"", b).split(b"\n")

def so_ascii(b):
    return all(x < 0x80 for x in b)

# 1) normalizacao e precedencia
assert A._normaliza_cp("CP-850") == "cp850" and A._normaliza_cp(" ASCII ") == "ascii" and A._normaliza_cp("lixo") == ""
assert A._cp_da_impressora({"codepage": "ascii"}) == "ascii"
assert A._cp_da_impressora({}) == "cp850" and A._cp_da_impressora(None) == "cp850"
A.cfg["codepage"] = "cp860"
assert A._cp_da_impressora({}) == "cp860" and A._cp_da_impressora({"codepage": "cp1252"}) == "cp1252"
A.cfg.pop("codepage")
print("1) normalizacao/precedencia OK (impressora > cfg > cp850)")

# 2) contexto por thread: override vale so dentro do with e so nesta thread
assert A._cp() == "cp850"
visto = {}
with A._usar_codepage("ascii"):
    assert A._cp() == "ascii"
    with A._usar_codepage("cp860"):
        assert A._cp() == "cp860"
    assert A._cp() == "ascii"
    t = threading.Thread(target=lambda: visto.setdefault("outra", A._cp())); t.start(); t.join()
assert A._cp() == "cp850" and visto["outra"] == "cp850"
print("2) _usar_codepage OK (aninhado, restaura, isolado por thread)")

# 3) _enc: ascii tira acentos e nunca manda byte >= 0x80; cp850 inalterado
with A._usar_codepage("ascii"):
    b = A._enc(A._txt("Pão Maçã Açaí Café Avô 1º 2ª · x€"))
assert b == b"Pao Maca Acai Cafe Avo 1o 2a - xEUR", b
assert so_ascii(b)
assert A._enc("ã") == b"\xc6" and A._enc("Ç") == b"\x80", "cp850 padrao mudou"
with A._usar_codepage("ascii"):
    assert A._escpos_cp() == b"\x1c\x2e\x1b\x74\x00"
assert A._escpos_cp() == b"\x1c\x2e\x1b\x74\x02"
print("3) _enc ascii/cp850 e ESC t OK")

# 4) pagina de teste: 6 blocos, cada um com o seu ESC t; rotulos ASCII; cabe em 58 mm
pg = A._bytes_teste_acentos()
assert pg.startswith(b"\x1b\x40\x1c\x2e"), "tem que comecar com ESC @ + FS ."
for i, nome in enumerate(A._CP_TESTE, 1):
    esc = bytes([0x1b, 0x74, A.CP_TAB[nome]])
    k = pg.find(esc + f"{i}) {nome}\n".encode())
    assert k >= 0, f"bloco {nome} ausente"
assert "Pão Maçã".encode("cp850") in pg and "Pão Maçã".encode("cp1252") in pg and b"Pao Maca" in pg
def colunas(l):
    # o bloco utf8 tem 2 bytes por acento mas ocupa 1 coluna por letra no papel
    try: return len(l.decode("utf-8"))
    except UnicodeDecodeError: return len(l)
linhas = visivel(pg)
assert max(colunas(l) for l in linhas) <= 30, max(linhas, key=colunas)
assert len(A._CP_TESTE) == 7 and A._CP_TESTE[-2:] == ("utf8", "ascii"), A._CP_TESTE
k = pg.find(b"6) utf8\n")
assert k > 0 and "Pão Maçã Açaí".encode("utf-8") in pg[k:k + 80]
print("4) pagina de teste OK (7 blocos, utf8 no 6, ascii por ultimo, rotulos ASCII, <= 30 colunas)")

# 4b) utf8: texto em UTF-8 de verdade, sem mexer no resto
assert A._normaliza_cp("UTF-8") == "utf8" and A._cp_da_impressora({"codepage": "utf-8"}) == "utf8"
with A._usar_codepage("utf8"):
    assert A._enc(A._txt("Pão Maçã 1º")) == "Pão Maçã 1º".encode("utf-8")
    assert A._escpos_cp() == b"\x1c\x2e\x1b\x74\x00"
print("4b) utf8 OK (normaliza 'utf-8', _enc em UTF-8, FS . + ESC t 0)")

base = {"numero": 7, "created_at": "2026-09-29T15:00:00+00:00", "order_type": "delivery",
        "customer_name": "João Ação", "company_name": "Pão & Cia", "paper_width": "48",
        "itens": [{"nome": "Maçã do Amor", "quantidade": 1, "preco_cents": 900,
                   "adicionais": [{"nome": "Açúcar", "preco_cents": 0}], "observacao": "sem limão"}],
        "subtotal_cents": 900, "total_cents": 900, "payment_method": "pix",
        "scheduled_label": "AGENDADO: 30/09 AS 12:00"}

# 5) cupom inteiro em ascii: nenhum byte >= 0x80 (inclui o aviso 'NAO E DOCUMENTO FISCAL')
with A._usar_codepage("ascii"):
    txt = A._fmt(copy.deepcopy(base), "order", "receipt")
    pay = A._escpos_cp() + A._substituir_marcadores_escpos(txt)
assert so_ascii(pay), [hex(x) for x in pay if x >= 0x80][:5]
assert b"Maca do Amor" in pay and b"Joao Acao" in pay and b"NAO E DOCUMENTO FISCAL" in pay
A.cfg["font_size"] = 1
try:
    with A._usar_codepage("ascii"):
        bk = A._fmt(copy.deepcopy(base), "kitchen", "kitchen")
    assert isinstance(bk, bytes) and so_ascii(bk) and bk.startswith(b"\x1c\x2e\x1b\x74\x00")
    bk850 = A._fmt(copy.deepcopy(base), "kitchen", "kitchen")
    assert "Maçã".encode("cp850") in bk850 and bk850.startswith(b"\x1c\x2e\x1b\x74\x02")
finally:
    A.cfg["font_size"] = 0
print("5) cupom e comanda (fonte grande) em ascii: 100% ASCII; cp850 intacto")

# 6) proc_job de ponta a ponta: 1 job, 2 impressoras do caixa (normal 80 mm + mini 58 mm ascii)
enviados = []
def fake_imprimir(imp, dados):
    if isinstance(dados, str):
        payload = A._escpos_cp() + A._substituir_marcadores_escpos(dados)
    else:
        payload = dados
    enviados.append((imp.get("nome"), A._cp(), payload))
    return {"ok": True}
A._imprimir_com_roteamento = fake_imprimir
A.ef_update_job = lambda *a, **k: True
A.ef_get_order = lambda *a, **k: None
A._post = lambda *a, **k: ({}, 200)
A.cfg["impressoras"] = [
    {"nome": "Caixa", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "EPSON TM-T20"},
    {"nome": "Mini", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "POS-58",
     "codepage": "ascii", "colunas": 32},
]
job = {"id": "job-teste-cp", "printer_type": "receipt", "job_type": "order", "copies": 1,
       "created_at": "2026-09-29T15:00:00+00:00", "content": copy.deepcopy(base)}
with contextlib.redirect_stdout(io.StringIO()):
    A.proc_job(job)
por_nome = {n: (cp, p) for n, cp, p in enviados}
assert set(por_nome) == {"Caixa", "Mini"}, list(por_nome)
cp_c, p_c = por_nome["Caixa"]; cp_m, p_m = por_nome["Mini"]
assert cp_c == "cp850" and b"\x1b\x74\x02" in p_c and "Maçã".encode("cp850") in p_c, "caixa deve sair em cp850"
assert cp_m == "ascii" and so_ascii(p_m) and b"Maca do Amor" in p_m, "mini deve sair em ascii"
vis_m = visivel(p_m)
assert max(len(l) for l in vis_m) <= 32, max(vis_m, key=len)
vis_c = visivel(p_c)
assert max(len(l) for l in vis_c) == 48
print("6) proc_job: caixa em cp850/48 colunas e mini em ascii/32 colunas no MESMO job OK")

# 7) job com printer_id (UUID do servidor que a config local nao guarda): a impressora resolvida
#    mantem 'codepage' e 'colunas' (antes voltava so o nome e a mini utf8 recebia cp850/48)
A.cfg["impressoras"] = [
    {"nome": "Mini", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "POS-58",
     "codepage": "utf8", "colunas": 32},
]
r = A._res_imp_por_rede("receipt", printer_id="7d0c1f7e-0000-4000-8000-000000000000")
assert r and r.get("codepage") == "utf8" and r.get("colunas") == 32 and r.get("nome_impressora") == "POS-58", r
enviados.clear()
job_pid = dict(job, id="job-pid", printer_id="7d0c1f7e-0000-4000-8000-000000000000")
with contextlib.redirect_stdout(io.StringIO()):
    A.proc_job(job_pid)
assert enviados and enviados[0][1] == "utf8" and "Maçã do Amor".encode("utf-8") in enviados[0][2], enviados[:1]
print("7) job com printer_id mantem Acentos (utf8) e Colunas da impressora OK")

# 8) gaveta: bytes crus, iguais em qualquer codepage
for cp in ("cp850", "utf8", "ascii"):
    with A._usar_codepage(cp):
        g = A._fmt({"type": "command", "command": "open_drawer"}, "command", "receipt")
    assert g == b"\x1b\x70\x00\x19\xfa", (cp, g)
print("8) comando da gaveta sai cru (1B 70 00 19 FA) em cp850/utf8/ascii OK")

# 9) ascii: a troca que muda o tamanho ('½' -> '1/2') acontece ANTES de medir a coluna do preco
cpz = {"numero": 9, "order_type": "delivery", "company_name": "X", "paper_width": "32",
       "itens": [{"nome": "Pizza ½ Frango", "quantidade": 1, "preco_cents": 4990}], "total_cents": 4990}
with A._usar_codepage("ascii"):
    pay = A._substituir_marcadores_escpos(A._fmt(copy.deepcopy(cpz), "order", "receipt"))
linhas_pay = visivel(pay)
assert max(len(l) for l in linhas_pay) <= 32, max(linhas_pay, key=len)
# em 32 colunas o preco nao cabe ao lado do nome: vai para a linha de baixo, alinhado a direita
i_pz = next(k for k, l in enumerate(linhas_pay) if b"Pizza 1/2 Frango" in l)
assert any(l.endswith(b"R$ 49.90") for l in linhas_pay[i_pz:i_pz + 2]), linhas_pay[i_pz:i_pz + 2]
print("9) ascii: 'Pizza 1/2 Frango' alinhado e dentro de 32 colunas OK")
print("\nCODEPAGE: TUDO OK")
