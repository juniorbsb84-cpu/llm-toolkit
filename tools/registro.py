#!/usr/bin/env python
"""Registro de lotes do esquadrao: um JSONL por lote, e o relatorio de saude.

  python registro.py [--dias 7]     relatorio por slot (o mesmo de `esquadrao relatorio`)

Cada lote vira ~/.claude/esquadrao/runs/AAAA-MM/<run_id>.jsonl, com tudo o que
passa pelo `_ev` do esquadrao (inicio, resposta, tentativa, voto, plano...),
inclusive o texto das respostas: o objetivo e poder reconstruir o lote depois.
Gravar nunca derruba o lote -- erro de disco aqui e engolido.
"""
import json, os, re, sys, threading, time, uuid
from datetime import datetime, timedelta

DIR = os.path.join(os.path.expanduser("~"), ".claude", "esquadrao", "runs")
_TRAVA = threading.Lock()
_ARQ = None
_RUN = None

# Ordem importa: a primeira que casar ganha. Medidas reais, nao suposicao:
# lock (opencode, 9 canais juntos, 12/11/2026), login (Claude CLI, 12/11/2026),
# modelo (agy recusando nome/effort), vazio (pergunta_a), cortado (llm.roda).
CLASSES = [
    ("lock", r"database is locked"),
    ("login", r"authenticat|login|unauthori[sz]ed|\b401\b"),
    # Limite de taxa vem antes da cota: o Gemini diz "quota will reset after
    # 2s" e "Quota exceeded per minute (429)", que voltam em segundos.
    ("limite", r"\b429\b|rate.?limit|too many requests|overload|capacity|\b50[23]\b|reset after|per minute"),
    # Cota esgotada (semanal, saldo) nao volta em segundos: nao e transitoria.
    ("cota", r"quota|spend.?cap|insufficient balance|\bbalance\b|weekly|semanal"),
    ("modelo", r"invalid model|not supported|unknown model|model not found|nao existe em|has no \"?xhigh|so roda"),
    ("vazio", r"resposta vazia"),
    ("cortado", r"agente cortado|teto de seguranca|timed? ?out"),
]
TRANSITORIAS = {"lock", "limite"}


def classifica(texto):
    t = (texto or "").lower()
    for nome, padrao in CLASSES:
        if re.search(padrao, t):
            return nome
    return "outro"


def transitorio(classe):
    return classe in TRANSITORIAS


def abre(modo, nomes, juiz, pergunta, origem):
    """Comeca um lote novo. Devolve o run_id."""
    global _ARQ, _RUN
    agora = datetime.now()
    rid = agora.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    pasta = os.path.join(DIR, agora.strftime("%Y-%m"))
    try:
        os.makedirs(pasta, exist_ok=True)
        with _TRAVA:
            _ARQ, _RUN = os.path.join(pasta, rid + ".jsonl"), rid
    except OSError:
        _ARQ, _RUN = None, None
        return rid
    escreve({"tipo": "lote", "modo": modo, "nomes": list(nomes), "juiz": juiz,
             "pergunta": pergunta, "origem": origem})
    return rid


def escreve(evento):
    with _TRAVA:
        if not _ARQ:
            return
        try:
            linha = dict(evento, ts=datetime.now().isoformat(timespec="milliseconds"),
                         run=_RUN)
            with open(_ARQ, "a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass


def fecha(status, **extra):
    global _ARQ, _RUN
    escreve(dict(extra, tipo="fim", status=status))
    with _TRAVA:
        _ARQ, _RUN = None, None


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def relatorio(dias=7):
    limite = datetime.now() - timedelta(days=dias)
    slots, lotes = {}, 0
    for raiz, _, arqs in os.walk(DIR):
        for a in arqs:
            if not a.endswith(".jsonl"):
                continue
            try:
                linhas = [json.loads(x) for x in open(os.path.join(raiz, a), encoding="utf-8")]
            except Exception:
                continue
            if not linhas or datetime.fromisoformat(linhas[0]["ts"]) < limite:
                continue
            # Ping de saude ("Responda OK") puxaria a latencia de todo slot para baixo.
            if linhas[0].get("modo") == "saude":
                continue
            lotes += 1
            for e in linhas:
                n = e.get("nome")
                if not n:
                    continue
                s = slots.setdefault(n, {"ok": 0, "falhas": {}, "repetidas": {},
                                         "seg": [], "modelo": ""})
                if e["tipo"] == "resposta":
                    s["modelo"] = e.get("modelo") or s["modelo"]
                    if e.get("ok"):
                        s["ok"] += 1
                        s["seg"].append(e.get("seg") or 0)
                    else:
                        c = e.get("classe") or classifica(e.get("texto"))
                        s["falhas"][c] = s["falhas"].get(c, 0) + 1
                elif e["tipo"] == "tentativa":
                    c = e.get("classe", "outro")
                    s["repetidas"][c] = s["repetidas"].get(c, 0) + 1
    if not slots:
        return f"nenhum lote registrado nos ultimos {dias} dias em {DIR}"
    fmt = lambda d: ", ".join(f"{k}:{v}" for k, v in sorted(d.items())) or "-"
    out = [f"{lotes} lotes nos ultimos {dias} dias  ({DIR})", "",
           f"{'slot':14}{'modelo':42}{'ok':>5}{'falha':>7}{'p50 s':>8}{'p95 s':>8}  falhas | repetidas"]
    for n in sorted(slots):
        s = slots[n]
        nf = sum(s["falhas"].values())
        out.append(f"{n:14}{s['modelo'][:41]:42}{s['ok']:>5}{nf:>7}"
                   f"{_pct(s['seg'], .5):>8.0f}{_pct(s['seg'], .95):>8.0f}  "
                   f"{fmt(s['falhas'])} | {fmt(s['repetidas'])}")
    return "\n".join(out)


if __name__ == "__main__":
    d = 7
    if "--dias" in sys.argv:
        d = int(sys.argv[sys.argv.index("--dias") + 1])
    print(relatorio(d))
