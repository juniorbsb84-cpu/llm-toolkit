#!/usr/bin/env python
"""Tabela de escolha de LLM: os 25 melhores modelos que você acessa, com dados medidos.

  python ~/.claude/tools/tabela_llm.py            baixa a Artificial Analysis agora
  python ~/.claude/tools/tabela_llm.py --offline  usa o ultimo JSON salvo

Saida: ~/.claude/docs/ESQUADRAO_TABELA_LLM.md
Fontes: Artificial Analysis (provas objetivas e custo medido por tarefa, baixado a
cada execucao) e LMArena (voto humano em sites, digitado abaixo com data).
So entram modelos que estao nos menus do painel e respondem (ver MORTOS em
modelos.py). Fable e Astra ficam fora por ordem do usuario.
"""
import json, os, re, sys, urllib.request
from datetime import date

CASA = os.path.expanduser("~/.claude")
FONTES = os.path.join(CASA, "esquadrao", "fontes")
SAIDA = os.path.join(CASA, "docs", "ESQUADRAO_TABELA_LLM.md")
URL = "https://artificialanalysis.ai/leaderboards/models?status=all"

GOOGLE, FILA = "Grátis (conta Google)", "Grátis (fila no pico)"
CLAUDE, CHATGPT, CC, ALI = "Cota Claude", "Cota ChatGPT", "Cota commandcode", "Crédito Alibaba"
OR = "Saldo OpenRouter"

# slug da Artificial Analysis -> (onde roda no painel, como paga, effort)
ACESSO = {
    "gemini-3-8-flash": ("agy · gemini-3.8-flash", GOOGLE, "high"),
    "gemini-3-8-flash-medium": ("agy · gemini-3.8-flash", GOOGLE, "medium"),
    "gemini-3-8-flash-low": ("agy · gemini-3.8-flash", GOOGLE, "low"),
    # 13/11/2026: Google trocou o catalogo do agy. Sairam 3.7/3.6 Flash, 3.1 Pro
    # e Claude 4.6; entraram Gemini 4 Argon (ate high) e Hydrogen (ate xhigh).
    # 20/11/2026: Gemini 4 Neon, nota 61 em xhigh, a maior acessivel.
    "gemini-4-neon": ("agy · gemini-4-neon", GOOGLE, "xhigh"),
    "gemini-4-neon-high": ("agy · gemini-4-neon", GOOGLE, "high"),
    "gemini-4-neon-medium": ("agy · gemini-4-neon", GOOGLE, "medium"),
    "gemini-4-neon-low": ("agy · gemini-4-neon", GOOGLE, "low"),
    "gemini-4-hydrogen": ("agy · gemini-4-hydrogen", GOOGLE, "xhigh"),
    "gemini-4-hydrogen-high": ("agy · gemini-4-hydrogen", GOOGLE, "high"),
    "gemini-4-hydrogen-medium": ("agy · gemini-4-hydrogen", GOOGLE, "medium"),
    "gemini-4-hydrogen-low": ("agy · gemini-4-hydrogen", GOOGLE, "low"),
    "gemini-4-argon": ("agy · gemini-4-argon", GOOGLE, "high"),
    "gemini-4-argon-medium": ("agy · gemini-4-argon", GOOGLE, "medium"),
    "gemini-4-argon-low": ("agy · gemini-4-argon", GOOGLE, "low"),
    "muse-spark-1-3-xhigh": ("opencode · muse-spark-1.3-contributor-free", FILA, "xhigh"),
    "muse-spark-1-3": ("commandcode/openrouter · muse-spark-1.3-contributor", CC, "max"),
    "mimo-v2-6-flash": ("opencode · mimo-v2.6-flash-free", FILA, "padrao"),
    "nvidia-nemotron-3-ultra-550b-a55b": ("opencode/openrouter · nemotron-3-ultra free", FILA, "padrao"),
    "longcat-2-0": ("opencode · longcat-2.5-preview-free (medido: 2.0)", FILA, "padrao"),
    "claude-opus-5-5": ("claude · claude-opus-5-5", CLAUDE, "max"),
    "claude-opus-5-5-xhigh": ("claude · claude-opus-5-5", CLAUDE, "xhigh"),
    "claude-opus-5-5-high": ("claude · claude-opus-5-5", CLAUDE, "high"),
    "claude-opus-5-5-medium": ("claude · claude-opus-5-5", CLAUDE, "medium"),
    "claude-opus-5-5-low": ("claude · claude-opus-5-5", CLAUDE, "low"),
    "claude-sonnet-5-5": ("claude · claude-sonnet-5-5", CLAUDE, "max"),
    "claude-sonnet-5-5-xhigh": ("claude · claude-sonnet-5-5", CLAUDE, "xhigh"),
    "claude-sonnet-5-5-high": ("claude · claude-sonnet-5-5", CLAUDE, "high"),
    "claude-sonnet-5-5-medium": ("claude · claude-sonnet-5-5", CLAUDE, "medium"),
    "claude-4-5-haiku-reasoning": ("claude · haiku", CLAUDE, "padrao"),
    # 12/11/2026: codex, alibaba e deepseek sairam do painel (sem assinatura).
    # O DeepSeek segue pelo commandcode e pela OpenRouter.
    "deepseek-v4-1-flash-1017": ("commandcode/openrouter · deepseek-v4.1-flash", CC, "max"),
    "deepseek-v4-1-flash-1017-high": ("commandcode/openrouter · deepseek-v4.1-flash", CC, "high"),
    # 29/09/2026: tambem estao no menu da OpenRouter (paga, ping OK). Antes
    # constavam so pelo commandcode, que a regra de cota bloqueia -- e por isso
    # o MiMo-V2.6-Pro ficou fora da tabela sem motivo.
    "mimo-v2-6-pro": ("openrouter · xiaomi/mimo-v2.6-pro", OR, "padrao"),
    "glm-5-3-flash": ("openrouter · z-ai/glm-5.3-flash", OR, "padrao"),
    "hy3": ("openrouter · tencent/hy3", OR, "padrao"),
    "minimax-m3": ("openrouter · minimax/minimax-m3", OR, "padrao"),
}

