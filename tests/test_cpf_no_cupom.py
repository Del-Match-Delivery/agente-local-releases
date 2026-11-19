# CPF na nota no cupom (v5.86), com o codigo REAL do agente e o papel interpretado byte a byte.
# Uso: venv_build\Scripts\python.exe tests\test_cpf_no_cupom.py [caminho\agente_local.py]
#   AGENTE_ANTERIOR=<agente_local.py da v5.85> compara tambem com a versao anterior: pedido SEM
#   documento sai IDENTICO, e pedido com documento so ganha a linha "CPF na nota:".
#
# Formatos REAIS que o servidor manda (repo ComprouTai, PR #1796 de 19/11/2026):
#   * job criado no banco: content.customer_cpf = digitos crus de orders.customer_cpf, injetados
#     pelo gatilho trg_print_content_schedule (enrich_print_content_with_schedule, migration
#     20261119140000) em TODO caminho que grava print_jobs.content
#   * cupom do PDV (o job nasce vazio e o agente busca o pedido no agent-get-order): resposta =
#     orders.* (customer_cpf cru) + order_items + items[] + fiscal
#   * agent-jobs (pedido aninhado em pt-BR): pedido.customer_cpf
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

CPF, CPF_FMT = "12345678909", "123.456.789-09"
CNPJ, CNPJ_FMT = "12345678000195", "12.345.678/0001-95"
LINHA = f"CPF na nota: {CPF_FMT}"
TEL = "16999990000"
NOME = "X-Burger"

def item():
    return {"name": NOME, "quantity": 1, "unit_price_cents": 2500, "notes": None, "addons": [],
            "printer_destinations": ["kitchen"]}

def content_banco(**extra):
    c = {"type": "order", "order_id": "fd8a8761-0000-0000-0000-000000000002", "order_number": "58",
         "order_type": "delivery", "customer_name": "Maria", "customer_phone": TEL,
         "payment_method": "pix", "notes": None, "items": [item()], "subtotal_cents": 2500,
         "discount_cents": 0, "delivery_fee_cents": 0, "total_cents": 2500,
         "delivery_address": "Rua A, 1", "created_at": "2026-11-19T17:00:00+00:00"}
    c.update(extra)
    return c

def linhas(mod, content, jt, pt, font_size=0):
    mod.cfg["font_size"] = font_size
    mod._print_barcode_servidor = None
    with contextlib.redirect_stdout(io.StringIO()):
        out = mod._fmt(copy.deepcopy(content), jt, pt)
    assert isinstance(out, str), f"{jt}/{pt}: esperado texto, veio {type(out)}"
    return out.split("\n")

TIPOS_CLIENTE = [("order", "receipt"), ("pickup", "pickup"), ("delivery", "delivery")]
TIPOS_PRODUCAO = [("kitchen", "kitchen"), ("bar", "bar")]

# 1) Funcoes puras
assert A._fmt_cpf_cnpj(CPF) == CPF_FMT
assert A._fmt_cpf_cnpj(CPF_FMT) == CPF_FMT, "ja mascarado entra e sai igual"
assert A._fmt_cpf_cnpj(CNPJ) == CNPJ_FMT
assert A._fmt_cpf_cnpj(" " + CNPJ_FMT + " ") == CNPJ_FMT
for vazio in (None, "", "   "):
    assert A._fmt_cpf_cnpj(vazio) == "", f"doc={vazio!r}"
assert A._fmt_cpf_cnpj("1234") == "1234", "tamanho estranho sai como veio (o servidor so manda 11 ou 14)"
assert A._linha_cpf_na_nota({"customer_cpf": CPF}) == LINHA
assert A._linha_cpf_na_nota({"customer_cpf": CNPJ}) == f"CPF na nota: {CNPJ_FMT}"
for sem in ({"customer_cpf": ""}, {"customer_cpf": None}, {}, None, "x"):
    assert A._linha_cpf_na_nota(sem) == "", f"content={sem!r}"
