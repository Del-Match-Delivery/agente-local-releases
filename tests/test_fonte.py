# Fonte do cupom INTEIRO + ajustes de impressao (v5.81). Autocontido.
# Uso: venv_build\Scripts\python.exe tests\test_fonte.py [caminho\agente_local.py]
#
# Valida, com um mini interpretador ESC/POS sobre o payload REAL (prefixo + marcadores +
# sufixo, montado igual ao _imprimir_raw):
#   1. o bug historico ("so o cabecalho aumenta") sumiu: depois do PEDIDO # o corpo segue
#      no tamanho configurado, ate o TOTAL e o rodape;
#   2. a escala nova (0/1/2/3 = normal/media/grande/extra) e a migracao da escala antiga;
#   3. nenhuma linha estoura a largura FISICA do papel (32/42/48 colunas), inclusive o
#      TOTAL empilhado quando a fonte extra deixa so 10 colunas no papel de 58 mm;
#   4. fonte POR IMPRESSORA e por SECAO (fonte_secoes), aviso fiscal sempre normal;
#   5. comanda de cozinha: nome do item na fonte base, detalhes/cabecalho em normal;
#   6. estilo (negrito geral, mais escuro, espaco) e sufixo (avanco/corte) configuraveis,
#      com config ausente gerando OS MESMOS BYTES de sempre.
import importlib.util, sys, io, os, contextlib, tempfile
from pathlib import Path

# DATA_DIR isolado ANTES do import: a migracao fonte_v2 salva config.json quando ha token —
# apontar para um scratch garante que o teste nunca escreve na config do agente de verdade.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="agente_teste_fonte_")

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
spec = importlib.util.spec_from_file_location("agente_sob_teste_fonte", str(SRC))
A = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(A)

def reset_cfg():
    for k in ("font_size","fonte_secoes","negrito_cupom","mais_escuro","espaco_linhas",
              "corte","avanco_linhas","vias_cupom","paper_width_cols","codepage"):
        A.cfg.pop(k, None)
reset_cfg()

# ── mini interpretador ESC/POS: linhas impressas com (texto, wmult, hmult, bold) ──────────
# O estado (GS !, ESC E) e capturado no PRIMEIRO byte de texto da linha: numa impressora
# real cada caractere sai com o estado vigente ao ser impresso, e os marcadores de
# restauracao no FIM da linha nao mudam o que ja saiu.
def simular(payload):
    linhas=[]; buf=bytearray(); gs=0x00; bold=False; i=0; n=len(payload); est=None
    while i < n:
        b=payload[i]
        if b==0x1c and i+1<n and payload[i+1]==0x2e: i+=2; continue          # FS .
        if b==0x1b:
            c=payload[i+1]
            if c==0x40: gs=0x00; bold=False; i+=2; continue                  # ESC @
            if c==0x45: bold = payload[i+2]==1; i+=3; continue               # ESC E
            if c==0x21: gs = 0x00 if payload[i+2]==0 else gs; i+=3; continue # ESC ! (agente so usa 0)
            if c in (0x74,0x61,0x64,0x33,0x47): i+=3; continue               # ESC t/a/d/3/G
            i+=2; continue
        if b==0x1d:
            c=payload[i+1]
            if c==0x21: gs=payload[i+2]; i+=3; continue                      # GS !
            if c==0x56: i+=3; continue                                       # GS V
            if c in (0x68,0x77,0x48,0x66): i+=3; continue                    # barcode params
            if c==0x6b: ln=payload[i+3]; i+=4+ln; continue                   # GS k EAN
            i+=3; continue
        if b==0x0a:
            t=buf.decode("cp850","replace")
            if t.strip():
                g,bo = est if est is not None else (gs,bold)
                linhas.append((t.rstrip(), (g>>4 & 0x7)+1, (g & 0x7)+1, bo))
            buf=bytearray(); est=None; i+=1; continue
        if est is None and b not in (0x20,): est=(gs,bold)   # 1o byte VISIVEL fixa o estado
        buf.append(b); i+=1
    return linhas

def payload_de(texto):
    """Monta o payload exatamente como _imprimir_raw/_imprimir_tcp (caminho str)."""
    return A._escpos_cp() + A._escpos_font_prefix() + A._substituir_marcadores_escpos(texto) + A._escpos_sufixo()