# LMArena WebDev (arena.ai/leaderboard/code/webdev), coletado 2026-09-29:
# humanos votam às cegas no site gerado que ficou melhor. Chave = nome do modelo.
ARENA_DATA = "2026-09-29"
FRONTEND = {"Claude Opus 5.5": 1820, "Claude Sonnet 5.5": 1699, "GPT-6 Sol": 1692,
            "Qwen3.8 Max": 1671, "Muse Spark 1.3": 1655, "GLM-5.3": 1623,
            "DeepSeek V4.1 Flash": 1620, "Gemini 3.7 Flash": 1593, "GPT-6 Luna": 1583,
            "DeepSeek V4 Pro 0813": 1582, "Gemini 3.8 Flash": 1581, "DeepSeek V4 Flash 0731": 1581,
            "Claude Opus 4.6": 1546, "Gemini 3.6 Flash": 1536, "Claude Sonnet 4.6": 1521,
            "GPT-5.6 Terra": 1520, "GPT-5.6 Luna": 1517, "Qwen3.7 Max": 1515,
            "Gemini 3.1 Pro Preview": 1446, "MiMo-V2.6-Pro": 1618, "GLM-5.3-Flash": 1614,
            "Hy3": 1509, "MiniMax-M3": 1482}

# (titulo da coluna, chave) -- todas em % de acerto, 0 a 100, maior é melhor.
PROVAS = [("Código", "codigo"), ("Raciocínio", "raciocinio"), ("Visão", "visao"),
          ("Planilha e relatório", "dados"), ("Documento longo", "longo"),
          ("Não inventa", "confia")]
LEGENDA = {
    "codigo": "tarefas reais de programação no terminal resolvidas (Terminal-Bench 4.0)",
    "raciocinio": "perguntas de nível especialista (Humanity's Last Exam)",
    "visao": "perguntas sobre imagens, gráficos e diagramas (MMMU-Pro)",
    "dados": "trabalho de escritório real: planilha, relatório, apresentação (GDPval)",
    "longo": "perguntas sobre documentos muito grandes (AA-LCR)",
    "confia": "quando não sabe, admite em vez de inventar (100 menos a taxa de alucinação do AA-Omniscience)",
}


