# Ajustes de layout do cupom (v5.87, aba Impressao). Autocontido.
# Uso: venv_build\Scripts\python.exe tests\test_ajustes_cupom.py [caminho\agente_local.py]
#   AGENTE_ANTERIOR=<agente_local.py da v5.86> compara tambem com a versao anterior: com os
#   ajustes novos DESLIGADOS o texto e os bytes ESC/POS tem que ser IDENTICOS (cupom de caixa,
#   mesa, conferencia, retirada, entrega, cozinha e bar; 32/42/48 colunas; fonte 0..3; secoes).
#
# Com um mini interpretador ESC/POS sobre o payload REAL valida:
#   1. negrito so na observacao ('>>' do item, Obs/OBS do pedido), no cupom e na comanda
#      (caminho texto e caminho bytes), e nada a mais em negrito;
#   2. valores estilo mercado: nome sozinho, 'q x unitario ..... total' e adicionais na MESMA
#      coluna da direita, somando o subtotal; totais com virgula e 'R$' so no TOTAL;
#   3. nome do produto Media/Grande: so o nome cresce, adicionais e observacao em normal;
#   4. formato de entrega economico/compacto: menos papel, aviso fiscal mantido, so em ENTREGA;
#   5. nome da loja na comanda, abaixo do titulo e antes do PEDIDO, sem repetir o selo de filial;
#   6. NENHUMA linha passa da largura fisica do papel em nenhuma combinacao de ajustes.
import importlib.util, sys, io, os, contextlib, tempfile, itertools, re, copy
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="agente_teste_ajustes_")   # DATA_DIR isolado

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    return m

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
A = load(SRC, "agente_587")
ANT = load(Path(os.environ["AGENTE_ANTERIOR"]), "agente_anterior") if os.environ.get("AGENTE_ANTERIOR") else None

CHAVES = ("font_size","fonte_secoes","negrito_cupom","mais_escuro","espaco_linhas","corte","avanco_linhas",
          "vias_cupom","paper_width_cols","codepage","negrito_obs","valores_mercado","fonte_nome_produto",
          "formato_delivery","loja_na_comanda")
def reset(mod, **kw):
    for k in CHAVES: mod.cfg.pop(k, None)
    mod.cfg["restaurant_name"] = "Loja Pareada"
    mod.cfg.update(kw)

# ── mini interpretador ESC/POS (mesmo do test_fonte): (texto, wmult, hmult, negrito) ───────
def simular(payload):
    linhas=[]; buf=bytearray(); gs=0x00; bold=False; i=0; n=len(payload); est=None
    while i < n:
        b=payload[i]
        if b==0x1c and i+1<n and payload[i+1]==0x2e: i+=2; continue
        if b==0x1b:
            c=payload[i+1]
            if c==0x40: gs=0x00; bold=False; i+=2; continue
            if c==0x45: bold = payload[i+2]==1; i+=3; continue
            if c==0x21: gs = 0x00 if payload[i+2]==0 else gs; i+=3; continue
            if c in (0x74,0x61,0x64,0x33,0x47,0x4d): i+=3; continue
            i+=2; continue
        if b==0x1d:
            c=payload[i+1]
            if c==0x21: gs=payload[i+2]; i+=3; continue
            if c==0x56: i+=3; continue
            if c in (0x68,0x77,0x48,0x66): i+=3; continue
            if c==0x6b: ln=payload[i+3]; i+=4+ln; continue
            i+=3; continue
        if b==0x0a:
            t=buf.decode("cp850","replace")
            if t.strip():
                g,bo = est if est is not None else (gs,bold)
                linhas.append((t.rstrip(), (g>>4 & 0x7)+1, (g & 0x7)+1, bo))
            buf=bytearray(); est=None; i+=1; continue
        if est is None and b != 0x20: est=(gs,bold)
        buf.append(b); i+=1
    return linhas

def bruto(mod, content, jt, pt, imp=None):
    imp = imp or {}
    with mod._usar_codepage(mod._cp_da_impressora(imp)), mod._usar_fonte(mod._fonte_da_impressora(imp)):
        out = mod._fmt(copy.deepcopy(content), jt, pt, imp)
        if isinstance(out, str):
            pay = mod._escpos_cp() + mod._escpos_font_prefix() + mod._substituir_marcadores_escpos(out) + mod._escpos_sufixo()
        else:
            pay = out
    return out, pay

