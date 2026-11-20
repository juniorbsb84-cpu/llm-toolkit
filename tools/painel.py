#!/usr/bin/env python
"""Painel do esquadrao: escolhe provedores e effort no navegador.

  python ~/.claude/tools/painel.py        (abre http://127.0.0.1:8777)

Salvar grava ~/.claude/esquadrao.json, que o esquadrao.py le como padrao e o
Claude le para saber a configuracao vigente. So isso: a pagina nao dispara
nada, so escreve a config.

Este arquivo e lido UMA VEZ, quando o processo sobe: editar aqui nao muda a
pagina de quem ja esta no ar, e recarregar a aba nao resolve. Use
`painel --reinicia` (ou deixe o hook PostToolUse do settings.json fazer isso
sozinho -- ver hooks/reinicia-painel.py).
"""
import http.server, json, os, re, queue, socketserver, sys, threading, time, webbrowser
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import PROVEDORES, SLOT_FIXO, base  # noqa: E402
import llm  # noqa: E402
import registro  # noqa: E402
from esquadrao import PADRAO, CONFIG  # noqa: E402
import esquadrao as esq  # noqa: E402
import modelos as cat  # noqa: E402

PORTA = 8777
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Quem aceita "xhigh" de fato — cada um testado a mao em 12/09/2026 mandando um
# valor invalido e lendo a recusa, e depois uma chamada real no xhigh:
#   codex      erro lista 'high', 'xhigh', 'max'
#   commandcode erro diz "Supported: low, medium, high, xhigh"
#   claude     `claude --help` lista low|medium|high|xhigh|max; valor invalido
#              so vira Warning no stderr, nunca erro (entrou em 13/09/2026)
# Fora fica quem nao da para PROVAR que honra o degrau -- oferecer no painel um
# esforco que o outro lado engole calado e mentir para quem escolhe:
#   agy       depende do modelo, nao do provedor: em 20/11/2026 so o Gemini 4
#             Neon e o Hydrogen aceitam xhigh (o Argon para no high). A pagina
#             decide pelo modelo escolhido (XHIGH_AGY la).
#   opencode  a CLI aceita qualquer --variant calada (testei "banana", rc=0)
#   openrouter/alibaba/deepseek  sairam em 19/09/2026 ao trocarem de API crua
#             para via_opencode: a chamada real que provava o xhigh no HTTP
#             direto ("banana" dava 400 com o enum) nao vale mais, porque quem
#             recebe o effort agora e o mesmo --variant do opencode acima.
#             (o deepseek ja tinha saido em 16/09/2026, por outra razao: o
#              servidor dele mapeava xhigh para high internamente.)
XHIGH = ["codex", "commandcode", "claude"]
# "max": so quem lista o degrau na propria CLI (`claude --help`, erro do codex).
MAX = ["codex", "claude"]

# Provedores sem assinatura (12/11/2026): somem do painel e nunca ficam ativos.
# O llm.py continua sabendo chama-los; voltar = tirar daqui.
FORA_DO_PAINEL = {"codex", "alibaba", "deepseek"}

# CLI = chamada por subprocesso de um cliente instalado; API = HTTP direto.
# Desde 19/09/2026 sao todos CLI: openrouter, alibaba e deepseek passaram a
# chamar o agente do opencode (ver llm.py) e nao existe mais HTTP direto no
# esquadrao. A categoria "API" fica documentada aqui para o dia em que entrar
# um provedor sem cliente instalado.
TIPO = {"agy": "CLI", "codex": "CLI", "commandcode": "CLI", "opencode": "CLI",
        "claude": "CLI", "openrouter": "CLI", "alibaba": "CLI",
        "deepseek": "CLI"}


