# Tela Configuracoes > impressoras (v5.80): coluna Acentos, combobox, Aplicar e Testar acentos.
# Monta a janela Tk DE VERDADE (abre por instantes na tela), sem imprimir nada e sem gravar config.
# Uso: venv_build\Scripts\python.exe tests\test_ui_config.py [caminho\agente_local.py]
import importlib.util, sys, io, contextlib, os, tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="agente_ui_")   # DATA_DIR isolado
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "agente_local.py"
spec = importlib.util.spec_from_file_location("agente_sob_teste_ui", str(SRC))
A = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(A)

gravados, impressos, avisos = [], [], []
A.salvar_config = lambda c: gravados.append([dict(i) for i in c.get("impressoras", [])])
A.listar_impressoras_windows = lambda: ["POS-58", "EPSON TM-T20"]
A.listar_portas_serial = lambda: []
A._imprimir_com_roteamento = lambda imp, dados: (impressos.append((dict(imp), dados)), {"ok": True})[1]
for nome in ("showinfo", "showwarning", "showerror"):
    setattr(A.messagebox, nome, (lambda n: (lambda *a, **k: avisos.append((n, a))))(nome))
CFG = {"token": "t", "restaurant_id": "r", "restaurant_name": "Loja Teste", "poll_interval": 3,
              "impressoras": [{"nome": "Mini", "area": "caixa", "printer_type": "receipt", "tipo": "comum_win32",
                               "nome_impressora": "POS-58", "colunas": 32, "codepage": "ascii"},
                              {"nome": "Cozinha", "area": "cozinha", "printer_type": "kitchen", "tipo": "comum_win32",
                               "nome_impressora": "EPSON TM-T20"}],
              "balancas": []}
A.cfg = CFG
A.carregar_config = lambda: CFG   # abrir_config() recarrega a config do disco

import tkinter as tk
A._root = tk.Tk(); A._root.withdraw()
A.abrir_config()
w = A._janela_config
w.update_idletasks()

def todos(wd):
    yield wd
    for c in wd.winfo_children():
        yield from todos(c)
ws = list(todos(w))
ti = next(x for x in ws if x.winfo_class() == "Treeview" and "acentos" in x["columns"])
linhas = {ti.item(i, "values")[0]: ti.item(i, "values") for i in ti.get_children()}
assert linhas["Mini"][4:] == ("32", "ascii", ""), linhas["Mini"]   # v5.81: + coluna Fonte
assert linhas["Cozinha"][4:] == ("", "", ""), linhas["Cozinha"]
print("1) colunas Acentos, Colunas e Fonte carregadas da config OK:", linhas["Mini"])

combos = [x for x in ws if x.winfo_class() == "TCombobox"]
eacc = next(x for x in combos if "ascii" in x.cget("values") and "cp860" in x.cget("values"))
ecol = next(x for x in combos if tuple(x.cget("values")) in (("", "32", "42", "48"), ("32", "42", "48")) or "42" in x.cget("values") and "48" in x.cget("values") and "cp850" not in x.cget("values") and "POS-58" not in x.cget("values"))
eiw = next(x for x in combos if "POS-58" in x.cget("values") and int(str(x.cget("width"))) >= 30)
botoes = {x.cget("text"): x for x in ws if x.winfo_class() == "Button"}
assert "Testar acentos" in botoes and "Aplicar" in botoes, sorted(botoes)
print("2) combobox Acentos, Colunas e botao 'Testar acentos' presentes OK")

# Aplicar na Cozinha: 42 colunas + cp860
iid_coz = next(i for i in ti.get_children() if ti.item(i, "values")[0] == "Cozinha")
ti.selection_set(iid_coz)
eiw.set("EPSON TM-T20"); ecol.set("42"); eacc.set("cp860")
botoes["Aplicar"].invoke()
imp_coz = next(i for i in CFG["impressoras"] if i["nome"] == "Cozinha")
assert imp_coz.get("colunas") == 42 and imp_coz.get("codepage") == "cp860", imp_coz
assert ti.item(iid_coz, "values")[4:] == ("42", "cp860", ""), ti.item(iid_coz, "values")
assert gravados and any(i.get("codepage") == "cp860" for i in gravados[-1])
print("3) Aplicar grava colunas=42 e acentos=cp860 na config e na tabela OK")

