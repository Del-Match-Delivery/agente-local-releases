# SKU do produto no cupom (v5.84), com o codigo REAL do agente e o papel interpretado byte a byte.
# Uso: venv_build\Scripts\python.exe tests\test_sku_no_cupom.py [caminho\agente_local.py]
#   AGENTE_ANTERIOR=<agente_local.py da v5.83> compara tambem com a versao anterior: produto SEM SKU
#   sai IDENTICO, e produto com codigo de barras E SKU so ganha a linha do SKU.
#
# Formatos REAIS que o servidor manda (repo ComprouTai, PR #926 de 23/10/2026):
#   * job criado no banco (create_print_jobs_on_new_order/_on_accept, reissue_order_print_jobs,
#     create_public_order): content.items[] = {name, quantity, unit_price_cents, notes, addons,
#     barcode, sku, printer_destinations}
#   * cupom do PDV (o job nasce vazio e o agente busca o pedido no agent-get-order): resposta =
#     orders.* + order_items + items[] {name, size_name, quantity, unit_price_cents, notes, addons,
#     category_name, barcode, sku} + fiscal
import importlib.util, sys, io, contextlib, copy, os
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI.parent / "agente_local.py"

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    return m

A = load(SRC, "agente_sob_teste")
SIM = load(AQUI / "simular_impressao.py", "sim")   # so o interpretar() (papel a partir dos bytes)
ANT = load(Path(os.environ["AGENTE_ANTERIOR"]), "agente_anterior") if os.environ.get("AGENTE_ANTERIOR") else None

EAN = "7899026401792"            # EAN-13 valido (Colorama 9ml Base Setim, Emy)
SKU = "COL-9ML-SETIM"
NOME = "Colorama Esmalte 9ml Base Setim"

def item_banco(barcode=EAN, sku=SKU):
    return {"name": NOME, "quantity": 1, "unit_price_cents": 990, "notes": None, "addons": [],
            "barcode": barcode, "sku": sku, "printer_destinations": ["kitchen"]}

def content_banco(itens, **extra):
    c = {"type": "order", "order_id": "fd8a8761-0000-0000-0000-000000000001", "order_number": "57",
         "order_type": "delivery", "customer_name": "Maria", "customer_phone": "16999990000",
         "payment_method": "pix", "notes": None, "items": itens, "subtotal_cents": 990,
         "discount_cents": 0, "delivery_fee_cents": 0, "total_cents": 990,
         "delivery_address": "Rua A, 1", "created_at": "2026-10-29T17:00:00+00:00"}
    c.update(extra)
    return c

def linhas(mod, content, jt, pt, font_size=0):
    mod.cfg["font_size"] = font_size
    mod._print_barcode_servidor = None
    with contextlib.redirect_stdout(io.StringIO()):
        out = mod._fmt(copy.deepcopy(content), jt, pt)
    assert isinstance(out, str), f"{jt}/{pt}: esperado texto, veio {type(out)}"
    return out.split("\n")

TIPOS = [("order", "receipt"), ("pickup", "pickup"), ("delivery", "delivery"), ("kitchen", "kitchen")]

# 1) Funcoes puras
assert A._sku_do_item({"sku": SKU}) == SKU
assert A._sku_do_item({"sku": "  " + SKU + " "}) == SKU, "SKU sai sem espacos nas pontas"
for vazio in (None, "", "   ", 0):
    assert A._sku_do_item({"sku": vazio, "barcode": EAN}) == "", f"sku={vazio!r} nao imprime nada"
assert A._sku_do_item({"barcode": EAN}) == "", "item sem a chave sku"
assert A._sku_do_item({"sku": EAN, "barcode": EAN}) == "", "SKU igual ao codigo de barras nao repete"
assert A._sku_do_item(None) == "" and A._sku_do_item("x") == ""
assert A._codigo_do_item({"sku": SKU}) == "", "sku NAO e mais lido como codigo de barras"
assert A._codigo_do_item({"barcode": EAN, "sku": SKU}) == EAN
assert A._linha_sku_item({"sku": SKU}) == f"  SKU: {SKU}"
assert A._linha_sku_item({"barcode": EAN}) == ""
print("1) funcoes puras OK")

# 2) Codigo de barras E SKU: em todo cupom que mostra 'Cod:', a linha do SKU vem logo abaixo, e o
#    cupom e o MESMO de sem SKU + essa linha (nada mais muda).
for jt, pt in TIPOS:
    com = linhas(A, content_banco([item_banco()]), jt, pt)
    sem = linhas(A, content_banco([item_banco(sku=None)]), jt, pt)
    i = com.index(f"  Cod: {EAN}")
    assert com[i + 1] == f"  SKU: {SKU}", f"{jt}: linha abaixo do Cod: {com[i + 1]!r}"
    assert com[:i + 1] + com[i + 2:] == sem, f"{jt}: alem da linha do SKU, algo mudou"
    assert f"  SKU: {SKU}" not in sem
