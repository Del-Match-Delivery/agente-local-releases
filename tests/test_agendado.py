# Destaque de pedido AGENDADO e largura do papel em _fmt (autocontido).
# Uso: venv_build\Scripts\python.exe tests\test_agendado.py [caminho\agente_local.py]
import importlib.util, sys, io, contextlib, copy
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    return m

A = load(SRC, "agente_sob_teste")
A.cfg["font_size"] = 0

base = {
    "numero": 123, "created_at_brt": "09/09/2026 10:00", "order_type": "delivery",
    "customer_name": "Maria", "customer_phone": "11999999999", "table_number": "",
    "company_name": "Pizzaria Teste", "paper_width": "48", "hora_brt": "10:00",
    "itens": [{"nome": "Pizza Calabresa", "quantidade": 1, "preco_cents": 4500,
               "adicionais": [{"nome": "Borda", "preco_cents": 500}], "observacao": "sem cebola"}],
    "subtotal_cents": 5000, "total_cents": 5000, "payment_method": "pix", "notes": "",
    "delivery_address": "Rua A, 1", "pickup_code": "AB12",
}
sched = dict(base, is_scheduled=True, scheduled_for="2026-09-09T15:00:00+00:00",
             scheduled_date="09/09/2026", scheduled_time="12:00",
             scheduled_label="AGENDADO: 09/09 AS 12:00")
LBL = "[[ALTO_ON]]AGENDADO: 09/09 AS 12:00[[ALTO_OFF]]"
S48 = "-" * 48
MARC = ("[[ALTO_ON]]", "[[ALTO_OFF]]", "[[BIG_ORDER_ON]]", "[[BIG_ORDER_OFF]]", "[[NEG_ON]]", "[[NEG_OFF]]",
        "[[FS0]]", "[[FS1]]", "[[FS2]]", "[[FS3]]", "[[FSB]]")   # v5.81: tamanho por secao
def limpo(l):
    for m in MARC: l = l.replace(m, "")
    return l

# 1) Equivalencia: pedido agendado == pedido comum + (separador + label) e nada mais muda
for jt, pt in [("order", "receipt"), ("pickup", "pickup"), ("delivery", "delivery")]:
    sem = A._fmt(copy.deepcopy(base), jt, pt).split("\n")
    com = A._fmt(copy.deepcopy(sched), jt, pt).split("\n")
    assert "[[ALTO_ON]]" not in "\n".join(sem), f"{jt}: sem agendamento nao pode ter destaque"
    i = com.index(LBL)
    assert com[i - 1] == S48, f"{jt}: separador antes da label"
    reduzido = com[:i - 1] + com[i + 1:]     # tira separador extra + label
    assert reduzido == sem, f"{jt}: agendado deve ser o cupom comum + (separador, label)"
for jt in ("kitchen", "bar"):
    sem = A._fmt(copy.deepcopy(base), jt, jt).split("\n")
    com = A._fmt(copy.deepcopy(sched), jt, jt).split("\n")
    i = com.index(LBL)
    assert com[:i] + com[i + 1:] == sem, f"{jt}: comanda agendada = comum + label"
print("1) equivalencia OK: agendado = comum + destaque, em 5 layouts")

# 2) Cupom: label depois de Cliente/Tel, antes dos itens, entre separadores; hora do agendamento so na label
out = A._fmt(copy.deepcopy(sched), "order", "receipt").split("\n")
i = out.index(LBL)
assert out[i - 1] == S48 and out[i + 1] == S48
assert any(l.startswith("Cliente:") for l in out[:i]) and any(l.startswith("Tel:") for l in out[:i])
assert not any("Pizza Calabresa" in l for l in out[:i]) and any("Pizza Calabresa" in l for l in out[i:])
assert "12:00" not in "\n".join(l for l in out if l != LBL), "scheduled_time/date NAO deve ser impresso separadamente"
print("2) cupom OK: linha", i)

# 3) Comanda: label logo apos PEDIDO #, antes de tipo/mesa/itens
outk = A._fmt(copy.deepcopy(sched), "kitchen", "kitchen").split("\n")
j = outk.index(LBL)
# v5.81: o destaque do pedido virou NEG_ON+FSn (fonte da secao, clampada ao papel); o que
# importa aqui e a ORDEM (label logo apos o PEDIDO #), nao os bytes do destaque.
assert "PEDIDO #123" in outk[j - 1] and outk[j - 1].startswith("[[NEG_ON]][[FS"), outk[j - 1]
assert not any("Pizza Calabresa" in l for l in outk[:j])
print("3) comanda OK: linha", j)