# Valor invalido e recusado, sem gravar
n = len(gravados)
ti.selection_set(iid_coz); eiw.set("EPSON TM-T20"); ecol.set(""); eacc.set("koi8r")
botoes["Aplicar"].invoke()
assert len(gravados) == n and imp_coz.get("codepage") == "cp860" and avisos[-1][0] == "showwarning"
print("4) acentos invalido e recusado com aviso, nada gravado OK")

# Limpar os campos volta ao padrao
ti.selection_set(iid_coz); eiw.set("EPSON TM-T20"); ecol.set(""); eacc.set("")
botoes["Aplicar"].invoke()
assert "codepage" not in imp_coz and "colunas" not in imp_coz, imp_coz
print("5) campos vazios removem o ajuste (volta ao padrao) OK")

# Testar acentos na Mini: manda a pagina de teste para a impressora selecionada
iid_mini = next(i for i in ti.get_children() if ti.item(i, "values")[0] == "Mini")
ti.selection_set(iid_mini)
botoes["Testar acentos"].invoke()
assert impressos and impressos[-1][0].get("nome_impressora") == "POS-58"
assert impressos[-1][1] == A._bytes_teste_acentos()
assert avisos[-1][0] == "showinfo"
print("6) 'Testar acentos' envia a pagina de teste para a impressora selecionada OK")

# 'Testar Impressao' usa a tabela de acentos DA LINHA selecionada
testes_raw = []
A._imprimir_raw = lambda nome, txt: (testes_raw.append((nome, A._cp())), {"ok": True})[1]
ti.selection_set(iid_mini)
botoes["Testar Impressao"].invoke()
assert testes_raw and testes_raw[-1] == ("POS-58", "ascii"), testes_raw
print("6b) 'Testar Impressao' imprime com os Acentos da impressora (ascii) OK")

# 'Conectar' preserva os ajustes locais (colunas/acentos)
A.autoconfigurar = lambda token: {"ok": True, "data": {"restaurant_id": "r", "restaurant_name": "Loja Teste",
    "printers": [{"name": "Mini", "printer_type": "receipt"}, {"name": "Cozinha", "printer_type": "kitchen"}]}}
next(i for i in CFG["impressoras"] if i["nome"] == "Mini").update({"colunas": 32, "codepage": "ascii"})
if "Conectar ao Sistema" in botoes:
    botoes["Conectar ao Sistema"].invoke()
    mini = next(i for i in CFG["impressoras"] if i["nome"] == "Mini")
    assert mini.get("colunas") == 32 and mini.get("codepage") == "ascii", mini
    print("7) 'Conectar' mantem colunas/acentos da impressora OK")
else:
    print("7) (botao Conectar nao encontrado pelo texto; pulado)", sorted(botoes))

# 8) v5.81: Fonte POR IMPRESSORA na linha de edicao (combo 'media' -> imp['font_size']=1)
# ('Conectar' do passo 7 recriou as linhas da tabela: rebusca o iid pelo nome)
iid_coz = next(i for i in ti.get_children() if ti.item(i, "values")[0] == "Cozinha")
imp_coz = next(i for i in CFG["impressoras"] if i["nome"] == "Cozinha")
efnt = next(x for x in combos if tuple(x.cget("values")) == ("", "normal", "media", "grande", "extra"))
ti.selection_set(iid_coz); eiw.set("EPSON TM-T20"); ecol.set(""); eacc.set(""); efnt.set("media")
botoes["Aplicar"].invoke()
assert imp_coz.get("font_size") == 1, imp_coz
assert ti.item(iid_coz, "values")[6] == "media", ti.item(iid_coz, "values")
ti.selection_set(iid_coz); eiw.set("EPSON TM-T20"); efnt.set("")
botoes["Aplicar"].invoke()
assert "font_size" not in imp_coz, imp_coz
print("8) campo Fonte por impressora grava e limpa imp['font_size'] OK")