# O que o llm.py faz de fato com o effort de cada provedor:
#   sempre — vira flag/campo na chamada: codex e commandcode (--effort),
#            opencode/openrouter (--variant), deepseek (reasoning_effort),
#            alibaba (enable_thinking + reasoning_effort)
#   gemini — vai na flag --effort e so a familia gemini- aceita (agy);
#            claude-* e gpt-oss-* ja trazem o modo no nome
#   nunca  — o wrapper recebe e descarta. Ninguem hoje: a categoria fica porque
#            ate 11/09/2026 deepseek, alibaba e opencode estavam aqui por
#            suposicao, e o painel mostrava um ajuste que nao acontecia.
EFEITO = {"codex": "sempre", "commandcode": "sempre", "openrouter": "sempre",
          "opencode": "sempre", "deepseek": "sempre", "alibaba": "sempre",
          "claude": "sempre", "agy": "gemini"}


# Clone (openrouter2, openrouter3) nao tem entrada propria em lugar nenhum:
# herda tudo do provedor-base. Uma funcao so, usada por TIPO, EFEITO, XHIGH e
# pelo menu de modelos — assim acrescentar um clone no llm.py basta.
def por_base(mapa):
    return {n: mapa[base(n)] for n in PROVEDORES if base(n) in mapa}


def catalogo():
    """Modelos de cada provedor, do cache de modelos.py. Sem cache ainda, cai
    para o padrao do llm.py: menu curto e certo vale mais que menu vazio.
    O clone mostra o mesmo menu do pai — o que muda entre eles e o modelo
    escolhido, nao o que esta disponivel."""
    d = cat.le()
    if not d:
        return {n: [PROVEDORES[n][1]] for n in PROVEDORES}
    lidos = {n: (v["modelos"] or [PROVEDORES[n][1]])
             for n, v in d["provedores"].items()}
    return {n: lidos.get(base(n), [PROVEDORES[n][1]]) for n in PROVEDORES}


def notas():
    """Nota geral e custo por tarefa (Artificial Analysis) de cada modelo que o
    painel oferece, para a linha mostrar o que a escolha rende e quanto gasta.
    Vem do ultimo JSON que tools/tabela_llm.py salvou; sem ele, lista vazia e a
    linha diz "sem medicao". Entrada: [bases, nome, effort, nota, custo]."""
    try:
        import tabela_llm as tb
        salvos = sorted(x for x in os.listdir(tb.FONTES) if x.startswith("aa_modelos_"))
        aa = {m["slug"]: m for m in json.load(open(
            os.path.join(tb.FONTES, salvos[-1]), encoding="utf-8"))["modelos"]}
    except Exception:
        return []
    out = []
    for slug, (onde, _paga, effort) in tb.ACESSO.items():
        m = aa.get(slug)
        if not m or m.get("intelligenceIndex") is None:
            continue
        bases, _, nome = onde.partition(" · ")
        custo = m.get("intelligenceIndexCostPerTask")
        out.append([bases.split("/"), nome.split(" ")[0], effort,
                    round(m["intelligenceIndex"]),
                    round(custo, 2) if isinstance(custo, (int, float)) else None])
    return out


def rota_previa(provs, cap=None):
    """Para onde o modo arquiteto manda cada especialidade com estes canais
    ligados: os 3 primeiros e a nota de cada um. Usa o mesmo _posicao do
    esquadrao.py, para a previa nunca divergir da rota real."""
    if cap is None:
        try:
            cap = json.load(open(esq.CAPACIDADES, encoding="utf-8"))
        except Exception:
            return {}
    ativos = {n: (c.get("modelo") or "", c.get("effort") or "low")
              for n, c in provs.items() if c.get("ativo")}
    out = {}
    for e, padroes in cap.get("ranking", {}).items():
        notas = cap.get("notas", {}).get(e, [])
        pos = {n: esq._posicao(padroes, *ativos[n]) for n in ativos}
        ordem = sorted(ativos, key=lambda n: pos[n])[:3]
        out[e] = [[n, notas[pos[n]][1] if pos[n] < len(notas) else None] for n in ordem]
    return out


