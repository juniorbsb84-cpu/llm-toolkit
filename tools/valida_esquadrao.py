#!/usr/bin/env python
"""Valida o pipeline do esquadrao de ponta a ponta, gastando o minimo de token.

  python ~/.claude/tools/valida_esquadrao.py          nivel 0: zero token
  python ~/.claude/tools/valida_esquadrao.py --vivo   + ping de cada slot ativo e do juiz
  python ~/.claude/tools/valida_esquadrao.py --tudo   + os 4 modos, equipe e painel

Nivel 0: sintaxe, testes unitarios, config, rota, CLIs, atalhos, painel no ar,
Parar, tabela e hook. --vivo: uma pergunta de uma palavra por slot, effort low.
--tudo: cada modo com 2 slots baratos (agy2 = conta Google, codex = low) e
pergunta de uma linha. Sai com codigo 1 se algo falhar.
"""
import json, os, shutil, subprocess, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
PY = sys.executable
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FALHAS = []


def res(ok, nome, detalhe=""):
    print(f"{'ok   ' if ok else 'FALHA'} {nome}" + (f"  {detalhe}" if detalhe else ""), flush=True)
    if not ok:
        FALHAS.append(nome)


def roda(args, stdin=None):
    r = subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=AQUI, creationflags=SEM_JANELA)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def nivel0():
    print("== nivel 0 (sem token)")
    for f in ("llm.py", "esquadrao.py", "equipe.py", "painel.py", "modelos.py",
              "tabela_llm.py", "inicia_painel.py"):
        try:
            compile(open(os.path.join(AQUI, f), encoding="utf-8").read(), f, "exec")
            res(True, f"sintaxe {f}")
        except SyntaxError as e:
            res(False, f"sintaxe {f}", str(e))
    for t in ("test_esquadrao.py", "test_registro.py", "test_capacidades.py", "test_painel_juiz.py"):
        rc, out = roda([PY, t])
        res(rc == 0, f"testes {t}", out.strip()[-200:])

    import llm, esquadrao as esq
    cfg = esq.le_config() or {}
    provs = cfg.get("provedores", {})
    ativos = [n for n, c in provs.items() if c.get("ativo")]
    desconhecidos = [n for n in provs if n not in llm.PROVEDORES]
    res(bool(ativos) and not desconhecidos, "config esquadrao.json",
        f"{len(ativos)} ativos" + (f"; desconhecidos: {desconhecidos}" if desconhecidos else ""))
    juiz = cfg.get("juiz", "agy")
    cj = provs.get(juiz, {})
    res(juiz in llm.PROVEDORES, "juiz", f"{juiz} = {cj.get('modelo')} {cj.get('effort')}")
    proibidos = [n for n in ativos if any(x in (provs[n].get("modelo") or "")
                                          for x in ("fable", "astra"))]
    res(not proibidos, "sem Fable/Astra ativo", ", ".join(proibidos))

    esq.AJUSTES.clear()
    for n in ativos:
        esq.AJUSTES[n] = (provs[n].get("modelo") or llm.PROVEDORES[n][1], provs[n].get("effort") or "low")
    try:
        rota = esq.rota_final()
        res(all(rota[e] for e in esq.ROTA_PADRAO), "rota por capacidades.json",
            " ".join(f"{e}={rota[e][0]}" for e in rota))
    except Exception as e:
        res(False, "rota por capacidades.json", str(e))

    for cli in sorted({llm.base(n) for n in ativos} | {llm.base(juiz)}):
        exe = {"openrouter": "opencode", "alibaba": "opencode", "deepseek": "opencode"}.get(cli, cli)
        rc, out = roda([llm.bin_de(exe), "--version"])
        res(rc == 0, f"CLI {exe}", out.strip().splitlines()[-1][:60] if out.strip() else "")
    # Os atalhos moraram no npm; hoje ficam em ~/.local/bin. Vale qualquer um.
    pastas = [os.path.join(os.environ.get("APPDATA", ""), "npm"),
              os.path.expanduser("~/.local/bin")]
    for s in ("llm", "esquadrao", "equipe", "painel"):
        res(any(os.path.exists(os.path.join(d, s + ".cmd")) for d in pastas), f"atalho {s}")
    res(bool(os.environ.get("LLM_APIS")) or os.path.exists(os.path.expanduser("~/.llm-apis.txt")),
        "chaves (LLM_APIS)")

    try:
        html = urllib.request.urlopen("http://127.0.0.1:8777/", timeout=10).read().decode()
        res("__CONFIG__" not in html and "caixa" in html, "painel no ar (8777)")
    except Exception as e:
        res(False, "painel no ar (8777)", f"{e} -- rode: painel")

    # Parar: processo pendurado tem que morrer na hora (sem timeout, e a unica saida).
    box = {}
    t = threading.Thread(target=lambda: box.update(
        e=_captura(lambda: llm.roda([PY, "-c", "import time; time.sleep(60)"]))))
    t.start()
    time.sleep(1.5)
    llm.mata_vivos()
    t.join(15)
    res(not t.is_alive() and not llm.VIVOS, "Parar mata processo vivo")

    # Gera numa copia: rodar o tabela_llm de verdade sobrescreve o
    # docs/ESQUADRAO_TABELA_LLM.md, que hoje tem correcoes a mao (secao 0).
    rc, out = roda([PY, "-c", "import tempfile, os, sys; sys.argv=['x','--offline']; "
                    "import tabela_llm as t; t.SAIDA=os.path.join(tempfile.gettempdir(),"
                    "'tabela_valida.md'); t.main()"])
    res(rc == 0, "tabela_llm --offline (copia)", out.strip()[-80:])
    hook = os.path.join(CASA, "hooks", "esquadrao-config.py")
    if os.path.exists(hook):
        rc, out = roda([PY, hook], stdin="{}")
        res(rc == 0 and "esquadrao" in out.lower(), "hook SessionStart")


