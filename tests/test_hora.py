# Data/Hora do cupom e comanda em horario de Brasilia (autocontido).
# Uso: venv_build\Scripts\python.exe tests\test_hora.py [caminho\agente_local.py]
import importlib.util, sys, io, contextlib, copy
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
spec = importlib.util.spec_from_file_location("agente_sob_teste_h", str(SRC))
A = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(A)
A.cfg["font_size"] = 0

# 1) conversao pura
f = A._data_hora_brt
assert f({"created_at": "2026-09-09T15:00:00.123456+00:00"}) == ("09/09/2026 12:00", "12:00")
assert f({"created_at": "2026-09-09T15:00:00Z"}) == ("09/09/2026 12:00", "12:00")
assert f({"created_at": "2026-09-09T15:00:00"}) == ("09/09/2026 12:00", "12:00"), "sem fuso => assume UTC"
assert f({"created_at": "2026-09-09 02:30:00+00"}) == ("08/09/2026 23:30", "23:30"), "vira o dia corretamente"
assert f({"created_at_brt": "09/09/2026 10:00", "hora_brt": "10:00", "created_at": "2026-09-09T15:00:00Z"}) == ("09/09/2026 10:00", "10:00"), "BRT pronto do servidor tem prioridade e NAO e reconvertido"
assert f({"created_at_brt": "09/09/2026 10:00", "created_at": "2026-09-09T15:00:00Z"}) == ("09/09/2026 10:00", "12:00"), "completa so o que falta"
assert f({}) == ("", "") and f({"created_at": "lixo"}) == ("", "") and f(None) == ("", "")
print("1) _data_hora_brt OK")

# 2) job do agent-unified-poll: content com created_at UTC (trigger), sem *_brt
base = {"numero": 77, "created_at": "2026-09-09T15:00:00.123456+00:00", "order_type": "delivery",
        "customer_name": "Maria", "company_name": "Pizzaria Teste", "paper_width": "48",
        "itens": [{"nome": "Pizza", "quantidade": 1, "preco_cents": 4500}],
        "subtotal_cents": 4500, "total_cents": 4500, "payment_method": "pix"}
cup = A._fmt(copy.deepcopy(base), "order", "receipt").split("\n")
assert "Data: 09/09/2026 12:00" in cup, cup[:8]
com = A._fmt(copy.deepcopy(base), "kitchen", "kitchen").split("\n")
assert "Hora: 12:00" in com and "Hora: 15:00" not in com, "comanda NUNCA imprime a hora UTC"
for jt in ("pickup", "delivery"):
    assert "Data: 09/09/2026 12:00" in A._fmt(copy.deepcopy(base), jt, jt).split("\n")
print("2) cupom 'Data:' em BRT; comanda 'Hora:' em BRT; pickup/delivery OK")

# 3) shape agent-jobs (*_brt prontos): usa o que veio, sem reconverter
rich = dict(base, created_at_brt="09/09/2026 10:00", hora_brt="10:00")
assert "Data: 09/09/2026 10:00" in A._fmt(copy.deepcopy(rich), "order", "receipt").split("\n")
assert "Hora: 10:00" in A._fmt(copy.deepcopy(rich), "kitchen", "kitchen").split("\n")
print("3) *_brt do servidor tem prioridade OK")

# 4) sem created_at no content: proc_job injeta o created_at do print_job (logica replicada)
job = {"id": "x", "printer_type": "receipt", "job_type": "order", "copies": 1,
       "created_at": "2026-09-09T15:00:00+00:00", "content": dict(base)}
del job["content"]["created_at"]
c = job["content"]
if not c.get("created_at") and job.get("created_at"):
    c = dict(c); c["created_at"] = job["created_at"]
assert "Data: 09/09/2026 12:00" in A._fmt(copy.deepcopy(c), "order", "receipt")
print("4) fallback pelo created_at do proprio print_job OK")

# 5) agendado + hora convivem
sched = dict(base, scheduled_label="AGENDADO: 10/09 AS 12:00")
o = A._fmt(copy.deepcopy(sched), "order", "receipt").split("\n")
assert "Data: 09/09/2026 12:00" in o and "[[ALTO_ON]]AGENDADO: 10/09 AS 12:00[[ALTO_OFF]]" in o
print("5) Data em BRT + label de agendamento juntos OK")
print("\nHORA: TUDO OK")
