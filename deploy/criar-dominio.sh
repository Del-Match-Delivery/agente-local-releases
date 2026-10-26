#!/usr/bin/env bash
# Cria agente.delmatch.com.br (AGLR-5): certificado ACM, CloudFront e DNS.
#
# Roda UMA vez, na maquina de quem administra a conta 830528708244 (nao no CI:
# a role do deploy so pode SSM na PROJETOS). Idempotente: rodar de novo so
# confere o que ja existe.
#
# Mesmo desenho do agenda.delmatch.com.br (EK5ZJWXRH3WD9): CloudFront com TLS ->
# EC2 Delmatch_PROJETOS na porta 80 (Traefik) -> container agente-local-espelho.
#   - Cache: Managed-CachingOptimized NAO; usa Managed-UseOriginCacheControlHeaders,
#     para valer o Cache-Control do nginx (version.json no-cache, exe 1h).
#   - Origem: Managed-AllViewer, que repassa o Host. O Traefik roteia por Host.
set -euo pipefail

DOMINIO=agente.delmatch.com.br
ZONA=Z07095342K6RUGHC46KT4                         # delmatch.com.br
ORIGEM=ec2-44-219-80-134.compute-1.amazonaws.com   # Delmatch_PROJETOS
CACHE_POLICY=83da9c7e-98b4-4e11-a168-04f0df8e2c65  # Managed-UseOriginCacheControlHeaders
ORIGIN_REQ_POLICY=216adef6-5c7f-47e4-b989-5492eafa07d3  # Managed-AllViewer
ZONA_CLOUDFRONT=Z2FDTNDATAQYW2
export AWS_DEFAULT_REGION=us-east-1                # ACM do CloudFront so em us-east-1

echo "== 1/3 certificado"
CERT=$(aws acm list-certificates --certificate-statuses PENDING_VALIDATION ISSUED \
  --query "CertificateSummaryList[?DomainName=='$DOMINIO'].CertificateArn | [0]" --output text)
if [ "$CERT" = "None" ] || [ -z "$CERT" ]; then
  CERT=$(aws acm request-certificate --domain-name "$DOMINIO" --validation-method DNS \
    --idempotency-token aglr5 --query CertificateArn --output text)
  echo "solicitado: $CERT"
  sleep 10
fi
read -r NOME VALOR < <(aws acm describe-certificate --certificate-arn "$CERT" \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord.[Name,Value]' --output text)
aws route53 change-resource-record-sets --hosted-zone-id "$ZONA" --change-batch "$(jq -n \
  --arg n "$NOME" --arg v "$VALOR" \
  '{Changes:[{Action:"UPSERT",ResourceRecordSet:{Name:$n,Type:"CNAME",TTL:300,ResourceRecords:[{Value:$v}]}}]}')" \
  --query ChangeInfo.Status --output text
echo "aguardando validacao do certificado (costuma levar 1-5 min)..."
aws acm wait certificate-validated --certificate-arn "$CERT"
echo "certificado emitido: $CERT"

echo "== 2/3 CloudFront"
DIST=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Aliases.Items && contains(Aliases.Items, '$DOMINIO')].Id | [0]" --output text)
if [ "$DIST" = "None" ] || [ -z "$DIST" ]; then
  CFG=$(jq -n --arg d "$DOMINIO" --arg o "$ORIGEM" --arg c "$CERT" \
    --arg cp "$CACHE_POLICY" --arg orp "$ORIGIN_REQ_POLICY" --arg ref "aglr5-$(date +%s)" '{
    CallerReference: $ref,
    Comment: ($d + " - espelho das releases do Agente Local (EC2 Delmatch_PROJETOS via Traefik) AGLR-5"),
    Enabled: true,
    Aliases: {Quantity: 1, Items: [$d]},
    Origins: {Quantity: 1, Items: [{
      Id: "projetos-traefik", DomainName: $o, OriginPath: "",
      CustomHeaders: {Quantity: 0},
      CustomOriginConfig: {HTTPPort: 80, HTTPSPort: 443, OriginProtocolPolicy: "http-only",
        OriginSslProtocols: {Quantity: 1, Items: ["TLSv1.2"]},
        OriginReadTimeout: 60, OriginKeepaliveTimeout: 5}}]},
    DefaultCacheBehavior: {
      TargetOriginId: "projetos-traefik",
      ViewerProtocolPolicy: "redirect-to-https",
      AllowedMethods: {Quantity: 2, Items: ["GET","HEAD"], CachedMethods: {Quantity: 2, Items: ["GET","HEAD"]}},
      Compress: true,
      CachePolicyId: $cp,
      OriginRequestPolicyId: $orp},
    ViewerCertificate: {ACMCertificateArn: $c, SSLSupportMethod: "sni-only", MinimumProtocolVersion: "TLSv1.2_2021"},
    PriceClass: "PriceClass_All",
    HttpVersion: "http2",
    IsIPV6Enabled: true}')
  DIST=$(aws cloudfront create-distribution --distribution-config "$CFG" --query Distribution.Id --output text)
  echo "criada: $DIST"
else
  echo "ja existe: $DIST"
fi
CF=$(aws cloudfront get-distribution --id "$DIST" --query Distribution.DomainName --output text)

echo "== 3/3 DNS"
aws route53 change-resource-record-sets --hosted-zone-id "$ZONA" --change-batch "$(jq -n \
  --arg n "$DOMINIO" --arg cf "$CF" --arg z "$ZONA_CLOUDFRONT" '{Changes:[
    {Action:"UPSERT",ResourceRecordSet:{Name:$n,Type:"A",AliasTarget:{HostedZoneId:$z,DNSName:$cf,EvaluateTargetHealth:false}}},
    {Action:"UPSERT",ResourceRecordSet:{Name:$n,Type:"AAAA",AliasTarget:{HostedZoneId:$z,DNSName:$cf,EvaluateTargetHealth:false}}}]}')" \
  --query ChangeInfo.Status --output text

echo "aguardando o CloudFront terminar de propagar (5-15 min)..."
aws cloudfront wait distribution-deployed --id "$DIST"
echo "pronto: https://$DOMINIO -> $CF ($DIST)"
curl -s -o /dev/null -w "https://$DOMINIO/healthz -> %{http_code}\n" "https://$DOMINIO/healthz" || true
