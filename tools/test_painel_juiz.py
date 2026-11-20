"""Neon puro disponivel como juiz: python test_painel_juiz.py"""
import json, os, tempfile
import painel

# config salva antes do agy4 existir: ele entra desligado, com o Neon puro,
# e nao com o modelo do pai (que hoje e o Orchestrator)
cfg = {"juiz": "claude", "provedores": {"agy": {"ativo": True, "modelo": "gemini-pro-orchestrator-neon",
                                                "effort": "medium"}}}
f = os.path.join(tempfile.gettempdir(), "teste_painel_juiz.json")
json.dump(cfg, open(f, "w", encoding="utf-8"))
painel.CONFIG = f
p = painel.config_atual()["provedores"]
assert p["agy4"] == {"ativo": False, "modelo": "gemini-4-neon", "effort": "high"}, p.get("agy4")
assert p["agy2"]["modelo"] == "gemini-pro-orchestrator-neon"   # clone comum segue o pai
print("ok")

# Rota do arquiteto: cada especialidade vai para o canal ligado de melhor nota
cap = {"ranking": {"codigo": ["gemini-4-neon@high", "claude-opus-5-5@max", "claude-opus-5-5"]},
       "notas": {"codigo": [["gemini-4-neon@high", 52.4], ["claude-opus-5-5@max", 53.0],
                            ["claude-opus-5-5", 40.0]]}}
provs = {"claude": {"ativo": True, "modelo": "claude-opus-5-5", "effort": "medium"},
         "agy4": {"ativo": True, "modelo": "gemini-4-neon", "effort": "high"},
         "agy2": {"ativo": False, "modelo": "gemini-4-neon", "effort": "xhigh"},
         "x": {"ativo": True, "modelo": "nada-medido", "effort": "low"}}
r = painel.rota_previa(provs, cap)
assert r["codigo"] == [["agy4", 52.4], ["claude", 40.0], ["x", None]], r
print("ok rota")