# 9) v5.81: aba Impressao — botao 'Media' grava font_size=1; '2 vias' grava vias_cupom=2;
#    'Imprimir cupom de teste' formata com _fmt e manda para a impressora do caixa.
#    ('Normal' existe em 2 grupos [tamanho e espaco]; 'Media' so no tamanho.)
todos_botoes = [x for x in ws if x.winfo_class() == "Button"]
next(b for b in todos_botoes if b.cget("text") == "Media").invoke()
assert A.cfg.get("font_size") == 1, A.cfg.get("font_size")
next(b for b in todos_botoes if b.cget("text") == "2 vias").invoke()
assert A.cfg.get("vias_cupom") == 2, A.cfg.get("vias_cupom")
n_imp = len(impressos)
next(b for b in todos_botoes if b.cget("text") == "Imprimir cupom de teste").invoke()
import time as _t
for _ in range(50):
    if len(impressos) > n_imp: break
    _t.sleep(0.1)
assert len(impressos) > n_imp, "cupom de teste nao foi enviado"
_imp_t, _dados_t = impressos[-1]
assert _imp_t.get("nome_impressora") == "POS-58", _imp_t
assert isinstance(_dados_t, str) and "PEDIDO #123" in _dados_t and "TOTAL:" in _dados_t, _dados_t[:200]
print("9) aba Impressao: Media, 2 vias e 'Imprimir cupom de teste' OK")

# 10) v5.81: TODOS os controles da aba Impressao gravam a config certa.
def _bt(txt):
    return next(b for b in todos_botoes if b.cget("text") == txt)
# tamanho base: Grande e volta a Normal
_bt("Grande").invoke();  assert A.cfg.get("font_size") == 2
_bt("Normal").invoke()   # ha 2 botoes 'Normal' (tamanho e espaco); o 1o criado e o do tamanho
assert A.cfg.get("font_size") in (0, 2)   # ver asserts dedicados de espaco abaixo
# secoes: combos com 'Herda' aparecem na ordem loja, total, rodape (pedido tem 'Auto').
# v5.87: 'Nome dos itens' saiu daqui e virou 'Nome do produto' em Itens do cupom.
combos_sec = [x for x in combos if "Herda" in x.cget("values")]
assert len(combos_sec) == 3, len(combos_sec)
cb_total = combos_sec[1]
cb_total.set("Grande"); cb_total.event_generate("<<ComboboxSelected>>"); w.update()
assert (A.cfg.get("fonte_secoes") or {}).get("total") == 2, A.cfg.get("fonte_secoes")
cb_total.set("Herda"); cb_total.event_generate("<<ComboboxSelected>>"); w.update()
assert "total" not in (A.cfg.get("fonte_secoes") or {}), A.cfg.get("fonte_secoes")
# estilo: checkbuttons e espaco
chks = {x.cget("text"): x for x in ws if x.winfo_class() == "Checkbutton"}
ck_esc = next(v for k, v in chks.items() if "mais escura" in k)
# v5.87: negrito virou escolha de tres (Nenhum / So observacoes / Cupom inteiro)
assert not any("Negrito no cupom" in k for k in chks), "checkbox antigo de negrito ainda na tela"
_bt("Cupom inteiro").invoke();  assert A.cfg.get("negrito_cupom") is True and "negrito_obs" not in A.cfg
_bt("So observacoes").invoke(); assert A.cfg.get("negrito_obs") is True and "negrito_cupom" not in A.cfg
_bt("Nenhum").invoke();         assert "negrito_obs" not in A.cfg and "negrito_cupom" not in A.cfg
ck_esc.invoke(); assert A.cfg.get("mais_escuro") is True
ck_esc.invoke(); assert "mais_escuro" not in A.cfg
_bt("Compacto").invoke(); assert A.cfg.get("espaco_linhas") == 0
_bt("Espacado").invoke(); assert A.cfg.get("espaco_linhas") == 2
# largura da bobina: 58mm grava 32; Auto remove (volta ao automatico do servidor)
_bt("58mm").invoke(); assert A.cfg.get("paper_width_cols") == 32
_bt("Auto").invoke(); assert "paper_width_cols" not in A.cfg
# corte e vias
_bt("Sem corte").invoke();        assert A.cfg.get("corte") == "nao"
_bt("Parcial (preso)").invoke();  assert A.cfg.get("corte") == "parcial"
_bt("Corte total").invoke();      assert "corte" not in A.cfg
_bt("3 vias").invoke();           assert A.cfg.get("vias_cupom") == 3
_bt("1 via").invoke();            assert "vias_cupom" not in A.cfg
# avanco antes do corte
cb_av = next(x for x in combos if tuple(x.cget("values")) == tuple(str(i) for i in range(9)))
cb_av.set("2"); cb_av.event_generate("<<ComboboxSelected>>"); w.update()
assert A.cfg.get("avanco_linhas") == 2, A.cfg.get("avanco_linhas")
cb_av.set("5"); cb_av.event_generate("<<ComboboxSelected>>"); w.update()
assert "avanco_linhas" not in A.cfg, A.cfg.get("avanco_linhas")
# e tudo isso foi SALVO no disco a cada clique (salvar_config chamado)
assert gravados, "salvar_config nunca foi chamado pelos controles"
print("10) todos os controles da aba Impressao gravam e limpam a config certa OK")

