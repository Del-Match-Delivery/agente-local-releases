#!/usr/bin/env bash
# Publica o espelho das releases do agente em agente.delmatch.com.br (AGLR-5).
#
# Roda NO SERVIDOR (EC2 Delmatch_PROJETOS), como ec2-user, no clone em
# /home/ec2-user/agente-local-releases ja posicionado no commit da main. Chamado
# pelo .github/workflows/deploy-prod.yml via SSM; tambem serve para rodar a mao:
#   cd /home/ec2-user/agente-local-releases && bash deploy/publicar.sh
#
# Os arquivos servidos ficam FORA do clone (o deploy faz git reset --hard) e
# entram no container como somente leitura.
set -euo pipefail

PUBLICO=/home/ec2-user/agente-local-publico
DOMINIO=https://agente.delmatch.com.br
HOST=agente.delmatch.com.br
COMPOSE=deploy/docker-compose.yml
LOCAL=http://127.0.0.1:3014

echo "commit publicado: $(git rev-parse --short HEAD)"
mkdir -p "$PUBLICO/releases"
chmod 755 "$PUBLICO" "$PUBLICO/releases"

python3 deploy/espelhar.py "$PUBLICO" "$DOMINIO"

# O primeiro deploy (26/10/2026) subiu o container no projeto `deploy`, antes do
# `name:` no compose. Com o mesmo container_name, o `up` no projeto novo daria
# conflito; o container antigo e removido aqui (ninguem depende dele ainda).
proj=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' agente-local-espelho 2>/dev/null || true)
if [ -n "$proj" ] && [ "$proj" != "agente-local-espelho" ]; then
  echo "removendo agente-local-espelho do projeto compose '$proj'"
  docker rm -f agente-local-espelho
fi
docker compose -f "$COMPOSE" up -d
sleep 3
docker compose -f "$COMPOSE" ps

# Container de pe nao prova arquivo servido: sem estas checagens o deploy fica
# verde com o manifesto apontando para um 404.
falha() { echo "ERRO: $*" >&2; exit 1; }

codigo=$(curl -s -o /dev/null -w '%{http_code}' "$LOCAL/healthz")
echo "healthz: $codigo"
[ "$codigo" = "200" ] || falha "/healthz devolveu $codigo"

servido=$(curl -fsS "$LOCAL/version.json") || falha "version.json nao servido"
[ "$servido" = "$(cat "$PUBLICO/version.json")" ] || falha "version.json servido difere do gerado"

# Cada exe do manifesto e o instalador: 200 e Content-Length igual ao arquivo.
# O agente aborta o update quando o tamanho baixado difere do Content-Length.
for caminho in $(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
pre = sys.argv[2] + "/"
for u in sorted({d.get("url"), d.get("latest_url")} - {None, ""}):
    print(u[len(pre):] if u.startswith(pre) else "FORA:" + u)
print("releases/installer/InstalarAgente.exe")
' "$PUBLICO/version.json" "$DOMINIO"); do
  case "$caminho" in FORA:*) falha "manifesto aponta para fora do dominio: ${caminho#FORA:}";; esac
  esperado=$(stat -c%s "$PUBLICO/$caminho")
  cab=$(curl -fsSI "$LOCAL/$caminho") || falha "/$caminho nao servido"
  tam=$(printf '%s' "$cab" | tr -d '\r' | awk 'tolower($1)=="content-length:"{print $2}')
  [ "$tam" = "$esperado" ] || falha "/$caminho com Content-Length '$tam', esperado $esperado"
  echo "ok: /$caminho ($tam bytes)"
done

# A rota do Traefik, pelo mesmo caminho que o CloudFront usa (Host no cabecalho).
# O Traefik descobre o container pelo Docker em alguns segundos na primeira subida.
for _ in $(seq 1 15); do
  codigo=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/version.json)
  [ "$codigo" = "200" ] && break
  sleep 2
done
echo "traefik ($HOST): $codigo"
[ "$codigo" = "200" ] || falha "Traefik devolveu $codigo para $HOST"

echo "espelho publicado"