def papel(content, jt="order", pt="receipt", cols=48, mod=None):
    _, pay = bruto(mod or A, content, jt, pt, {"colunas": cols})
    return simular(pay)

def textos(ls): return [t for t,_,_,_ in ls]

def nenhuma_estoura(ls, w_fis, ctx=""):
    for t,wm,hm,b in ls:
        assert len(t)*wm <= w_fis, f"[{ctx}] linha estoura {w_fis} col: {wm}x{hm} '{t}' ({len(t)} ch)"

# ── pedidos de exemplo ───────────────────────────────────────────────────────────────────
ITENS = [
    {"nome":"X-Burger Artesanal","qtd":1,"preco_cents":3290,
     "adicionais":[{"nome":"Bacon","preco_cents":400},{"nome":"Cheddar","preco_cents":350}],
     "notes":"sem cebola, pao bem tostado"},
    {"nome":"Pizza Calabresa","tamanho":"G","qtd":1,"preco_cents":4500,
     "adicionais":[{"nome":"G","preco_cents":0},{"nome":"Borda catupiry","preco_cents":800}]},
    {"nome":"Coca-Cola Lata","qtd":2,"preco_cents":600},
    {"nome":"Combo Familia Supremo com Batata Frita Grande e Refrigerante de Dois Litros","qtd":3,"preco_cents":12990,
     "adicionais":[{"nome":"Molho especial da casa com ervas finas e alho assado","preco_cents":250},
                   {"nome":"Sachê de ketchup","preco_cents":0}],
     "notes":"cortar em 8 pedacos\nmandar guardanapo extra e talheres descartaveis para quatro pessoas"},
]
SUB = 3290+400+350 + 4500+800 + 1200 + 3*(12990+250)
ENTREGA = {"type":"order","numero":"123","order_type":"delivery","customer_name":"Maria Souza",
           "customer_phone":"(16) 99999-1234","customer_cpf":"12345678909",
           "company_name":"BURGUER DO ZE","company_address":"Av. Brasil, 1450 - Centro","company_phone":"(16) 3333-4444",
           "created_at":"2026-11-24T22:42:00Z","itens":ITENS,
           "subtotal_cents":SUB,"delivery_fee_cents":700,"discount_cents":500,"total_cents":SUB+700-500,
           "payment_method":"cash","notes":"Interfone quebrado, ligar ao chegar",
           "delivery_address":{"street":"Rua das Flores","number":"123","complement":"Apto 42","neighborhood":"Centro",
                               "city":"Araraquara","state":"SP","reference":"em frente a padaria"},
           "footer_message":"Obrigado pela preferencia!"}
ENTREGA_STR = dict(ENTREGA, delivery_address="Rua das Flores, 123 - Apto 42 - Centro - Araraquara/SP",
                   scheduled_label="AGENDADO: 24/11 AS 20:30", store_name="Burguer do Ze Filial Centro")
MESA = dict(ENTREGA, order_type="dine_in", table_number="7", delivery_address=None, delivery_fee_cents=0,
            total_cents=SUB-500)
CONF = dict(MESA, type="pre_closing_full", service_fee_cents=1100, total_cents=SUB-500+1100)
RETIRADA = dict(ENTREGA, type="pickup", order_type="pickup", pickup_code="A12", delivery_address=None)
TIPO_ENT = dict(ENTREGA, type="delivery")
SEM_SUB = dict(ENTREGA, subtotal_cents=0, total_cents=0, discount_cents=0)
COZ = {"type":"kitchen","numero":"123","order_type":"delivery","customer_name":"Maria Souza",
       "created_at":"2026-11-24T22:42:00Z","itens":ITENS,"notes":"Interfone quebrado, ligar ao chegar",
       "store_name":"Burguer do Ze"}
BAR = dict(COZ, type="bar", itens=[{"nome":"Caipirinha de Limao","qtd":2,"preco_cents":0,"notes":"pouco gelo",
                                   "is_addon_line":True,"parent_name":"Combo Happy Hour"}])
