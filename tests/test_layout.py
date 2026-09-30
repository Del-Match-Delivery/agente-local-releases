# Layout do cupom (v5.80): quebra de linha na palavra, numero da casa x numero do pedido, e
# NENHUMA linha maior que o papel em 32/42/48 colunas, mesmo com campos longos. Autocontido.
# Uso: venv_build\Scripts\python.exe tests\test_layout.py [caminho\agente_local.py]
import importlib.util, sys, io, contextlib, copy, re
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
spec = importlib.util.spec_from_file_location("agente_sob_teste_layout", str(SRC))
A = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(A)
A.cfg["font_size"] = 0
A.cfg.pop("paper_width_cols", None)
MARC = re.compile(r"\[\[[A-Z_]+(?::\d+)?\]\]")

# 1) _quebrar_linhas_longas
q = A._quebrar_linhas_longas
curtas = ["abc", "", "-" * 32, "[[BIG_ORDER_ON]]PEDIDO #1[[BIG_ORDER_OFF]]"]
assert q(curtas, 32) == curtas, "linhas que cabem saem iguais"
assert q(["Rua Guido Dalceno, nº 461, Jardim Paraíso, Matão - SP"], 32) == ["Rua Guido Dalceno, nº 461,", "Jardim Paraíso, Matão - SP"]
r = q(["  + Molho especial da casa com alho e ervas finas"], 32)
assert r[0].startswith("  + ") and all(x.startswith("    ") for x in r[1:]) and max(map(len, r)) <= 32, r
r = q(["  >> sem cebola, sem tomate, bem passado, cortar ao meio"], 32)
assert r[0].startswith("  >> ") and all(x.startswith("    ") for x in r[1:]) and max(map(len, r)) <= 32, r
assert q(["Obs: linha1\nlinha 2"], 32) == ["Obs: linha1", "linha 2"], "quebra de linha do cliente preservada"
assert q(["x" * 40], 32) == ["x" * 32, "x" * 8], "palavra maior que o papel e fatiada"
print("1) _quebrar_linhas_longas OK")

# 2) numero da casa x numero do PEDIDO no bloco de entrega
b = A._bloco_endereco({"numero": 2, "delivery_address": "Rua Guido Dalceno, Casa"}, 32)
assert not any("Numero: 2" in l for l in b), f"numero do PEDIDO saiu como numero da casa: {b}"
b = A._bloco_endereco({"numero": 2, "delivery_address": "Rua Guido Dalceno", "delivery_address_number": "461"}, 32)
assert "Numero: 461" in b, b
b = A._bloco_endereco({"numero": 2, "delivery_address": "Rua Guido Dalceno, 461", "delivery_address_number": "461"}, 32)
assert not any(l.startswith("Numero:") for l in b), "numero ja esta na string: nao repete"
b = A._bloco_endereco({"numero": 2, "delivery_address": {"street": "Rua X", "numero": "461", "city": "Matão", "state": "SP"}}, 32)
assert "Rua X, 461" in b and "Matão - SP" in b, b
print("2) numero da casa: nunca usa o numero do PEDIDO; objeto de endereco com 'numero' continua OK")

# 3) cupons com campos longos: nenhuma linha maior que o papel
longo = {
    "numero": 1234, "order_type": "delivery", "created_at": "2026-09-24T17:05:00+00:00",
    "customer_name": "Maria Aparecida dos Santos Oliveira de Albuquerque", "customer_phone": "5516999990000",
    "company_name": "Lanchonete e Pizzaria Sabor da Terra",
    "scheduled_label": "AGENDADO: 24/09 AS 19:00",
    "itens": [
        {"nome": "X-Tudo Especial da Casa com Bacon Duplo e Cheddar Cremoso", "quantidade": 2, "preco_cents": 3990,
         "adicionais": [{"nome": "Molho especial da casa com alho e ervas finas", "preco_cents": 300}],
         "observacao": "sem cebola, sem tomate, bem passado, cortar ao meio por favor"},
        {"nome": "Refrigerante", "quantidade": 1, "preco_cents": 800},
    ],
    "subtotal_cents": 9080, "delivery_fee_cents": 800, "total_cents": 9880, "payment_method": "pix",
    "notes": "Portão vermelho — tocar a campainha duas vezes\nCachorro bravo no quintal",
    "delivery_address": "Rua Guido Dalceno, nº 461, Jardim Paraíso, Matão - SP, CEP 15990-000",
    "delivery_address_complement": "Casa dos fundos, entrada pela lateral esquerda",
}
for w in (32, 42, 48):
    for jt, pt in [("order", "receipt"), ("pickup", "pickup"), ("delivery", "delivery"), ("kitchen", "kitchen"), ("bar", "bar")]:
        c = dict(copy.deepcopy(longo), paper_width=str(w))
        out = A._fmt(c, jt, pt)
        vis = [MARC.sub("", l) for l in out.split("\n") if "[[BIG_ORDER_ON]]" not in l]
        big = [MARC.sub("", l) for l in out.split("\n") if "[[BIG_ORDER_ON]]" in l]
        assert max(len(l) for l in vis) <= w, (w, jt, max(vis, key=len))
        assert all(len(l) * 2 <= w for l in big), (w, jt, big)
        assert "Cachorro bravo no quintal" in out, "segunda linha da observacao nao pode sumir"
print("3) 5 layouts x 32/42/48 colunas com campos longos: nenhuma linha passa do papel")

# 4) comanda em fonte grande (caminho de bytes): observacao e adicional quebram na palavra
A.cfg["font_size"] = 1
try:
    bk = A._fmt(dict(copy.deepcopy(longo), paper_width="32"), "kitchen", "kitchen")
    assert isinstance(bk, bytes)
    txt = re.sub(rb"\x1b\x40|\x1c\x2e|[\x1b\x1d][\x00-\xff][\x00-\xff]", b"", bk).decode("cp850")
    normais = [l for l in txt.split("\n") if not l.startswith("[ ")]   # item em fonte grande fica de fora
    assert max(len(l) for l in normais) <= 32, max(normais, key=len)
finally:
    A.cfg["font_size"] = 0
print("4) comanda em fonte grande: linhas normais cabem em 32 colunas")
print("\nLAYOUT: TUDO OK")
