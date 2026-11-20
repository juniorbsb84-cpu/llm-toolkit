#!/usr/bin/env python
"""Varre os modelos que cada provedor oferece HOJE e cacheia em ~/.claude/modelos.json.

  python ~/.claude/tools/modelos.py           # atualiza o cache e imprime o resumo
  python ~/.claude/tools/modelos.py --mostra  # so le o cache

Lista escrita a mao envelhece calada: o wrapper continua mandando um nome que o
provedor ja aposentou e o erro so aparece na hora da pergunta. Cada provedor
tem seu jeito (HTTP /models nas APIs, subcomando proprio em cada CLI), entao a
varredura mora aqui e o painel so le o cache.
"""
import json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import PROVEDORES, bin_de  # noqa: E402

CACHE = os.path.join(os.path.expanduser("~"), ".claude", "modelos.json")

# O codex nao tem "list models": a conta aceita ou recusa o nome na chamada.
# Esta lista veio de teste a mao (ver docs/PROVEDORES_LLM.md) e so muda a mao.
# O gpt-6-astra entrou em 16/09/2026, quando o usuario liberou o modelo caro.
# Sol e Luna entraram em 22/09/2026, apos verificacao no catalogo oficial.
# gpt-5.6-sol saiu em 23/09/2026: gpt-6-sol custa metade dos creditos.
# 29/09/2026: gpt-6.1-sol entrou (testado, exige codex-cli >= 0.159); astra e
# fable sairam dos menus por ordem do usuario.
# gpt-5.6-pro, gpt-5.x-codex e gpt-5.4-mini sairam no mesmo dia: 400 na conta.
CODEX_CONHECIDOS = ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna",
                    "gpt-5.6-terra", "gpt-5.6-luna"]

# O claude tambem nao lista modelos: sao os apelidos que o --model aceita
# (ver `claude --help`). Mesma situacao do codex — lista a mao, muda a mao.
# So o nome completo: o apelido ("opus") nao casa com nota nem com a rota do
# arquiteto, e duplicava cada modelo no menu (20/11/2026).
CLAUDE_CONHECIDOS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

# ALIAS existia para "deepseek-chat" (id da API oficial que nao aparecia no
# /models dela, mas respondia). Saiu em 19/09/2026 junto com a API crua: o
# deepseek agora e o namespace deepseek/ do opencode, e la "deepseek-chat" nao
# existe -- so os 4 ids curados por ele (ver FONTES). Injetar um alias de fora
# desse catalogo quebraria a chamada, entao nao ha mais ALIAS nenhum.
ALIAS = {}