def baixa():
    # 20/11/2026: a pagina mudou de formato (notas aninhadas em "scores", sem
    # slug por variante). O parser novo mora no capacidades.py.
    import capacidades
    return capacidades.normaliza(capacidades.baixa_bruto())


def _baixa_formato_antigo():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    h = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
    f = ""
    for m in re.finditer(r'self\.__next_f\.push\((\[.*?\])\)</script>', h, re.S):
        try:
            a = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if len(a) > 1 and isinstance(a[1], str):
            f += a[1]
    dec, por, pos = json.JSONDecoder(), {}, 0
    while (pos := f.find('"intelligenceIndexCostPerTask":', pos + 1)) > 0:
        d, k = 0, pos
        while k >= 0 and not (f[k] == "{" and d == 0):
            d += (f[k] == "}") - (f[k] == "{" and d > 0)
            k -= 1
        try:
            o, _ = dec.raw_decode(f, k)
        except (ValueError, json.JSONDecodeError):
            continue
        if o.get("slug") and o["slug"] not in por:
            por[o["slug"]] = {c: v for c, v in o.items()
                              if not isinstance(v, (dict, list)) and "Logo" not in c
                              and "Color" not in c and c != "logo"}
    if len(por) < 100:
        raise SystemExit(f"so {len(por)} modelos extraidos: a pagina mudou de formato")
    return list(por.values())


def num(v):
    return float(v) if isinstance(v, (int, float)) else None


def pct(v):
    v = num(v)
    return None if v is None else v * 100


