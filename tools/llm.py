#!/usr/bin/env python
"""Uma pergunta, qualquer provedor, de qualquer projeto.

  python tools/llm.py codex "pergunta"
  python tools/llm.py openrouter -i foto.png "o que tem aqui?"
  echo "pergunta longa" | python tools/llm.py agy

Provedores: agy (+2,+3), codex (+2), claude (+2), opencode (+2), commandcode,
openrouter (+2,+3), alibaba, deepseek. Os slots numerados dividem gateway e
sessao com o slot base -- muda so o modelo escolhido (ver CLONES, no fim).
Os de CLI usam sessao ja logada; openrouter, alibaba e deepseek leem a chave
do arquivo apontado por LLM_APIS (nunca impressa).

O provedor "gemini" (CLI por API key) foi removido em 10/09/2026: a mesma
familia de modelos vem pelo agy, na assinatura, sem a cota que estourava.

Ver ~/.claude/docs/PROVEDORES_LLM.md para o porque de cada detalhe.
"""
import argparse, base64, io, json, mimetypes, os, re, shutil, subprocess, sys
import urllib.error, urllib.request

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


def data_url(caminho):
    tipo = mimetypes.guess_type(caminho)[0] or "image/png"
    b64 = base64.b64encode(open(caminho, "rb").read()).decode()
    return f"data:{tipo};base64,{b64}"


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


def roda(cmd, entrada=None, timeout=900):
    r = subprocess.run(cmd, input=entrada, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=SEM_JANELA)
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


def sem_surrogates(t):
    """Tira surrogate solto de uma string antes de ela virar JSON.

    Cinto e suspensorio do `le_stdin`: o texto tambem chega de saida de
    subprocesso (o dossie das rodadas do esquadrao e feito das respostas dos
    outros), e um unico surrogate perdido no meio derruba a chamada inteira com
    um erro que parece do provedor. Substitui pelo caractere de substituicao,
    que atravessa JSON sem reclamacao.
    """
    return "".join("�" if 0xD800 <= ord(c) <= 0xDFFF else c for c in t)


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

# Espera maxima por uma resposta de API.
#
# Eram 300s, e isso derrubava o alibaba DUAS VEZES na mesma consulta (rodadas 1
# e 2 de 15/09/2026, "The read operation timed out" em 301s). Nao era rede ruim:
# com `enable_thinking` e effort alto, num prompt de dezenas de milhares de
# caracteres, o modelo passa minutos raciocinando antes do primeiro byte. Os
# CLIs daqui ja usam 900-1200s justamente por isso; a API estava com um teto
# apertado demais para o mesmo trabalho.
TIMEOUT_API = 900

# Orcamento de saida das APIs, em tokens. Inclui o raciocinio nos modelos que
# pensam, entao precisa caber PENSAR e ESCREVER -- ver o comentario em
# `openai_compat`, onde 4.000 truncou respostas no meio.
MAX_TOKENS = 16000


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
    # O menu do agy para no high: xhigh/max vindos do painel ou de -e sao
    # reancorados no teto em vez de quebrar a chamada.
    if effort in ("xhigh", "max"):
        effort = "high"
    if modelo.startswith("gemini-"):
        # Config antiga podia ter gravado o sufixo no nome (gemini-3.8-flash-medium).
        # Normaliza para a base — o effort agora anda por fora.
        modelo = re.sub(r"-(low|medium|high)$", "", modelo)
    cmd = [bin_de("agy"), "--model", modelo]
    if effort and modelo.startswith("gemini-"):
        cmd += ["--effort", effort]
    # Sem isto o agy trava esperando aprovacao de cada ferramenta e devolve
    # resposta VAZIA no modo -p, sem dizer que travou. So ligar quando a tarefa
    # realmente precisa de web/arquivo: auto-aprova tudo, inclusive shell.
    if ferramentas:
        cmd.append("--dangerously-skip-permissions")
    for x in imagens:
        cmd += ["--add-dir", os.path.dirname(os.path.abspath(x))]
        pergunta = f"{os.path.abspath(x)}\n{pergunta}"
    if not cabe_no_argv(cmd + ["-p"], pergunta):
        return _agy_streaming(cmd, pergunta)
    return roda(cmd + ["-p", pergunta])


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
        return roda(cmd, entrada=pergunta, timeout=1200)
    return roda(cmd + ["--", pergunta], timeout=1200)


