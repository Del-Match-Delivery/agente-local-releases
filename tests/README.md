# Testes do Agente Local

Rodar sempre com o Python do build (`venv_build`), a partir da pasta do projeto:

```
venv_build\Scripts\python.exe tests\test_eleicao.py     # eleicao de instancia unica + alvo do update (funcoes puras)
venv_build\Scripts\python.exe tests\test_agendado.py    # destaque AGENDADO, largura do papel, EAN-13, bytes ESC/POS
venv_build\Scripts\python.exe tests\test_hora.py        # Data/Hora em horario de Brasilia
venv_build\Scripts\python.exe tests\test_codepage.py    # acentos por impressora, modo ascii, pagina de teste, proc_job com 2 impressoras
venv_build\Scripts\python.exe tests\test_ui_config.py   # tela Configuracoes REAL (abre por instantes): Acentos, Aplicar, Testar acentos, Conectar
powershell -NoProfile -ExecutionPolicy Bypass -File tests\runtime_test.ps1 -Dist dist_5.80
```

`runtime_test.ps1` executa o exe de verdade em ambiente isolado (LOCALAPPDATA de teste em
`tests\_la_teste`), simula o clique do lojista com o agente ja rodando e restaura a chave Run e o
atalho de Startup originais no fim. Nao toca na instancia de desenvolvimento da maquina. Abre
janelas do agente de teste por ~90 s: avise quem estiver usando a maquina.

Com o Smart App Control do Windows 11 ligado, um exe recem-compilado (sem assinatura) pode ser
bloqueado por alguns minutos ("Uma politica de Controle de Aplicativo bloqueou este arquivo").
Veja o motivo em `Microsoft-Windows-CodeIntegrity/Operational` (eventos 3033/3077).

Receita do build de release (ver build.py: `--runtime-tmpdir` e `--windowed` sao obrigatorios):

```
venv_build\Scripts\python.exe -c "import build,subprocess,sys; sys.exit(subprocess.run(build.BASE+['--onefile','--windowed','--distpath','dist_5.80','--workpath','build_5.80','--specpath','build_5.80',build.MAIN_SCRIPT]).returncode)"
```

Publicacao: `publicar_release.ps1 -Chave AGLR-N` (le `latest_version` do version.json, exige
`release_<versao>\AgenteLocal.exe`, `notes.md`, `AgenteLocal.exe.sha256` do build testado e
`base.sha` = blob do agente_local.py da develop sobre o qual o merge foi feito). Cria a release e
abre PR para a develop; os tech-leads promovem develop -> staging -> main.