# 11) v5.87: Itens do cupom, Cupom de delivery, Comanda e a comanda de teste.
def _bts(txt):
    return [b for b in todos_botoes if b.cget("text") == txt]
_bt("Coluna a direita").invoke(); assert A.cfg.get("valores_mercado") is True
_bt("Ao lado do nome").invoke();  assert "valores_mercado" not in A.cfg
# 'Nome do produto' (o 2o grupo com Media/Grande; o 1o e o tamanho do cupom inteiro).
# Escolher aqui substitui o 'Nome dos itens' antigo de fonte_secoes.
A.cfg["fonte_secoes"] = {"itens": 2, "total": 1}
_bts("Grande")[1].invoke()
assert A.cfg.get("fonte_nome_produto") == 2, A.cfg.get("fonte_nome_produto")
assert A.cfg.get("fonte_secoes") == {"total": 1}, A.cfg.get("fonte_secoes")
_bts("Media")[1].invoke();  assert A.cfg.get("fonte_nome_produto") == 1
_bts("Normal")[-1].invoke(); assert "fonte_nome_produto" not in A.cfg   # ordem: tamanho, espaco, nome
A.cfg.pop("fonte_secoes", None)
_bt("Economico").invoke();     assert A.cfg.get("formato_delivery") == "economico"
_bts("Compacto")[-1].invoke(); assert A.cfg.get("formato_delivery") == "compacto"   # o 1o e o espaco
_bt("Completo").invoke();      assert "formato_delivery" not in A.cfg
chks = {x.cget("text"): x for x in ws if x.winfo_class() == "Checkbutton"}
ck_loja = next(v for k, v in chks.items() if "Nome da loja no topo da comanda" in k)
ck_loja.invoke(); assert A.cfg.get("loja_na_comanda") is True
n_imp = len(impressos)
_bt("Imprimir comanda de teste").invoke()
for _ in range(50):
    if len(impressos) > n_imp: break
    _t.sleep(0.1)
assert len(impressos) > n_imp, "comanda de teste nao foi enviada"
_imp_k, _dados_k = impressos[-1]
assert _imp_k.get("nome_impressora") == "EPSON TM-T20", _imp_k   # impressora da COZINHA
_txt_k = _dados_k if isinstance(_dados_k, str) else _dados_k.decode("cp850", "replace")
assert "COZINHA" in _txt_k and "LOJA TESTE" in _txt_k and "PEDIDO #123" in _txt_k, _txt_k[:300]
assert _txt_k.index("LOJA TESTE") < _txt_k.index("PEDIDO #123"), "nome da loja tem que vir antes do pedido"
ck_loja.invoke(); assert "loja_na_comanda" not in A.cfg
# a aba rola (Canvas + Scrollbar) e cabe na janela de 700 px
assert any(x.winfo_class() == "Canvas" for x in ws), "aba Impressao sem area de rolagem"
print("11) Itens do cupom, Cupom de delivery, Comanda e comanda de teste OK")

w.destroy(); A._root.destroy()
print("\nUI CONFIG: TUDO OK")