print("2) codigo de barras + SKU: 'SKU:' logo abaixo do 'Cod:' no caixa, retirada, entrega e cozinha OK")

# 3) So SKU (sem codigo de barras): sai 'SKU:', nao 'Cod:' — inclusive barcode "" (o banco manda
#    p.barcode cru, que pode ser string vazia)
for barcode in (None, ""):
    for jt, pt in TIPOS:
        so = linhas(A, content_banco([item_banco(barcode=barcode)]), jt, pt)
        assert f"  SKU: {SKU}" in so, f"{jt}: SKU sozinho tem que sair"
        assert not any(l.startswith("  Cod:") for l in so), f"{jt}: sem codigo de barras nao sai 'Cod:'"
print("3) so SKU: '  SKU: <sku>' sem linha 'Cod:' OK")

# 4) SKU igual ao codigo de barras, ou SKU vazio: cupom IGUAL ao de sem SKU
for jt, pt in TIPOS:
    sem = linhas(A, content_banco([item_banco(sku=None)]), jt, pt)
    for sku in (EAN, "", "   "):
        assert linhas(A, content_banco([item_banco(sku=sku)]), jt, pt) == sem, f"{jt}: sku={sku!r} mudou o cupom"
print("4) SKU vazio ou igual ao codigo de barras: cupom identico ao de sem SKU OK")

# 5) Ajuste 'Imprimir codigo de barras' LIGADO: barras EAN-13 e, abaixo, a linha do SKU em texto
for jt, pt in TIPOS:
    o = linhas(A, content_banco([item_banco()], print_barcode=True), jt, pt)
    i = o.index(f"[[EAN13:{EAN}]]")
    assert o[i + 1] == f"  SKU: {SKU}", f"{jt}: abaixo das barras: {o[i + 1]!r}"
print("5) com barras EAN-13 ligadas: 'SKU:' em texto logo abaixo das barras OK")

# ---- ponta a ponta: proc_job real + bytes ESC/POS interpretados como a termica faria ----
CAIXA = [{"nome": "Caixa", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "CAIXA", "colunas": 48}]
CAIXA58 = [{"nome": "Mini 58", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "POS58", "colunas": 32}]
COZINHA = [{"nome": "Cozinha", "area": "cozinha", "printer_type": "kitchen", "tipo": "comum_win32", "nome_impressora": "COZ", "colunas": 48}]

def papel(mod, job, impressoras, font_size=0, get_order=None):
    enviados, buscas = [], []
    def fake(imp, d):
        sufixo = mod._escpos_sufixo() if hasattr(mod, "_escpos_sufixo") else b"\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00"
        p = d if isinstance(d, bytes) else (mod._escpos_cp() + mod._escpos_font_prefix() + mod._substituir_marcadores_escpos(d) + sufixo)
        enviados.append((imp, p)); return {"ok": True}
    def fake_get(oid):
        buscas.append(oid); return copy.deepcopy(get_order) if get_order else None
    mod._imprimir_com_roteamento = fake; mod.ef_get_order = fake_get
    mod.ef_update_job = lambda *a, **k: True; mod._post = lambda *a, **k: ({}, 200)
    mod.cfg["impressoras"] = impressoras; mod.cfg["font_size"] = font_size
    mod.cfg["restaurant_name"] = "ComprouTai"; mod._print_barcode_servidor = None
    mod.cfg.pop("paper_width_cols", None); mod.cfg.pop("codepage", None)
    with contextlib.redirect_stdout(io.StringIO()):
        mod.proc_job(copy.deepcopy(job))
    assert len(enviados) == 1, f"esperado 1 envio, veio {len(enviados)}"
    imp, payload = enviados[0]
    ls, avisos = SIM.interpretar(payload, int(imp.get("colunas") or 48), "normal")
    assert not avisos, avisos
    return [l["texto"] for l in ls if l["tipo"] == "texto"], buscas

def abaixo_do_item(textos):
    i = next(k for k, t in enumerate(textos) if NOME in t)
    return textos[i + 1:i + 3]

def job(content, pt="receipt", jt="order"):
    return {"id": "job-sku-1", "printer_type": pt, "job_type": jt, "copies": 1,
            "created_at": "2026-10-29T17:00:00+00:00", "content": content}

# 6) Job criado no banco (poll): o content ja traz items com barcode e sku
t, buscas = papel(A, job(content_banco([item_banco()])), CAIXA)
assert buscas == [], "content com itens nao busca o pedido"
assert abaixo_do_item(t) == [f"  Cod: {EAN}", f"  SKU: {SKU}"], abaixo_do_item(t)
print("6) job do banco (poll) -> papel: item / Cod / SKU OK")