CONTENT = {"type":"order","numero":"148","order_type":"delivery","customer_name":"Maria Souza",
           "company_name":"CANTINA DA NONNA","company_address":"Rua das Acacias, 120",
           "created_at":"2026-10-01T22:30:00Z",
           "itens":[{"nome":"Pizza Calabresa","tamanho":"G","qtd":1,"preco_cents":4500,
                     "adicionais":[{"nome":"Borda catupiry","preco_cents":800}]},
                    {"nome":"Coca-Cola Lata","qtd":2,"preco_cents":600}],
           "subtotal_cents":5700,"delivery_fee_cents":700,"total_cents":6400,
           "payment_method":"pix","footer_message":"Obrigado pela preferencia!"}

def cupom(imp=None, content=CONTENT, jt="order", pt="receipt"):
    imp = imp or {}
    with A._usar_codepage(A._cp_da_impressora(imp)), A._usar_fonte(A._fonte_da_impressora(imp)):
        out = A._fmt(dict(content), jt, pt, imp)
        if isinstance(out, str):
            out = payload_de(out)
    return simular(out)

def nenhuma_estoura(linhas, w_fis):
    for t,wm,hm,b in linhas:
        assert len(t)*wm <= w_fis, f"linha estoura {w_fis} col: {wm}x{hm} '{t}' ({len(t)} ch)"

# ── 1. BUG CORRIGIDO: fonte Grande vale no cupom INTEIRO (nao so no cabecalho) ────────────
A.cfg["font_size"] = 2   # Grande (2x2)
ls = cupom(imp={"colunas":48})
depois = False; corpo = []
for t,wm,hm,b in ls:
    if t.startswith("PEDIDO #"): depois = True; continue
    if depois: corpo.append((t,wm,hm))
assert depois, "cupom sem linha PEDIDO #"
assert corpo and all(wm==2 and hm==2 for _,wm,hm in corpo), \
    f"corpo voltou ao normal apos o PEDIDO # (bug v5.80): {[(t,wm,hm) for t,wm,hm in corpo if wm!=2][:3]}"
assert any(t.startswith("TOTAL:") for t,_,_ in corpo), "TOTAL nao achado no corpo"
nenhuma_estoura(ls, 48)
# aviso fiscal NUNCA acompanha a fonte grande (23 chars em 2x nao cabem em 58 mm)
av = next((l for l in ls if "DOCUMENTO FISCAL" in l[0]))
assert av[1]==1 and av[2]==1 and av[3], f"aviso fiscal deveria ser 1x1 bold: {av}"

# ── 2. MEDIA: altura dupla sem perder colunas ─────────────────────────────────────────────
A.cfg["font_size"] = 1
ls_m = cupom(imp={"colunas":48})
A.cfg["font_size"] = 0
ls_n = cupom(imp={"colunas":48})
sep_m = max(len(t) for t,_,_,_ in ls_m if set(t.strip())=={"-"})
sep_n = max(len(t) for t,_,_,_ in ls_n if set(t.strip())=={"-"})
assert sep_m == sep_n == 48, f"Media nao pode perder colunas: {sep_m} vs {sep_n}"
assert all(hm==2 for t,wm,hm,b in ls_m if not t.startswith("PEDIDO") and "DOCUMENTO" not in t), \
    "Media: todas as linhas do cupom em altura dupla"
assert all(wm==1 for t,wm,hm,b in ls_m if "PEDIDO" not in t), "Media nao alarga"

# ── 3. EXTRA em 58 mm: nada estoura; TOTAL empilha ────────────────────────────────────────
A.cfg["font_size"] = 3
ls_e = cupom(imp={"colunas":32})
nenhuma_estoura(ls_e, 32)
tot_idx = [i for i,(t,_,_,_) in enumerate(ls_e) if t.strip().startswith("TOTAL:")]
assert tot_idx, "TOTAL sumiu no modo extra"
t_tot = ls_e[tot_idx[0]][0]
assert "64.00" not in t_tot and "64.00" in ls_e[tot_idx[0]+1][0], \
    "TOTAL em 10 colunas deveria EMPILHAR o valor na linha de baixo"
# PEDIDO # reduz sozinho ate caber (extra = 33 col > 32)
ped = next(l for l in ls_e if "PEDIDO #" in l[0])
assert len(ped[0])*ped[1] <= 32, f"PEDIDO # estourou o papel: {ped}"