# 4) Largura: a label (24 chars, altura dupla = largura normal) cabe INTEIRA em 48/42/32/24 colunas
for pw in ("48", "42", "32", "24"):
    o = A._fmt(copy.deepcopy(dict(sched, paper_width=pw)), "order", "receipt").split("\n")
    alto = [l for l in o if l.startswith("[[ALTO_ON]]")]
    assert alto == [LBL], (pw, alto)
print("4) label inteira em 48/42/32/24 colunas OK (horario nunca partido)")

# 5) Shapes aninhados e blindagem
nested = {"pedido": dict(base), "scheduled_label": "AGENDADO: 09/09 AS 12:00", "is_scheduled": True}
assert LBL in A._fmt(copy.deepcopy(nested), "order", "receipt").split("\n")
assert LBL in A._fmt(copy.deepcopy({"order": dict(sched)}), "order", "receipt").split("\n")
for bad in ("null", "", None, "None", "undefined"):
    assert "AGENDADO" not in A._fmt(copy.deepcopy(dict(base, scheduled_label=bad)), "order", "receipt")
o = A._fmt(copy.deepcopy(dict(base, scheduled_label="AGENDADO: 09/09 ÀS 12:00")), "order", "receipt")
assert "[[ALTO_ON]]AGENDADO: 09/09 ÀS 12:00[[ALTO_OFF]]" in o.split("\n"), "nao re-formata"
print("5) shapes aninhados + blindagem OK")

# 6) Bytes ESC/POS: center + bold + ALTURA dupla (GS ! 0x01), texto, volta ao estado BASE
# (v5.81: o OFF restaura o tamanho base do cupom em vez de zerar; com fonte normal os bytes
# sao IDENTICOS aos historicos: GS ! 0x00 + bold off + left).
ALTO_ON = b"\x1b\x61\x01\x1b\x45\x01\x1d\x21\x01"
b = A._substituir_marcadores_escpos(LBL)
assert b == ALTO_ON + b"AGENDADO: 09/09 AS 12:00" + A._bytes_restaura_base(), b
assert A._bytes_restaura_base() == b"\x1d\x21\x00\x1b\x45\x00\x1b\x61\x00", "fonte normal => bytes historicos"
assert b"[[" not in b
print("6) bytes OK")

# 7) Comanda em fonte grande (cfg font_size=1 = Media na escala v5.81): caminho de bytes
A.cfg["font_size"] = 1
try:
    bk = A._fmt(copy.deepcopy(sched), "kitchen", "kitchen")
    assert isinstance(bk, bytes)
    assert ALTO_ON + b"AGENDADO: 09/09 AS 12:00" + A._bytes_restaura_base() in bk
    o = A._fmt(copy.deepcopy(sched), "order", "receipt").split("\n")
    assert LBL in o, "label em UMA linha no receipt com font_size=1"
finally:
    A.cfg["font_size"] = 0
print("7) fonte grande OK")

# 8) Largura efetiva do papel: impressora > ajuste local geral (COMP-46) > content > cardapio > 48
le = A._largura_efetiva; cv = A._colunas_validas
A.cfg.pop("paper_width_cols", None)
assert le({"colunas": 42}, {"paper_width": "48"}, 48) == 42, "colunas da impressora vence tudo"
assert le({}, {"paper_width": "48"}, 42) == 48, "paper_width do job vence o do cardapio"
assert le({}, {}, 42) == 42, "sem nada no job => largura do cardapio"
assert le({}, {}, None) == 48 and le(None, None, "lixo") == 48, "nada valido => W"
assert le({"colunas": "abc"}, {}, 48) == 48, "colunas invalida e ignorada"
A.cfg["paper_width_cols"] = 32
try:
    assert le({}, {"paper_width": "48"}, 48) == 32, "ajuste local geral (COMP-46) vence job e cardapio"
    assert le({"colunas": 42}, {}, 48) == 42, "...mas nao vence a coluna da propria impressora"
    o = A._fmt(copy.deepcopy(sched), "order", "receipt").split("\n")
    assert max(len(limpo(l)) for l in o) <= 32, "ajuste local geral aplicado no _fmt"
finally:
    A.cfg.pop("paper_width_cols", None)
assert cv("42") == 42 and cv(48) == 48 and cv(" 32 ") == 32
assert cv(10) is None and cv(100) is None and cv("x") is None and cv(None) is None
print("8) largura efetiva OK (impressora > ajuste geral > job > cardapio > 48)")

# 8b) Codigo de barras do time (COMP-46) segue funcionando no cupom agendado
it = dict(base["itens"][0], barcode="7891000100103")
o = A._fmt(copy.deepcopy(dict(sched, itens=[it], print_barcode=True)), "order", "receipt").split("\n")
assert "[[EAN13:7891000100103]]" in o and LBL in o
o = A._fmt(copy.deepcopy(dict(sched, itens=[it])), "order", "receipt").split("\n")
assert "  Cod: 7891000100103" in o, "sem print_barcode => so o texto do codigo"
print("8b) EAN-13 (COMP-46) + agendado juntos OK")