# 7) Cupom do PDV: job nasce VAZIO, o agente busca o pedido no agent-get-order (formato real da edge)
pedido = {"id": "fd8a8761-0000-0000-0000-000000000001", "order_number": 57, "order_type": "counter",
          "customer_name": None, "payment_method": "cash", "subtotal_cents": 990, "discount_cents": 0,
          "delivery_fee_cents": 0, "total_cents": 990, "created_at": "2026-10-29T17:00:00+00:00",
          "order_items": [{"product_id": "p1", "name_snapshot": NOME, "quantity": 1, "price_cents_snapshot": 990,
                           "notes": None, "addons_json": []}],
          "fiscal": None}
def resposta_get_order(barcode=EAN, sku=SKU):
    return dict(pedido, items=[{"name": NOME, "size_name": "", "quantity": 1, "unit_price_cents": 990,
                                "notes": None, "addons": [], "category_name": "", "barcode": barcode, "sku": sku}])
job_pdv = job(content_banco([], order_type="counter", triggered_by="trigger_on_new"))
t, buscas = papel(A, job_pdv, CAIXA, get_order=resposta_get_order())
assert buscas == [pedido["id"]], "job vazio: o agente busca o pedido"
assert abaixo_do_item(t) == [f"  Cod: {EAN}", f"  SKU: {SKU}"], abaixo_do_item(t)
t0, _ = papel(A, job_pdv, CAIXA, get_order=resposta_get_order(sku=None))
assert [x for x in t if x != f"  SKU: {SKU}"] == t0, "PDV: o cupom so ganha a linha do SKU"
print("7) cupom do PDV (job vazio -> agent-get-order) -> papel: item / Cod / SKU OK")

# 8) Papel de 58 mm (32 colunas) com SKU comprido: quebra sem passar da largura (interpretar avisa)
longo = "SKU-MUITO-COMPRIDO-DA-LOJA-0000000123456789"
t, _ = papel(A, job(content_banco([item_banco(sku=longo)])), CAIXA58)
junto = "".join(x.strip() for x in t)
assert longo in junto.replace(" ", ""), "SKU comprido tem que sair inteiro (quebrado em linhas)"
print("8) SKU comprido em 58 mm: quebra dentro das 32 colunas OK")

# 9) Comanda de cozinha em fonte grande (caminho que devolve BYTES, separado do texto)
t, _ = papel(A, job(content_banco([item_banco()]), pt="kitchen", jt="kitchen"), COZINHA, font_size=1)
assert f"  Cod: {EAN}" in t and f"  SKU: {SKU}" in t, t
assert t.index(f"  SKU: {SKU}") == t.index(f"  Cod: {EAN}") + 1, "SKU logo abaixo do Cod na comanda"
t0, _ = papel(A, job(content_banco([item_banco(sku=None)]), pt="kitchen", jt="kitchen"), COZINHA, font_size=1)
assert [x for x in t if x != f"  SKU: {SKU}"] == t0, "comanda em fonte grande: so ganha a linha do SKU"
print("9) comanda da cozinha em fonte grande (bytes): Cod / SKU OK")

# 10) Comparacao com a versao anterior (opcional, AGENTE_ANTERIOR)
if ANT is not None:
    for jt, pt in TIPOS:
        for itens in ([item_banco(sku=None)], [item_banco(barcode=None, sku=None)], [item_banco(sku=EAN)]):
            assert linhas(A, content_banco(itens), jt, pt) == linhas(ANT, content_banco(itens), jt, pt), \
                f"{jt}: produto sem SKU (ou SKU = codigo) tem que sair IDENTICO a versao anterior"
        antes = linhas(ANT, content_banco([item_banco()]), jt, pt)
        agora = linhas(A, content_banco([item_banco()]), jt, pt)
        assert f"  SKU: {SKU}" not in antes and SKU not in "".join(antes), f"{jt}: a anterior NAO imprimia o SKU"
        assert [x for x in agora if x != f"  SKU: {SKU}"] == antes, f"{jt}: so a linha do SKU e nova"
    a0, _ = papel(ANT, job_pdv, CAIXA, get_order=resposta_get_order())
    a1, _ = papel(A, job_pdv, CAIXA, get_order=resposta_get_order())
    print("\n--- cupom do PDV na versao anterior ---")
    for x in a0:
        if x.strip(): print("   |" + x)
    print("--- cupom do PDV nesta versao ---")
    for x in a1:
        if x.strip(): print("   |" + x)
    assert [x for x in a1 if x != f"  SKU: {SKU}"] == a0
    print("10) comparado com a versao anterior: sem SKU identico; com SKU so ganha a linha 'SKU:' OK")

print("\nSKU NO CUPOM: TUDO OK")
