# Funcoes PURAS da eleicao de instancia unica e do update, sem importar o modulo inteiro.
# Uso: venv_build\Scripts\python.exe tests\test_eleicao.py [caminho\agente_local.py]
import ast, sys, time, json
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
src = SRC.read_text(encoding="utf-8")
tree = ast.parse(src)
ns = {"time": time, "json": json}
want = {"_ver_tuple", "_since_registro", "_decidir_vencedor", "_alvo_update", "_colunas_validas", "_largura_efetiva"}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in want:
        exec(compile(ast.Module(body=[node], type_ignores=[]), "agente_local.py", "exec"), ns)
assert want <= set(ns), f"faltou: {want - set(ns)}"
dv = ns["_decidir_vencedor"]
now = time.time()
# 1) maior versao vence, independente de idade/pid
assert dv({100: {"version": "5.77", "since": now-9999}, 200: {"version": "5.78", "since": now}}) == 200
# 2) empate de versao: MAIS ANTIGA vence, mesmo com pid maior (o caso do clique no .exe)
assert dv({900: {"version": "5.78", "since": now-3600}, 100: {"version": "5.78", "since": now}}) == 900
# 3) empate de versao E de since: menor pid
assert dv({300: {"version": "5.78", "since": 1000.0}, 200: {"version": "5.78", "since": 1000.0}}) == 200
# 4) compat: registros sem since (5.77) => menor pid, como antes
assert dv({300: {"version": "5.77"}, 200: {"version": "5.77"}}) == 200
# 5) misto: com since (antiga) vs sem since => quem tem since vence
assert dv({300: {"version": "5.78", "since": now}, 200: {"version": "5.78"}}) == 300
# 6) determinismo em qualquer ordem
regs = {5: {"version": "5.78", "since": now-50}, 3: {"version": "5.78", "since": now-500}, 9: {"version": "5.78", "since": now-5}}
for order in ([5,3,9],[9,5,3],[3,9,5]):
    assert dv({k: regs[k] for k in order}) == 3
# 7) versao ilegivel perde
assert dv({1: {"version": "abc", "since": now-9999}, 2: {"version": "5.78", "since": now}}) == 2
print("eleicao: 7/7 testes OK")
# 8) alvo do update: latest_* antes de version/url
au = ns["_alvo_update"]
assert au({"version": "5.77", "url": "u77", "latest_version": "5.79", "latest_url": "u79"}) == ("5.79", "u79")
assert au({"version": "5.77", "url": "u77"}) == ("5.77", "u77")
assert au(None) == ("", "") and au({}) == ("", "")
print("update: alvo OK")
print("\nELEICAO: TUDO OK")
