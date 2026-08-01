# -*- coding: utf-8 -*-
"""Renderiza cupom e comanda com o content que a trigger vai carimbar e confere:
  1) filial no concentrador ganha o selo e o cabecalho da LOJA QUE VENDEU;
  2) loja unica sai byte a byte igual ao de hoje (nenhuma outra loja pode ver mudanca);
  3) job antigo (sem os campos novos) tambem sai igual;
  4) nome de loja gigante quebra em linhas em vez de estourar/truncar.
Importa o agente de verdade — nada de reimplementar a formatacao aqui."""
import importlib.util, io, os, re, sys

AG = r"c:\Users\Fábio Araujo - SDR\Desktop\Agente Local\agente_local.py"
spec = importlib.util.spec_from_file_location("agente_local", AG)
ag = importlib.util.module_from_spec(spec)
sys.modules["agente_local"] = ag
spec.loader.exec_module(ag)  # protegido por if __name__ == "__main__"

W = 42
CFG_MATRIZ = {"restaurant_name": "Mundo do Sorvete e do Acai", "paper_width": W}

ITENS = [{"nome": "Milk Shake", "qtd": 1, "preco_cents": 2000,
          "adicionais": [{"nome": "500ml", "preco_cents": 0},
                         {"nome": "Recheio Trufa Branca", "preco_cents": 250}]}]

BASE = {
    "order_number": "1234", "created_at_brt": "01/08/2026 19:42",
    "order_type": "delivery", "customer_name": "Joao",
    "itens": ITENS, "subtotal_cents": 2250, "total_cents": 2250,
    "payment_method": "pix", "hora_brt": "19:42",
    "paper_width": W,   # _fmt le a largura do CONTENT (nao do cfg)
}


def render(tipo, extra, cfg=CFG_MATRIZ):
    content = dict(BASE); content.update(extra)
    ag.cfg = dict(cfg)          # _fmt le cfg do modulo, nao por parametro
    raw = ag._fmt(content, tipo, tipo)   # _fmt(content, job_type, printer_type)
    txt = raw.decode("cp850", "replace") if isinstance(raw, bytes) else str(raw)
    # tira bytes ESC/POS para inspecionar o texto
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", txt)


def bloco(titulo, s):
    print("=" * 60); print(titulo); print("=" * 60)
    print("\n".join(l for l in s.splitlines() if l.strip())[:900]); print()


falhas = []


def check(cond, msg):
    print(("  OK   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


# ---------------------------------------------------------------- 1) FILIAL
filial = {"store_name": "Roxo Por Acai", "print_store_label": True}
cup = render("order", filial)
com = render("kitchen", filial)
bloco("CUPOM — filial 'Roxo Por Acai' impressa na matriz", cup)
bloco("COMANDA — filial 'Roxo Por Acai' impressa na matriz", com)

print("-- filial no concentrador --")
check("LOJA: ROXO POR ACAI" in cup, "cupom estampa o selo da loja")
check("ROXO POR ACAI" in cup.split("PEDIDO")[0], "cabecalho do cupom usa a loja que vendeu")
check("MUNDO DO SORVETE" not in cup, "cupom NAO mostra o nome da matriz")
check("LOJA: ROXO POR ACAI" in com, "comanda estampa o selo da loja")
i_selo, i_ped = com.find("LOJA: ROXO POR ACAI"), com.find("PEDIDO #")
check(0 <= i_selo < i_ped, "na comanda o selo vem ANTES do numero do pedido")
print()

# company_name (razao social) continua ganhando do store_name
cn = render("order", dict(filial, company_name="ROXO ACAI LTDA ME"))
check("ROXO ACAI LTDA ME" in cn, "company_name cadastrado tem prioridade no cabecalho")
check("LOJA: ROXO POR ACAI" in cn, "mesmo com company_name, o selo da loja sai")
print()

# --------------------------------------------------- 2) LOJA UNICA: NAO MUDA
print("-- loja unica (regressao: cupom nao pode mudar) --")
antes_cup = render("order", {})
antes_com = render("kitchen", {})
# loja unica: a trigger carimba store_name, mas print_store_label = false
depois_cup = render("order", {"store_name": "Mundo do Sorvete e do Acai", "print_store_label": False})
depois_com = render("kitchen", {"store_name": "Mundo do Sorvete e do Acai", "print_store_label": False})
check(antes_cup == depois_cup, "cupom de loja unica identico com e sem os campos novos")
check(antes_com == depois_com, "comanda de loja unica identica com e sem os campos novos")
check("LOJA:" not in depois_cup, "loja unica nao ganha selo")
print()

# ------------------------------------------- 3) JOB ANTIGO (sem campo nenhum)
print("-- job antigo, criado antes da migration --")
check("LOJA:" not in antes_cup, "job sem os campos nao estampa selo")
check("MUNDO DO SORVETE E DO ACAI" in antes_cup, "job antigo cai no nome do agente, como antes")
print()

# ------------------------------------------------ 4) NOME GIGANTE / SUJEIRAS
print("-- nome longo e valores sujos --")
gigante = {"store_name": "Droga Ven LJ24 - AV. MARIA ANTONIA CAMARGO DE OLIVEIRA (VIA EXPRESSA) - ARARAQUARA",
           "print_store_label": True}
g = render("kitchen", gigante)
linhas_selo = [l for l in g.splitlines() if "LOJA:" in l or "ARARAQUARA" in l]
bloco("COMANDA — nome de loja com 80+ colunas", g)
# marcadores [[NEG_ON]]/[[BIG_ORDER_ON]] viram bytes ESC/POS: nao ocupam coluna no papel
sem_marcador = lambda l: re.sub(r"\[\[[A-Z_]+\]\]", "", l)
largas = [sem_marcador(l) for l in g.splitlines() if len(sem_marcador(l)) > W]
check(not largas, f"nenhuma linha passa das {W} colunas do papel (estouraram: {largas})")
check(len(linhas_selo) >= 2, "nome longo quebra em varias linhas (nao trunca)")
check("ARARAQUARA" in g, "o final do nome — o que diferencia as lojas — nao e perdido")

sujo = render("order", {"store_name": "null", "print_store_label": True})
check("LOJA: NULL" not in sujo, "string 'null' do servidor nao vira nome de loja")
espaco = render("order", {"store_name": "  Primitivos do Acai  ", "print_store_label": True})
check("LOJA: PRIMITIVOS DO ACAI" in espaco, "espaco sobrando no cadastro e removido")
txt_flag = render("order", {"store_name": "Roxo Por Acai", "print_store_label": "true"})
check("LOJA: ROXO POR ACAI" in txt_flag, "flag booleana chegando como texto e aceita")
shape_jobs = render("order", {"pedido": {"store_name": "La Casa de Pastel", "print_store_label": True}})
check("LOJA: LA CASA DE PASTEL" in shape_jobs, "shape do agent-jobs (content.pedido) e lido")
shape_poll = render("order", {"order": {"store_name": "La Casa de Pastel", "print_store_label": True}})
check("LOJA: LA CASA DE PASTEL" in shape_poll, "shape do print-agent-poll (content.order) e lido")

print()
print("=" * 60)
print(f"FALHAS: {len(falhas)}")
for f in falhas:
    print("  -", f)
sys.exit(1 if falhas else 0)
