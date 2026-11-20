"""Checagem do gerador de capacidades: python test_capacidades.py"""
import capacidades as cap
import esquadrao as esq

def v(slug, suf, idx, **sc):
    return {"release": {"slug": slug}, "name": f"{slug} ({suf})", "suffix": suf,
            "intelligenceIndex": idx, "omniscience": idx / 2, "scores": sc}

BRUTO = [
    v("gemini-4-neon", "Xhigh", 61, **{"terminalbench-4-0": .6, "frontiercode-2-0": .4, "hle-diamond": .7, "gdp-pdf": .5}),
    v("gemini-4-neon", "Low", 44, **{"terminalbench-4-0": .3, "frontiercode-2-0": .2, "hle-diamond": .5, "gdp-pdf": .3}),
    v("claude-opus-5-5", "Max, Default Fallback", 59, **{"terminalbench-4-0": .7, "frontiercode-2-0": .5, "hle-diamond": .6, "gdp-pdf": .6, "mmmu-pro": .87}),
    v("deepseek-v4-1-flash-1017", "Non-reasoning", 18, **{"terminalbench-4-0": .1}),
    v("modelo-que-voce-nao-tem", "High", 99, **{"terminalbench-4-0": .99}),
]

# slug no formato antigo: o effort mais forte fica sem sufixo
norm = {m["slug"]: m for m in cap.normaliza(BRUTO)}
assert "gemini-4-neon" in norm and "gemini-4-neon-low" in norm, sorted(norm)
assert norm["gemini-4-neon"]["effort"] == "xhigh" and norm["gemini-4-neon-low"]["effort"] == "low"
assert norm["claude-opus-5-5"]["effort"] == "max"
assert norm["deepseek-v4-1-flash-1017-non-reasoning"]["effort"] is None

r = cap.gera(BRUTO, arena={"frontend": {("gemini-4-neon", "high"): 1763,
                                        ("claude-opus-5-5", "max"): 1818}})
rank = r["ranking"]
# so entra modelo que o painel alcanca
assert not any("voce-nao-tem" in p for e in rank.values() for p in e)
# codigo = media de Terminal-Bench 4.0 e FrontierCode 2.0: Opus (60) > Neon xhigh (50)
assert rank["codigo"][:2] == ["claude-opus-5-5@max", "gemini-4-neon@xhigh"], rank["codigo"]
assert rank["raciocinio"][0] == "gemini-4-neon@xhigh"
assert rank["geral"][0] == "gemini-4-neon@xhigh"
assert rank["frontend"] == ["claude-opus-5-5@max", "claude-opus-5-5", "gemini-4-neon@high", "gemini-4-neon"], rank["frontend"]
# deepseek sem raciocinio: entra sem @ e com o nome do painel
assert "deepseek-v4.1-flash" in rank["codigo"]
# cada posicao guarda a nota que a justificou
assert r["notas"]["codigo"][0] == ["claude-opus-5-5@max", 60.0]

# agy serve Claude com o effort no nome: o effort do slot nao vale, vale o do nome
P = ["claude-opus-5-5@high", "claude-opus-5-5@medium"]
assert esq._posicao(P, "claude-opus-5-5-medium", "high") == 1
assert esq._posicao(P, "claude-opus-5-5", "high") == 0
# LMArena mede um effort so: outro effort do mesmo modelo entra pela menor nota
# medida dele, em vez de sumir do ranking
r2 = cap.gera([], arena={"texto": {("claude-opus-5-5", "high"): 1506,
                                   ("claude-opus-5-5", "max"): 1520}})
assert r2["ranking"]["texto"] == ["claude-opus-5-5@max", "claude-opus-5-5@high", "claude-opus-5-5"]
assert esq._posicao(r2["ranking"]["texto"], "claude-opus-5-5", "medium") == 2

# Orchestrator do agy: sem medicao propria, entra como o executor (Hydrogen high)
r3 = cap.gera([v("gemini-4-hydrogen", "High", 50, **{"hle-diamond": .5})], arena={})
assert "gemini-pro-orchestrator@" not in str(r3)
assert esq._posicao(r3["ranking"]["geral"], "gemini-pro-orchestrator-neon", "medium") == 1
print("ok")