CAT = dict(ENTREGA, print_item_category=True,
           itens=[dict(ITENS[0], category_name="Lanches"), dict(ITENS[2], category_name="Bebidas")])

CUPONS = [("entrega",ENTREGA,"order","receipt"),("entrega_str",ENTREGA_STR,"order","receipt"),
          ("mesa",MESA,"order","receipt"),("conferencia",CONF,"pre_closing_full","receipt"),
          ("retirada",RETIRADA,"pickup","receipt"),("tipo_delivery",TIPO_ENT,"delivery","delivery"),
          ("sem_subtotal",SEM_SUB,"order","receipt"),("categoria",CAT,"order","receipt"),
          ("cozinha",COZ,"kitchen","kitchen"),("bar",BAR,"bar","bar")]
BASES = [dict(), dict(font_size=1), dict(font_size=2), dict(font_size=3),
         dict(fonte_secoes={"itens":2,"total":3,"loja":1}), dict(fonte_secoes={"itens":1,"pedido":3}),
         dict(negrito_cupom=True, mais_escuro=True, espaco_linhas=0)]

# ── 0. sem ajustes novos = v5.86 byte a byte ────────────────────────────────────────────
if ANT:
    n = 0; corrigidos = 0
    for (nome, ct, jt, pt), base, cols in itertools.product(CUPONS, BASES, (32, 42, 48)):
        reset(A, **copy.deepcopy(base)); reset(ANT, **copy.deepcopy(base))
        a_txt, a_pay = bruto(A, ct, jt, pt, {"colunas": cols})
        b_txt, b_pay = bruto(ANT, ct, jt, pt, {"colunas": cols})
        if a_pay != b_pay:
            # Unica diferenca aceita: a anterior ESTOURAVA o papel (nome comprido com 'Nome dos
            # itens' Grande/Extra, cortado no meio pela impressora) e a nova quebra na palavra.
            # Config padrao (BASES[0]) nunca pode mudar.
            assert base, f"{nome} {cols}: cupom PADRAO difere da versao anterior"
            try: nenhuma_estoura(simular(b_pay), cols); estourava = False
            except AssertionError: estourava = True
            assert estourava, f"{nome} {base} {cols}: bytes diferem da anterior sem ela estourar o papel"
            nenhuma_estoura(simular(a_pay), cols, f"{nome} {base} {cols}")
            corrigidos += 1
        n += 1
    print(f"0) sem ajustes novos: {n-corrigidos} cupons/comandas IDENTICOS a versao anterior (texto e bytes); "
          f"{corrigidos} so diferem onde a anterior estourava o papel e agora quebra na palavra OK")
else:
    print("0) (AGENTE_ANTERIOR nao informado: comparacao com a versao anterior pulada)")

def linha_com(ls, trecho):
    return next(l for l in ls if trecho in l[0])

# ── 1. negrito so na observacao ─────────────────────────────────────────────────────────
reset(A, negrito_obs=True)
ls = papel(ENTREGA)
for t,wm,hm,b in ls:
    obs = t.lstrip().startswith(">>") or t.startswith("Obs:") or "guardanapo" in t or "descartaveis" in t or "quatro pessoas" in t
    if obs: assert b, f"observacao sem negrito: {t!r}"
    elif b: assert t.strip() in ("NÃO É DOCUMENTO FISCAL", "PEDIDO #123"), f"negrito sobrando: {t!r}"
assert linha_com(ls, ">> sem cebola")[3] and linha_com(ls, "Obs: Interfone")[3]
assert not linha_com(ls, "+ Bacon")[3] and not linha_com(ls, "X-Burger")[3]
for fs in (0, 2):   # comanda: caminho texto (fonte normal) e caminho bytes (fonte maior)
    reset(A, negrito_obs=True, font_size=fs)
    lc = papel(COZ, "kitchen", "kitchen")
    assert linha_com(lc, ">> sem cebola")[3] and linha_com(lc, "OBS: Interfone")[3], fs
    assert not linha_com(lc, "+ Bacon")[3], fs
    assert not linha_com(lc, "X-Burger")[3], fs