def carrega():
    if "--offline" in sys.argv:
        arq = os.path.join(FONTES, sorted(x for x in os.listdir(FONTES)
                                          if x.startswith("aa_modelos_"))[-1])
        return json.load(open(arq, encoding="utf-8"))["modelos"], arq
    modelos = baixa()
    arq = os.path.join(FONTES, f"aa_modelos_{date.today()}.json")
    os.makedirs(FONTES, exist_ok=True)
    json.dump({"fonte": URL, "coletado": str(date.today()), "modelos": modelos},
              open(arq, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return modelos, arq


def main():
    modelos, arq = carrega()
    aa = {m["slug"]: m for m in modelos}

    # Uma linha por modelo: a nota e a do effort mais forte; o gasto vai do
    # effort mais barato ao mais caro, porque e o effort que decide a conta.
    grupos = {}
    for slug, (onde, paga, effort) in ACESSO.items():
        m = aa.get(slug)
        if not m or num(m.get("intelligenceIndex")) is None:
            continue
        nome = re.sub(r"\s*\(.*$", "", m.get("shortName") or slug)
        nh = num(m.get("omniscienceNonHallucination"))
        grupos.setdefault(nome, []).append({
            "onde": onde, "paga": paga, "effort": effort, "velho": bool(m.get("deprecated")),
            "geral": num(m["intelligenceIndex"]), "custo": num(m.get("intelligenceIndexCostPerTask")),
            "tps": num(m.get("medianOutputTokensPerSecond")),
            "codigo": pct(m.get("terminalBench40")), "raciocinio": pct(m.get("hle")),
            "visao": pct(m.get("mmmuPro")), "dados": pct(m.get("gdpvalNormalized")),
            "longo": pct(m.get("lcr")), "confia": None if nh is None else nh * 100,
        })
    linhas = []
    for nome, rs in grupos.items():
        top = max(rs, key=lambda r: r["geral"])
        custos = [r["custo"] for r in rs if r["custo"] is not None]
        linhas.append(dict(top, nome=nome,
                           onde=" / ".join(dict.fromkeys(r["onde"] for r in rs)),
                           paga=" ou ".join(dict.fromkeys(r["paga"] for r in rs)),
                           cmin=min(custos) if custos else None,
                           cmax=max(custos) if custos else None,
                           front=FRONTEND.get(nome)))
    linhas = sorted(linhas, key=lambda r: -r["geral"])[:25]

    # Ponto forte / fraco: posicao do modelo em cada prova, entre os 25.
    chaves = [k for _, k in PROVAS] + ["front"]
    nomes_prova = {k: t for t, k in PROVAS} | {"front": "Frontend"}
    for k in chaves:
        com = sorted((r for r in linhas if r[k] is not None), key=lambda r: -r[k])
        for i, r in enumerate(com):
            r.setdefault("pos", {})[k] = (i, len(com))
    melhor_col = {k: max((r[k] for r in linhas if r[k] is not None), default=None)
                  for k in chaves}

    def forte(r):
        ps = r.get("pos", {})
        bons = [nomes_prova[k] for k in chaves if k in ps and ps[k][0] < 3]
        fracos = [nomes_prova[k] for k in chaves
                  if k in ps and ps[k][1] >= 8 and ps[k][0] >= ps[k][1] - 3]
        return ", ".join(bons) or "—", ", ".join(fracos) or "—"

    def n(v):
        return "—" if v is None else f"{v:.0f}"

    def dinheiro(a, b):
        if a is None:
            return "sem medição"
        f = lambda x: "< 0,01" if x < 0.005 else f"{x:.2f}".replace(".", ",")
        return f"US$ {f(a)}" if abs(a - b) < 0.005 else f"US$ {f(a)} a {f(b)}"

    out = [
        "# Os 25 melhores LLMs que você tem",
        "",
        f"Atualizado em {date.today()} por `python ~/.claude/tools/tabela_llm.py`. "
        f"Fontes: [Artificial Analysis]({URL}) (provas com correção automática) e "
        f"[LMArena](https://arena.ai/leaderboard) (voto humano, {ARENA_DATA}). "
        "Só modelos que estão no painel e respondem. Fable e Astra fora.",
        "",
        "## 1. Visão geral",
        "",
        "**Nota** = média de 10 provas, de 0 a 100 (mesma escala para todos). "
        "**Gasto por tarefa** = quanto uma tarefa custa no preço de API, do effort mais baixo ao mais alto. "
        "Na assinatura você não paga em dinheiro, paga em cota: gasto alto = cota acaba rápido. "
        "Em modelo grátis, o número só mostra o tamanho do serviço.",
        "",
        "| # | Modelo | Nota | Como você paga | Gasto por tarefa | Bom para | Fraco em | No painel |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(linhas, 1):
        bom, fraco = forte(r)
        velho = " *(versão antiga)*" if r["velho"] else ""
        out.append(f"| {i} | **{r['nome']}**{velho} | **{n(r['geral'])}** | {r['paga']} | "
                   f"{dinheiro(r['cmin'], r['cmax'])} | {bom} | {fraco} | `{r['onde']}` |")
    out += [
        "",
        "## 2. Nota por tipo de tarefa",
        "",
        "Todas em % de acerto, de 0 a 100: maior é melhor. Em **negrito**, o melhor da coluna. "
        "— = a fonte não mediu.",
        "",
        *[f"- **{t}**: {LEGENDA[k]}." for t, k in PROVAS],
        "",
        "| # | Modelo | " + " | ".join(t for t, _ in PROVAS) + " | Velocidade (tokens/s) |",
        "|---|---|" + "---|" * (len(PROVAS) + 1),
    ]
    for i, r in enumerate(linhas, 1):
        cel = [f"**{n(r[k])}**" if r[k] is not None and r[k] == melhor_col[k] else n(r[k])
               for _, k in PROVAS]
        out.append(f"| {i} | {r['nome']} | " + " | ".join(cel) + f" | {n(r['tps'])} |")

    front = sorted((r for r in linhas if r["front"]), key=lambda r: -r["front"])
    out += [
        "",
        "## 3. Frontend e sites (voto humano)",
        "",
        "Pessoas comparam às cegas dois sites gerados e votam no melhor (LMArena WebDev). "
        "Diferença menor que 20 pontos é empate.",
        "",
        "| Posição | Modelo | Pontos |",
        "|---|---|---|",
        *[f"| {i} | {r['nome']} | {r['front']} |" for i, r in enumerate(front, 1)],
        "",
        f"Dados brutos: `esquadrao/fontes/{os.path.basename(arq)}`.",
        "",
    ]
    open(SAIDA, "w", encoding="utf-8").write("\n".join(out))
    print(f"{len(linhas)} modelos -> {SAIDA}")


if __name__ == "__main__":
    main()
