#!/usr/bin/env python
"""Executa um plano: cada tarefa vai para um provedor, respeitando dependencias.

  python tools/equipe.py plano.json
  python tools/equipe.py plano.json --saida C:/tmp/rodada1

Quem PENSA o plano e o Claude (o orquestrador da sessao). Quem EXECUTA sao os
provedores do llm.py. Esta ferramenta e so o motor: nao decide nada, so respeita
a ordem e roda em paralelo o que nao depende de mais nada.

Formato do plano — uma lista de tarefas:

  [
    {"id": "levanta",  "provedor": "agy",        "tarefa": "Levante os fatos sobre X"},
    {"id": "critica",  "provedor": "codex",      "tarefa": "Ache os erros",
     "depende": ["levanta"]},
    {"id": "redige",   "provedor": "openrouter", "tarefa": "Escreva a versao final",
     "depende": ["levanta", "critica"]}
  ]

Campos opcionais por tarefa: "modelo", "effort", "imagens" (lista de caminhos)
e "ferramentas": true — libera web/arquivo para o agente. Sem isso o agy devolve
resposta vazia quando a tarefa precisa da internet, sem avisar que travou.
A saida de cada dependencia entra no prompt da seguinte, rotulada pelo id.

Tarefas do mesmo nivel rodam ao mesmo tempo. Uma que falha nao derruba as
independentes; quem dependia dela e marcado PULADA, porque rodar sem a entrada
produziria resposta inventada — pior que nao produzir nada.
"""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import PROVEDORES, diz  # noqa: E402
from esquadrao import le_config  # noqa: E402

# Modelo e effort de cada provedor, vindos do painel (painel.py escreve o
# esquadrao.json). Tarefa que declara "modelo"/"effort" ganha do painel — o
# plano manda, o painel sugere. Sem nenhum dos dois, vale o padrao do llm.py.
# Chumbar modelo no plano e o erro que isto evita: o ajuste vira uma edicao de
# arquivo em vez de um clique.
AJUSTES = {}


def valida(plano):
    ids = [t["id"] for t in plano]
    if len(ids) != len(set(ids)):
        raise SystemExit("ha ids repetidos no plano")
    for t in plano:
        if t["provedor"] not in PROVEDORES:
            raise SystemExit(f"{t['id']}: provedor desconhecido {t['provedor']!r}")
        for d in t.get("depende", []):
            if d not in ids:
                raise SystemExit(f"{t['id']}: depende de {d!r}, que nao existe")
        for x in t.get("imagens", []):
            if not os.path.exists(x):
                raise SystemExit(f"{t['id']}: imagem nao existe: {x}")


def niveis(plano):
    """Agrupa as tarefas em ondas: tudo que ja tem as dependencias prontas roda
    junto. Detecta ciclo pela onda que nao consegue avancar."""
    pendentes, prontos, ondas = {t["id"]: t for t in plano}, set(), []
    while pendentes:
        onda = [t for t in pendentes.values()
                if all(d in prontos for d in t.get("depende", []))]
        if not onda:
            raise SystemExit(f"dependencia circular entre: {sorted(pendentes)}")
        ondas.append(onda)
        for t in onda:
            del pendentes[t["id"]]
            prontos.add(t["id"])
    return ondas


def monta(tarefa, saidas):
    partes = []
    for d in tarefa.get("depende", []):
        partes.append(f"### Resultado da etapa '{d}'\n{saidas[d]}")
    partes.append(f"### Sua tarefa\n{tarefa['tarefa']}")
    return "\n\n".join(partes)


def executa(tarefa, saidas):
    fn, padrao = PROVEDORES[tarefa["provedor"]]
    modelo, effort = AJUSTES.get(tarefa["provedor"], (padrao, "low"))
    t = time.time()
    try:
        r = fn(monta(tarefa, saidas), tarefa.get("imagens", []),
               tarefa.get("modelo") or modelo, tarefa.get("effort", effort),
               tarefa.get("ferramentas", False))
        return tarefa["id"], r.strip(), time.time() - t, True
    except BaseException as e:
        return tarefa["id"], f"[FALHOU] {e}", time.time() - t, False


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plano", help="arquivo .json")
    p.add_argument("--saida", help="pasta onde gravar um .md por tarefa")
    a = p.parse_args()

    plano = json.load(open(a.plano, encoding="utf-8"))
    valida(plano)
    if a.saida:
        os.makedirs(a.saida, exist_ok=True)

    # O painel manda quando a tarefa nao declara: cada provedor entra com o
    # modelo e o effort do dashboard, como o esquadrao ja faz.
    cfg = le_config() or {}
    provs = cfg.get("provedores", {})
    for n, (_fn, padrao) in PROVEDORES.items():
        c = provs.get(n, {})
        AJUSTES[n] = (c.get("modelo") or padrao, c.get("effort") or "low")
    if cfg:
        usados = sorted({t["provedor"] for t in plano})
        diz("config: " + ", ".join(f"{n}{AJUSTES[n]}" for n in usados))

    saidas, mortas = {}, set()
    for i, onda in enumerate(niveis(plano), 1):
        roda_agora = [t for t in onda
                      if not (set(t.get("depende", [])) & mortas)]
        for t in onda:
            if t not in roda_agora:
                diz(f"[{t['id']}] PULADA — dependencia falhou")
                mortas.add(t["id"])
        if not roda_agora:
            continue

        diz(f"\n{'=' * 70}\nONDA {i}: " +
            ", ".join(f"{t['id']}({t['provedor']})" for t in roda_agora) +
            f"\n{'=' * 70}")
        with ThreadPoolExecutor(max_workers=len(roda_agora)) as ex:
            res = [f.result() for f in
                   [ex.submit(executa, t, saidas) for t in roda_agora]]

        for tid, texto, seg, ok in res:
            saidas[tid] = texto
            if not ok:
                mortas.add(tid)
            diz(f"\n--- {tid}  ({seg:.0f}s) ---\n{texto}")
            if a.saida:
                with open(os.path.join(a.saida, f"{tid}.md"), "w",
                          encoding="utf-8") as f:
                    f.write(texto)

    if a.saida:
        diz(f"\ngravado em {a.saida}")
    if mortas:
        raise SystemExit(f"\ntarefas sem resultado: {', '.join(sorted(mortas))}")


if __name__ == "__main__":
    main()
