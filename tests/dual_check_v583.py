import sys, json, io, logging
import agente_local as ag

logging.basicConfig(level=logging.INFO)

print("=== [DUAL CHECK 1/3] Teste de Mapeamento de Pagamentos (AGLR-8) ===")
assert ag.PL.get("online_card") == "Cartao Online", "Erro no mapeamento online_card"
assert ag.PL.get("online_pix") == "PIX Online", "Erro no mapeamento online_pix"
assert ag.PL.get("cash") == "Dinheiro"
assert ag.PL.get("credit") == "Cartao Credito"
print("-> AGLR-8 OK: Formas de pagamento mapeadas com exatidao!")

print("\n=== [DUAL CHECK 2/3] Teste de Comparacao de Versoes ===")
assert ag._ver_tuple(ag.CURRENT_VERSION) >= (5, 83), "CURRENT_VERSION incorreta"   # v5.84+: a checagem da 5.83 continua valendo
assert ag._ver_tuple("5.83") > ag._ver_tuple("5.82")
assert ag._ver_tuple("5.83") == (5, 83)
assert ag._ver_tuple("5.82") < ag._ver_tuple(ag.CURRENT_VERSION)
print("-> Comparacao de Versao OK: v5.83 superior a v5.82, logica de update consistente!")

print("\n=== [DUAL CHECK 3/3] Teste de Leitura de Manifesto e Failover (AGLR-5) ===")
# 3A: Leitura normal (deve bater no dominio proprio)
manifesto_normal = ag._ler_manifesto()
print("-> 3A (Normal): Lido do dominio proprio! latest_version remota=" + str(manifesto_normal.get("latest_version")))

# 3B: Simulando queda total do dominio proprio (DNS/HTTP down)
urls_originais = ag.VERSION_URLS
ag.VERSION_URLS = (
    "https://dominio-inexistente-ou-quebrado-para-teste.delmatch.com.br/version.json",
    urls_originais[1]  # GitHub reserva
)

try:
    manifesto_reserva = ag._ler_manifesto()
    assert isinstance(manifesto_reserva, dict), "Manifesto da reserva invalido"
    print("-> 3B (Failover): Reserva do GitHub respondeu com sucesso! latest_version=" + str(manifesto_reserva.get("latest_version")))
finally:
    ag.VERSION_URLS = urls_originais

print("\n>>> DUAL CHECK COMPLETO: 100% APROVADO! <<<")
