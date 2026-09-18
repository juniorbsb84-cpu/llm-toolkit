#!/usr/bin/env python
"""Varre os modelos que cada provedor oferece HOJE e cacheia em ~/.claude/modelos.json.

  python tools/modelos.py           # atualiza o cache e imprime o resumo
  python tools/modelos.py --mostra  # so le o cache

Lista escrita a mao envelhece calada: o wrapper continua mandando um nome que o
provedor ja aposentou e o erro so aparece na hora da pergunta. Cada provedor
tem seu jeito (HTTP /models nas APIs, subcomando proprio em cada CLI), entao a
varredura mora aqui e o painel so le o cache.
"""
import json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import PROVEDORES, chave, bin_de  # noqa: E402

CACHE = os.path.join(os.path.expanduser("~"), ".claude", "modelos.json")

# O codex nao tem "list models": a conta aceita ou recusa o nome na chamada.
# Esta lista veio de teste a mao (ver docs/PROVEDORES_LLM.md) e so muda a mao.
# O gpt-6-astra entrou em 16/09/2026, quando o usuario liberou o modelo caro.
CODEX_CONHECIDOS = ["gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna",
                    "gpt-5.6-pro", "gpt-5.x-codex", "gpt-5.4-mini"]

# O claude tambem nao lista modelos: sao os apelidos que o --model aceita
# (ver `claude --help`). Mesma situacao do codex — lista a mao, muda a mao.
CLAUDE_CONHECIDOS = ["sonnet", "opus", "fable"]

# Nomes que a API aceita mas NAO devolve no /models. `deepseek-chat` e alias
# legado: testado em 11/09/2026, responde normalmente, e continua sendo o
# padrao do wrapper. Some da lista viva sem sumir do servico.
ALIAS = {"deepseek": ["deepseek-chat"]}


def _json_get(url, k=None):
    h = {"Authorization": "Bearer " + k} if k else {}
    return json.load(urlopen(Request(url, headers=h), timeout=40))


def _ids(url, k=None):
    return sorted(m["id"] for m in _json_get(url, k)["data"])


def _cli(args, regex):
    """Roda a CLI e cata os nomes de modelo da saida (cada uma formata do seu
    jeito, entao o que separa nome de descricao e o regex)."""
    p = subprocess.run(args, capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    saida = (p.stdout or "") + (p.stderr or "")
    return sorted({m.group(1) for m in re.finditer(regex, saida, re.M)})


FONTES = {
    # Busca pelo rotulo, como no llm.py: o sk- generico do deepseek colide com
    # o de outros provedores no APIs.txt, e o regex pegava a chave errada.
    "deepseek":    lambda: _ids("https://api.deepseek.com/models",
                                chave(r"(?<=DEEPSEEK_API_KEY=)\S+")),
    "openrouter":  lambda: _ids("https://openrouter.ai/api/v1/models"),
    "alibaba":     lambda: _ids("https://token-plan.ap-southeast-1.maas."
                                "aliyuncs.com/compatible-mode/v1/models",
                                chave(r"sk-sp-\S+")),
    # agy lista "nome<TAB>descricao"; o effort ja vem embutido no nome.
    "agy":         lambda: _cli([bin_de("agy"), "models"], r"^([\w.\-/]+)\t"),
    # commandcode lista "nome  descricao" com dois espacos separando.
    "commandcode": lambda: _cli([bin_de("commandcode"), "--list-models"],
                                r"^([\w.\-]+/[\w.\-]+)\s{2,}"),
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
SEMPRE = re.compile(r"(^|/)(minimax-m3|hy3|mimo-v2\.5|mimo-v2\.5-pro)$")

# Pedidos a mao para FORA do menu do openrouter: aparecem no /models mas a
# chamada direta do wrapper nunca passa. O inkling:free saiu em 13/09/2026 com
# 403 restrito a harnesses agenticos registrados — nem com headers passa.
FORA = {"inclusionai/ling-3.0-flash", "inclusionai/ling-3.0-flash-fin",
        "rekaai/reka-flash-3", "stepfun/step-3.7-flash",
        "thinkingmachines/inkling:free"}

# Fora do menu do opencode: versao antiga que so ocupa espaco.
FORA_OC = {"opencode/muse-spark-1.2-contributor-free"}

# GO curado que nao tem "flash" no nome e cairia no filtro — confirmado via
# `opencode models` que existem no catalogo GO (16/09/2026).
PEDIDOS_OC_GO = {"opencode-go/mimo-v2.5-pro", "opencode-go/deepseek-v4-pro",
                 "opencode-go/grok-4.6", "opencode-go/kimi-k3",
                 "opencode-go/mimo-v2.5", "opencode-go/hy3"}

# O opencode e um gateway: `opencode models` lista tambem os modelos das OUTRAS
# contas do usuario (alibaba-token-plan/, deepseek/, google/, openrouter/...),
# que ja entram no esquadrao pelo provedor proprio. No menu do opencode ficam so
# os namespaces dele.
NAMESPACES = {"opencode": ("opencode/", "opencode-go/")}


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


# O opencode-go e um catalogo pequeno e curado (o plano que o usuario assina):
# ali cabem todas as variantes flash, inclusive as que a poda de versao comeria
# (o v4-flash convive com o v4.1-flash). Ja o namespace opencode/ repete meia
# duzia de geracoes do gemini, entao esse passa pela poda normal.
def _flash_vivos(ids):
    return [m for m in ids if "flash" in m and not m.startswith("~")
            and not re.search(r":batch|:free|-lite|-image|-preview"
                              r"|-\d{4}$|-\d{2}-\d{2}$|-free$", m)]


def so_do_opencode(ids):
    """GO curado inteiro + os `-free` do namespace opencode/.

    Leitura dos namespaces (12/09/2026): opencode-go/ = assinatura GO;
    opencode/ = catalogo ZEN pay-as-you-go, que e onde moram os `-free`. Os
    free valem DENTRO do cliente oficial do OpenCode, e e exatamente esse
    cliente que o wrapper chama por CLI local — entao entram no menu (decisao
    revista em 16/09/2026, a pedido do usuario).
    """
    ids = [m for m in ids if m.startswith(NAMESPACES["opencode"])]
    go = [m for m in ids if m.startswith("opencode-go/")]
    livres = [m for m in ids if m.startswith("opencode/") and m.endswith("-free")
              and m not in FORA_OC]
    podados = so_flash_de_ponta([m for m in ids if m.startswith("opencode/")])
    return sorted(set(_flash_vivos(go))
                  | {m for m in go if SEMPRE.search(m)
                     or m.endswith("muse-spark-1.3-contributor")}
                  | {m for m in go if m in PEDIDOS_OC_GO}
                  | set(livres) | set(podados))


def agy_sem_effort(ids):
    """O llm.py manda o effort na flag --effort com o nome-base do gemini. Se o
    menu trouxesse o sufixo, o painel gravaria ...-medium e o wrapper teria que
    adivinhar o que e nome e o que e esforco. Guarda so o nome-base."""
    # So a familia gemini: e a unica em que o llm.py normaliza o nome. O
    # gpt-oss-120b-medium, por exemplo, precisa do nome inteiro.
    return sorted({re.sub(r"-(low|medium|high)$", "", m)
                   if m.startswith("gemini-") else m for m in ids})


FILTRO = {"openrouter": so_flash_de_ponta, "opencode": so_do_opencode,
          "agy": agy_sem_effort}


def varre(nome):
    try:
        ms = FONTES[nome]()
        if nome in FILTRO:
            ms = FILTRO[nome](ms)
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
