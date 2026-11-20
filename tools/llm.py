#!/usr/bin/env python
"""Uma pergunta, qualquer provedor, de qualquer projeto.

  python ~/.claude/tools/llm.py codex "pergunta"
  python ~/.claude/tools/llm.py openrouter -i foto.png "o que tem aqui?"
  echo "pergunta longa" | python ~/.claude/tools/llm.py agy
  python ~/.claude/tools/llm.py agy -f "resuma https://exemplo.com/pagina"

-f / --ferramentas liga web e arquivo. Sem ela o provedor responde so do treino
-- e, pior, sem avisar: modelo sem web nao devolve "nao sei", ele preenche.
So os de CLI tem ferramenta (FERRAMENTAS_OK, no fim); os de API crua aceitam o
parametro para a assinatura ficar uniforme e o ignoram, com aviso no stdout.

Provedores: agy (+2,+3), codex (+2), claude (+2), opencode (+2), commandcode,
openrouter (+2,+3), alibaba, deepseek. Os slots numerados dividem gateway e
sessao com o slot base -- muda so o modelo escolhido (ver CLONES, no fim).
Todos usam sessao ja logada; nenhum le mais LLM_APIS (ver nota abaixo).

openrouter, alibaba e deepseek NAO sao mais API crua (mudou em 19/09/2026):
os tres chamam o agente do opencode com o modelo prefixado pelo namespace
certo ("openrouter/", "alibaba-token-plan/", "deepseek/"), porque o gateway
dele ja tem as tres contas como credencial propria (`opencode providers
list`). Mesma conta, mesmo catalogo de cada um, mas com ferramenta de verdade
em vez de so completar texto. O opencode virou o motor de todo provedor sem
CLI dedicado; so ficam de fora os que ja tem CLI propria (codex, claude,
commandcode) e o proprio opencode.

O provedor "gemini" (CLI por API key) foi removido em 10/09/2026: a mesma
familia de modelos vem pelo agy, na assinatura, sem a cota que estourava.

Ver ~/.claude/docs/PROVEDORES_LLM.md para o porque de cada detalhe.
"""
import argparse, io, json, os, re, shutil, subprocess, sys

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Onde moram as chaves de API: a variavel de ambiente LLM_APIS, ou
# ~/.llm-apis.txt como padrao. O caminho NAO e fixo no codigo de proposito --
# convencao de armazenamento de quem escreveu o projeto nao deve virar padrao
# de quem clona.
#
# O arquivo guarda uma chave por linha, no formato ROTULO=valor; nada aqui
# imprime o conteudo dele, e ele nunca deve entrar num repositorio.
APIS = os.environ.get("LLM_APIS") or os.path.expanduser("~/.llm-apis.txt")


def diz(msg):
    """O console do Windows e cp1252; acento e travessao quebram o print."""
    sys.stdout.buffer.write(msg.encode("utf-8", "replace") + b"\n")
    sys.stdout.flush()


def chave(padrao, prefixo=None):
    t = io.open(APIS, encoding="utf-8", errors="replace").read()
    if prefixo:
        for linha in t.splitlines():
            if linha.strip().upper().startswith(prefixo):
                return linha.split()[-1].strip()
    m = re.search(padrao, t)
    if not m:
        raise SystemExit(f"chave nao encontrada em {APIS}")
    return m.group(0)


# Frases que um CLI imprime no STDOUT, com codigo de saida ZERO, quando na
# verdade nao respondeu nada. Sem esta lista elas viram "a resposta do modelo".
#
# O caso que motivou (15/09/2026): com a sessao do `claude` expirada, o esquadrao
# registrava como resposta dele a linha "Failed to authenticate: OAuth session
# expired and could not be refreshed" -- e seguia em frente. Isso e pior que uma
# falha: o texto entra no dossie da rodada seguinte, os outros modelos criticam
# uma "opiniao" que nunca existiu, e o juiz consolida em cima disso. Uma falha
# declarada custa um provedor; uma falha disfarcada contamina a rodada inteira.
NAO_E_RESPOSTA = (
    "failed to authenticate",
    "oauth session expired",
    "please run `claude login`",
    "please login",
    "not logged in",
    "insufficient balance",
    "insufficient credits",
    "quota exceeded",
    "rate limit exceeded",
)


def limpa_ansi(t):
    """Tira cor e banner de terminal do texto de um CLI.

    Duas formas, porque o ESC as vezes se perde no caminho e sobra so o resto
    da sequencia: `\\x1b[91m` (completa) e `[0m` (orfa, que e o que aparecia no
    relatorio do esquadrao como se fosse a resposta do provedor).
    """
    t = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", t)
    t = re.sub(r"(?m)^\s*\[[0-9;]*m\s*", "", t)
    return re.sub(r"\[[0-9;]*m", "", t)


# 2026-09-24: 900/1200s cortava tarefa de agente (-f) no meio da escrita e
# deixava arquivo pela metade (senac.py perdeu o parse). Depois o teto fixo de 1h
# matou o Qwen trabalhando (rodada 2 dos prototipos). Com -f o corte e por
# OCIOSIDADE: 20 min sem arquivo criado/alterado no diretorio de trabalho.
TETO_AGENTE = None
OCIOSO_AGENTE = 20 * 60
TETO_DURO_AGENTE = 6 * 3600