def _captura(fn):
    try:
        return fn()
    except BaseException as e:
        return e


def vivo():
    print("== vivo (1 palavra por slot, effort low)")
    import llm, esquadrao as esq
    cfg = esq.le_config() or {}
    provs = cfg.get("provedores", {})
    juiz = cfg.get("juiz", "agy")
    nomes = [n for n, c in provs.items() if c.get("ativo")]
    if juiz not in nomes:
        nomes.append(juiz)

    def ping(n):
        fn, padrao = llm.PROVEDORES[n]
        modelo = provs.get(n, {}).get("modelo") or padrao
        t = time.time()
        r = _captura(lambda: fn("Responda apenas a palavra OK.", [], modelo, "low", False))
        seg = time.time() - t
        ok = isinstance(r, str) and "ok" in r.lower()
        det = f"{modelo} {seg:.0f}s" + ("" if ok else f" -> {str(r).strip()[:120]}")
        return n, ok, det

    with ThreadPoolExecutor(max_workers=len(nomes)) as ex:
        for n, ok, det in ex.map(ping, nomes):
            res(ok, f"slot {n}" + (" (juiz)" if n == juiz else ""), det)


def tudo():
    print("== modos (agy2 + codex, effort low, 1 rodada)")
    base = [PY, "esquadrao.py", "--so", "agy2,codex", "-e", "low", "--rodadas", "1"]
    q = "Quanto e 7*8? Responda so o numero."
    casos = {
        "modo todos": (base + ["--modo", "todos", q], "RODADA 1"),
        "modo refino": (base + ["--modo", "refino", q], "CONSOLIDADO"),
        "modo consenso": (base + ["--modo", "consenso", q], "CONSENSO"),
        "modo arquiteto": (base + ["--modo", "arquiteto",
                                   "Diga o resultado de 7*8 e o de 9*9, em duas etapas."],
                           "ARQUITETO consolidado"),
    }
    plano = os.path.join(os.environ.get("TEMP", AQUI), "valida_plano.json")
    json.dump([{"id": "a", "provedor": "agy2", "tarefa": "Responda so: 7*8", "effort": "low"},
               {"id": "b", "provedor": "codex", "tarefa": "Some 1 ao numero recebido. So o numero.",
                "depende": ["a"], "effort": "low"}], open(plano, "w", encoding="utf-8"))
    casos["equipe (DAG)"] = ([PY, "equipe.py", plano], "--- b")

    def um(item):
        nome, (args, marca) = item
        t = time.time()
        rc, out = roda(args)
        ok = rc == 0 and marca in out and "[FALHOU]" not in out and "Traceback" not in out
        return nome, ok, f"{time.time() - t:.0f}s" + ("" if ok else " -> " + out.strip()[-200:])

    with ThreadPoolExecutor(max_workers=len(casos)) as ex:
        for nome, ok, det in ex.map(um, casos.items()):
            res(ok, nome, det)
    painel_rodar()


def painel_rodar():
    """Um lote pelo painel (/rodar + SSE /eventos), como a pagina faz."""
    corpo = json.dumps({"pergunta": "Responda apenas OK.", "modo": "todos", "rodadas": 1,
                        "juiz": "agy2",
                        "provedores": {"agy2": {"ativo": True, "modelo": "gemini-3.8-flash",
                                                "effort": "low"}}}).encode()
    t = time.time()
    try:
        req = urllib.request.Request("http://127.0.0.1:8777/rodar", data=corpo, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        viu = []
        with urllib.request.urlopen("http://127.0.0.1:8777/eventos", timeout=600) as s:
            for raw in s:
                linha = raw.decode("utf-8", "replace").strip()
                if not linha.startswith("data:"):
                    continue
                ev = json.loads(linha[5:])
                viu.append(ev.get("tipo"))
                if ev.get("tipo") == "fim":
                    ok = not ev.get("erro") and "resposta" in viu
                    res(ok, "painel /rodar + eventos", f"{time.time() - t:.0f}s {ev.get('erro') or ''}")
                    return
    except Exception as e:
        res(False, "painel /rodar + eventos", str(e)[:150])


if __name__ == "__main__":
    nivel0()
    if "--vivo" in sys.argv or "--tudo" in sys.argv:
        vivo()
    if "--tudo" in sys.argv:
        tudo()
    print(f"\n{'TUDO OK' if not FALHAS else str(len(FALHAS)) + ' FALHA(S): ' + ', '.join(FALHAS)}")
    sys.exit(1 if FALHAS else 0)