# com o negrito do cupom inteiro ligado, o marcador da observacao nem aparece (tudo ja e negrito)
reset(A, negrito_obs=True, negrito_cupom=True)
txt, _ = bruto(A, ENTREGA, "order", "receipt", {"colunas": 48})
assert "[[B_ON]]" not in txt
print("1) negrito so na observacao (cupom e comanda, texto e bytes) OK")

# ── 2. valores estilo mercado ───────────────────────────────────────────────────────────
VAL = re.compile(r"(-?\d+,\d\d)$")
for cols in (32, 42, 48):
    reset(A, valores_mercado=True)
    ls = papel(SEM_SUB, cols=cols); tx = textos(ls)
    nenhuma_estoura(ls, cols, f"mercado {cols}")
    i0 = tx.index(next(t for t in tx if t.startswith("X-Burger")))
    i1 = tx.index(next(t for t in tx if t.startswith("Subtotal")))
    bloco = tx[i0:i1]
    assert tx[i0+1] == A._par("  1 x 32,90", "32,90", cols)[0], (cols, tx[i0+1])
    assert any(t.startswith("  + Bacon") and t.endswith("4,00") and len(t) == cols for t in bloco), bloco
    assert any(t.startswith("  2 x 6,00") and t.endswith("12,00") for t in bloco), bloco
    assert not any("R$" in t for t in bloco), "R$ dentro da coluna de itens"
    assert not any(t.startswith("[ ") for t in bloco), "linha '[ 1x ]' antiga no modo mercado"
    vals = [int(VAL.search(t).group(1).replace(",", "")) for t in bloco if VAL.search(t)]
    assert sum(vals) == SUB, (cols, sum(vals), SUB)   # a coluna fecha com o subtotal
    for t in bloco:
        if VAL.search(t): assert len(t) == cols, f"valor fora da coluna da direita: {t!r}"
    assert tx[i1] == A._par("Subtotal", A._M(SUB), cols)[0]
    tot = next(t for t in tx if t.startswith("TOTAL"))
    assert tot.endswith(f"R$ {A._M(SUB+700)}"), tot
    assert next(t for t in tx if t.startswith("Taxa de entrega")).endswith("7,00")
# adicional de item com quantidade 3: valor ja multiplicado (3 x 2,50 = 7,50)
reset(A, valores_mercado=True)
tx = textos(papel(ENTREGA))
assert any("Molho especial" in t or t.rstrip().endswith("7,50") for t in tx)
assert any(t.endswith("7,50") for t in tx), "adicional nao multiplicado pela quantidade do item"
# retirada e conferencia de mesa tambem (todo cupom do cliente)
assert any(t.startswith("TOTAL") and "R$" in t and "," in t for t in textos(papel(RETIRADA, "pickup")))
assert any(t.startswith("Taxa de servico") and t.endswith("11,00") for t in textos(papel(CONF, "pre_closing_full")))
# comanda nao tem valores: identica com e sem o ajuste
reset(A); c0 = bruto(A, COZ, "kitchen", "kitchen", {"colunas": 48})
reset(A, valores_mercado=True); c1 = bruto(A, COZ, "kitchen", "kitchen", {"colunas": 48})
assert c0 == c1
print("2) valores estilo mercado: coluna da direita soma o subtotal, virgula, R$ so no TOTAL OK")

# ── 3. nome do produto maior; adicionais e obs em normal ───────────────────────────────
for escala, (wm_e, hm_e) in ((1, (1, 2)), (2, (2, 2))):
    for mercado in (None, True):
        for cols in (32, 48):
            reset(A, fonte_nome_produto=escala, valores_mercado=mercado)
            ls = papel(ENTREGA, cols=cols)
            nenhuma_estoura(ls, cols, f"nome {escala} mercado={mercado} {cols}")
            nm = linha_com(ls, "X-Burger")
            assert (nm[1], nm[2]) == (wm_e, hm_e), (escala, mercado, cols, nm)
            for trecho in ("+ Bacon", ">> sem cebola", "Subtotal", "Cliente:"):
                l = linha_com(ls, trecho); assert (l[1], l[2]) == (1, 1), (escala, trecho, l)
    for fs in (0, 1):
        reset(A, fonte_nome_produto=escala, font_size=fs)
        lc = papel(COZ, "kitchen", "kitchen", cols=32)
        nenhuma_estoura(lc, 32, f"comanda nome {escala} base {fs}")
        nm = linha_com(lc, "X-Burger")
        assert (nm[1], nm[2]) == (wm_e, hm_e) or (fs == 1 and escala == 1), (escala, fs, nm)
        for trecho in ("+ Bacon", ">> sem cebola"):
            l = linha_com(lc, trecho); assert (l[1], l[2]) == (1, 1), (escala, fs, trecho, l)