def _ultima_escrita(raiz, limite=20000):
    mais, vistos = 0.0, 0
    for d, subs, arqs in os.walk(raiz):
        subs[:] = [s for s in subs if s not in (".git", "node_modules", "__pycache__", ".pytest_cache")]
        for a in arqs:
            vistos += 1
            try:
                mais = max(mais, os.path.getmtime(os.path.join(d, a)))
            except OSError:
                pass
            if vistos >= limite:
                return mais
    return mais


def _roda_agente(cmd, entrada):
    import threading, time
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if entrada is not None else None,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
    VIVOS.add(p)
    saida = {}
    t = threading.Thread(target=lambda: saida.update(zip(("out", "err"), p.communicate(entrada))), daemon=True)
    t.start()
    inicio = ultimo = time.time()
    raiz = os.getcwd()
    while t.is_alive():
        t.join(30)
        agora = time.time()
        ultimo = max(ultimo, _ultima_escrita(raiz))
        if t.is_alive() and (agora - ultimo > OCIOSO_AGENTE or agora - inicio > TETO_DURO_AGENTE):
            # o .CMD do npm e so o cmd.exe: sem /T o node filho continua vivo
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=SEM_JANELA)
            t.join(10)
            motivo = "sem escrever arquivo" if agora - ultimo > OCIOSO_AGENTE else "teto de 6 h"
            VIVOS.discard(p)
            raise SystemExit(f"agente cortado: {int((agora - ultimo) // 60)} min {motivo} (total {int((agora - inicio) // 60)} min)")
    VIVOS.discard(p)
    return subprocess.CompletedProcess(cmd, p.returncode, saida.get("out", ""), saida.get("err", ""))


# 2026-09-29, pedido do usuario: sem timeout no esquadrao. O teto fixo de
# 900/1200s cortava resposta longa de modelo com effort alto e aparecia como
# [FALHOU] -- o esquadrao nunca ligava TETO_AGENTE, entao caia sempre aqui.
# Agora so resta o teto de seguranca de 6 h; quem para um lote e o botao Parar
# do painel, que mata as arvores vivas por mata_vivos().
VIVOS = set()


def mata_vivos():
    for p in list(VIVOS):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                       capture_output=True, creationflags=SEM_JANELA)


def _roda_simples(cmd, entrada):
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if entrada is not None else None,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
    VIVOS.add(p)
    try:
        out, err = p.communicate(entrada, timeout=TETO_DURO_AGENTE)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                       capture_output=True, creationflags=SEM_JANELA)
        raise SystemExit("cortado pelo teto de seguranca de 6 h")
    finally:
        VIVOS.discard(p)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def roda(cmd, entrada=None):
    r = _roda_agente(cmd, entrada) if TETO_AGENTE else _roda_simples(cmd, entrada)
    if r.returncode and not r.stdout.strip():
        # Junta stderr e stdout: o opencode manda o banner por um e a causa pelo
        # outro, entao olhar so um deles devolve "[0m" como diagnostico. As
        # linhas de banner (comecam com ">") saem; o resto vira uma linha so.
        bruto = limpa_ansi((r.stderr or "") + "\n" + (r.stdout or ""))
        uteis = [l.strip() for l in bruto.splitlines()
                 if l.strip() and not l.strip().startswith(">")]
        raise SystemExit(" | ".join(uteis)[-400:] or "falhou sem saida")
    saida = r.stdout
    # Erro operacional impresso no stdout com rc=0 nao pode passar por resposta.
    # So olha o comeco: uma resposta longa que MENCIONE "rate limit" no meio do
    # texto e assunto, nao sintoma -- o sintoma aparece sozinho e no topo.
    limpo = limpa_ansi(saida).strip()
    cabeca = limpo[:300].lower()
    for frase in NAO_E_RESPOSTA:
        if frase in cabeca:
            # A mensagem util costuma vir depois do banner; manda as linhas que
            # dizem alguma coisa, nao as primeiras que aparecerem.
            uteis = [l.strip() for l in limpo.splitlines()
                     if l.strip() and not l.strip().startswith(">")]
            raise SystemExit(" | ".join(uteis)[:300])
    return saida


def le_stdin():
    """Le o stdin como UTF-8, em vez de confiar no padrao do Windows.

    Sem isto, `esquadrao < pergunta.md` corrompe TODA pergunta acentuada, em
    silencio. Medido em 15/09/2026: `sys.stdin` aqui vem com encoding `cp1252`
    e errors `surrogateescape`. Um arquivo UTF-8 de 6.606 caracteres chegava
    como 6.825 (cada acento virando dois caracteres de lixo), e todo byte que o
    cp1252 nao conhece -- 0x81, por exemplo -- virava um SURROGATE SOLTO
    (\\udc81) preso na string.

    Os dois estragos que isso causava:

      * os provedores de CLI recebiam mojibake e ninguem percebia, porque texto
        errado ainda parece texto;
      * os de API morriam com "lone leading surrogate in hex escape", porque
        `json.dumps` escapa o surrogate solto e o servidor recusa o corpo. O
        erro aparecia como falha do provedor, nao como defeito daqui.

    Le os bytes crus e decodifica UTF-8; se nao for UTF-8 valido, cai para
    cp1252, que e o que o Windows realmente usaria.
    """
    b = sys.stdin.buffer.read()
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