# --- modo gratis ----------------------------------------------------------
# Quem conta como gratis: conta Google (agy, qualquer modelo), os "-free" da
# Zen, os ":free" da OpenRouter e o space-bunny do commandcode (unico gratis que
# a trava de cota do llm.py deixa passar).
def _gratis_candidatos():
    cat_ = catalogo()
    out = [("agy", m) for m in cat_.get("agy", [])]
    out += [("opencode", m) for m in cat_.get("opencode", []) if m.endswith("-free")]
    out += [("openrouter", m) for m in cat_.get("openrouter", []) if m.endswith(":free")]
    if "stealth/space-bunny-alpha" in (cat.le() or {}).get("gratis", []):
        out.append(("commandcode", "stealth/space-bunny-alpha"))
    return out


def gratis():
    """Liga so modelos gratis que respondem AGORA e monta o lote no modo
    arquiteto: o juiz divide a tarefa e a rota de capacidades.json manda cada
    etapa para o gratis mais apto. Se houver menos gratis que etapas, o mesmo
    canal pega mais de uma -- e o que a rota ja faz sozinha.

    Nao salva nada: devolve a configuracao para a pagina aplicar na tela."""
    from concurrent.futures import ThreadPoolExecutor, wait
    cands = _gratis_candidatos()
    nota = {}
    for bases, nome, _e, n, _c in notas():
        for b in bases:
            nota[(b, nome)] = max(n, nota.get((b, nome), 0))
    def pontua(b, m):
        return max([v for (bb, nm), v in nota.items() if bb == b and nm in m] or [0])

    def ping(bm):
        b, m = bm
        t = time.time()
        try:
            r = PROVEDORES[b][0]("Responda apenas a palavra OK.", [], m, "low", False)
            return "ok" in r.lower(), time.time() - t
        except BaseException:
            return False, time.time() - t
    ex = ThreadPoolExecutor(max_workers=max(1, len(cands)))
    futs = {ex.submit(ping, c): c for c in cands}
    # Gratis que nao responde em 90 s esta com fila: para o lote, nao serve.
    feitos, pendentes = wait(futs, timeout=90)
    if pendentes:
        llm.mata_vivos()
    ex.shutdown(wait=False, cancel_futures=True)
    relato, vivos = [], []
    for f, (b, m) in futs.items():
        ok, seg = f.result() if f in feitos else (False, 90.0)
        relato.append({"provedor": b, "modelo": m, "ok": ok, "seg": round(seg)})
        if ok:
            vivos.append((pontua(b, m), b, m))
    vivos.sort(reverse=True)
    # Distribui nos slots de cada provedor (agy, agy2, agy3...), melhores primeiro.
    livres = {b: [n for n in sorted(PROVEDORES, key=lambda x: (len(x), x)) if base(n) == b]
              for b in {c[0] for c in cands}}
    provs = {n: {"ativo": False, "modelo": c.get("modelo", ""), "effort": c.get("effort", "low")}
             for n, c in config_atual()["provedores"].items()}
    usados, familias = [], set()
    for _n, b, m in vivos:
        # Versao velha da mesma familia (gemini-3.7-flash atras do 3.8) nunca
        # ganha rota -- a rota pega o primeiro apto --, e ocuparia o slot de um
        # modelo diferente que ganharia (o Opus 4.6 do agy, por exemplo).
        fam = (b, re.sub(r"[\d.]+", "", m))
        if fam in familias:
            continue
        familias.add(fam)
        if livres.get(b):
            slot = livres[b].pop(0)
            # Gratis nao cobra esforco: sempre high (flash em low rende pouco).
            provs[slot] = {"ativo": True, "modelo": m, "effort": "high"}
            usados.append(slot)
    # Juiz: o melhor do agy (conta Google, sem fila). Medido em 29/09/2026: o
    # Spark gratis, de nota maior, voltou vazio duas vezes seguidas justo na
    # divisao da tarefa -- juiz que falha trava o lote inteiro.
    do_agy = [u for u in usados if base(u) == "agy"]
    juiz = (do_agy or usados or ["agy"])[0]
    return {"provedores": provs, "modo": "arquiteto", "juiz": juiz, "relato": relato}