def via_opencode(pergunta, imagens, modelo, effort, ferramentas=False):
    # O -f nao entrega a imagem ao modelo; passa o caminho no texto e deixa o
    # agente abrir. Prompt em UMA linha: \n em argv sai com erro vazio aqui.
    if imagens:
        pergunta = ("Leia estas imagens: "
                    + " ".join(os.path.abspath(x) for x in imagens)
                    + ". " + pergunta)
    elif not ferramentas:
        pergunta = "NAO use ferramenta. " + pergunta
    cmd = [bin_de("opencode"), "run", "-m", modelo]
    # --variant e o esforco do provider. Cuidado: a CLI aceita QUALQUER valor
    # calada (testei "banana", rc=0), entao nao da para provar que ela honra o
    # que foi pedido — por isso o teto fica no high, que existe no catalogo.
    if effort:
        cmd += ["--variant", "high" if effort in ("xhigh", "max") else effort]
    uma_linha = " ".join(pergunta.split())
    # `opencode.CMD` passa por cmd.exe e morre em ~8.191 caracteres (WinError
    # 206). Pelo stdin ele aceita a pergunta inteira -- e com quebra de linha,
    # entao achatar em uma linha so faz sentido no caminho do argv.
    if not cabe_no_argv(cmd, uma_linha):
        saida = roda(cmd, entrada=pergunta)
    else:
        saida = roda(cmd + [uma_linha])
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
    return "\n".join(linhas)


def via_commandcode(pergunta, imagens, modelo, effort, ferramentas=False):
    # A pergunta vai por stdin: o launcher .cmd do npm passa pelo cmd.exe, que
    # estoura (WinError 206) num argumento longo.
    if imagens:
        pergunta = ("Leia estas imagens: "
                    + " ".join(os.path.abspath(x) for x in imagens)
                    + "\n\n" + pergunta)
    base = [bin_de("commandcode"), "-p", "-m", modelo]
    resto = ["--tools-all", "-t", "--no-session", "--max-turns", "16",
             "--skip-onboarding", "--no-auto-update"]
    for x in imagens:
        resto += ["--add-dir", os.path.dirname(os.path.abspath(x))]

    def chama(com_effort):
        cmd = base + (["--effort", effort] if (effort and com_effort) else []) + resto
        return roda(cmd, entrada=pergunta, timeout=1200)

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


# --- APIs (chave) -----------------------------------------------------------

