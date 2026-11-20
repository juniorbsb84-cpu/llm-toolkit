#!/usr/bin/env python
"""Gera o ranking que o modo arquiteto usa para escolher quem faz cada etapa.

  python ~/.claude/tools/capacidades.py            baixa a Artificial Analysis agora
  python ~/.claude/tools/capacidades.py --offline  usa o ultimo bruto salvo

Saidas:
  ~/.claude/esquadrao/capacidades.json     ranking + a nota que justifica cada posicao
  ~/.claude/esquadrao/fontes/aa_bruto_DATA.json, aa_modelos_DATA.json
  ~/.claude/docs/ESQUADRAO_CAPACIDADES.md  a mesma coisa, para ler

Fontes, por especialidade (nada de nota geral repetida em todas):
  codigo      media de Terminal-Bench 4.0 e FrontierCode 2.0      (Artificial Analysis)
  raciocinio  HLE Diamond                                          (Artificial Analysis)
  dados       GDP-PDF: trabalho de escritorio com documento real   (Artificial Analysis)
  pesquisa    AA-Omniscience: acerto factual menos alucinacao      (Artificial Analysis)
  geral       Intelligence Index                                   (Artificial Analysis)
  frontend    WebDev Arena, voto humano cego                       (LMArena)
  texto       Text Arena                                           (LMArena)
  visao       Vision Arena                                         (LMArena)

A Artificial Analysis roda as provas ela mesma, com o mesmo verificador para
todos e separado por effort: e a referencia para o que tem resposta certa. O
LMArena mede preferencia humana, que e o que conta em site, texto e imagem. O
LMArena nao tem API publica; os Elos abaixo sao copiados da pagina, com data.
"""
import json, os, re, shutil, sys, urllib.request
from datetime import date

CASA = os.path.expanduser("~/.claude")
FONTES = os.path.join(CASA, "esquadrao", "fontes")
SAIDA = os.path.join(CASA, "esquadrao", "capacidades.json")
DOC = os.path.join(CASA, "docs", "ESQUADRAO_CAPACIDADES.md")
URL = "https://artificialanalysis.ai/leaderboards/models?status=all"
NIVEIS = ("low", "medium", "high", "xhigh", "max")

# Modelo da Artificial Analysis -> nome como aparece no painel. So entra no
# ranking o que algum canal VISIVEL do painel alcanca (codex, alibaba e
# deepseek direto estao escondidos desde 12/11/2026).
ACESSIVEIS = {
    "gemini-4-neon": "gemini-4-neon",            # agy
    "gemini-4-hydrogen": "gemini-4-hydrogen",    # agy
    "gemini-4-argon": "gemini-4-argon",          # agy
    "gemini-3-8-flash": "gemini-3.8-flash",      # agy
    "claude-opus-5-5": "claude-opus-5-5",        # claude, agy (ate high)
    "claude-sonnet-5-5": "claude-sonnet-5-5",    # claude, agy (ate high)
    "gpt-oss-120b": "gpt-oss-120b",              # agy
    "deepseek-v4-1-flash-1017": "deepseek-v4.1-flash",  # commandcode, openrouter
    "mimo-v2-6-pro": "mimo-v2.6-pro",            # openrouter
    "mimo-v2-6-flash": "mimo-v2.6-flash",        # opencode free
    "glm-5-3-flash": "glm-5.3-flash",            # openrouter
    "minimax-m3": "minimax-m3",                  # openrouter
    "nemotron-3-ultra-550b-a55b": "nemotron-3-ultra",   # opencode/openrouter free
    "muse-spark-1-3": "muse-spark-1.3",          # commandcode, openrouter
}

ORQ_EXECUTOR = "gemini-4-hydrogen@high"

# LMArena (arena.ai), lido em 2026-11-20. Chave: (nome no painel, effort medido).
ARENA_DATA = "2026-11-20"
ARENA = {
    "frontend": {("claude-opus-5-5", "max"): 1818, ("claude-sonnet-5-5", "xhigh"): 1771,
                 ("gemini-4-neon", "high"): 1763, ("claude-sonnet-5-5", "high"): 1716,
                 ("gemini-4-hydrogen", "high"): 1701, ("deepseek-v4.1-flash", "max"): 1659,
                 ("mimo-v2.6-flash", None): 1638},
    "texto": {("gemini-4-argon", "high"): 1520, ("gemini-4-neon", "high"): 1513,
              ("claude-opus-5-5", "high"): 1506, ("muse-spark-1.3", "max"): 1498,
              ("gemini-3.8-flash", "high"): 1497, ("mimo-v2.6-pro", None): 1481,
              ("glm-5.3-flash", None): 1475, ("mimo-v2.6-flash", None): 1448},
    "visao": {("claude-opus-5-5", "high"): 1293, ("gemini-3.8-flash", "high"): 1293,
              ("glm-5.3-flash", None): 1274},
}


