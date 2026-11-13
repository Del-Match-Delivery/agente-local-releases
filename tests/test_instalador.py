# Funcoes puras do instalador (installer/stub_installer.py): leitura do codigo no nome do
# arquivo / linha de comando / clipboard, escolha da pasta e gravacao do config.json.
# Uso: venv_build\Scripts\python.exe tests\test_instalador.py
import importlib.util, io, contextlib, json, os, sys, tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="instalador_ut_")
os.environ["COMPROUTAI_INSTALADOR_TESTE"] = json.dumps({"sem_clipboard": True})
SRC = Path(__file__).resolve().parent.parent / "installer" / "stub_installer.py"
spec = importlib.util.spec_from_file_location("instalador_sob_teste", str(SRC))
I = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(I)

# 1) codigo: mesma regra do servidor (install-code.ts)
ESPERADO = "CT-7K3M-9Q2F"
for e in ["CT-7K3M-9Q2F", "ct-7k3m-9q2f", "ct 7k3m 9q2f", "CT7K3M9Q2F", "  CT-7K3M-9Q2F  ",
          "ComprouTai-Instalar-CT-7K3M-9Q2F.exe", "ComprouTai-Instalar-CT-7K3M-9Q2F (1).exe",
          "ComprouTai-Instalar-CT-7K3M-9Q2F(2).exe", r"C:\Users\caixa\Downloads\ComprouTai-Instalar-CT-7K3M-9Q2F - Copia.exe",
          "comproutai-instalar-ct7k3m9q2f.exe"]:
    assert I.normalizar_codigo(e) == ESPERADO, (e, I.normalizar_codigo(e))
for e in ["", None, "CT-7K3M", "InstalarAgente.exe", "CT-0OO0-1II1", "ComprouTai-Instalar.exe"]:
    assert I.normalizar_codigo(e) is None, (e, I.normalizar_codigo(e))
print("1) normalizar_codigo: aceita como o lojista digita e como o navegador renomeia OK")

# 2) nome do exe e linha de comando
I.T["nome_exe"] = "ComprouTai-Instalar-CT-7K3M-9Q2F (3).exe"
assert I.codigo_do_nome_do_exe() == ESPERADO
I.T["nome_exe"] = "ComprouTai-Instalar.exe"
assert I.codigo_do_nome_do_exe() is None, "sem codigo no nome (python.exe) -> None"
assert I.codigo_da_linha_de_comando(["--codigo", "ct 7k3m 9q2f"]) == ESPERADO
assert I.codigo_da_linha_de_comando(["CT-7K3M-9Q2F"]) == ESPERADO
assert I.codigo_da_linha_de_comando(["--outra", "x"]) is None
print("2) codigo no nome do exe e na linha de comando OK")

# 3) token: formato do painel (40 chars A-Za-z0-9); clipboard desligado no teste
assert I.parece_token("A" * 40) and I.parece_token("a1B2" * 10)
assert not I.parece_token("CT-7K3M-9Q2F") and not I.parece_token("x" * 20) and not I.parece_token("a b" * 15)
print("3) parece_token OK")

# 4) pasta de instalacao: C:\ComprouTai\Agente por padrao; override no teste; fallback se bloqueada
I.T.pop("install_dir", None)
assert str(I.pasta_instalacao()).lower().endswith(r"\comproutai\agente"), I.pasta_instalacao()
tmp = Path(tempfile.mkdtemp(prefix="inst_"))
I.T["install_dir"] = str(tmp / "Agente")
assert I.pasta_instalacao() == tmp / "Agente" and (tmp / "Agente").exists()
assert I.pasta_dados() == Path(os.environ["LOCALAPPDATA"]) / "AgenteLocalMIA", "= DATA_DIR do agente"
print("4) pastas: C:\\ComprouTai\\Agente e DATA_DIR do agente OK")

# 5) config.json: completo, preserva impressoras da mesma loja, zera se loja mudou, tira 'modo', tolera BOM
data = Path(tempfile.mkdtemp(prefix="dados_"))
cred = {"token": "T" * 40, "restaurant_id": "loja-1", "restaurant_name": "Pizzaria Dom Pepe"}
p = I.gravar_config(data, cred)
c = json.loads(p.read_text(encoding="utf-8"))
assert c == {"token": "T" * 40, "restaurant_id": "loja-1", "restaurant_name": "Pizzaria Dom Pepe",
             "poll_interval": 3, "impressoras": []}, c
# mesma loja com impressoras ja mapeadas (e BOM, como o Bloco de Notas grava)
p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"token": "velho", "restaurant_id": "loja-1", "modo": "rede_unica",
                                           "poll_interval": 5,
                                           "impressoras": [{"nome": "Caixa", "nome_impressora": "EPSON"}]}).encode())