# Teto de caracteres que ainda vale mandar no argv de um CLI.
#
# Nao e estimativa: os wrappers `.CMD` do npm (claude, opencode, codex,
# commandcode) passam por `cmd.exe`, cuja linha de comando morre em ~8.191
# caracteres; um `.EXE` direto (agy) vai ate 32.767. Os dois estouram com
# WinError 206, e o estouro NAO e excepcional no modo refino: medido em
# 15/09/2026, o dossie da rodada 2 com apenas 4 respostas ja tinha 51.945
# caracteres. Com os 10 provedores do painel passaria de 110 mil.
#
# Por isso nenhum provedor deste arquivo pode depender de argv para a pergunta:
# quem tem stdin usa stdin, e quem nao tem (agy) usa o protocolo de streaming.
# O teto abaixo fica bem longe dos dois limites porque o resto do comando --
# flags, caminho do binario, aspas que o cmd.exe duplica -- tambem conta.
LIMITE_ARGV = 6000

# Teto real do cmd.exe. O `.EXE` aguenta 32.767, mas nao vale a pena manter dois
# numeros: acima deste tamanho todo mundo ja vai por stdin de qualquer jeito.
LIMITE_CMD_EXE = 8191

def cabe_no_argv(cmd, pergunta):
    """A pergunta cabe na linha de comando DEPOIS de escapada?

    Contar caracteres crus da a resposta errada, e erra justamente no caso que
    interessa. O Windows escapa aspas e barras invertidas, entao o tamanho que
    chega ao processo pode ser o DOBRO do texto original -- medido em
    15/09/2026: 5.999 aspas viram 11.998 caracteres de linha de comando e
    estouram o limite, apesar de o texto estar abaixo de qualquer teto que se
    conte na mao. Colar codigo no prompt e exatamente isso.

    `list2cmdline` e a mesma funcao que o `subprocess` usa para montar a linha,
    entao aqui nao ha estimativa: e o comando que seria enviado.
    """
    if len(pergunta) > LIMITE_ARGV:
        return False
    # 2026-09-24: aspas + < > | & ^ % no argv passam pelo cmd.exe dos wrappers .CMD e viram
    # redirecionamento ("O sistema nao pode encontrar o caminho"). Com esses caracteres, stdin.
    if any(c in pergunta for c in '<>|&^%'):
        return False
    return len(subprocess.list2cmdline(list(cmd) + [pergunta])) <= LIMITE_CMD_EXE