def quando():
    d = cat.le()
    return d["atualizado_em"] if d else "nunca"


def config_atual():
    if os.path.exists(CONFIG):
        try:
            cfg = json.load(open(CONFIG, encoding="utf-8"))
            # Config gravada antes de um provedor existir nao tem a linha dele.
            # Em vez de sumir do painel, ele entra desligado no padrao do
            # llm.py — e o clone nasce com o modelo do pai.
            provs = cfg.setdefault("provedores", {})
            for n in PROVEDORES:
                if n in SLOT_FIXO and n not in provs:
                    provs[n] = {"ativo": False, "modelo": SLOT_FIXO[n][0],
                                "effort": SLOT_FIXO[n][1]}
                if n not in provs:
                    pai = provs.get(base(n), {})
                    provs[n] = {"ativo": False,
                                "modelo": pai.get("modelo") or PROVEDORES[n][1],
                                "effort": pai.get("effort") or "low"}
            return cfg
        except Exception:
            pass
    return {"provedores": {n: {"ativo": n in PADRAO, "effort": "low",
                               "modelo": PROVEDORES[n][1]}
                           for n in PROVEDORES},
            "modo": "todos", "juiz": "agy", "rodadas": 4}


AQUI = os.path.dirname(os.path.abspath(__file__))
PAGINA = os.path.join(AQUI, "painel.html")


# --- executar o esquadrao pela pagina ---------------------------------------
#
# A pagina nao reimplementa nada: ela chama as MESMAS funcoes do esquadrao.py
# que a linha de comando chama, com o gancho EVENTO ligado. Assim os quatro
# modos valem aqui sem uma segunda copia da logica para sair de sincronia.
#
# Um trabalho por vez, de proposito. Dois lotes simultaneos disputariam os
# mesmos slots (o mesmo CLI, a mesma sessao, a mesma cota) e o resultado seria
# pior nos dois; alem disso AJUSTES e global no esquadrao.py.
# O lote atual, ou None. Cada lote e um dicionario PROPRIO: quem mexe nele
# carrega a referencia, nunca le este global de novo. Sem isso o lote que
# termina pode fechar a fila do lote que acabou de comecar (ja aconteceu).
TRABALHO = None
TRAVA = threading.Lock()


def _empurra(t, ev):
    """Publica um evento NO LOTE t -- e so nele, mesmo que outro ja seja o atual."""
    ev["t"] = round(time.time() - t["inicio"], 1)
    with t["trava"]:
        t["n"] += 1
        ev["i"] = t["n"]                 # id do evento, para o SSE retomar
        t["historico"].append(ev)
    t["fila"].put(ev)