print("1) funcoes puras OK")

# 2) Caixa, retirada e entrega: a linha vem logo abaixo do Tel, e o cupom e o MESMO de sem
#    documento + essa linha (nada mais muda).
for jt, pt in TIPOS_CLIENTE:
    com = linhas(A, content_banco(customer_cpf=CPF), jt, pt)
    sem = linhas(A, content_banco(), jt, pt)
    assert LINHA in com, f"{jt}: sem a linha do CPF: {com}"
    i = com.index(LINHA)
    assert com[i - 1] == f"Tel: {TEL}", f"{jt}: linha acima do CPF: {com[i - 1]!r}"
    assert com[:i] + com[i + 1:] == sem, f"{jt}: alem da linha do CPF, algo mudou"
    assert LINHA not in sem and CPF not in "".join(sem)
print("2) caixa, retirada e entrega: 'CPF na nota:' logo abaixo do Tel; sem documento, cupom identico OK")

# 3) 'Imprimir dados do cliente' desligado: o Tel some, o CPF continua (e pedido do cliente para
#    a nota, nao dado de contato) — logo abaixo do Cliente.
for jt, pt in TIPOS_CLIENTE:
    o = linhas(A, content_banco(customer_cpf=CPF, print_customer_info=False), jt, pt)
    assert LINHA in o and f"Tel: {TEL}" not in o, f"{jt}: {o}"
    assert o[o.index(LINHA) - 1] == "Cliente: Maria", f"{jt}: {o[o.index(LINHA) - 1]!r}"
print("3) print_customer_info desligado: Tel nao sai, CPF na nota sai OK")

# 4) Cozinha e bar: nunca (a comanda de producao nao leva documento do cliente)
for jt, pt in TIPOS_PRODUCAO:
    # content.type decide o layout (o job de cozinha chega com type "kitchen"/"bar")
    o = linhas(A, content_banco(customer_cpf=CPF, type=jt), jt, pt)
    assert LINHA not in o and CPF_FMT not in "".join(o), f"{jt}: {o}"
print("4) cozinha e bar: sem a linha OK")

# 5) CNPJ e documento ja mascarado pelo servidor
for jt, pt in TIPOS_CLIENTE:
    assert f"CPF na nota: {CNPJ_FMT}" in linhas(A, content_banco(customer_cpf=CNPJ), jt, pt), jt
    assert LINHA in linhas(A, content_banco(customer_cpf=CPF_FMT), jt, pt), jt
print("5) CNPJ com mascara propria; documento ja mascarado nao e mascarado duas vezes OK")