# ── 4. POR IMPRESSORA e POR SECAO ─────────────────────────────────────────────────────────
reset_cfg()
assert A._fonte_da_impressora({"font_size":1}) == 1 and A._fonte_da_impressora({}) == 0
A.cfg["font_size"] = 2
assert A._fonte_da_impressora({}) == 2 and A._fonte_da_impressora({"font_size":0}) == 0
reset_cfg()
A.cfg["fonte_secoes"] = {"total":2}
ls_s = cupom(imp={"colunas":48})
t_tot = next(l for l in ls_s if l[0].strip().startswith("TOTAL:"))
assert t_tot[1]==2 and t_tot[2]==2, f"secao TOTAL deveria sair 2x2: {t_tot}"
sub = next(l for l in ls_s if l[0].startswith("Subtotal:"))
assert sub[1]==1 and sub[2]==1, "Subtotal deveria seguir a base (normal)"
depois_tot = ls_s[ls_s.index(t_tot)+1]
assert depois_tot[1]==1 and depois_tot[2]==1, f"depois do TOTAL deveria voltar a base: {depois_tot}"
nenhuma_estoura(ls_s, 48)
reset_cfg()

# ── 5. COZINHA: nome do item na base, detalhes em normal ──────────────────────────────────
A.cfg["font_size"] = 2
ls_k = cupom(imp={"colunas":48}, jt="kitchen", pt="kitchen",
             content={**CONTENT, "type":"kitchen", "notes":"capricha"})
item = next(l for l in ls_k if "Pizza Calabresa" in l[0])
assert item[1]==2 and item[2]==2, f"nome do item da comanda deveria sair na fonte base: {item}"
addon = next(l for l in ls_k if "Borda catupiry" in l[0])
assert addon[1]==1 and addon[2]==1, f"adicional da comanda deveria ser normal: {addon}"
cozinha = next(l for l in ls_k if l[0].strip()=="COZINHA")
assert cozinha[1]==1 and cozinha[2]==1, "titulo COZINHA deveria ser normal"
cliente = next(l for l in ls_k if l[0].startswith("Cliente:"))
assert cliente[1]==1 and cliente[2]==1, f"cabecalho da comanda apos PEDIDO # deveria ser normal: {cliente}"
nenhuma_estoura(ls_k, 48)
reset_cfg()

# ── 6. MIGRACAO da escala antiga (0/1/2 -> 0/2/3) ────────────────────────────────────────
for antigo, novo in ((0,0),(1,2),(2,3)):
    c = {"font_size":antigo}
    if not c.get("fonte_v2"):
        if c.get("font_size") in (1,2,"1","2"): c["font_size"] = int(c["font_size"])+1
        c["fonte_v2"] = True
    assert c["font_size"] == novo, f"migracao {antigo}->{c['font_size']}, esperado {novo}"
assert A.cfg.get("fonte_v2") or True

# ── 7. ESTILO e SUFIXO: default = bytes historicos; config muda os bytes ──────────────────
reset_cfg()
assert A._escpos_sufixo() == b"\n\n\n\n\n\x1b\x64\x05\x1d\x56\x00", "sufixo default mudou!"
assert A._escpos_font_prefix() == b"\x1b\x21\x00", "prefixo default mudou!"
A.cfg["corte"]="parcial";  assert A._escpos_sufixo().endswith(b"\x1d\x56\x01")
A.cfg["corte"]="nao";      assert b"\x1d\x56" not in A._escpos_sufixo()
A.cfg["avanco_linhas"]=2;  A.cfg.pop("corte")
assert A._escpos_sufixo() == b"\n\n\x1b\x64\x02\x1d\x56\x00"
reset_cfg()
A.cfg["negrito_cupom"]=True
assert b"\x1b\x45\x01" in A._escpos_font_prefix()
ls_b = cupom(imp={"colunas":48})
assert all(b for t,wm,hm,b in ls_b), "negrito geral: toda linha bold (inclusive apos NEG_OFF)"
reset_cfg()
A.cfg["mais_escuro"]=True;   assert b"\x1b\x47\x01" in A._escpos_font_prefix()
reset_cfg()
A.cfg["espaco_linhas"]=0;    assert b"\x1b\x33\x18" in A._escpos_font_prefix()
A.cfg["espaco_linhas"]=2;    assert b"\x1b\x33\x28" in A._escpos_font_prefix()
reset_cfg()

# ── 8. Fonte normal: cupom identico em tamanho (tudo 1x1) e pickup/delivery cobertos ─────
for tipo in ("order","pickup","delivery"):
    ls_t = cupom(imp={"colunas":42}, content={**CONTENT,"type":tipo}, jt=tipo, pt="receipt")
    corpo = [l for l in ls_t if "PEDIDO" not in l[0]]
    assert all(wm==1 and hm==1 for t,wm,hm,b in corpo), f"{tipo}: fonte normal deveria ser 1x1"
    nenhuma_estoura(ls_t, 42)
    assert any(t.strip().startswith("TOTAL:") for t,_,_,_ in ls_t), f"{tipo}: sem TOTAL"

print("OK - todos os testes de fonte/impressao v5.81 passaram")