# 8c) v5.82: job SEM a chave print_barcode (trigger do banco / create_public_order / print-job-create)
#     obedece ao ajuste da loja vindo do poll; chave presente no job sempre vence; bytes compativeis.
_pb_antes = A._print_barcode_servidor
try:
    A._print_barcode_servidor = True
    o = A._fmt(copy.deepcopy(dict(sched, itens=[it])), "order", "receipt").split("\n")
    assert "[[EAN13:7891000100103]]" in o, "job sem a chave + loja LIGADA => barras"
    o = A._fmt(copy.deepcopy(dict(sched, itens=[it], print_barcode=False)), "order", "receipt").split("\n")
    assert "  Cod: 7891000100103" in o and "[[EAN13:7891000100103]]" not in o, "print_barcode=False no job vence a loja"
    A._print_barcode_servidor = False
    o = A._fmt(copy.deepcopy(dict(sched, itens=[it])), "order", "receipt").split("\n")
    assert "  Cod: 7891000100103" in o, "loja desligada => texto"
    o = A._fmt(copy.deepcopy(dict(sched, itens=[it], print_barcode=True)), "order", "receipt").split("\n")
    assert "[[EAN13:7891000100103]]" in o, "print_barcode=True no job vence a loja desligada"
    A._print_barcode_servidor = None
    o = A._fmt(copy.deepcopy(dict(sched, itens=[it])), "order", "receipt").split("\n")
    assert "  Cod: 7891000100103" in o, "sem chave e sem ajuste => texto (nenhuma loja muda sem pedir)"
    # comanda de cozinha: mesma regra de precedencia do cupom (comportamento da v5.80/5.81 mantido:
    # a comanda tambem leva a linha do codigo quando o item tem codigo)
    A._print_barcode_servidor = True
    k = A._fmt(copy.deepcopy(dict(sched, itens=[it])), "kitchen", "kitchen")
    k2 = A._fmt(copy.deepcopy(dict(sched, itens=[it], print_barcode=True)), "kitchen", "kitchen")
    assert k == k2, "comanda: job sem a chave + loja ligada == job com print_barcode=True"
    assert A._flag_print_barcode({"print_barcode": "true"}) is False, "so booleano True liga"
    assert A._flag_print_barcode(None) is False
finally:
    A._print_barcode_servidor = _pb_antes
# bytes: funcao A (GS k 2 ... NUL), sem HRI da impressora, numero em texto logo abaixo, sem LF final
b = A._escpos_barcode_ean13("7891000100103")
assert b.startswith(bytes([0x1d, 0x68])), "GS h (altura) primeiro"
assert bytes([0x1d, 0x77, 3]) in b, "GS w 3 (modulo 3 pontos)"
assert bytes([0x1d, 0x48, 0]) in b, "GS H 0: HRI da impressora desligado"
assert b"\x1d\x6b\x02" + b"7891000100103" + b"\x00" in b, "GS k m=2 + 13 digitos + NUL (funcao A)"
assert b"\x1d\x6b\x43" not in b, "funcao B (m=67) nao e mais usada"
assert b.endswith(b"  Cod: 7891000100103"), "numero legivel em texto e sem LF final (o join poe)"
assert b.count(b"\n") == 1, "exatamente um LF entre as barras e o texto"
m = A._substituir_marcadores_escpos("[[EAN13:7891000100103]]\n")
assert m.startswith(b"\x1d\x68") and m.endswith(b"  Cod: 7891000100103\n"), "marcador vira barras + texto + LF do join"
assert A._valida_ean13("7899026422315") and A._valida_ean13("7899026401792"), "EANs reais da Emy validam"
assert not A._valida_ean13("7899026422316"), "digito verificador errado reprova"
print("8c) v5.82: fallback do ajuste da loja + bytes EAN-13 funcao A com numero em texto OK")

# 9) Cupom em 42 colunas: nenhuma linha passa de 42 e o preco alinha na coluna 42
o = A._fmt(copy.deepcopy(dict(sched, paper_width="42")), "order", "receipt").split("\n")
vis = [limpo(l) for l in o]
assert max(len(l) for l in vis) <= 42, max(vis, key=len)
assert any(l.endswith("R$ 45.00") and len(l) == 42 for l in vis), [l for l in vis if "45.00" in l]
assert any(l.startswith("TOTAL:") and len(l) == 42 for l in vis)
print("9) cupom em 42 colunas OK (nada passa de 42; preco na coluna 42)")

print("\nAGENDADO: TUDO OK")