# nome nunca MENOR que o corpo: base Grande + nome Media = como antes
reset(A, font_size=2); a = bruto(A, ENTREGA, "order", "receipt", {"colunas": 48})
reset(A, font_size=2, fonte_nome_produto=1); b = bruto(A, ENTREGA, "order", "receipt", {"colunas": 48})
assert a == b
print("3) nome do produto Media/Grande, adicionais e observacao em normal (cupom e comanda) OK")

# ── 4. formato do cupom de entrega ─────────────────────────────────────────────────────
reset(A); completo = papel(ENTREGA)
reset(A, formato_delivery="economico"); eco = papel(ENTREGA)
reset(A, formato_delivery="compacto"); cmp_ = papel(ENTREGA)
def altura(ls): return sum(hm for _,_,hm,_ in ls)
assert altura(cmp_) < altura(eco) < altura(completo), (altura(cmp_), altura(eco), altura(completo))
for ls in (eco, cmp_):
    tx = textos(ls)
    assert tx[0].strip() == "NÃO É DOCUMENTO FISCAL"
    assert not any(set(t) == {"."} for t in tx), "pontilhado entre itens no formato enxuto"
    assert not any("Av. Brasil" in t or "3333-4444" in t for t in tx), "endereco/telefone da loja no formato enxuto"
    assert any("BURGUER DO ZE" in t for t in tx) and any(t.startswith("TOTAL") for t in tx)
    assert any("CPF na nota:" in t for t in tx) and any("Pagamento:" in t for t in tx)
    assert any(t.startswith("Entrega: Rua das Flores, 123") for t in tx), tx
    assert any("Ref: em frente a padaria" in t for t in tx)
tx = textos(eco)
assert any(t.startswith("Cliente: Maria Souza") and t.endswith("(16) 99999-1234") for t in tx), tx
assert any("** ENTREGA **" in t and t.startswith(A._data_hora_brt(ENTREGA)[0]) for t in tx)
assert any("Obrigado pela preferencia!" in t for t in tx) and any(t.startswith("Subtotal") for t in tx)
tx = textos(cmp_)
assert not any(t.startswith("Subtotal") for t in tx) and not any("Obrigado" in t for t in tx)
i_end = next(i for i,t in enumerate(tx) if t.startswith("Entrega:"))
i_it = next(i for i,t in enumerate(tx) if "X-Burger" in t)
assert i_end < i_it, "no compacto o endereco vem antes dos itens"
lx = next(t for t in tx if "X-Burger" in t)
assert lx.startswith("1x X-Burger Artesanal") and lx.endswith(A._R(3290+400+350)), lx   # valor fechado
assert any(t == "  + Bacon, Cheddar" for t in tx), tx
assert any(t.lstrip().startswith(">> sem cebola") for t in tx)
# AGENDADO e endereco em texto tambem no compacto; tipo 'delivery' segue o mesmo formato
reset(A, formato_delivery="compacto")
tx = textos(papel(ENTREGA_STR))
assert any("AGENDADO" in t for t in tx) and any(t.startswith("Entrega: Rua das Flores, 123") for t in tx)
assert altura(papel(TIPO_ENT, "delivery", "delivery")) < altura((reset(A), papel(TIPO_ENT, "delivery", "delivery"))[1])
# so ENTREGA: mesa, conferencia, retirada e comanda iguais com qualquer formato
for fmt in ("economico", "compacto"):
    for ct, jt, pt in ((MESA,"order","receipt"),(CONF,"pre_closing_full","receipt"),(RETIRADA,"pickup","receipt"),
                       (COZ,"kitchen","kitchen")):
        reset(A); x0 = bruto(A, ct, jt, pt, {"colunas": 48})
        reset(A, formato_delivery=fmt); x1 = bruto(A, ct, jt, pt, {"colunas": 48})
        assert x0 == x1, (fmt, ct.get("type"), ct.get("order_type"))
