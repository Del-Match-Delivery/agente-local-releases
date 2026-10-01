# ============================================================================
#  PUBLICAR UMA VERSAO DO AGENTE LOCAL  (rodar com o `gh` ja autenticado)
#     powershell -NoProfile -ExecutionPolicy Bypass -File "<esta pasta>\publicar_release.ps1" -Chave AGLR-<numero real>
#  Opcoes:
#     -Simular   so LE o GitHub e mostra o que seria feito (nada e criado nem gravado)
#     -Hotfix    emergencia: PR direto na main (branch hotfix/*), backport para develop depois
#
#  O repositorio de releases tem a ESTEIRA develop -> staging -> main (rulesets + check
#  "validate"): nada entra na main sem PULL REQUEST com a chave do work package (AGLR-N/COMP-N),
#  1 aprovacao e code owner (tech-leads). Por isso este roteiro NAO grava direto na main:
#    1) cria a branch fix/<chave>-agente-<versao> a partir da develop com o CODIGO-FONTE
#       (agente_local.py), version.json, diagnostico e testes;
#    2) cria a release v<versao> com o AgenteLocal.exe TESTADO, com a tag no commit dessa branch
#       (o "Source code" da release e o codigo que gerou o exe);
#    3) confere o download publico do exe;
#    4) abre o PR para a develop. Os tech-leads revisam e promovem develop -> staging -> main.
#  Os agentes SO passam a ver a versao nova quando o version.json chegar na main.
#
#  Guardas: hash do exe = release_<versao>\AgenteLocal.exe.sha256 (build testado); 'version' do
#  version.json continua 5.77; a branch sai de um commit da develop cujo agente_local.py e o MESMO
#  sobre o qual o merge foi feito (release_<versao>\base.sha); a branch/titulo passam na mesma regra
#  do check "validate"; e um PR ja mergeado nao e reaberto. Idempotente: pode rodar de novo apos
#  qualquer falha (inclusive release deixada como RASCUNHO por um upload interrompido).
#
#  Windows PowerShell 5.1: TODA chamada ao gh passa por Invoke-Gh. Com ErrorActionPreference
#  'Stop', redirecionar o stderr de um programa nativo (2>$null / 2>&1) vira erro FATAL - era isso
#  que derrubava o roteiro em "release not found" (a resposta normal quando a release nao existe).
#  Este arquivo e ASCII puro: o PS 5.1 le .ps1 sem BOM na pagina ANSI e estragaria acentos/emoji.
# ============================================================================
param(
    [Parameter(Mandatory = $true)][string]$Chave,   # chave REAL do work package no OpenProject
    [switch]$Hotfix,
    [switch]$Simular,
    [switch]$ForcarChave                             # so se AGLR-123/COMP-123 for mesmo a chave real
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # a barra de progresso deixa o Invoke-WebRequest de 20 MB muito lento no PS 5.1
$repo = 'Del-Match-Delivery/agente-local-releases'
$aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Fail($m) { Write-Host ("ERRO: " + $m) -ForegroundColor Red; exit 1 }
function Plano($m) { Write-Host ("      [SIMULACAO] " + $m) -ForegroundColor Cyan }

function Invoke-Gh {
    # Roda o gh SEM transformar stderr em erro fatal. Devolve Ok (exit 0), Out (stdout) e Err (stderr).
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $saida = & gh @args 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    $out = @($saida | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
    $err = @($saida | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
    [pscustomobject]@{ Ok = ($code -eq 0); Code = $code; Out = (($out -join "`n").Trim()); Err = (($err -join "`n").Trim()) }
}

# ---- chave: MAIUSCULAS e digitos 0-9, com um prefixo que o check "validate" aceite ----
# (o grep do check diferencia maiuscula; 'agnt-039' vira 'AGNT-039'. Os prefixos aceitos sao lidos do
#  proprio .github/workflows/esteira.yml da branch de destino: se o time incluir um prefixo novo,
#  este roteiro passa a aceita-lo sem editar nada.)
$Chave = $Chave.Trim().ToUpperInvariant()
if ($Hotfix) { $baseRef = 'main' } else { $baseRef = 'develop' }

$gv = Invoke-Gh --version
if (-not $gv.Ok) { Fail "gh nao encontrado/nao funciona: $($gv.Err)" }
$au = Invoke-Gh auth status
if (-not $au.Ok) { Fail "gh nao autenticado. Rode 'gh auth login'. $($au.Err)" }

$prefixos = 'AGLR|COMP'
$rest = Invoke-Gh api "repos/$repo/contents/.github/workflows/esteira.yml?ref=$baseRef" -H 'Accept: application/vnd.github.raw'
if ($rest.Ok) {
    $mc = [regex]::Match($rest.Out, 'CHAVES:\s*"([A-Z|]+)"')
    if ($mc.Success) { $prefixos = $mc.Groups[1].Value }
} else { Write-Host "      (aviso: nao li o esteira.yml da $baseRef; usando os prefixos padrao $prefixos)" -ForegroundColor Yellow }
$listaPrefixos = ($prefixos -split '\|' | ForEach-Object { "$_-N" }) -join ' ou '
if ($Chave -cnotmatch ('^(' + $prefixos + ')-[0-9]+$')) {
    if ($Chave -cmatch '^[A-Z]+-[0-9]+$') {
        Fail "a chave '$Chave' nao e aceita pelo check 'validate' da $baseRef, que so aceita $listaPrefixos (CHAVES no .github/workflows/esteira.yml). Com ela o PR fica BLOQUEADO pelo check obrigatorio e nunca pode ser mergeado. Saidas: (1) um tech-lead inclui o prefixo $($Chave.Split('-')[0]) em CHAVES no esteira.yml, pela esteira, como o PR #1 fez com COMP, e voce roda este comando de novo sem mudar nada; ou (2) use a chave $listaPrefixos desta entrega."
    }
    Fail "Chave invalida '$Chave': use $listaPrefixos, so letras maiusculas e digitos 0-9 (o check 'validate' exige)."
}
if (-not $ForcarChave -and $Chave -cin @('AGLR-123', 'COMP-123')) {
    Fail "'$Chave' e o EXEMPLO da documentacao, nao a chave do work package. O check 'validate' so confere o FORMATO: com a chave errada o sync move o ticket errado no OpenProject. Use a chave real (a do ticket desta entrega em genpro.delmatch.com.br). Se $Chave for mesmo a real, rode de novo com -ForcarChave."
}

$vj  = Get-Content (Join-Path $aqui 'version.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = [string]$vj.latest_version
if (-not $ver) { Fail "version.json sem latest_version" }
$tag   = "v$ver"
$dir   = Join-Path $aqui ("release_" + $ver)
$exe   = Join-Path $dir 'AgenteLocal.exe'
$notes = Join-Path $dir 'notes.md'
$shaF  = Join-Path $dir 'AgenteLocal.exe.sha256'
$baseF = Join-Path $dir 'base.sha'
$titF  = Join-Path $dir 'title.txt'
$titulo = "v$ver"; if (Test-Path $titF) { $titulo = (Get-Content $titF -Raw -Encoding UTF8).Trim() }
if ($Hotfix) { $branch = "hotfix/$Chave-agente-$ver" } else { $branch = "fix/$Chave-agente-$ver" }
# Titulo do PR sai do title.txt da release (sem o prefixo "vX.Y - "), nao de texto fixo:
# na v5.81 o texto fixo da v5.80 teria saido no PR errado.
$tituloPr = "[$Chave] Agente Local ${tag}: " + ($titulo -replace '^v[0-9.]+\s*-\s*', '')

# arquivos que vao no PR (caminho no repo -> arquivo local)
$arquivos = [ordered]@{
    'agente_local.py'        = 'agente_local.py'
    'version.json'           = 'version.json'
    'diagnostico_agente.ps1' = 'diagnostico_agente.ps1'
    'publicar_release.ps1'   = 'publicar_release.ps1'
    'pagina_prova_impressora.ps1' = 'pagina_prova_impressora.ps1'
}
Get-ChildItem (Join-Path $aqui 'tests') -File | Where-Object { $_.Extension -in '.py', '.ps1', '.md' } | Sort-Object Name | ForEach-Object { $arquivos[('tests/' + $_.Name)] = ('tests\' + $_.Name) }

function Bytes-Locais($caminhoRepo, $caminhoLocal) {
    $b = [IO.File]::ReadAllBytes((Join-Path $aqui $caminhoLocal))
    # o repo guarda texto em LF (.py/.ps1/.md/.json); o arquivo local pode estar em CRLF, o que faria o
    # PR mostrar o arquivo inteiro como reescrito. .bat fica de fora: o cmd precisa de CRLF.
    if ($caminhoRepo -notlike '*.bat') {
        $b = $utf8.GetBytes(($utf8.GetString($b) -replace "`r`n", "`n"))
    }
    return , $b
}
function Conteudo-Remoto($caminhoRepo, $ref) {
    # @{Existe; Sha; B64} do arquivo no ref (Existe=$false se nao existe). Falha de rede/permissao => Fail.
    $r = Invoke-Gh api "repos/$repo/contents/${caminhoRepo}?ref=$ref"
    if (-not $r.Ok) {
        if ($r.Err -match '404|Not Found') { return @{ Existe = $false; Sha = $null; B64 = $null } }
        Fail "nao consegui ler $caminhoRepo em $ref : $($r.Err)"
    }
    $j = $r.Out | ConvertFrom-Json
    $b64 = $null
    if ($j.content) { $b64 = [Convert]::ToBase64String([Convert]::FromBase64String(($j.content -replace '\s', ''))) }
    return @{ Existe = $true; Sha = $j.sha; B64 = $b64 }
}
function Chave-De($texto) {
    $m = [regex]::Match($texto, ('\b(' + $prefixos + ')-[0-9]+\b'))
    if ($m.Success) { return $m.Value } else { return '' }
}
function Corpo-Pr {
    $notasTxt = Get-Content $notes -Raw -Encoding UTF8
    $obsHotfix = ''; if ($Hotfix) { $obsHotfix = 'Hotfix direto na main: abrir o backport para develop depois do merge.' }
    $robo = [char]::ConvertFromUtf32(0x1F916)   # emoji montado em tempo de execucao: o arquivo fica ASCII
    return @"
## Agente Local $tag

Release publicada (exe TESTADO): https://github.com/$repo/releases/tag/$tag
AgenteLocal.exe sha256 ``$hashEsp``

Este PR traz para o repositorio o **codigo-fonte** que gerou o exe (base: o agente_local.py que ja estava na develop, sha em ``release_$ver\base.sha``), o ``version.json`` apontando ``latest_*`` para $tag e os testes em ``tests/``. $(if ($tagCommit) { "A tag $tag aponta para o commit $tagCommit (codigo-fonte que gerou o exe)." } else { "A tag $tag aponta para o commit mais recente desta branch." })

**Os agentes so passam a ver a $tag quando este ``version.json`` chegar na main** (develop -> staging -> main). ``version`` continua 5.77 de proposito: ver ``_REGRA_DE_PUBLICACAO`` dentro do arquivo.

### Como validar
``````
venv_build\Scripts\python.exe tests\test_eleicao.py
venv_build\Scripts\python.exe tests\test_agendado.py
venv_build\Scripts\python.exe tests\test_hora.py
venv_build\Scripts\python.exe tests\test_codepage.py
venv_build\Scripts\python.exe tests\test_ui_config.py
venv_build\Scripts\python.exe tests\test_fonte.py
``````

### Notas da versao
$notasTxt

$obsHotfix

$robo Generated with [Claude Code](https://claude.com/claude-code)
"@
}

if ($Simular) { Write-Host "*** SIMULACAO: so leitura. Nada sera criado nem gravado no GitHub. ***" -ForegroundColor Cyan }

# ---- 0) sanidade local ----
foreach ($f in $exe, $notes, $shaF, $baseF) { if (-not (Test-Path $f)) { Fail "nao achei $f" } }
$hashEsp = (Get-Content $shaF -Raw).Trim().ToUpper()
$h = (Get-FileHash $exe -Algorithm SHA256).Hash
if ($h -ne $hashEsp) { Fail "hash do exe local ($h) difere do testado ($hashEsp). Nao publique um build diferente do testado." }
$tamExe = (Get-Item $exe).Length
if ($vj.version -ne '5.77') { Fail "version.json: 'version' tem que continuar 5.77 (agentes antigos se matam se mudar)." }
foreach ($u in $vj.url, $vj.latest_url) { if ($u -notlike "*/download/$tag/AgenteLocal.exe") { Fail "version.json: url/latest_url nao apontam para $tag ($u)" } }
$src = Get-Content (Join-Path $aqui 'agente_local.py') -Raw -Encoding UTF8
if ($src -notmatch ('CURRENT_VERSION = "' + [regex]::Escape($ver) + '"')) { Fail "agente_local.py nao esta na versao $ver" }
foreach ($k in $arquivos.Keys) { if (-not (Test-Path (Join-Path $aqui $arquivos[$k]))) { Fail "arquivo do PR nao encontrado: $($arquivos[$k])" } }
Write-Host "[0/4] $tag : exe OK ($tamExe bytes, sha256 $($h.Substring(0,12))...), version.json OK, chave $Chave, base $baseRef"

# ---- pre-checagem do "validate" (mesma regra do .github/workflows/esteira.yml) ----
if ($baseRef -eq 'main') {
    if ($branch -notlike 'hotfix/*') { Fail "main so recebe de staging ou hotfix/*; branch '$branch'." }
} else {
    if ($branch -cnotmatch '^(feat|feature|fix|bugfix|hotfix|chore|docs|refactor|security|sec|spike|release)/') { Fail "develop nao aceita a branch '$branch'." }
}
$kt = Chave-De $tituloPr; $kb = Chave-De $branch
if (-not $kt -and -not $kb) { Fail "PR sem chave $listaPrefixos no titulo nem na branch." }
if ($kt -and $kb -and $kt -cne $kb) { Fail "chave divergente: titulo $kt x branch $kb." }
Write-Host "      validate (local): ordem ok ($branch -> $baseRef), chave ok ($kt)"

# ---- PRs desta branch (qualquer estado): um PR ja MERGEADO nao e reaberto ----
$rl = Invoke-Gh pr list -R $repo --head $branch --base $baseRef --state all --json 'url,state'
if (-not $rl.Ok) { Fail "nao consegui listar PRs: $($rl.Err)" }
$prUrl = ''; $prMergeado = ''
foreach ($p in ($rl.Out | ConvertFrom-Json)) {   # sem @(): no PS 5.1 ConvertFrom-Json entrega o array como UM objeto e @() o embrulharia de novo
    if (-not $p) { continue }
    if ($p.state -eq 'OPEN' -and -not $prUrl) { $prUrl = $p.url }
    elseif ($p.state -eq 'MERGED' -and -not $prMergeado) { $prMergeado = $p.url }
}
if (-not $prUrl -and $prMergeado) {
    Write-Host "O PR de $branch para $baseRef JA foi mergeado: $prMergeado" -ForegroundColor Yellow
    Write-Host "Nada a publicar nesta branch. Uma correcao nova precisa de outra versao (novo latest_version) e outra branch."
    exit 0
}

# ---- guarda: a branch sai de um commit cujo agente_local.py e a base do merge ----
# (le o commit UMA vez e usa o MESMO para checar e para criar a branch: se o time publicar no meio
#  da execucao, a branch nao nasce de um commit nao conferido - o que reverteria o codigo dele)
$baseSha = (Get-Content $baseF -Raw).Trim()
$rbase = Invoke-Gh api "repos/$repo/git/ref/heads/$baseRef" --jq '.object.sha'
if (-not $rbase.Ok -or -not $rbase.Out) { Fail "nao li o commit da $baseRef : $($rbase.Err)" }
$baseCommit = $rbase.Out
$rbrRef = Invoke-Gh api "repos/$repo/git/ref/heads/$branch" --jq '.object.sha'
if (-not $rbrRef.Ok -and $rbrRef.Err -notmatch '404|Not Found') { Fail "nao consegui consultar a branch $branch : $($rbrRef.Err)" }
$branchExiste = $rbrRef.Ok -and [bool]$rbrRef.Out
if ($branchExiste) {
    $mbq = Invoke-Gh api "repos/$repo/compare/${baseRef}...${branch}" --jq '.merge_base_commit.sha'
    if (-not $mbq.Ok -or -not $mbq.Out) { Fail "nao consegui comparar $branch com $baseRef : $($mbq.Err)" }
    $mb = Conteudo-Remoto 'agente_local.py' $mbq.Out
    if ($mb.Sha -ne $baseSha) { Fail "a branch $branch saiu de um commit ($($mbq.Out.Substring(0,7))) com OUTRO agente_local.py: o PR reverteria codigo do time. Apague a branch (se nao tiver PR) e refaca o merge contra a $baseRef atual." }
    Write-Host "      guarda OK: branch $branch saiu de $($mbq.Out.Substring(0,7)), cujo agente_local.py e a base do merge"
} else {
    $rb = Conteudo-Remoto 'agente_local.py' $baseCommit
    if (-not $rb.Existe) { Fail "agente_local.py nao existe na $baseRef ($($baseCommit.Substring(0,7))) ?!" }
    if ($rb.Sha -ne $baseSha) { Fail "o agente_local.py da $baseRef mudou desde o merge (era $($baseSha.Substring(0,7)), agora $($rb.Sha.Substring(0,7))). O time publicou codigo novo: refaca o merge antes de publicar, senao o trabalho deles seria sobrescrito." }
    Write-Host "      guarda OK: agente_local.py da $baseRef ($($baseCommit.Substring(0,7))) = base do merge ($($baseSha.Substring(0,7)))"
}

# ---- 1) branch + arquivos ----
$refLeitura = $baseCommit; if ($branchExiste) { $refLeitura = $branch }
Write-Host "[1/4] branch $branch $(if ($branchExiste) { 'ja existe' } else { "(nova, a partir da $baseRef $($baseCommit.Substring(0,7)))" }); $($arquivos.Count) arquivos:"
if (-not $branchExiste -and -not $Simular) {
    $refBody = Join-Path $env:TEMP 'gh_ref.json'
    [IO.File]::WriteAllText($refBody, (@{ ref = "refs/heads/$branch"; sha = $baseCommit } | ConvertTo-Json -Compress), $utf8)
    $rr = Invoke-Gh api -X POST "repos/$repo/git/refs" --input $refBody
    Remove-Item $refBody -Force -ErrorAction SilentlyContinue
    if (-not $rr.Ok) { Fail "nao consegui criar a branch $branch : $($rr.Err)" }
    Write-Host "      branch criada"
    $refLeitura = $branch
}
$rodape = "`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
$mudou = 0
foreach ($k in $arquivos.Keys) {
    $bytes = Bytes-Locais $k $arquivos[$k]
    $b64 = [Convert]::ToBase64String($bytes)
    $rem = Conteudo-Remoto $k $refLeitura
    if ($rem.Existe -and $rem.B64 -ceq $b64) { Write-Host "      = $k (igual)"; continue }   # -ceq: base64 diferencia maiuscula
    $mudou++
    $acao = 'novo'; if ($rem.Existe) { $acao = 'alterado' }
    if ($Simular) { Plano "  + $k ($acao, $($bytes.Length) bytes)"; continue }
    $body = @{ message = "[$Chave] Agente Local ${tag}: $k$rodape"; content = $b64; branch = $branch }
    if ($rem.Existe) { $body.sha = $rem.Sha }
    $bodyFile = Join-Path $env:TEMP ('gh_put_' + ($k -replace '[\\/]', '_') + '.json')
    [IO.File]::WriteAllText($bodyFile, ($body | ConvertTo-Json -Compress), $utf8)
    $rp = Invoke-Gh api -X PUT "repos/$repo/contents/$k" --input $bodyFile --jq '.commit.sha'
    Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
    if (-not $rp.Ok -or -not $rp.Out) { Fail "falha ao gravar $k na branch $branch : $($rp.Err)" }
    Write-Host "      + $k ($acao, commit $($rp.Out.Substring(0,[Math]::Min(7,$rp.Out.Length))))"
}
if ($mudou -eq 0 -and -not $branchExiste) { Fail "nenhum arquivo difere da ${baseRef}: nao ha o que publicar." }

# ---- 2) release (tag no commit da branch, que tem o codigo-fonte desta versao) ----
function Ler-Release {
    $r = Invoke-Gh release view $tag -R $repo --json 'tagName,isDraft,assets'   # entre aspas: sem elas o PowerShell vira array
    if (-not $r.Ok) {
        if ($r.Err -match 'not found') { return $null }
        Fail "nao consegui consultar a release $tag : $($r.Err)"
    }
    return ($r.Out | ConvertFrom-Json)
}
function Asset-Testado($rel) {
    # o asset so conta se terminou de subir E e o exe testado (digest sha256 do GitHub)
    $a = @($rel.assets | Where-Object { $_.name -eq 'AgenteLocal.exe' })
    if ($a.Count -eq 0) { return 'ausente' }
    # so 'state' diferente de 'uploaded' e INCOMPLETO. Um exe completo de outro tamanho ou outro digest e
    # 'diferente' e NUNCA e sobrescrito numa release publicada (a main pode ja estar servindo esse exe).
    $x = $a[0]
    if ($x.state -ne 'uploaded') { return 'incompleto' }
    if ([int64]$x.size -ne [int64]$tamExe) { return 'diferente' }
    if ($x.digest -and ([string]$x.digest).ToLower() -ne ('sha256:' + $hashEsp.ToLower())) { return 'diferente' }
    return 'ok'
}
function Commit-Da-Tag {
    # commit para onde a tag aponta de verdade ('' se a tag nao existe). Tag anotada e desreferenciada.
    # jq SEM aspas duplas: o PS 5.1 estraga aspas duplas dentro de argumento de programa nativo (o gh
    # recebia a expressao partida em dois). Saida: tipo na 1a linha, sha na 2a. /git/ref/ = nome exato.
    $r = Invoke-Gh api "repos/$repo/git/ref/tags/$tag" --jq '.object.type, .object.sha'
    if (-not $r.Ok) {
        if ($r.Err -match '404|Not Found') { return '' }
        Fail "nao consegui consultar a tag $tag : $($r.Err)"
    }
    $partes = @($r.Out -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($partes.Count -ne 2) { Fail "resposta inesperada ao consultar a tag $tag : $($r.Out)" }
    $tipo = $partes[0]; $sha = $partes[1]
    if ($tipo -eq 'tag') {
        $rt = Invoke-Gh api "repos/$repo/git/tags/$sha" --jq '.object.sha'
        if (-not $rt.Ok -or -not $rt.Out) { Fail "nao consegui desreferenciar a tag anotada $tag : $($rt.Err)" }
        $sha = $rt.Out
    }
    return $sha
}
function Conferir-Tag($alvo, [switch]$SoAvisar) {
    # O GitHub IGNORA --target quando a tag ja existe (ex.: release apagada; a tag e protegida contra
    # delecao). Se a tag existente tem OUTRO agente_local.py, a release apontaria para codigo que nao
    # gerou este exe. Mesmo codigo em outro commit: so avisa.
    $tc = Commit-Da-Tag
    if (-not $tc -or $tc -eq $alvo) { return $alvo }
    $naTag = Conteudo-Remoto 'agente_local.py' $tc
    $noAlvo = Conteudo-Remoto 'agente_local.py' $alvo
    if ($naTag.Sha -ne $noAlvo.Sha) {
        $msg = "a tag $tag ja existe no commit $($tc.Substring(0,7)) com OUTRO agente_local.py (o GitHub ignora --target quando a tag existe, e ela e protegida contra delecao)."
        if ($SoAvisar) { Write-Host ("      aviso: " + $msg + " O exe publicado e o testado; so a origem do codigo difere.") -ForegroundColor Yellow; return $tc }
        Fail ($msg + " A release apontaria para codigo que nao gerou este exe: publique como versao nova.")
    }
    Write-Host "      aviso: a tag $tag ja existe em $($tc.Substring(0,7)), nao em $($alvo.Substring(0,7)); o agente_local.py nela e o mesmo" -ForegroundColor Yellow
    return $tc
}
$rel = Ler-Release
$precisaConferir = $true
if (-not $rel) {
    if ($Simular) { Plano "[2/4] release $tag NAO existe: seria criada (Latest) com AgenteLocal.exe, tag no commit mais recente da branch $branch"; $precisaConferir = $false }
    else {
        $hd = Invoke-Gh api "repos/$repo/git/ref/heads/$branch" --jq '.object.sha'
        if (-not $hd.Ok -or -not $hd.Out) { Fail "nao li o commit da branch $branch : $($hd.Err)" }
        $tagEm = Conferir-Tag $hd.Out
        Write-Host "[2/4] criando release $tag (tag em $($tagEm.Substring(0,7))) e subindo AgenteLocal.exe ($tamExe bytes, aguarde)..."
        $rc = Invoke-Gh release create $tag $exe -R $repo --target $hd.Out --title $titulo --notes-file $notes --latest
        if (-not $rc.Ok) { Fail "gh release create falhou: $($rc.Err). Rode de novo: o roteiro retoma de onde parou (inclusive rascunho)." }
        Write-Host "      $($rc.Out)"
        $rel = Ler-Release
        if (-not $rel) { Fail "a release $tag nao aparece depois de criada ?!" }
    }
}
if ($rel) {
    $estado = Asset-Testado $rel
    if ($rel.isDraft) {
        # 'release create' com asset cria RASCUNHO, sobe o exe e so depois publica (e a publicacao cria a tag).
        # Execucao interrompida no meio => rascunho sem tag e sem download publico: reenvia o exe TESTADO e publica.
        if ($Simular) { Plano "[2/4] release $tag esta como RASCUNHO (execucao anterior interrompida): o exe testado seria reenviado e a release publicada como Latest"; $precisaConferir = $false }
        else {
            Write-Host "[2/4] release $tag esta como RASCUNHO: reenviando o exe testado e publicando..."
            if ($estado -ne 'ok') {
                $ru = Invoke-Gh release upload $tag $exe -R $repo --clobber
                if (-not $ru.Ok) { Fail "upload no rascunho falhou: $($ru.Err)" }
                $rel = Ler-Release; $estado = Asset-Testado $rel
                if ($estado -ne 'ok') { Fail "o asset do rascunho $tag continua '$estado' depois do upload." }
            }
            $hd = Invoke-Gh api "repos/$repo/git/ref/heads/$branch" --jq '.object.sha'
            if (-not $hd.Ok -or -not $hd.Out) { Fail "nao li o commit da branch $branch : $($hd.Err)" }
            $null = Conferir-Tag $hd.Out
            $re = Invoke-Gh release edit $tag -R $repo --target $hd.Out --draft=false --latest
            if (-not $re.Ok) { Fail "nao consegui publicar o rascunho $tag : $($re.Err)" }
            Write-Host "      rascunho publicado"
        }
    } elseif ($estado -eq 'ok') {
        Write-Host "[2/4] release $tag ja existe com o exe testado"
        $hd = Invoke-Gh api "repos/$repo/git/ref/heads/$branch" --jq '.object.sha'
        if ($hd.Ok -and $hd.Out) { $null = Conferir-Tag $hd.Out -SoAvisar }
    } elseif ($estado -eq 'diferente') {
        Fail "a release $tag ja tem um AgenteLocal.exe DIFERENTE do testado. Nao sobrescrevo: se a main ja aponta para $tag, agentes e instalador baixam dele. Publique como versao nova."
    } else {
        if ($Simular) { Plano "[2/4] release $tag existe com o exe '$estado': o exe testado seria enviado (--clobber)" }
        else {
            Write-Host "[2/4] release $tag existe com o exe '$estado'; enviando o exe testado..."
            $ru = Invoke-Gh release upload $tag $exe -R $repo --clobber
            if (-not $ru.Ok) { Fail "upload falhou: $($ru.Err)" }
        }
    }
}

# ---- 3) confere o download publico ----
if ($precisaConferir -and -not ($Simular -and $rel -and (Asset-Testado $rel) -ne 'ok')) {
    Write-Host "[3/4] conferindo o download publico..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $tmp = Join-Path $env:TEMP "AgenteLocal_${ver}_check.exe"
    try { Invoke-WebRequest -UseBasicParsing "https://github.com/$repo/releases/download/$tag/AgenteLocal.exe" -OutFile $tmp }
    catch { Fail "download publico de $tag falhou: $($_.Exception.Message). Rode de novo em instantes." }
    $h2 = (Get-FileHash $tmp -Algorithm SHA256).Hash
    Remove-Item $tmp -Force
    if ($h2 -ne $hashEsp) { Fail "o exe publicado ($h2) NAO bate com o testado ($hashEsp)." }
    Write-Host "      download OK, hash confere."
} else { Plano "[3/4] o download publico seria conferido (hash $($hashEsp.Substring(0,12))...)" }

# ---- 4) pull request ----
$tagCommit = ''; if (-not $Simular) { $tagCommit = Commit-Da-Tag }   # para a descricao do PR citar o commit REAL da tag
$corpoFile = Join-Path $env:TEMP 'gh_pr_body.md'
if ($prUrl) {
    Write-Host "[4/4] PR ja aberto: $prUrl"
    $pb = Invoke-Gh pr view $prUrl -R $repo --json body --jq '.body'
    if (-not $pb.Ok) { Fail "nao consegui ler o PR $prUrl : $($pb.Err)" }
    if ($pb.Out -notmatch [regex]::Escape($hashEsp)) {
        if ($Simular) { Plano "      a descricao do PR cita outro sha256: seria atualizada" }
        else {
            [IO.File]::WriteAllText($corpoFile, (Corpo-Pr), $utf8)
            $pe = Invoke-Gh pr edit $prUrl -R $repo --body-file $corpoFile
            Remove-Item $corpoFile -Force -ErrorAction SilentlyContinue
            if (-not $pe.Ok) { Fail "nao consegui atualizar a descricao do PR: $($pe.Err)" }
            Write-Host "      descricao do PR atualizada com o sha256 atual"
        }
    }
} elseif ($Simular) { Plano "[4/4] seria aberto o PR '$tituloPr' ($branch -> $baseRef)" }
else {
    Write-Host "[4/4] abrindo pull request..."
    [IO.File]::WriteAllText($corpoFile, (Corpo-Pr), $utf8)
    $rpr = Invoke-Gh pr create -R $repo --base $baseRef --head $branch --title $tituloPr --body-file $corpoFile
    Remove-Item $corpoFile -Force -ErrorAction SilentlyContinue
    if (-not $rpr.Ok) { Fail "gh pr create falhou: $($rpr.Err)" }
    $prUrl = $rpr.Out
    Write-Host "      PR aberto: $prUrl"
}

Write-Host ""
if ($Simular) {
    Write-Host "SIMULACAO concluida: nada foi alterado. Rode de novo SEM -Simular para publicar." -ForegroundColor Cyan
} else {
    Write-Host "FEITO: release $tag no ar e PR aberto ($prUrl). FALTA (time):"
    Write-Host "  1) o check 'validate' passar e um tech-lead aprovar e fazer o merge;"
    if (-not $Hotfix) { Write-Host "  2) promover develop -> staging -> main (como nos PRs #7/#8/#10/#11);" }
    Write-Host "  3) com o version.json na main, lojas em 5.78+ atualizam sozinhas em ate 5 min;"
    Write-Host "     lojas em 5.77 ou anterior precisam do ATUALIZAR_E_ABRIR_AGENTE.bat uma vez."
}