def openai_compat(url, k, modelo, pergunta, imagens, extra=None):
    conteudo = [{"type": "image_url", "image_url": {"url": data_url(x)}}
                for x in imagens]
    # Surrogate solto aqui derruba a chamada inteira com um erro que PARECE do
    # provedor ("lone leading surrogate in hex escape"), e nao daqui. Ver
    # `sem_surrogates`.
    conteudo.append({"type": "text", "text": sem_surrogates(pergunta)})
    # max_tokens curto devolve resposta VAZIA: o raciocinio consome tudo e o
    # finish_reason vem "length" com content nulo.
    #
    # Eram 4.000, e isso NAO era teto de sobra. Em 15/09/2026, na consulta com
    # effort alto, o juiz observou que "openrouter2 e openrouter3 ficaram como
    # raciocinios incompletos, sem as tres propostas fechadas": nao foi o modelo
    # sendo raso, foi a cota acabando no meio. Com raciocinio ligado, os tokens
    # de pensamento saem DESTE mesmo orcamento -- 4.000 e pouco para pensar e
    # ainda escrever. O prejuizo e pior no refino, onde a resposta cortada vira
    # insumo da rodada seguinte e os outros criticam um texto pela metade.
    pedido = {"model": modelo, "max_tokens": MAX_TOKENS,
              "messages": [{"role": "user", "content": conteudo}]}
    pedido.update(extra or {})
    req = urllib.request.Request(url, data=json.dumps(pedido).encode(), headers={
        "Authorization": "Bearer " + k, "Content-Type": "application/json",
        # Sem User-Agent a borda da CommandCode devolve Cloudflare 1010.
        "User-Agent": "llm-tool/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_API) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        # 401/403/400 e regra do provedor, nao falha tecnica: falha e para, sem
        # retry e sem trocar de porta. Foi insistir depois de uma negativa que
        # bloqueou a conta OpenCode.
        raise SystemExit(f"HTTP {e.code} de {url.split('/')[2]}: "
                         f"{e.read().decode('utf-8', 'replace')[:200]}")
    # Nem toda resposta 200 traz `choices`. Alguns gateways devolvem erro com
    # status 200 e corpo {"error": {...}}, e ai o acesso direto estourava um
    # KeyError CRU -- que sobe como traceback e nao como falha de provedor,
    # entao o esquadrao mostra "[FALHOU] 'choices'" e ninguem descobre o motivo.
    # A mensagem util esta no corpo; e ela que tem de aparecer.
    if not d.get("choices"):
        err = d.get("error")
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)[:200]
        raise SystemExit(f"{url.split('/')[2]} respondeu sem 'choices': "
                         f"{err or json.dumps(d)[:200]}")
    escolha = d["choices"][0]
    m = escolha["message"]
    # Alguns modelos poem a resposta no fim do raciocinio e deixam content nulo.
    # Cada API nomeia esse rascunho de um jeito: reasoning no openrouter,
    # reasoning_content no deepseek e no alibaba.
    texto = (m.get("content") or m.get("reasoning")
             or m.get("reasoning_content") or "")
    if not texto.strip():
        # Devolver "" daqui e a pior saida possivel: o esquadrao registra o
        # provedor como tendo participado, o dossie ganha uma entrada vazia e
        # ninguem descobre por que aquele modelo "nao teve opiniao". Falha
        # declarada, com o motivo que a API deu.
        motivo = escolha.get("finish_reason") or "sem finish_reason"
        uso = d.get("usage") or {}
        raise SystemExit(
            f"resposta vazia de {url.split('/')[2]} (finish_reason={motivo}; "
            f"tokens: entrada={uso.get('prompt_tokens', '?')} "
            f"saida={uso.get('completion_tokens', '?')} de {MAX_TOKENS})"
            + (" -- o raciocinio consumiu o orcamento inteiro; subir MAX_TOKENS"
               if motivo == "length" else ""))
    return texto


def via_openrouter(pergunta, imagens, modelo, effort, ferramentas=False):
    return openai_compat("https://openrouter.ai/api/v1/chat/completions",
                         chave(r"sk-or-[A-Za-z0-9._-]+"), modelo, pergunta, imagens,
                         {"reasoning_effort": effort} if effort else None)


def via_deepseek(pergunta, imagens, modelo, effort, ferramentas=False):
    # Chave em APIS na linha DEEPSEEK_API_KEY=. O sk- generico dela colide com
    # o de outros provedores no arquivo, por isso a busca e pelo rotulo.
    # reasoning_effort existe aqui: verificado em 12/09/2026 que low/high/xhigh
    # passam e que "banana" da 400 listando o enum. Nao e ignorado como o
    # wrapper supunha.
    if imagens:
        raise SystemExit("deepseek nao aceita imagem; use agy/openrouter/alibaba")
    # O servidor aceita xhigh mas mapeia para high internamente. Reancorar aqui
    # faz o painel e a CLI concordarem sobre o que foi de fato pedido.
    if effort == "xhigh":
        effort = "high"
    return openai_compat("https://api.deepseek.com/chat/completions",
                         chave(r"(?<=DEEPSEEK_API_KEY=)\S+"), modelo, pergunta, [],
                         {"reasoning_effort": effort} if effort else None)


def via_alibaba(pergunta, imagens, modelo, effort, ferramentas=False):
    # A sk-sp- do Token Plan so responde no gateway dele.
    # O pensamento aqui e opt-in: sem enable_thinking o reasoning_effort nao faz
    # nada. Com ele, low/xhigh/max passam e voltam com reasoning_content;
    # "banana" da 400. Effort vazio desliga o pensamento de proposito.
    extra = ({"enable_thinking": True, "reasoning_effort": effort} if effort
             else {"enable_thinking": False})
    return openai_compat("https://token-plan.ap-southeast-1.maas.aliyuncs.com"
                         "/compatible-mode/v1/chat/completions",
                         chave(r"sk-sp-\S+"), modelo, pergunta, imagens, extra)


PROVEDORES = {
    # terra > luna na pratica: o luna dava respostas rasas no esquadrao.
    "codex":       (via_codex,           "gpt-5.6-terra"),
    "agy":         (via_agy,             "gemini-3.8-flash-medium"),
    "opencode":    (via_opencode,        "opencode-go/muse-spark-1.3-contributor"),
    "commandcode": (via_commandcode,     "meta/muse-spark-1.3-contributor"),
    "openrouter":  (via_openrouter,      "z-ai/glm-5.3-flash"),
    "alibaba":     (via_alibaba,         "qwen3.8-flash"),
    "deepseek":    (via_deepseek,        "deepseek-chat"),
    "claude":      (via_claude,          "sonnet"),
}

# Batalhao: o mesmo provedor entrando mais de uma vez, cada linha com seu
# modelo. O nome e o do provedor + um numero, e TUDO que olha provedor (menu de
# modelos, tipo, efeito do esforco) usa base() para achar o original — assim um
# clone novo e so acrescentar aqui, nao editar cinco listas.
ORIGINAIS = tuple(PROVEDORES)          # antes dos clones entrarem
# 16/09/2026: os slots extras dos CLIs entraram junto com os do openrouter.
# Slots do mesmo nome-base compartilham gateway e sessao; o que muda entre eles
# e so o modelo escolhido no painel.
CLONES = {"openrouter": 3, "agy": 3, "codex": 2, "claude": 2, "opencode": 2}
for _nome, _n in CLONES.items():
    for _i in range(2, _n + 1):
        PROVEDORES[f"{_nome}{_i}"] = PROVEDORES[_nome]


def base(nome):
    """openrouter2 -> openrouter. Quem nao e clone volta igual — e a checagem e
    contra ORIGINAIS, nao contra o regex, para um provedor que legitimamente
    termine em numero nao ser decapitado."""
    return nome if nome in ORIGINAIS else re.sub(r"\d+$", "", nome)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("provedor", choices=sorted(PROVEDORES))
    p.add_argument("pergunta", nargs="?", help="ou por stdin")
    p.add_argument("-i", "--imagem", action="append", default=[])
    p.add_argument("-m", "--modelo")
    p.add_argument("-e", "--effort", default="low", help='"" desliga')
    a = p.parse_args()

    pergunta = a.pergunta or le_stdin().strip()
    if not pergunta:
        raise SystemExit("sem pergunta")
    for x in a.imagem:
        if not os.path.exists(x):
            raise SystemExit(f"imagem nao existe: {x}")

    fn, padrao = PROVEDORES[a.provedor]
    diz(fn(pergunta, a.imagem, a.modelo or padrao, a.effort).strip())


if __name__ == "__main__":
    main()