def _media(*xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) * 100 if xs else None


# Especialidade -> nota de 0 a 100 tirada de UM modelo normalizado.
PROVAS = {
    "codigo": lambda m: _media(m["scores"].get("terminalbench-4-0"),
                               m["scores"].get("frontiercode-2-0")),
    "raciocinio": lambda m: _media(m["scores"].get("hle-diamond")),
    "dados": lambda m: _media(m["scores"].get("gdp-pdf")),
    "pesquisa": lambda m: m.get("omniscience"),
    "geral": lambda m: m.get("intelligenceIndex"),
}


def baixa_bruto():
    """Lista crua de variantes de modelo da pagina (formato de 2026-11)."""
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
    while (pos := f.find('{"id":"', pos + 1)) > 0:
        try:
            o, _ = dec.raw_decode(f, pos)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(o, dict) and isinstance(o.get("release"), dict) and "name" in o:
            por[o["id"]] = o
    if len(por) < 100:
        raise SystemExit(f"so {len(por)} modelos extraidos: a pagina mudou de formato")
    return list(por.values())


def _effort(suffix):
    p = (suffix or "").split(",")[0].strip().lower()
    return p if p in NIVEIS else None


def normaliza(bruto):
    """Uma entrada por variante, com o slug no formato antigo (o effort mais
    forte sem sufixo: gemini-4-neon = Xhigh, gemini-4-neon-low = Low) e os
    campos que o tabela_llm.py e o painel ja liam."""
    por_release = {}
    for o in bruto:
        por_release.setdefault(o["release"]["slug"], []).append(o)
    out = []
    for rel, vs in por_release.items():
        com = [v for v in vs if _effort(v.get("suffix"))]
        topo = (None if any(not v.get("suffix") for v in vs)
                else max(com, key=lambda v: NIVEIS.index(_effort(v["suffix"])), default=None))
        for v in vs:
            ef = _effort(v.get("suffix"))
            if v is topo or not v.get("suffix"):
                slug = rel
            else:
                slug = f"{rel}-{ef or re.sub(r'[^a-z0-9]+', '-', v['suffix'].lower()).strip('-')}"
            sc = v.get("scores") or {}
            out.append({
                "slug": slug, "release": rel, "effort": ef, "name": v.get("name"),
                "shortName": v.get("shortName") or v.get("name"),
                "deprecated": v.get("deprecated"),
                "intelligenceIndex": v.get("intelligenceIndex"),
                "intelligenceIndexCostPerTask": v.get("intelligenceIndexCostPerTask"),
                "omniscience": v.get("omniscience"),
                "medianOutputTokensPerSecond": v.get("outputSpeed"),
                # nomes que o tabela_llm.py usa
                "terminalBench40": sc.get("terminalbench-4-0"),
                "hle": sc.get("hle-diamond"), "mmmuPro": sc.get("mmmu-pro"),
                "gdpvalNormalized": sc.get("gdp-pdf"),
                "lcr": sc.get("mlcr-aa", sc.get("artificial-analysis-long-context-reasoning")),
                "omniscienceNonHallucination": sc.get("omniscience-non-hallucination"),
                "scores": sc,
            })
    return out