def _roda_lote(t, pedido):
    """Executa o lote e fecha a fila no fim, deu certo ou nao."""
    provs = pedido.get("provedores") or {}
    nomes = [n for n in sorted(provs) if provs[n].get("ativo") and n in PROVEDORES]
    juiz = pedido.get("juiz") or "agy"
    modo = pedido.get("modo") or "todos"
    try:
        if not nomes:
            raise SystemExit("nenhum provedor no lote")
        if juiz not in PROVEDORES:
            raise SystemExit(f"juiz desconhecido: {juiz}")
        # O que vale e o que esta NA TELA, nao o que esta salvo em disco: o
        # usuario pode ter mexido num modelo sem salvar, e rodar com outra
        # coisa seria mentir sobre o que ele pediu.
        esq.AJUSTES = {n: (provs[n].get("modelo") or PROVEDORES[n][1],
                           provs[n].get("effort") or "low")
                       for n in nomes}
        if juiz not in esq.AJUSTES:
            cj = provs.get(juiz, {})
            esq.AJUSTES[juiz] = (cj.get("modelo") or PROVEDORES[juiz][1],
                                 cj.get("effort") or "low")
        _empurra(t, {"tipo": "comecou", "nomes": nomes, "modo": modo,
                     "juiz": juiz})
        registro.abre(modo, nomes, juiz, pedido["pergunta"], "painel")
        rodadas = max(1, int(pedido.get("rodadas") or 3))
        texto = pedido["pergunta"]
        if modo == "consenso":
            esq.consenso(nomes, texto, [], None, juiz, rodadas)
        elif modo == "arquiteto":
            esq.arquiteto(nomes, texto, [], None, juiz, rodadas)
        elif modo == "refino":
            esq.refino(nomes, texto, [], None, juiz, rodadas)
        else:
            esq.todos(nomes, texto, [], None, rodadas)
        _empurra(t, {"tipo": "fim"})
        registro.fecha("ok")
    except esq.Parado:
        _empurra(t, {"tipo": "fim", "parado": True})
        registro.fecha("parado")
    except BaseException as e:
        _empurra(t, {"tipo": "fim", "erro": str(e)[:400]})
        registro.fecha("erro", erro=str(e)[:400])
    finally:
        t["vivo"] = False
        t["fila"].put(None)              # sentinela: fecha o SSE deste lote
        # So desmonta os ganchos se ninguem tiver assumido no meio tempo --
        # devolver o `diz` de um lote ja terminado por cima de um lote vivo
        # apagaria o espelho DELE.
        with TRAVA:
            if TRABALHO is t:
                esq.EVENTO = None
                esq.PARAR = None
                esq.diz = t["diz_original"]
                if t.get("diz_equipe") is not None:
                    try:
                        import equipe
                        equipe.diz = t["diz_equipe"]
                    except Exception:
                        pass