def _cli(args, regex):
    """Roda a CLI e cata os nomes de modelo da saida (cada uma formata do seu
    jeito, entao o que separa nome de descricao e o regex)."""
    p = subprocess.run(args, capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    saida = (p.stdout or "") + (p.stderr or "")
    return sorted({m.group(1) for m in re.finditer(regex, saida, re.M)})


def _linhas_cli(args):
    """Como `_cli`, mas devolve as linhas cruas sem regex -- o namespace do
    opencode para openrouter tem MAIS de uma barra (`openrouter/z-ai/glm-...`),
    e o regex de `_cli` (`[\\w.\\-]+/[\\w.\\-]+` sem barra no meio) foi desenhado
    para o caso de uma barra so e simplesmente nao bate nesses ids."""
    p = subprocess.run(args, capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    saida = (p.stdout or "") + (p.stderr or "")
    return [l.strip() for l in saida.splitlines()]


def _namespace_opencode(prefixo, com_prefixo=True):
    """Modelos de UM namespace do gateway do opencode (`opencode models`),
    filtrados pelo prefixo (ex.: "openrouter/", "alibaba-token-plan/").

    `com_prefixo=False` devolve o id NU (sem o namespace) -- para reaproveitar
    filtros que ja trabalham com o id puro do provedor de origem (FORA,
    FORA_NEMOTRON, so_flash_de_ponta), que foram escritos quando a fonte ainda
    era a API direta desses provedores.
    """
    linhas = [l for l in _linhas_cli([bin_de("opencode"), "models"])
             if l.startswith(prefixo)]
    if com_prefixo:
        return sorted(set(linhas))
    return sorted({l[len(prefixo):] for l in linhas})


# deepseek, openrouter e alibaba pararam de ler a API de cada um direto em
# 19/09/2026: os tres viraram chamada via_opencode (ver llm.py), e o menu
# precisa mostrar so o que o NAMESPACE DO OPENCODE realmente aceita -- que nao
# e o mesmo catalogo da API oficial (o deepseek/ do opencode, por exemplo, tem
# 4 ids curados, nao os "deepseek-chat"/"deepseek-reasoner" da api.deepseek.com
# direta). Fonte de verdade agora e sempre `opencode models`.
ALIBABA_NEGADOS = {"glm-5", "glm-5.1", "MiniMax-M2.5", "kimi-k2.5", "kimi-k2.6",
                   "kimi-k2.7-code", "deepseek-v3.2", "deepseek-v4-flash",
                   "qwen3.6-plus"}

FONTES = {
    "deepseek":    lambda: _namespace_opencode("deepseek/"),
    "openrouter":  lambda: _namespace_opencode("openrouter/", com_prefixo=False),
    # Token Plan mistura imagem/video (qwen-image, wan, happyhorse) no mesmo
    # namespace; o esquadrao so conversa, entao o menu fica so com texto.
    # ALIBABA_NEGADOS: listados pelo opencode, mas "Access to model denied"
    # na assinatura (testados um a um em 24/09/2026).
    "alibaba":     lambda: [m for m in _namespace_opencode("alibaba-token-plan/")
                            if not re.search(r"/(qwen-image|wan|happyhorse)", m)
                            and m.split("/", 1)[1] not in ALIBABA_NEGADOS],
    # agy lista "nome<TAB>descricao"; o effort ja vem embutido no nome.
    "agy":         lambda: _cli([bin_de("agy"), "models"], r"^([\w.\-/]+)\t"),
    # commandcode lista "nome  descricao" com dois espacos separando.
    "commandcode": lambda: _cli([bin_de("commandcode"), "--list-models"],
                                r"^([\w.\-]+/[\w.\-:]+)\s{2,}"),
    "opencode":    lambda: _cli([bin_de("opencode"), "models"],
                                r"^([\w.\-]+/[\w.\-]+)\s*$"),
    "codex":       lambda: list(CODEX_CONHECIDOS),
    "claude":      lambda: list(CLAUDE_CONHECIDOS),
}


# O OpenRouter serve 443 modelos — menu inutil. Fica so o topo de cada familia
# "flash" (o nicho pelo qual ele entra no esquadrao: barato e rapido), mais o
# muse-spark 1.3 contributor e o que estiver em SEMPRE. Calculado, nao escrito
# a mao, para nao envelhecer quando sair um gemini-3.9-flash.
def _versao(mid):
    return [int(x) for x in re.findall(r"\d+", mid)] or [0]


# Modelos que entram no menu mesmo sem ser "flash": pedidos a mao porque o
# esquadrao os quer disponiveis em todo provedor que os sirva.
# 10/10/2026: o menu do openrouter so mostrava flash/free e escondia os carros-chefe
# novos (kimi-k3, glm-5.3, qwen3.8-max-prime, grok-4.7, claude 5.5...).
SEMPRE = re.compile(r"(^|/)(minimax-m3|hy3|hy4-preview|mimo-v2\.5|mimo-v2\.5-pro|mimo-v2\.6-pro"
                    r"|mimo-v2\.6-pro-ultraspeed|kimi-k3|glm-5\.3|glm-5\.3-prime|qwen3\.8-max-prime"
                    r"|deepseek-v4-pro-0813|grok-4\.7|claude-opus-5\.5|claude-sonnet-5\.5)$")

# Pedidos a mao para FORA do menu do openrouter: aparecem no /models mas a
# chamada direta do wrapper nunca passa. O inkling:free saiu em 13/09/2026 com
# 403 restrito a harnesses agenticos registrados — nem com headers passa.
# Da familia nemotron ficam so o ultra-550b e o super-120b (pedido 19/09/2026):
# os outros sao nano/lightning/content-safety, nicho ou fracos demais para
# ocuparem linha no menu.
# 27/09/2026: pedido "todos os free no esquadrao" -- saiu tudo que responde.
# Ficam fora so os que falham na chamada: content-safety nao aceita tool use
# (o agente do opencode sempre manda o schema).
FORA_NEMOTRON = {"nvidia/nemotron-3.5-content-safety:free",
                 "nvidia/nemotron-3-super-120b-a12b:free",
                 "nvidia/nemotron-3.5-lightning:free",
                 "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"}
# 27/09/2026: reprovados no benchmark (erro de conta, indice baixo ou >80s).
REPROVADOS = {"thinkingmachines/inkling:free", "thinkingmachines/inkling-small:free",
              "liquid/lfm-2.5-2.6b:free", "cohere/north-mini-code:free",
              "openrouter/free", "qwen/qwen3.8-27b:free", "poolside/laguna-s-2.1:free",
              # ling: fica so o flash-fin:free (melhor indice e gratis)
              "inclusionai/ling-3.0-flash-fin", "inclusionai/ling-3.0-flash-sante:free",
              "inclusionai/ling-3.0-flash-vl",
              "google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"}

FORA = FORA_NEMOTRON | REPROVADOS | {"inclusionai/ling-3.0-flash",
        "rekaai/reka-flash-3", "stepfun/step-3.7-flash"}

# Fora do menu do opencode: versao antiga que so ocupa espaco.
FORA_OC = {"opencode/muse-spark-1.2-contributor-free",
           "opencode/nemotron-3.5-lightning-free"}

# Pedidos a mao para DENTRO do menu do opencode: nao tem "flash" no nome e
# cairiam no filtro. O mimo-v2.5-pro foi pedido junto (19/09/2026) mas nao
# existe no ZEN — so o GO tinha, e o GO saiu; no ZEN sobra o mimo-v2.5-free.
# O "muse-spark-1.3-contributor" pago nao existe com esse nome no ZEN: a versao
# nao-free se chama so "muse-spark-1.3" (o sufixo -contributor so aparece no
# free). E essa que entra (19/09/2026).
PEDIDOS_OC = {"opencode/deepseek-v4-pro", "opencode/muse-spark-1.3"}

# O namespace opencode-go/ (assinatura GO) saiu em 19/09/2026: o plano venceu e
# nao sera renovado. Fica so o ZEN (opencode/), pay-as-you-go.

# O opencode e um gateway: `opencode models` lista tambem os modelos das OUTRAS
# contas do usuario (alibaba-token-plan/, deepseek/, google/, openrouter/...),
# que ja entram no esquadrao pelo provedor proprio. No menu do opencode ficam so
# os namespaces dele.
NAMESPACES = {"opencode": ("opencode/",)}


def so_flash_de_ponta(ids):
    ids = [m for m in ids if m not in FORA]
    extras = [m for m in ids if SEMPRE.search(m)]
    vivos = [m for m in ids if "flash" in m and not m.startswith("~")
             and not re.search(r":batch|:free|-lite|-image|-preview"
                               r"|-\d{4}$|-\d{2}-\d{2}$|-free$", m)]
    familias = {}
    for m in vivos:
        # Familia = o que vem antes de "flash" sem numeros, MAIS o sufixo depois
        # dele. Junta gemini-3.6/3.7/3.8-flash num grupo so (sobra o mais novo) e
        # ao mesmo tempo deixa deepseek-v4-flash-vision-exp viver ao lado do
        # deepseek-v4.1-flash: sao variantes diferentes, nao versoes da mesma.
        cabeca, _, rabo = m.partition("flash")
        fam = (re.sub(r"[\d.]+", ".", cabeca), rabo)
        atual = familias.get(fam)
        if atual is None or (_versao(m), -len(m)) > (_versao(atual), -len(atual)):
            familias[fam] = m
    spark = [m for m in ids if m.endswith("muse-spark-1.3-contributor")]
    return sorted(set(familias.values()) | set(spark) | set(extras))


def so_do_opencode(ids):
    """So o namespace ZEN (opencode/): o catalogo pay-as-you-go.

    O opencode e um gateway e lista tambem modelos das outras contas do usuario
    (google/, deepseek/, openrouter/...), que ja entram pelo provedor proprio.
    Os `-free` valem DENTRO do cliente oficial do OpenCode, e e esse cliente que
    o wrapper chama por CLI local — entao entram no menu.
    """
    ids = [m for m in ids if m.startswith(NAMESPACES["opencode"])]
    livres = [m for m in ids if m.endswith("-free") and m not in FORA_OC]
    return sorted(set(livres) | set(so_flash_de_ponta(ids))
                  | {m for m in ids if m in PEDIDOS_OC})


def agy_sem_effort(ids):
    """O llm.py manda o effort na flag --effort com o nome-base do gemini. Se o
    menu trouxesse o sufixo, o painel gravaria ...-medium e o wrapper teria que
    adivinhar o que e nome e o que e esforco. Guarda so o nome-base."""
    # So a familia gemini: e a unica em que o llm.py normaliza o nome. O
    # gpt-oss-120b-medium, por exemplo, precisa do nome inteiro.
    return sorted({re.sub(r"-(low|medium|high|xhigh)$", "", m)
                   if m.startswith("gemini-") else m for m in ids})


def so_flash_e_free(ids):
    """Topo de cada familia flash MAIS todo modelo gratuito do catalogo.

    O free nao compete por qualidade, compete por custo zero: cabe no menu
    inteiro, sem poda de versao. O FORA continua valendo — la moram os que a
    API lista mas recusa a chamada (403 de harness registrado), free ou nao.
    """
    livres = [m for m in ids
              if (m.endswith(":free") or m.endswith("-free")) and m not in FORA]
    return sorted(set(so_flash_de_ponta(ids)) | set(livres))


def commandcode_sem_spark_velho(ids):
    """Menu do commandcode inteiro, menos a prateleira de muse-spark.

    O commandcode serve cinco variantes (1.1, 1.2, 1.2-contributor, 1.3,
    1.3-contributor) que so diferem em geracao e cota. Fica a de cima
    (pedido 19/09/2026); as outras so ocupavam linha.
    """
    return sorted(m for m in ids
                  if "muse-spark" not in m
                  or m.endswith("muse-spark-1.3-contributor"))


def openrouter_com_prefixo(ids):
    """so_flash_e_free trabalha com o id NU (e e onde FORA/FORA_NEMOTRON
    continuam validos, sem reescrever os dois conjuntos com o namespace na
    frente); o prefixo "openrouter/" volta so no final, para bater com o que
    via_opencode espera ao chamar (ver PROVEDORES em llm.py)."""
    return sorted("openrouter/" + m for m in so_flash_e_free(ids))


# 29/09/2026, pedido "LLM que nao responde mais pode retirar": pingados com uma
# palavra, todos os menus. Fora quem falhou por motivo que nao passa sozinho
# (403 fora do plano, rota inexistente, filtro de provedor quebrado, acesso
# desabilitado, conta ZEN sem saldo, erro de servidor duas vezes seguidas).
# Falha so por "effort low nao suportado" NAO entrou: o modelo responde.
# Se colocar saldo no ZEN, tire os opencode/ pagos daqui.
MORTOS = {
    "google/gemini-3.1-flash-lite", "google/gemini-3.5-flash",
    "google/gemini-3.5-flash-lite", "google/gemini-3.6-flash",
    "minimaxai/minimax-m2.7", "thinkingmachines/inkling",
    "thinkingmachines/inkling-small", "typesafe/jev", "xai/grok-4.6",
    "moonshotai/kimi-k2.6", "moonshotai/kimi-k2.7-code-highspeed",
    "opencode/ling-3.0-flash-fin-free", "opencode/gemini-3.8-flash",
    "opencode/muse-spark-1.3", "opencode/deepseek-v4-flash-vision-exp",
    "opencode/deepseek-v4-pro", "opencode/deepseek-v4.1-flash",
    "opencode/glm-5.3-flash", "opencode/minimax-m3", "opencode/qwen3.8-flash",
}


FILTRO = {"openrouter": openrouter_com_prefixo,
          "commandcode": commandcode_sem_spark_velho, "opencode": so_do_opencode,
          "agy": agy_sem_effort}


def varre(nome):
    try:
        ms = FONTES[nome]()
        if nome in FILTRO:
            ms = FILTRO[nome](ms)
        ms = [m for m in ms if m not in MORTOS]
        return nome, {"modelos": ms, "erro": None}
    except BaseException as e:
        return nome, {"modelos": [], "erro": f"{type(e).__name__}: {e}"[:200]}


def atualiza():
    with ThreadPoolExecutor(max_workers=len(FONTES)) as ex:
        res = dict(ex.map(varre, FONTES))
    # Provedor que falhou mantem a lista anterior: cache velho vale mais que
    # menu vazio por causa de um timeout.
    for n, extras in ALIAS.items():
        if n in res and not res[n]["erro"]:
            res[n]["modelos"] = sorted(set(res[n]["modelos"]) | set(extras))

    antigo = le() or {}
    for n, v in res.items():
        if v["erro"] and antigo.get("provedores", {}).get(n, {}).get("modelos"):
            v["modelos"] = antigo["provedores"][n]["modelos"]
            v["erro"] += " (mantido o cache anterior)"
    saida = {"provedores": res,
             "padroes": {n: PROVEDORES[n][1] for n in PROVEDORES},
             "atualizado_em": datetime.now().isoformat(timespec="seconds")}
    # O commandcode marca o free so na descricao (o stealth/ nao tem "free" no
    # nome); o painel usa isto para por "free" na frente no menu.
    try:
        saida["gratis"] = _cli([bin_de("commandcode"), "--list-models"],
                               r"^(\S+)\s+FREE ")
    except Exception:
        saida["gratis"] = antigo.get("gratis", [])
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)
    return saida


def le():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            pass
    return None


if __name__ == "__main__":
    d = le() if "--mostra" in sys.argv else atualiza()
    if not d:
        raise SystemExit(f"sem cache ainda: rode sem --mostra")
    for n, v in sorted(d["provedores"].items()):
        marca = f"  [{v['erro']}]" if v["erro"] else ""
        print(f"{n:12} {len(v['modelos']):3} modelos{marca}")
    print(f"\n{CACHE}  ({d['atualizado_em']})")