# ---- ponta a ponta: proc_job real + bytes ESC/POS interpretados como a termica faria ----
CAIXA = [{"nome": "Caixa", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "CAIXA", "colunas": 48}]
CAIXA58 = [{"nome": "Mini 58", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32", "nome_impressora": "POS58", "colunas": 32}]

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

def job(content, pt="receipt", jt="order"):
    return {"id": "job-cpf-1", "printer_type": pt, "job_type": jt, "copies": 1,
            "created_at": "2026-11-19T17:00:00+00:00", "content": content}

# 6) Job criado no banco (poll): o content ja traz customer_cpf (gatilho do print_jobs)
t, buscas = papel(A, job(content_banco(customer_cpf=CPF)), CAIXA)
assert buscas == [], "content com itens nao busca o pedido"
assert any(x.strip() == LINHA for x in t), t
t0, _ = papel(A, job(content_banco()), CAIXA)
assert [x for x in t if x.strip() != LINHA] == t0, "job do banco: o cupom so ganha a linha do CPF"
print("6) job do banco (poll) -> papel: 'CPF na nota:' OK")

# 7) Cupom do PDV: job nasce VAZIO, o agente busca o pedido no agent-get-order (orders.* cru)
pedido = {"id": "fd8a8761-0000-0000-0000-000000000002", "order_number": 58, "order_type": "counter",
          "customer_name": "Joao", "customer_phone": None, "customer_cpf": CPF, "payment_method": "cash",
          "subtotal_cents": 2500, "discount_cents": 0, "delivery_fee_cents": 0, "total_cents": 2500,
          "created_at": "2026-11-19T17:00:00+00:00",
          "order_items": [{"product_id": "p1", "name_snapshot": NOME, "quantity": 1, "price_cents_snapshot": 2500,
                           "notes": None, "addons_json": []}],
          "fiscal": None}
def resposta_get_order(cpf=CPF):
    return dict(pedido, customer_cpf=cpf,
                items=[{"name": NOME, "size_name": "", "quantity": 1, "unit_price_cents": 2500,
                        "notes": None, "addons": [], "category_name": "", "barcode": None, "sku": None}])
job_pdv = job(content_banco(items=[], order_type="counter", customer_cpf=None, triggered_by="trigger_on_new"))
t, buscas = papel(A, job_pdv, CAIXA, get_order=resposta_get_order())
assert buscas == [pedido["id"]], "job vazio: o agente busca o pedido"
assert any(x.strip() == LINHA for x in t), t
t0, _ = papel(A, job_pdv, CAIXA, get_order=resposta_get_order(cpf=None))
assert [x for x in t if x.strip() != LINHA] == t0, "PDV: o cupom so ganha a linha do CPF"
print("7) cupom do PDV (job vazio -> agent-get-order) -> papel: 'CPF na nota:' OK")

# 8) Papel de 58 mm (32 colunas): a linha cabe inteira ("CPF na nota: 00.000.000/0000-00" = 33 chars
#    quebra; CPF cabe). O interpretar avisa se passar da largura.
t, _ = papel(A, job(content_banco(customer_cpf=CPF)), CAIXA58)
assert any(x.strip() == LINHA for x in t), t
t, _ = papel(A, job(content_banco(customer_cpf=CNPJ)), CAIXA58)
assert CNPJ_FMT in "".join(x.strip() for x in t).replace(" ", ""), "CNPJ em 58 mm tem que sair inteiro (quebrado se preciso)"
print("8) 58 mm: CPF cabe numa linha; CNPJ sai inteiro OK")

# 9) Comparacao com a versao anterior (opcional, AGENTE_ANTERIOR)
if ANT is not None:
    for jt, pt in TIPOS_CLIENTE + TIPOS_PRODUCAO:
        tp = jt if (jt, pt) in TIPOS_PRODUCAO else "order"
        assert linhas(A, content_banco(type=tp), jt, pt) == linhas(ANT, content_banco(type=tp), jt, pt), \
            f"{jt}: pedido sem documento tem que sair IDENTICO a versao anterior"
        antes = linhas(ANT, content_banco(customer_cpf=CPF, type=tp), jt, pt)
        agora = linhas(A, content_banco(customer_cpf=CPF, type=tp), jt, pt)
        assert LINHA not in antes and CPF not in "".join(antes), f"{jt}: a anterior NAO imprimia o CPF"
        assert [x for x in agora if x != LINHA] == antes, f"{jt}: so a linha do CPF e nova"
    a0, _ = papel(ANT, job_pdv, CAIXA, get_order=resposta_get_order())
    a1, _ = papel(A, job_pdv, CAIXA, get_order=resposta_get_order())
    print("\n--- cupom do PDV na versao anterior ---")
    for x in a0:
        if x.strip(): print("   |" + x)
    print("--- cupom do PDV nesta versao ---")
    for x in a1:
        if x.strip(): print("   |" + x)
    assert [x for x in a1 if x.strip() != LINHA] == a0
    print("9) comparado com a versao anterior: sem documento identico; com documento so ganha 'CPF na nota:' OK")

print("\nCPF NA NOTA NO CUPOM: TUDO OK")
