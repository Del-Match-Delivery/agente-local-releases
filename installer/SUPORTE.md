# Instalar o agente de impressão num cliente (roteiro do suporte)

O instalador é um só: `ComprouTai-Instalar.exe`. O que muda é de onde vem a credencial.
Em todos os casos ele instala em `C:\ComprouTai\Agente`, grava o config completo em
`%LOCALAPPDATA%\AgenteLocalMIA\config.json`, encerra um agente que esteja aberto, cria o
atalho "ComprouTai Agente" e abre o agente já conectado. Ninguém cola token.

## 1. Pelo painel do cliente (preferido, zero digitação)

1. Entre no painel da loja (sua conta de suporte serve: `admin_saas`, `super_admin` e
   `support_agent` podem gerar o link de qualquer loja).
2. Integrações › Impressoras › aba **Agente** › **Instalar neste computador** se você está no
   PC da loja por acesso remoto, ou **Copiar link para outro computador** e mande o link por
   WhatsApp para o cliente abrir no PC do caixa.
3. O arquivo baixa como `ComprouTai-Instalar-CT-XXXX-XXXX.exe`. Abrir e esperar. O card do
   painel vira "Conectado: NOME-DO-PC · vX.YZ" sozinho.
4. Único passo manual: na janela do agente, aba Impressoras, escolher a impressora do Windows
   de cada setor. Se a loja ainda não cadastrou nenhuma impressora na aba Impressoras do
   painel, a grade do agente vem vazia: cadastre pelo menos "Caixa" (cupom) antes.

O código vale 24 h e é de uso único: um por computador. Para reinstalar ou instalar em outro
PC, gere outro link. O mapeamento de impressoras já feito é preservado na reinstalação da
mesma loja.

## 2. Instalador genérico + código digitado

Baixe o instalador sem código:

```
https://szlyzyflalerxuyxfxzh.supabase.co/storage/v1/object/public/agent-downloads/windows/ComprouTai-Instalar.exe
```

Ao abrir, ele mostra "Digite o código de instalação". Digite o `CT-XXXX-XXXX` que aparece no
card do painel (passo 1). Dez caracteres, sem diferença entre maiúsculas e minúsculas, com ou
sem hífen.

## 3. Token de API (fluxo antigo, continua funcionando)

Integrações › API › nova chave com escopos `printers:read` e `printers:write` › copiar. Com a
chave na área de transferência, abra o instalador: ele reconhece o token, valida no servidor e
grava o config completo. Também dá para colar o token na tela "Digite o código". Use este
caminho enquanto o PR do painel (ComprouTai #681) não estiver em produção, porque o código de
instalação só existe depois dele.

## Onde está cada coisa

| O quê | Onde |
|---|---|
| Programa | `C:\ComprouTai\Agente\AgenteLocal.exe` (ou `%LOCALAPPDATA%\ComprouTai\Agente` se o C:\ for bloqueado) |
| Config e log do agente | `%LOCALAPPDATA%\AgenteLocalMIA\` (atalho "Dados do agente" dentro da pasta do programa) |
| Log do instalador | `%LOCALAPPDATA%\AgenteLocalMIA\instalador.log` |
| Início automático | chave `AgenteLocal` em HKCU\...\Run e atalho na pasta Inicializar |

## Mensagens de erro do instalador e o que fazer

- "Este código já foi usado no computador X": gere outro link no painel.
- "Este código venceu": gere outro link (24 h).
- "Não encontrei este código": confira os 10 caracteres ou gere outro.
- "Não consegui falar com o servidor": internet do PC.
- "...a data ou a hora deste computador parece errada": acerte data, hora e fuso do Windows e
  clique em Tentar de novo.
- "...este Windows não reconheceu o certificado de segurança do site": o instalador já tentou
  o certificado do Windows, a validação do próprio Windows (que baixa a raiz que falta) e a
  lista embutida. Sobra antivírus/proxy inspecionando HTTPS ou Windows sem atualização. Veja
  as linhas `[SSL]` do `instalador.log`.
- Download falhou depois de "Conectar com a loja": clique em Tentar de novo (ou abra o mesmo
  arquivo outra vez). Desde a 2.1 o instalador reaproveita o código já resgatado
  (`instalacao_pendente.json` na pasta de dados) e não dá mais "código já usado".
- "O agente ainda está aberto": feche pela bandeja e clique em Tentar de novo.
- Instalador antigo (v1, 29/08) mostrava `<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] ...
  unable to get local issuer certificate>`: Windows sem a raiz do GitHub (Let's Encrypt / ISRG
  Root X1). Baixe o instalador atual (2.1+), que contorna isso verificando o certificado de
  outras formas.
- Aviso do Windows "Editor desconhecido": Mais informações › Executar assim mesmo. Em Windows
  11 com Smart App Control, o bloqueio pode durar alguns minutos no primeiro clique. Só o
  certificado de assinatura resolve de vez.

## Compilar e publicar o instalador

`installer\build_stub.bat` compila com o venv_build e roda os testes (unitários e ponta a
ponta). Publicar: subir `installer\dist\ComprouTai-Instalar.exe` no bucket `agent-downloads`
em `windows/ComprouTai-Instalar.exe` (apague o anterior antes: a policy do bucket não permite
sobrescrever) e, por compatibilidade com o link antigo do GitHub, na tag `installer` do
repositório de releases como `InstalarAgente.exe`.