def gera(bruto, arena=ARENA):
    notas = {}
    for m in normaliza(bruto):
        nome = ACESSIVEIS.get(m["release"])
        if not nome:
            continue
        chave = nome + (f"@{m['effort']}" if m["effort"] else "")
        for esp, f in PROVAS.items():
            n = f(m)
            if isinstance(n, (int, float)):
                d = notas.setdefault(esp, {})
                d[chave] = max(d.get(chave, n), n)
    for esp, tab in arena.items():
        d = notas.setdefault(esp, {})
        for (nome, ef), elo in tab.items():
            d[nome + (f"@{ef}" if ef else "")] = elo
        # O LMArena mede so um ou dois efforts por modelo. Outro effort entra
        # pela MENOR nota medida daquele modelo (estimativa conservadora), em
        # vez de cair no fim da fila como se o modelo nao existisse.
        for nome in {n for n, _ in tab}:
            if nome not in d:
                d[nome] = min(e for (n, _), e in tab.items() if n == nome)
    # Orchestrator do agy (planeja e entrega a execucao ao Hydrogen high) nao
    # tem medicao propria: herda a nota do executor. Recusa --effort, entao
    # entra sem @.
    for d in notas.values():
        if ORQ_EXECUTOR in d:
            d["gemini-pro-orchestrator"] = d[ORQ_EXECUTOR]
    lista = {e: sorted(([k, round(v, 1)] for k, v in d.items()), key=lambda x: -x[1])
             for e, d in notas.items()}
    return {
        "_nota": ("Ranking por especialidade, do melhor para o pior. Gerado por "
                  "tools/capacidades.py. 'modelo@nivel' so casa se o effort do slot "
                  "for >= nivel; sem @ casa em qualquer effort. Fora do ranking = fim da fila."),
        "atualizado_em": str(date.today()),
        "fontes": {"artificial_analysis": URL, "lmarena": f"arena.ai, lido em {ARENA_DATA}"},
        "ranking": {e: [k for k, _ in l] for e, l in lista.items()},
        "notas": lista,
    }


ROTULO = {"codigo": "Código (Terminal-Bench 4.0 + FrontierCode 2.0, %)",
          "frontend": "Frontend (WebDev Arena, Elo)", "visao": "Visão (Vision Arena, Elo)",
          "texto": "Texto (Text Arena, Elo)", "raciocinio": "Raciocínio (HLE Diamond, %)",
          "dados": "Dados/escritório (GDP-PDF, %)", "pesquisa": "Pesquisa factual (AA-Omniscience)",
          "geral": "Geral (Intelligence Index)"}


def escreve_doc(r):
    out = [f"# Onde cada LLM trabalha melhor", "",
           f"Gerado em {r['atualizado_em']} por `python ~/.claude/tools/capacidades.py`. "
           f"É o mesmo ranking que o modo `arquiteto` usa (`esquadrao/capacidades.json`).", "",
           "Fontes: [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models) "
           f"para o que tem resposta certa; [LMArena](https://arena.ai/leaderboard) "
           f"(voto humano, {ARENA_DATA}) para frontend, texto e visão. "
           "Só modelos que algum canal visível do painel alcança.", ""]
    for esp in ROTULO:
        l = r["notas"].get(esp, [])
        out += [f"## {ROTULO[esp]}", "", "| # | Modelo @ effort | Nota |", "|---|---|---|"]
        out += [f"| {i} | `{k}` | {v:g} |" for i, (k, v) in enumerate(l, 1)]
        out += ["" if l else "_sem medição para os modelos do painel_", ""]
    open(DOC, "w", encoding="utf-8").write("\n".join(out))


def main():
    hoje = str(date.today())
    os.makedirs(FONTES, exist_ok=True)
    if "--offline" in sys.argv:
        arq = sorted(x for x in os.listdir(FONTES) if x.startswith("aa_bruto_"))[-1]
        bruto = json.load(open(os.path.join(FONTES, arq), encoding="utf-8"))
    else:
        bruto = baixa_bruto()
        json.dump(bruto, open(os.path.join(FONTES, f"aa_bruto_{hoje}.json"), "w",
                              encoding="utf-8"), ensure_ascii=False)
    # Mesmo formato que o painel (notas ao lado do modelo) e o tabela_llm leem.
    json.dump({"fonte": URL, "coletado": hoje, "modelos": normaliza(bruto)},
              open(os.path.join(FONTES, f"aa_modelos_{hoje}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    r = gera(bruto)
    if os.path.exists(SAIDA):
        shutil.copy(SAIDA, SAIDA + ".anterior")
    json.dump(r, open(SAIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    escreve_doc(r)
    for e, l in r["ranking"].items():
        print(f"{e:11} {', '.join(l[:4])}")
    print(f"\n{SAIDA}\n{DOC}")


if __name__ == "__main__":
    main()