def bin_de(nome):
    """O PATH nem sempre traz o que o winget instalou: o shim de Links so entra
    em sessao aberta depois da instalacao, e o do agy nao apareceu nem assim.
    Antes de desistir, olhar onde o winget guarda o binario de verdade."""
    achado = shutil.which(nome)
    if achado:
        return achado
    for raiz in (os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
                 os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")):
        for pasta, _, arquivos in os.walk(raiz) if os.path.isdir(raiz) else []:
            for ext in (".exe", ".cmd", ".bat", ""):
                if nome + ext in arquivos:
                    return os.path.join(pasta, nome + ext)
    return nome


# --- CLIs (sessao ja logada, sem chave no codigo) ---------------------------

# O gpt-6-astra era barrado aqui por custo (o padrao do ~/.codex/config.toml
# caia nele por descuido). Liberado em 16/09/2026 a pedido explicito do
# usuario, inclusive na janela do Codex: sem trava, e o custo e escolha dele.


def via_codex(pergunta, imagens, modelo, effort, ferramentas=False):
    # -i e variadico: sem o "--" o codex engole o prompt como mais um arquivo
    # e vai ler do stdin vazio.
    cmd = [bin_de("codex"), "exec", "-m", modelo,
           "-c", f"model_reasoning_effort={effort}", "--skip-git-repo-check"]
    # Ate 19/09/2026 este wrapper ACEITAVA ferramentas e ignorava: o codex exec
    # roda sem rede e sem web, as buscas voltam vazias e ele responde do treino
    # com cara de quem consultou. workspace-write + network_access e a rota sem
    # desligar o sandbox.
    #
    # Ressalva medida em 19/09/2026 nesta maquina: mesmo assim o `web_search` do
    # codex voltou resultado VAZIO (as features web dele estao deprecated) e o
    # exec sandboxado falhou no Windows (CreateProcess/orchestrator_helper). Ou
    # seja: o codex e o CLI mais fraco para web aqui. Prefira agy/claude/
    # commandcode quando a tarefa depende de internet.
    if ferramentas:
        # 20/09/2026: o codex passou a REJEITAR -s junto com --approve-for-me
        # ("the argument '--sandbox' cannot be used with '--approve-for-me'").
        # E redundante mesmo: o --approve-for-me ja roda sob workspace-write.
        # Passar os dois derrubava o provedor com [FALHOU] em 0s — inclusive
        # quando ele era o JUIZ do esquadrao, que entao nao consolidava nada.
        cmd += ["-c", "tools.web_search=true",
                "-c", "sandbox_workspace_write.network_access=true",
                "--approve-for-me"]
    for x in imagens:
        cmd += ["-i", os.path.abspath(x)]
    # No argv o prompt chega CORTADO na primeira quebra de linha, e tambem se
    # for muito longo — em ambos os casos o codex responde "qual e a tarefa?"
    # com cara de resposta normal, sem erro nenhum. Multilinha vai por stdin.
    if "\n" in pergunta or not cabe_no_argv(cmd + ["--"], pergunta):
        return roda(cmd + ["-"], entrada=pergunta)
    return roda(cmd + ["--", pergunta])


def via_agy(pergunta, imagens, modelo, effort, ferramentas=False):
    """Antigravity CLI. Autentica pela conta Google, como o codex pela ChatGPT,
    entao nao tem a cota que fazia a GEMINI_API_KEY morrer em meia duzia de
    mensagens. Substituiu o provedor "gemini", removido em 10/09/2026.

    O effort vai na flag --effort (low|medium|high), nao no nome do modelo:
    verificado em 12/09/2026 que nome-base + --effort responde, e que um valor
    invalido e recusado ("valid: low, medium, high"). So a familia gemini- a
    aceita; claude-* e gpt-oss-* ja trazem o modo no proprio nome."""
    if modelo.startswith("gemini-"):
        # Config antiga podia ter gravado o sufixo no nome (gemini-3.8-flash-medium).
        # Normaliza para a base — o effort agora anda por fora.
        modelo = re.sub(r"-(low|medium|high|xhigh)$", "", modelo)
    # max nao existe no agy; xhigh so em parte dos gemini (Hydrogen e Neon em
    # 20/11/2026). Em vez de lista escrita a mao, tenta xhigh e, na recusa
    # explicita ('has no "xhigh" effort'), repete em high.
    if effort == "max":
        effort = "xhigh"
    # Orchestrator (planeja num modelo, executa em outro) recusa --effort.
    if not modelo.startswith("gemini-") or "orchestrator" in modelo:
        effort = None
    for x in imagens:
        pergunta = f"{os.path.abspath(x)}\n{pergunta}"

    def chama(ef):
        cmd = [bin_de("agy"), "--model", modelo] + (["--effort", ef] if ef else [])
        # Sem isto o agy trava esperando aprovacao de cada ferramenta e devolve
        # resposta VAZIA no modo -p, sem dizer que travou. So ligar quando a tarefa
        # realmente precisa de web/arquivo: auto-aprova tudo, inclusive shell.
        if ferramentas:
            cmd.append("--dangerously-skip-permissions")
        for x in imagens:
            cmd += ["--add-dir", os.path.dirname(os.path.abspath(x))]
        if not cabe_no_argv(cmd + ["-p"], pergunta):
            return _agy_streaming(cmd, pergunta)
        return roda(cmd + ["-p", pergunta])

    if effort == "xhigh":
        try:
            return chama("xhigh")
        except SystemExit as e:
            # Recusa real (agy 1.5.x): 'has no "xhigh" effort'. Aspas opcionais.
            if not re.search(r'no "?xhigh"? effort', str(e)):
                raise
            return chama("high")
    return chama(effort)


def _agy_streaming(cmd, pergunta):
    """Manda a pergunta pelo protocolo NDJSON do agy, sem passar pelo argv.

    O agy e o unico CLI daqui que NAO le a pergunta do stdin: `--print` sem
    argumento imprime o help. Com prompt grande isso e fatal -- ele e um `.EXE`,
    entao aguenta 32.767 caracteres, e o dossie do refino passa disso.

    A saida virou `--input-format stream-json`, descoberto por tentativa em
    15/09/2026. Duas armadilhas, as duas custaram teste:

      1. `--print` engole o proximo token como se fosse o prompt. Se
         `--input-format` vier depois dele, o agy reclama que pegou a flag como
         pergunta. Por isso `--print` nao entra: `--input-format stream-json`
         sozinho ja implica modo nao-interativo.
      2. A mensagem de entrada precisa do campo `event`. Sem ele o erro e
         "stream input message is missing the event field" -- o formato de
         mensagem que as APIs usam (`{"type":"user",...}`) NAO serve.
    """
    msg = {"event": "user",
           "message": {"role": "user",
                       "content": [{"type": "text", "text": pergunta}]}}
    saida = roda(cmd + ["--input-format", "stream-json",
                        "--output-format", "stream-json"],
                 entrada=json.dumps(msg) + "\n")
    resposta, erro = "", ""
    for linha in saida.splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            d = json.loads(linha)
        except json.JSONDecodeError:
            continue
        r = d.get("result") or {}
        resposta = resposta or r.get("response") or ""
        erro = erro or r.get("error") or ""
    if not resposta and erro:
        raise SystemExit(erro[:400])
    return resposta


def via_claude(pergunta, imagens, modelo, effort, ferramentas=False):
    """Claude Code CLI pela sessao OAuth (claude login) — irmao do codex: a
    assinatura autentica, sem chave no codigo.

    Cuidado com o effort: valor invalido NAO falha, sai um "Warning: Unknown
    --effort value" no stderr e a chamada segue no padrao. Como roda() so le o
    stdout, isso passaria calado; por isso o teto vem da lista abaixo, nao da
    reclamacao do CLI."""
    # Sem ferramenta o agente nao sai lendo arquivo por conta propria. Com
    # imagem ele precisa do Read para abrir o caminho, entao ali libera so o
    # Read; ferramentas=True libera tudo.
    ferramenta = "default" if ferramentas else ("Read" if imagens else "")
    cmd = [bin_de("claude"), "-p", "--output-format", "text", "--model", modelo,
           "--tools", ferramenta, "--no-session-persistence"]
    # `--tools default` libera a lista de tools, mas NAO a permissao: no modo -p
    # o prompt de permissao do WebFetch e negado automaticamente, e o agente
    # entao conclui que nao tem internet -- medido em 19/09/2026, respondia
    # SEM_WEB com as tools ligadas. Nomear as duas em --allowedTools resolve sem
    # precisar de --dangerously-skip-permissions: e o menor privilegio que faz a
    # web funcionar, e Bash/Edit seguem pedindo permissao (ou seja, negados).
    #
    # Uma string com virgula, nao dois argumentos: a flag e variadica e engoliria
    # o proximo item do cmd como se fosse nome de tool.
    if ferramentas:
        cmd += ["--allowedTools", "WebFetch,WebSearch"]
    if effort in ("low", "medium", "high", "xhigh", "max"):
        cmd += ["--effort", effort]
    for x in imagens:
        cmd += ["--add-dir", os.path.dirname(os.path.abspath(x))]
        pergunta = f"{os.path.abspath(x)}\n{pergunta}"
    # `claude.CMD` passa por cmd.exe: acima de ~8.191 caracteres o argv morre
    # com WinError 206. `-p` sem argumento le a pergunta do stdin. Multilinha
    # tambem vai por stdin: o launcher .cmd corta na primeira quebra, do mesmo
    # jeito que o codex. Timeout de 1200s porque com effort alto e dossie
    # grande ele passa minutos antes do primeiro byte (igual ao commandcode).
    if "\n" in pergunta or not cabe_no_argv(cmd + ["--"], pergunta):
        return roda(cmd, entrada=pergunta)
    return roda(cmd + ["--", pergunta])


def via_opencode(pergunta, imagens, modelo, effort, ferramentas=False):
    # Imagem vai como ANEXO de verdade, na flag `--file` da CLI (ver `anexos`
    # mais abaixo) -- nao como caminho escrito no meio do texto.
    #
    # Ate 19/09/2026 este wrapper mandava o caminho no prompt ("Leia estas
    # imagens: C:\..."), e o comentario aqui dizia que a flag "nao entrega a
    # imagem ao modelo". Entrega: o que quebrava era a ORDEM dos argumentos.
    # `--file` e variadico e, vindo antes do texto, engolia a pergunta como se
    # fosse mais um arquivo ("Error: File not found: descreva a imagem..."),
    # com ou sem `--` no meio (a CLI usa yargs, onde o separador nao ajuda).
    # Com a mensagem primeiro e `--file` no fim, o modelo descreve a imagem.
    #
    # O jeito antigo tinha um custo escondido: o agente precisava ABRIR o
    # arquivo com ferramenta, e todo caminho fora do diretorio de trabalho cai
    # em "permission requested: external_directory; auto-rejecting". Sem `-f`
    # (que liga o --auto) a leitura era negada e a resposta voltava VAZIA, com
    # rc=0 -- falha silenciosa. Como anexo isso nao acontece: nao passa por
    # permissao nenhuma.
    if not imagens and not ferramentas:
        pergunta = "NAO use ferramenta. " + pergunta
    cmd = [bin_de("opencode"), "run", "-m", modelo]
    # Mesma armadilha do agy: as tools existem, mas sem auto-aprovacao a CLI
    # fica esperando permissao de cada uma e devolve resposta vazia no modo nao
    # interativo. Tirar o "NAO use ferramenta" do prompt (acima) nao basta.
    #
    # `--auto` e grosso -- a propria CLI chama de "dangerous", porque aprova
    # tudo que nao esteja negado, shell incluso. O `run --help` do opencode nao
    # oferece allowlist por tool como o claude, entao e isto ou nada.
    #
    # Fronteira: isto mexe no NOSSO wrapper, que chama a CLI dele. A config do
    # opencode (~/.config/opencode) nao e nossa e nao se toca.
    if ferramentas:
        cmd.append("--auto")
    # --variant e o esforco do provider. Cuidado: a CLI aceita QUALQUER valor
    # calada (testei "banana", rc=0), entao nao da para provar que ela honra o
    # que foi pedido — por isso o teto fica no high, que existe no catalogo.
    if effort:
        cmd += ["--variant", "high" if effort in ("xhigh", "max") else effort]
    # Os anexos entram DEPOIS da mensagem no caminho do argv, porque `--file` e
    # variadico e comeria o texto da pergunta se viesse antes (ver o comentario
    # no topo). No caminho do stdin nao existe positional para disputar, entao
    # a posicao deles tanto faz.
    anexos = []
    for x in imagens:
        anexos += ["--file", os.path.abspath(x)]
    uma_linha = " ".join(pergunta.split())
    # `opencode.CMD` passa por cmd.exe e morre em ~8.191 caracteres (WinError
    # 206). Pelo stdin ele aceita a pergunta inteira -- e com quebra de linha,
    # entao achatar em uma linha so faz sentido no caminho do argv.
    if not cabe_no_argv(cmd + anexos, uma_linha):
        saida = roda(cmd + anexos, entrada=pergunta)
    else:
        saida = roda(cmd + [uma_linha] + anexos)
    # A CLI do opencode roda com o SessionStart do projeto dela, que imprime um
    # banner antes da resposta. E config do outro projeto (~/.config/opencode),
    # que nao e nossa para mexer — entao a limpeza mora aqui: corta as linhas de
    # banner do topo e nada mais, para nao comer conteudo de resposta.
    #
    # O reset ANSI (`[0m`) entra nessa conta. Ele sobra do banner e, quando a
    # resposta vinha vazia, era tudo o que restava: o esquadrao registrava
    # "[FALHOU] [0m", que nao diz nada a quem le o relatorio depois.
    linhas = saida.splitlines()
    lixo = ("\x1b[0m", "[0m")
    while linhas and (not linhas[0].strip()
                      or linhas[0].lstrip().startswith("[")
                      or linhas[0].strip() in lixo):
        linhas.pop(0)
    while linhas and (not linhas[-1].strip() or linhas[-1].strip() in lixo):
        linhas.pop()
    texto = "\n".join(linhas)
    # Sobra de bloco de raciocinio. Modelo que pensa em <think>...</think> as
    # vezes tem o CONTEUDO filtrado pela CLI e a tag de fechamento escapa, e ai
    # a resposta comeca com um "</think>" solto -- visto em 19/09/2026 com
    # alibaba-token-plan/qwen3.8-flash numa chamada com imagem. Passa
    # despercebido porque a resposta ao redor esta certa, mas vai inteiro para
    # o dossie da rodada seguinte do esquadrao. Corta o bloco todo quando as
    # duas tags vierem, e a tag solta quando so ela sobrar.
    texto = re.sub(r"(?is)^\s*<think>.*?</think>\s*", "", texto)
    texto = re.sub(r"(?i)^\s*</think>\s*", "", texto)
    return texto


def via_commandcode(pergunta, imagens, modelo, effort, ferramentas=False):
    # A pergunta vai por stdin: o launcher .cmd do npm passa pelo cmd.exe, que
    # estoura (WinError 206) num argumento longo.
    if imagens:
        pergunta = ("Leia estas imagens: "
                    + " ".join(os.path.abspath(x) for x in imagens)
                    + "\n\n" + pergunta)
    base = [bin_de("commandcode"), "-p", "-m", modelo]
    # sem permission-mode o print mode nega ferramenta em silencio ("nao tenho
    # acesso a ferramenta"); medido 2026-09-23, travou o Spark varias vezes
    # 2026-09-24: accept-edits libera arquivo mas NEGA shell -- o Spark nao roda
    # pytest e devolve o codigo em texto ("me reexecute com --yolo"). Com -f vai
    # yolo, o mesmo nivel do --approve-for-me do codex.
    # 2026-09-24 (2a medicao): no -p so o flag --yolo libera; "--permission-mode yolo"
    # e aceito e IGNORADO (arquivo nao criado). Testado criando arquivo de verdade.
    resto = ["--tools-all", "-t", *(["--yolo"] if ferramentas else ["--permission-mode", "accept-edits"]),
             # 2026-09-24: 200 turnos parou o Spark no meio do plano do frontend. Agente nao se
             # limita por contagem; travado quem pega e o corte por ociosidade de roda().
             "--no-session", "--max-turns", "10000" if ferramentas else "200",
             "--skip-onboarding", "--no-auto-update"]
    for x in imagens:
        resto += ["--add-dir", os.path.dirname(os.path.abspath(x))]

    def chama(com_effort):
        cmd = base + (["--effort", effort] if (effort and com_effort) else []) + resto
        return roda(cmd, entrada=pergunta)

    if not effort:
        return chama(False)
    try:
        return chama(True)
    except SystemExit as e:
        # Nem todo modelo do catalogo aceita --effort, e a recusa nao e um erro
        # de uso: o LongCat e o MiMo V2.5 Pro simplesmente nao tem raciocinio
        # ajustavel ("has no adjustable reasoning effort"). Ate 15/09/2026 isso
        # derrubava o provedor inteiro no esquadrao, como se ele estivesse fora
        # do ar -- o painel oferece o effort para todo mundo, entao bastava
        # escolher um desses modelos para perder a vaga no lote.
        #
        # Repetir SEM a flag e o certo: o pedido de esforco vira o padrao do
        # modelo, que e o que ele tem. So cai aqui na recusa especifica; outro
        # erro continua subindo.
        if "adjustable reasoning effort" not in str(e).lower():
            raise
        return chama(False)


# via_openrouter, via_deepseek e via_alibaba (API crua, chamada HTTP direta com
# openai_compat()) sairam em 19/09/2026: zero ferramenta, sempre -- so texto
# completado do treino. `opencode providers list` mostra que o gateway do
# opencode ja tem as tres contas como credencial propria ("OpenRouter api",
# "DeepSeek api", "Alibaba Token Plan api", em ~/.local/share/opencode/
# auth.json) e `opencode models` lista os tres namespaces (openrouter/,
# deepseek/, alibaba-token-plan/), cada um testado na mao com chamada real.
# Mesma conta, mesmo catalogo, mas agora e a CLI do opencode que chama -- com
# ferramenta disponivel quando `ferramentas=True` pede (--auto), e sem quando
# nao pede, exatamente como o slot "opencode" ja funcionava. via_opencode
# serve isto sozinho: so precisa do modelo vir com o namespace certo (ver
# PROVEDORES abaixo). openai_compat() nao tem mais chamador nenhum e saiu
# junto -- nao existe API crua sobrando no esquadrao depois desta troca.


PROVEDORES = {
    # terra > luna na pratica: o luna dava respostas rasas no esquadrao.
    "codex":       (via_codex,           "gpt-5.6-terra"),
    "agy":         (via_agy,             "gemini-3.8-flash-medium"),
    # O padrao do opencode e um FREE de proposito: o ZEN e pay-as-you-go e a
    # carteira esta zerada, entao qualquer modelo pago (o minimax-m3, que era o
    # padrao ate 19/09/2026) devolve "Insufficient balance" antes de pensar --
    # `llm opencode "pergunta"` falhava sem `-m`. O free tier exige CLI 1.18+.
    "opencode":    (via_opencode,        "opencode/nemotron-3-ultra-free"),
    "commandcode": (via_commandcode,     "meta/muse-spark-1.3-contributor"),
    "openrouter":  (via_opencode,        "openrouter/z-ai/glm-5.3-flash"),
    "alibaba":     (via_opencode,        "alibaba-token-plan/qwen3.8-flash"),
    "deepseek":    (via_opencode,        "deepseek/deepseek-flash"),
    "claude":      (via_claude,          "sonnet"),
}

# Provedores cujo wrapper honra `ferramentas` de verdade -- todos de CLI, onde
# existe um agente do outro lado.
#
# Desde 19/09/2026 sao TODOS: openrouter, alibaba e deepseek passaram a chamar
# via_opencode (o gateway do opencode tem as tres contas como credencial
# propria -- `opencode providers list`), e nao sobrou nenhuma API crua no
# esquadrao. A lista continua aqui porque e ela que o esquadrao consulta para
# avisar, e porque um provedor novo pode entrar sem agente do outro lado.
# Nomes-base: quem checa clone passa por base() primeiro.
#
# RESSALVA medida em 19/09/2026: o agente do opencode manda o schema de
# ferramenta para o modelo SEMPRE, mesmo sem `--auto` (ferramentas=False so
# tira a auto-aprovacao, nao o schema). Modelo sem suporte a tool-calling
# falha a chamada inteira -- visto em openrouter/z-ai/glm-5.2:free, que
# devolve "No endpoints found that support tool use". Nao e a maioria (4 de 5
# testados passaram), mas na hora de escolher modelo free no painel e isto que
# separa o que funciona do que nao.
FERRAMENTAS_OK = ("codex", "agy", "claude", "opencode", "commandcode",
                  "openrouter", "alibaba", "deepseek")

# Batalhao: o mesmo provedor entrando mais de uma vez, cada linha com seu
# modelo. O nome e o do provedor + um numero, e TUDO que olha provedor (menu de
# modelos, tipo, efeito do esforco) usa base() para achar o original — assim um
# clone novo e so acrescentar aqui, nao editar cinco listas.
ORIGINAIS = tuple(PROVEDORES)          # antes dos clones entrarem
# 16/09/2026: os slots extras dos CLIs entraram junto com os do openrouter.
# Slots do mesmo nome-base compartilham gateway e sessao; o que muda entre eles
# e so o modelo escolhido no painel.
CLONES = {"openrouter": 2, "agy": 4, "codex": 2, "claude": 2, "commandcode": 4,
          "opencode": 2, "alibaba": 2}
for _nome, _n in CLONES.items():
    for _i in range(2, _n + 1):
        PROVEDORES[f"{_nome}{_i}"] = PROVEDORES[_nome]
# 20/11/2026: slot com modelo proprio, que nao herda o do pai. O juiz do painel
# escolhe entre slots; o agy4 deixa o Gemini 4 Neon puro sempre na lista.
SLOT_FIXO = {"agy4": ("gemini-4-neon", "high")}
for _nome, (_modelo, _ef) in SLOT_FIXO.items():
    PROVEDORES[_nome] = (PROVEDORES[re.sub(r"\d+$", "", _nome)][0], _modelo)


def base(nome):
    """openrouter2 -> openrouter. Quem nao e clone volta igual — e a checagem e
    contra ORIGINAIS, nao contra o regex, para um provedor que legitimamente
    termine em numero nao ser decapitado."""
    return nome if nome in ORIGINAIS else re.sub(r"\d+$", "", nome)


# 24/09/2026: -m montado de cabeca (ex. `deepseek/deepseek-v4.1-flash`, que so
# existe como `opencode/...` e no commandcode) custou quatro lancamentos que
# falharam no provedor. Antes de lancar, conferir o nome na lista do proprio
# CLI e, se faltar, dizer onde ele existe. codex/agy/claude nao listam: passam.
LISTA_MODELOS = {
    "opencode": ("opencode", "models"),
    "openrouter": ("opencode", "models"),
    "alibaba": ("opencode", "models"),
    "deepseek": ("opencode", "models"),
    "commandcode": ("commandcode", "--list-models"),
}


def modelos_de(cmd):
    """Primeira coluna de cada linha com `/`; None se o CLI nao respondeu."""
    try:
        r = subprocess.run([bin_de(cmd[0]), *cmd[1:]], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    nomes = {l.split()[0] for l in r.stdout.splitlines() if l.strip() and "/" in l.split()[0]}
    return nomes or None


def confere_modelo(provedor, modelo):
    cmd = LISTA_MODELOS.get(base(provedor))
    if not cmd:
        return
    nomes = modelos_de(cmd)
    if nomes is None:
        diz(f"[aviso] nao consegui listar os modelos de {cmd[0]}; seguindo sem conferir {modelo}")
        return
    if modelo in nomes:
        return
    sufixo = modelo.split("/")[-1]
    onde = []
    vistos = set()
    for prov, outro in LISTA_MODELOS.items():
        if outro in vistos:
            continue  # openrouter/alibaba/deepseek dividem o gateway do opencode
        vistos.add(outro)
        lista = nomes if outro == cmd else modelos_de(outro)
        for n in sorted(lista or ()):
            if n.split("/")[-1] == sufixo and (prov, n) not in onde:
                onde.append((prov, n))
    dica = "\n".join(f"  llm {p} -m {n}" for p, n in onde) or "  (nenhum provedor listado tem esse nome)"
    raise SystemExit(f"modelo {modelo!r} nao existe em {provedor} ({' '.join(cmd)}). Onde existe:\n{dica}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("provedor", choices=sorted(PROVEDORES))
    p.add_argument("pergunta", nargs="?", help="ou por stdin")
    p.add_argument("-i", "--imagem", action="append", default=[])
    p.add_argument("-m", "--modelo")
    p.add_argument("-e", "--effort", default="low", help='"" desliga')
    p.add_argument("-f", "--ferramentas", action="store_true",
                   help="libera web/arquivo; so vale nos provedores de CLI")
    a = p.parse_args()

    pergunta = a.pergunta or le_stdin().strip()
    if not pergunta:
        raise SystemExit("sem pergunta")
    for x in a.imagem:
        if not os.path.exists(x):
            raise SystemExit(f"imagem nao existe: {x}")

    fn, padrao = PROVEDORES[a.provedor]
    global TETO_AGENTE
    if a.ferramentas:
        TETO_AGENTE = 3600
    if a.modelo:
        confere_modelo(a.provedor, a.modelo)
    # 2026-09-24, ordem do usuario: commandcode so com o Spark contributor
    # (os outros modelos consomem a cota semanal).
    # 2026-09-27: space-bunny-alpha (free) liberado a pedido.
    # 2026-10-19: minimax-m3.1-flash-preview:free e beam:free liberados a pedido (benchmark).
    if base(a.provedor) == "commandcode" and (a.modelo or padrao) not in (
            "meta/muse-spark-1.3-contributor", "stealth/space-bunny-alpha",
            "minimax/minimax-m3.1-flash-preview:free", "reflection/beam:free",
            "deepseek/deepseek-v4.1-flash", "z-ai/glm-5.3-flash", "qwen/qwen3.8-flash", "xiaomi/mimo-v2.6-flash"):
        raise SystemExit(f"commandcode so roda meta/muse-spark-1.3-contributor, stealth/space-bunny-alpha, minimax-m3.1-flash-preview:free ou beam:free (pedido: {a.modelo})")
    if a.ferramentas and base(a.provedor) not in FERRAMENTAS_OK:
        diz(f"[aviso] {a.provedor} e API crua, sem ferramenta nenhuma: "
            f"--ferramentas nao tem efeito. Ele vai responder do treino.")
    diz(fn(pergunta, a.imagem, a.modelo or padrao, a.effort,
           a.ferramentas).strip())


if __name__ == "__main__":
    main()
