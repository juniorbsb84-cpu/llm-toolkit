"""Checagem do registro, da classificacao de falha e do retry: python test_registro.py"""
import json, os, shutil, tempfile

import registro as reg
import esquadrao as esq

# --- classificacao de falha ------------------------------------------------
assert reg.classifica("Error: Unexpected error | database is locked") == "lock"
assert reg.classifica("429 Too Many Requests") == "limite"
assert reg.classifica("weekly quota exceeded") == "cota"
assert not reg.transitorio("cota")
assert reg.classifica("Failed to authenticate: this login reached its time limit") == "login"
assert reg.classifica('error: invalid model selection (--model "x")') == "modelo"
assert reg.classifica("resposta vazia de x (sem erro do provedor)") == "vazio"
assert reg.classifica("agente cortado: 20 min sem escrever") == "cortado"
assert reg.classifica("qualquer outra coisa") == "outro"
assert reg.transitorio("lock") and reg.transitorio("limite")
# limite de taxa com a palavra quota continua transitorio (formato do Gemini)
assert reg.classifica("You have exhausted your capacity on this model. Your quota will reset after 2s.") == "limite"
assert reg.classifica("RESOURCE_EXHAUSTED: Quota exceeded per minute (429)") == "limite"
assert reg.classifica("502 Bad Gateway from load balancer") == "limite"
assert reg.classifica("insufficient balance") == "cota"
assert not reg.transitorio("login") and not reg.transitorio("modelo")

# --- registro grava o lote inteiro ----------------------------------------
tmp = tempfile.mkdtemp()
reg.DIR = tmp
rid = reg.abre("todos", ["a", "b"], "a", "pergunta?", "teste")
reg.escreve({"tipo": "inicio", "nome": "a"})
reg.escreve({"tipo": "resposta", "nome": "a", "ok": True, "seg": 1.5})
reg.fecha("ok")
arqs = [os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs]
assert len(arqs) == 1 and rid in arqs[0]
linhas = [json.loads(x) for x in open(arqs[0], encoding="utf-8")]
assert [x["tipo"] for x in linhas] == ["lote", "inicio", "resposta", "fim"]
assert all("ts" in x and x["run"] == rid for x in linhas)
reg.escreve({"tipo": "depois"})            # lote fechado: nao grava nem quebra
assert len(open(arqs[0], encoding="utf-8").readlines()) == 4

# --- retry: erro transitorio repete, erro de uso nao -----------------------
chamadas = []

def instavel(texto, imagens, modelo, effort, ferramentas):
    chamadas.append(1)
    if len(chamadas) < 3:
        raise SystemExit("database is locked")
    return "OK"

def recusa(texto, imagens, modelo, effort, ferramentas):
    chamadas.append(1)
    raise SystemExit("error: invalid model selection")

esq.ESPERA_RETRY = (0, 0, 0)
esq.PROVEDORES["_t1"] = (instavel, "m")
esq.PROVEDORES["_t2"] = (recusa, "m")
esq.AJUSTES.clear()
reg.abre("todos", ["_t1"], "_t1", "p", "teste")
nome, resp, _ = esq.pergunta_a("_t1", "p", [], "low")
assert resp == "OK" and len(chamadas) == 3, (resp, chamadas)
chamadas.clear()
nome, resp, _ = esq.pergunta_a("_t2", "p", [], "low")
assert resp.startswith("[FALHOU]") and len(chamadas) == 1
chamadas.clear()
esq.FERRAMENTAS = True                      # agente com ferramenta: nunca repete
nome, resp, _ = esq.pergunta_a("_t1", "p", [], "low")
assert resp.startswith("[FALHOU]") and len(chamadas) == 1
esq.FERRAMENTAS = False
reg.fecha("ok")

# --- saude: pinga cada slot e classifica -----------------------------------
chamadas.clear()
esq.PROVEDORES["_t3"] = (lambda *a: "OK", "m")
r = {n: (ok, classe) for n, ok, seg, classe in esq.saude(["_t3", "_t2"])}
assert r == {"_t3": (True, ""), "_t2": (False, "modelo")}, r

# --- Parar durante a espera do retry nao abre chamada nova -------------------
chamadas.clear()
esq.ESPERA_RETRY = (0,)
parar = {"v": False}
def trava_e_para(*a):
    chamadas.append(1); parar["v"] = True
    raise SystemExit("database is locked")
esq.PROVEDORES["_t4"] = (trava_e_para, "m")
esq.PARAR = lambda: parar["v"]
try:
    esq._com_retry("_t4", "m", lambda: trava_e_para())
    assert False, "devia parar"
except esq.Parado:
    pass
assert len(chamadas) == 1, chamadas
esq.PARAR = None

# --- _formacao: config sem ativos nao cai no PADRAO --------------------------
try:
    esq._formacao({"provedores": {"agy": {"ativo": False}}})
    assert False, "devia recusar"
except SystemExit:
    pass

# --- lote de saude fica fora do relatorio ------------------------------------
reg.abre("saude", ["_t9"], None, "p", "teste")
reg.escreve({"tipo": "resposta", "nome": "_t9", "ok": True, "seg": 1})
reg.fecha("ok")

# --- relatorio agrega por slot --------------------------------------------
txt = reg.relatorio(dias=1)
assert "_t1" in txt and "_t2" in txt and "modelo" in txt and "lock" in txt, txt
assert "_t9" not in txt, txt
shutil.rmtree(tmp)
print("ok")
