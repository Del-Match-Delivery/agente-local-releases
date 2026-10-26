#!/usr/bin/env python3
"""Espelha no servidor os executaveis que o version.json da main aponta (AGLR-5).

Roda NO SERVIDOR, chamado pelo deploy/publicar.sh. So biblioteca padrao: o
Amazon Linux 2023 da PROJETOS tem python3, mas nao garante jq.

    python3 deploy/espelhar.py <pasta_publica> <dominio>

ORDEM QUE NAO PODE MUDAR: primeiro os .exe, depois o manifesto. O agente baixa
o que o manifesto manda; manifesto publicado antes do exe aponta para um 404 e
o agente gasta uma das 2 tentativas de update do dia (_update_pode_tentar).

INTEGRIDADE: cada exe so entra se o sha256 bater com o `digest` que o GitHub
calcula para o asset da release. O espelho nunca serve um binario diferente do
que foi publicado e testado.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = "Del-Match-Delivery/agente-local-releases"
# So asset de release DESTE repositorio. Qualquer outro endereco no manifesto e
# erro de publicacao, e espelhar de origem desconhecida seria pior que falhar.
PADRAO_URL = re.compile(
    r"^https://github\.com/" + re.escape(REPO) + r"/releases/download/"
    r"(?P<tag>[A-Za-z0-9._-]+)/(?P<nome>[A-Za-z0-9._-]+\.exe)$"
)
# O instalador nao aparece no manifesto, mas e o que a loja nova baixa primeiro.
INSTALADOR = f"https://github.com/{REPO}/releases/download/installer/InstalarAgente.exe"
# Mesmo piso do build-agente.yml: onefile com tkinter, PIL e pywin32 nunca fica abaixo.
TAMANHO_MIN = 8_000_000
VERSAO_CONGELADA = "5.77"
MANTER_RELEASES = 5


def log(msg):
    print(msg, flush=True)


def baixar(url, destino=None, tentativas=3):
    req = urllib.request.Request(url, headers={"User-Agent": "agente-local-espelho"})
    for n in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                if destino is None:
                    return r.read()
                with open(destino, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                return None
        except Exception as e:
            if n == tentativas:
                raise RuntimeError(f"falha ao baixar {url}: {e}") from e
            time.sleep(5 * n)


def sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def digest_da_release(tag, nome):
    rel = json.loads(baixar(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"))
    for a in rel.get("assets", []):
        if a.get("name") == nome:
            d = a.get("digest") or ""
            if not d.startswith("sha256:"):
                raise RuntimeError(f"{tag}/{nome}: o GitHub nao informou sha256 do asset ({d!r})")
            return d.split(":", 1)[1].lower()
    raise RuntimeError(f"a release {tag} nao tem o asset {nome}")


def espelhar(url, publico):
    m = PADRAO_URL.match(url)
    if not m:
        raise RuntimeError(f"endereco fora do padrao de release de {REPO}: {url}")
    tag, nome = m["tag"], m["nome"]
    esperado = digest_da_release(tag, nome)
    pasta = publico / "releases" / tag
    destino = pasta / nome
    if destino.exists() and sha256(destino) == esperado:
        log(f"  {tag}/{nome}: ja espelhado (sha256 {esperado[:12]}...)")
        return tag, nome
    pasta.mkdir(parents=True, exist_ok=True)
    # Temporario na MESMA pasta: o rename final e atomico, e o nginx nunca
    # entrega um exe pela metade.
    fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".baixando-")
    os.close(fd)
    try:
        baixar(url, tmp)
        tamanho = os.path.getsize(tmp)
        obtido = sha256(tmp)
        if obtido != esperado:
            raise RuntimeError(f"{tag}/{nome}: sha256 {obtido} difere do publicado {esperado}")
        if tamanho < TAMANHO_MIN:
            raise RuntimeError(f"{tag}/{nome}: {tamanho} bytes, pequeno demais para o agente")
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ":
                raise RuntimeError(f"{tag}/{nome}: nao e executavel Windows (sem MZ)")
        os.chmod(tmp, 0o644)
        os.replace(tmp, destino)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    (pasta / f"{nome}.sha256").write_text(esperado.upper() + "\n")
    log(f"  {tag}/{nome}: espelhado ({tamanho} bytes, sha256 {esperado[:12]}...)")
    return tag, nome


def podar(publico, em_uso):
    """Mantem as releases em uso e as MANTER_RELEASES mais recentes; apaga o resto."""
    pastas = sorted((p for p in (publico / "releases").iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    for p in pastas[MANTER_RELEASES:]:
        if p.name not in em_uso:
            shutil.rmtree(p)
            log(f"  removida release antiga: {p.name}")


def main():
    publico, dominio = Path(sys.argv[1]), sys.argv[2].rstrip("/")
    info = json.loads(Path("version.json").read_text(encoding="utf-8"))

    # A mesma trava da esteira: o espelho nunca publica manifesto que derruba
    # os agentes <= 5.77.
    if info.get("version") != VERSAO_CONGELADA:
        raise RuntimeError(f"version.json com version={info.get('version')!r}; tem que ser {VERSAO_CONGELADA}")

    urls = sorted({info.get(k) for k in ("url", "latest_url") if info.get(k)})
    if not urls:
        raise RuntimeError("version.json sem url/latest_url")

    log("executaveis:")
    em_uso = set()
    novo = {}
    for u in urls + [INSTALADOR]:
        tag, nome = espelhar(u, publico)
        em_uso.add(tag)
        novo[u] = f"{dominio}/releases/{tag}/{nome}"

    # Mesmos campos e valores do version.json da main, so com o endereco do exe
    # trocado para o dominio proprio.
    for k in ("url", "latest_url"):
        if info.get(k):
            info[k] = novo[info[k]]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    info["_espelho"] = {"commit": commit, "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    fd, tmp = tempfile.mkstemp(dir=publico, prefix=".version-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, publico / "version.json")
    log(f"manifesto: latest_version={info.get('latest_version')} -> {info.get('latest_url')}")

    podar(publico, em_uso)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)
