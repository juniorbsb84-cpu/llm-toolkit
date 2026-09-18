#!/usr/bin/env python
"""Painel do esquadrao: escolhe provedores e effort no navegador.

  python tools/painel.py        (abre http://127.0.0.1:8777)

Salvar grava ~/.claude/esquadrao.json, que o esquadrao.py le como padrao e o
Claude le para saber a configuracao vigente. So isso: a pagina nao dispara
nada, so escreve a config.
"""
import http.server, json, os, queue, socketserver, sys, threading, time, webbrowser
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import PROVEDORES, base  # noqa: E402
from esquadrao import PADRAO, CONFIG  # noqa: E402
import esquadrao as esq  # noqa: E402
import modelos as cat  # noqa: E402

PORTA = 8777
EFFORTS = ["low", "medium", "high", "xhigh"]

# Quem aceita "xhigh" de fato — cada um testado a mao em 12/09/2026 mandando um
# valor invalido e lendo a recusa, e depois uma chamada real no xhigh:
#   codex      erro lista 'high', 'xhigh', 'max'
#   openrouter erro lista max|xhigh|high|medium|low|minimal|none
#   commandcode erro diz "Supported: low, medium, high, xhigh"
#   deepseek/alibaba  "banana" da 400 com o enum; xhigh responde normalmente
# Fora: agy (a flag --effort so conhece low|medium|high) e opencode (a CLI
# engole qualquer --variant calada, entao nao da para provar que honra).
XHIGH = ["codex", "commandcode", "openrouter", "alibaba", "claude"]
# (o deepseek saiu em 16/09/2026: o servidor mapeia xhigh para high, e o
#  via_deepseek passou a reancorar — oferecer o degrau seria mentira do painel)
# (o claude entrou em 13/09/2026: `claude --help` lista low|medium|high|xhigh|max
#  e um valor invalido so vira Warning no stderr, nunca erro — ver via_claude)

# CLI = chamada por subprocesso de um cliente instalado; API = HTTP direto.
TIPO = {"agy": "CLI", "codex": "CLI", "commandcode": "CLI", "opencode": "CLI",
        "claude": "CLI",
        "openrouter": "API", "alibaba": "API", "deepseek": "API"}


# O que o llm.py faz de fato com o effort de cada provedor:
#   sempre — vira flag/campo na chamada: codex e commandcode (--effort),
#            opencode (--variant), openrouter/deepseek (reasoning_effort),
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
            esq.AJUSTES[juiz] = (PROVEDORES[juiz][1], "low")
        _empurra(t, {"tipo": "comecou", "nomes": nomes, "modo": modo,
                     "juiz": juiz})
        rodadas = int(pedido.get("rodadas") or 3)
        texto = pedido["pergunta"]
        if modo == "consenso":
            esq.consenso(nomes, texto, [], None, juiz, rodadas)
        elif modo == "arquiteto":
            esq.arquiteto(nomes, texto, [], None, juiz)
        elif modo == "refino":
            esq.refino(nomes, texto, [], None, juiz, rodadas)
        else:
            esq.mostra("RODADA 1 - respostas independentes",
                       esq.em_paralelo(nomes, texto, [], None))
        _empurra(t, {"tipo": "fim"})
    except esq.Parado:
        _empurra(t, {"tipo": "fim", "parado": True})
    except BaseException as e:
        _empurra(t, {"tipo": "fim", "erro": str(e)[:400]})
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
                      .replace("__CONFIG__", json.dumps(config_atual()))
                      .replace("__NOMES__", json.dumps(sorted(PROVEDORES)))
                      .replace("__MODELOS__", json.dumps(catalogo()))
                      .replace("__QUANDO__", json.dumps(quando()))
                      .replace("__RODANDO__", json.dumps(
                          bool(TRABALHO and TRABALHO.get("vivo"))))
                      .replace("__TIPO__", json.dumps(por_base(TIPO)))
                      .replace("__EFEITO__", json.dumps(por_base(EFEITO))))
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
        if self.path == "/parar":
            t = TRABALHO
            if t is not None:
                t["parar"] = True
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