c = json.loads(I.gravar_config(data, cred).read_text(encoding="utf-8"))
assert c["token"] == "T" * 40 and c["impressoras"] == [{"nome": "Caixa", "nome_impressora": "EPSON"}]
assert "modo" not in c and c["poll_interval"] == 5, c
# loja diferente: mapeamento zerado
c = json.loads(I.gravar_config(data, dict(cred, restaurant_id="loja-2")).read_text(encoding="utf-8"))
assert c["restaurant_id"] == "loja-2" and c["impressoras"] == [], c
# config ilegivel nao derruba
p.write_text("{isso nao e json", encoding="utf-8")
c = json.loads(I.gravar_config(data, cred).read_text(encoding="utf-8"))
assert c["restaurant_id"] == "loja-1"
print("5) config.json completo, preserva mapeamento da mesma loja, zera loja nova, tolera BOM/lixo OK")

# 6) certificado: Windows sem a raiz do GitHub (PDV antigo) -> truststore -> certifi, SEMPRE verificando
import ssl, urllib.error, urllib.request
VERIFY = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1032)"
chamadas = []


class _Ctx:
    def __init__(self, nome):
        self.nome = nome


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return b'{"version": "5.77", "latest_url": "https://x/AgenteLocal.exe", "latest_version": "9.9"}'


def _urlopen_falso(recusar, erro=None):
    def f(req, timeout=None, context=None):
        chamadas.append(context.nome)
        if context.nome in recusar:
            raise erro or urllib.error.URLError(ssl.SSLCertVerificationError(1, VERIFY))
        return _Resp()
    return f


orig_ctx, orig_open = I._CONTEXTOS_SSL, urllib.request.urlopen
I._CONTEXTOS_SSL = tuple((n, (lambda n=n: _Ctx(n))) for n in ("windows", "truststore", "certifi"))
try:
    urllib.request.urlopen = _urlopen_falso(set())
    assert I.ler_versao_publicada() == ("https://x/AgenteLocal.exe", "9.9") and chamadas == ["windows"], chamadas
    chamadas.clear()
    urllib.request.urlopen = _urlopen_falso({"windows"})
    assert I.ler_versao_publicada()[1] == "9.9" and chamadas == ["windows", "truststore"], chamadas
    chamadas.clear()
    urllib.request.urlopen = _urlopen_falso({"windows", "truststore"})
    assert I.ler_versao_publicada()[1] == "9.9" and chamadas == ["windows", "truststore", "certifi"], chamadas
    # todos recusam: mensagem de certificado, e NUNCA tenta sem verificacao
    chamadas.clear()
    urllib.request.urlopen = _urlopen_falso({"windows", "truststore", "certifi"})
    try:
        I.ler_versao_publicada()
        raise AssertionError("deveria falhar")
    except I.ErroInstalacao as e:
        assert "certificado de segurança" in str(e) and "_ssl.c" not in str(e), e
    assert chamadas == ["windows", "truststore", "certifi"], chamadas
    # relogio errado: manda acertar a data/hora
    urllib.request.urlopen = _urlopen_falso({"windows", "truststore", "certifi"}, urllib.error.URLError(
        ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")))
    try:
        I.ler_versao_publicada()
        raise AssertionError("deveria falhar")
    except I.ErroInstalacao as e:
        assert "data ou a hora" in str(e), e
    # sem internet (nao e certificado): nao fica trocando de contexto
    chamadas.clear()
    urllib.request.urlopen = _urlopen_falso({"windows", "truststore", "certifi"},
                                            urllib.error.URLError(ConnectionRefusedError(10061, "recusada")))
    try:
        I.ler_versao_publicada()
        raise AssertionError("deveria falhar")
    except I.ErroInstalacao as e:
        assert "internet" in str(e), e
    assert chamadas == ["windows"], chamadas
    # truststore ausente no exe: pula para o certifi
    chamadas.clear()
    I._CONTEXTOS_SSL = (("windows", lambda: _Ctx("windows")), ("truststore", lambda: 1 / 0), ("certifi", lambda: _Ctx("certifi")))
    urllib.request.urlopen = _urlopen_falso({"windows"})
    assert I.ler_versao_publicada()[1] == "9.9" and chamadas == ["windows", "certifi"], chamadas
finally:
    I._CONTEXTOS_SSL, urllib.request.urlopen = orig_ctx, orig_open
# contextos reais: os tres existem no ambiente de build e verificam certificado
for nome, fabrica in I._CONTEXTOS_SSL:
    c = fabrica()
    assert c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname, nome
print("6) certificado: Windows sem raiz -> truststore -> certifi; relogio/internet com mensagem certa; nunca sem verificar OK")

# 7) credencial ja resgatada: 'Tentar de novo' nao queima o codigo de uso unico
I.apagar_pendente()
assert I.credencial_pendente(ESPERADO) is None
I.guardar_pendente(ESPERADO, cred)
assert I.credencial_pendente(ESPERADO) == cred
assert I.credencial_pendente("CT-2222-3333") is None, "codigo diferente nao reaproveita"
I.apagar_pendente()
assert I.credencial_pendente(ESPERADO) is None and not I._arquivo_pendente().exists()
I.apagar_pendente()   # apagar de novo nao quebra
print("7) credencial pendente: reaproveita so o mesmo codigo e some depois de instalar OK")

print("\nTODOS OS TESTES DO INSTALADOR (unitarios) PASSARAM")