print("4) formato de entrega economico/compacto: menos papel, aviso fiscal, endereco, so em entrega OK")

# ── 5. nome da loja na comanda ─────────────────────────────────────────────────────────
for fs in (0, 2):
    reset(A, loja_na_comanda=True, font_size=fs)
    lc = papel(COZ, "kitchen", "kitchen", cols=32); tx = textos(lc)
    i_tit = tx.index(next(t for t in tx if t.strip() == "COZINHA"))
    i_loja = tx.index(next(t for t in tx if t.strip() == "BURGUER DO ZE"))
    i_ped = tx.index(next(t for t in tx if "PEDIDO #123" in t))
    assert i_tit < i_loja < i_ped, (fs, tx[:8])
    assert lc[i_loja][3] and (lc[i_loja][1], lc[i_loja][2]) == (1, 1), lc[i_loja]   # negrito, tamanho normal
    nenhuma_estoura(lc, 32, f"loja na comanda base {fs}")
# sem store_name no job: nome da loja pareada; nome comprido quebra (58 mm)
reset(A, loja_na_comanda=True)
tx = textos(papel(dict(COZ, store_name=None), "kitchen", "kitchen", cols=32))
assert any(t.strip() == "LOJA PAREADA" for t in tx)
tx = textos(papel(dict(COZ, store_name="Droga Ven LJ24 - Av. Maria Antonia Camargo de Oliveira"), "kitchen", "kitchen", cols=32))
assert any("DROGA VEN LJ24" in t for t in tx) and any("OLIVEIRA" in t for t in tx)
# selo de filial ligado pelo servidor: a loja sai UMA vez (o selo)
tx = textos(papel(dict(COZ, print_store_label=True), "kitchen", "kitchen"))
assert sum("BURGUER DO ZE" in t for t in tx) == 1 and any(t.startswith("LOJA: BURGUER DO ZE") for t in tx), tx
# cupom do cliente nao muda com o ajuste da comanda
reset(A); x0 = bruto(A, ENTREGA, "order", "receipt", {"colunas": 48})
reset(A, loja_na_comanda=True); x1 = bruto(A, ENTREGA, "order", "receipt", {"colunas": 48})
assert x0 == x1
print("5) nome da loja na comanda (texto e bytes, quebra, fallback, sem repetir o selo) OK")

# ── 6. nenhuma combinacao estoura o papel ─────────────────────────────────────────────
n = 0
OPC = list(itertools.product((None, True), (None, True), (None, 1, 2), (None, "economico", "compacto"), (None, True)))
for (nome, ct, jt, pt) in CUPONS:
    for neg, merc, nomep, fmt, loja in OPC:
        for base in (dict(), dict(font_size=1), dict(font_size=2), dict(font_size=3), dict(fonte_secoes={"itens":2})):
            for cols in (32, 42, 48):
                reset(A, negrito_obs=neg, valores_mercado=merc, fonte_nome_produto=nomep,
                      formato_delivery=fmt, loja_na_comanda=loja, **copy.deepcopy(base))
                for k in [k for k in CHAVES if A.cfg.get(k) is None]: A.cfg.pop(k, None)
                ls = papel(ct, jt, pt, cols)
                nenhuma_estoura(ls, cols, f"{nome} neg={neg} merc={merc} nome={nomep} fmt={fmt} loja={loja} {base} {cols}")
                assert all("[[" not in t for t in textos(ls)), f"marcador cru no papel: {nome}"
                n += 1
print(f"6) {n} combinacoes de ajustes x tipo x fonte x papel: nenhuma linha estoura OK")

print("\nAJUSTES DO CUPOM: TUDO OK")