def comeca(pedido):
    global TRABALHO
    with TRAVA:
        if TRABALHO is not None and TRABALHO.get("vivo"):
            return None
        t = {"vivo": True, "parar": False, "fila": queue.Queue(),
             "historico": [], "n": 0, "trava": threading.Lock(),
             "inicio": time.time(), "diz_original": esq.diz,
             "diz_equipe": None,
             "id": datetime.now().strftime("%H%M%S")}

        # O texto que o esquadrao imprime (cabecalho de rodada, plano do
        # arquiteto, sintese do consenso) tambem e resultado: sem capturar o
        # `diz` ele so existiria no console invisivel do servidor.
        def diz_espelho(msg):
            _empurra(t, {"tipo": "log", "texto": msg})
            t["diz_original"](msg)

        esq.diz = diz_espelho
        # equipe.py importou `diz` para o proprio namespace; o modo arquiteto
        # passa por ele, entao o espelho tem de valer la tambem.
        try:
            import equipe
            t["diz_equipe"] = equipe.diz
            equipe.diz = diz_espelho
        except Exception:
            pass
        esq.EVENTO = lambda ev: _empurra(t, ev)
        esq.PARAR = lambda: t["parar"]
        TRABALHO = t
        threading.Thread(target=_roda_lote, args=(t, pedido), daemon=True).start()
        return t["id"]


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, corpo, tipo="text/html; charset=utf-8"):
        b = corpo.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        # Sem isto o navegador reexibe a pagina anterior (com a config velha)
        # ao voltar, e parece que o Salvar nao pegou.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/eventos":
            return self._sse()
        if self.path not in ("/", "/index.html"):
            self.send_error(404); return
        html = (open(PAGINA, encoding="utf-8").read().replace("__EFFORTS__", json.dumps(EFFORTS))
                .replace("__XHIGH__", json.dumps(
                          [n for n in PROVEDORES if base(n) in XHIGH]))
                .replace("__MAX__", json.dumps([n for n in PROVEDORES if base(n) in MAX]))
                      .replace("__CONFIG__", json.dumps(config_atual()))
                      .replace("__NOMES__", json.dumps(sorted(n for n in PROVEDORES
                                                         if base(n) not in FORA_DO_PAINEL)))
                      .replace("__MODELOS__", json.dumps(catalogo()))
                      .replace("__GRATIS__", json.dumps((cat.le() or {}).get("gratis", [])))
                      .replace("__QUANDO__", json.dumps(quando()))
                      .replace("__RODANDO__", json.dumps(
                          bool(TRABALHO and TRABALHO.get("vivo"))))
                      .replace("__TIPO__", json.dumps(por_base(TIPO)))
                      .replace("__EFEITO__", json.dumps(por_base(EFEITO)))
                      .replace("__NOTAS__", json.dumps(notas())))
        self._send(html)

    def do_POST(self):
        # Pedido malformado (JSON quebrado, Content-Length ausente) derrubava a
        # conexao sem resposta nenhuma e o fetch da pagina falhava opaco — o
        # usuario via "erro ao salvar" sem motivo. Erro de pedido vira 400 e o
        # servidor continua de pe.
        try:
            return self._post()
        except Exception as e:
            try:
                self.send_error(400, f"pedido invalido: {e}"[:200])
            except Exception:
                pass

    def _sse(self):
        """Fluxo de eventos do lote em andamento.

        Server-sent events e nao WebSocket: o trafego aqui e de mao unica
        (servidor -> pagina) e o SSE reconecta sozinho, sem dependencia nova.
        Quem chega no meio recebe primeiro o historico, entao recarregar a
        pagina no meio de um lote nao perde o que ja saiu.
        """
        t = TRABALHO
        # Reconexao: o navegador devolve o id do ultimo evento que recebeu, e
        # so o que veio depois dele e reenviado. Sem isso uma queda de rede no
        # meio do lote reexecutava o historico inteiro e a pagina duplicava
        # todos os cartoes.
        try:
            visto = int(self.headers.get("Last-Event-ID") or 0)
        except ValueError:
            visto = 0

        # Nada para acompanhar: 204 e a unica resposta que faz o EventSource
        # PARAR de reconectar (ele volta sozinho em qualquer outra). Sem isso o
        # navegador batia aqui em laco, e cada conexao nova ainda recebia o
        # historico do lote anterior como se fosse ao vivo.
        # Cliente NOVO (sem Last-Event-ID) nunca recebe lote encerrado: para
        # ele aquilo nao esta acontecendo. So quem estava acompanhando -- e
        # portanto traz o id do ultimo evento que viu -- tem direito a cauda
        # que perdeu.
        parado = t is None or not t["vivo"]
        if parado and (t is None or not visto or visto >= t["n"]):
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        fila = t["fila"]
        try:
            for ev in list(t["historico"]):
                if ev.get("i", 0) > visto:
                    self._diz_sse(ev)
                    visto = ev.get("i", visto)
            if parado:
                # Terminou enquanto o cliente estava fora: ele acabou de
                # receber a cauda (o "fim" inclusive). Nao ha o que esperar.
                return
            while True:
                try:
                    ev = fila.get(timeout=15)
                except queue.Empty:
                    # Comentario SSE: segura a conexao de pe em lote longo sem
                    # virar evento para a pagina tratar.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if ev is None:
                    break
                # O historico ja foi despejado acima; o que a fila repetir
                # (evento gerado entre um e outro) sai fora pelo id.
                if ev.get("i", 0) > visto:
                    self._diz_sse(ev)
                    visto = ev.get("i", visto)
        except (BrokenPipeError, ConnectionResetError):
            # Aba fechada no meio do lote: o trabalho continua, o historico
            # guarda tudo e a proxima conexao recebe o que perdeu.
            pass

    def _diz_sse(self, ev):
        corpo = b""
        if ev.get("i"):
            corpo += b"id: " + str(ev["i"]).encode() + b"\n"
        corpo += (b"data: " + json.dumps(ev, ensure_ascii=False).encode("utf-8")
                  + b"\n\n")
        self.wfile.write(corpo)
        self.wfile.flush()

    def _post(self):
        if self.path == "/rodar":
            pedido = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if not (pedido.get("pergunta") or "").strip():
                self.send_error(400, "sem pergunta"); return
            # Recusa na porta em vez de abrir um lote que so morreria no
            # primeiro passo: erro de pedido e 400, nao "trabalho que falhou".
            provs = pedido.get("provedores") or {}
            if not [n for n in provs
                    if provs[n].get("ativo") and n in PROVEDORES]:
                self.send_error(400, "nenhum provedor no lote"); return
            if (pedido.get("juiz") or "agy") not in PROVEDORES:
                self.send_error(400, "juiz desconhecido"); return
            ident = comeca(pedido)
            if ident is None:
                self.send_error(409, "ja ha um lote em andamento"); return
            print(f"lote {ident}: {pedido.get('modo')} para "
                  f"{sum(1 for c in pedido.get('provedores', {}).values() if c.get('ativo'))} "
                  f"provedores")
            self._send(json.dumps({"id": ident}), "application/json")
            return
        if self.path == "/rota":
            pedido = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._send(json.dumps(rota_previa(pedido.get("provedores") or {})),
                       "application/json")
            return
        if self.path == "/gratis":
            if TRABALHO is not None and TRABALHO.get("vivo"):
                self.send_error(409, "ha um lote em andamento"); return
            self._send(json.dumps(gratis()), "application/json")
            return
        if self.path == "/parar":
            t = TRABALHO
            if t is not None:
                t["parar"] = True
                # Sem timeout, um CLI pendurado so sai daqui: mata as arvores vivas.
                llm.mata_vivos()
            self._send("{}", "application/json")
            return
        if self.path == "/modelos":
            # Varredura ao vivo: leva dezenas de segundos porque abre CLI de
            # verdade. A pagina recarrega sozinha quando volta.
            cat.atualiza()
            print("catalogo de modelos atualizado")
            self._send("{}", "application/json")
            return
        if self.path != "/salvar":
            self.send_error(404); return
        dados = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        dados["salvo_em"] = datetime.now().isoformat(timespec="seconds")
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        ativos = [n for n, c in dados["provedores"].items() if c["ativo"]]
        print(f"salvo: {', '.join(ativos) or '(nenhum)'}  modo={dados['modo']}")
        self._send("ok", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    # NAO ligar allow_reuse_address aqui: no Windows o SO_REUSEADDR deixa um
    # segundo processo ligar na MESMA porta em vez de dar erro, e os dois
    # passam a atender pedidos alternados — um painel velho servindo pagina
    # velha sem nenhum aviso. Melhor falhar alto.
    # Multithread agora e obrigatorio: o fluxo de eventos de um lote fica
    # minutos com a conexao aberta, e num servidor de uma thread so isso
    # congelaria a pagina inteira -- nem salvar, nem parar o lote passariam.
    class Servidor(socketserver.ThreadingTCPServer):
        daemon_threads = True
        # Continua DESLIGADO (ver a nota abaixo): o ganho e a thread por
        # pedido, nao reaproveitar porta ocupada.
        allow_reuse_address = False

    try:
        srv = Servidor(("127.0.0.1", PORTA), H)
    except OSError as e:
        raise SystemExit(f"porta {PORTA} ocupada ({e}) — o painel ja esta "
                         f"aberto em http://127.0.0.1:{PORTA}")
    with srv as s:
        url = f"http://127.0.0.1:{PORTA}"
        print(f"painel em {url}  (Ctrl+C para fechar)\nconfig: {CONFIG}")
        # Em thread: no Windows o webbrowser.open pode ficar preso esperando o
        # navegador, e ai o servidor nem chega a atender o primeiro pedido.
        if "--nao-abre" not in sys.argv:
            threading.Thread(target=webbrowser.open, args=(url,),
                             daemon=True).start()
        try:
            s.serve_forever()
        except KeyboardInterrupt:
            print("\nfechado")
