#!/usr/bin/env python
"""A mesma pergunta para varios modelos ao mesmo tempo.

  python tools/esquadrao.py "a pergunta"
  python tools/esquadrao.py -i foto.png "o que ha de errado aqui?"
  python tools/esquadrao.py --modo refino "como estruturar X?"
  cat contrato.txt | python tools/esquadrao.py --so agy,codex,openrouter

Modos:
  todos    (padrao) cada provedor responde sozinho; voce le as N respostas.
  refino   N rodadas: independente, (N-2) de critica cruzada, consolidacao.
           N=3 (padrao antigo) = 1 independente + 1 critica + consolidado.
  arquiteto o juiz quebra o objetivo em 2 a 6 subtarefas rotuladas por
           especialidade, cada uma vai para o primeiro slot ATIVO daquela rota
           (perfis.json), rodam em ondas, o juiz consolida e revisa o resultado.
  consenso LACO ate todos ratificarem a mesma sintese. Cada rodada: propor ->
           um juiz consolida -> todos votam RATIFICO/DISCORDO. Objecao volta
           para a rodada seguinte. Para na unanimidade, nao num numero fixo.
           Se o teto de rodadas estourar, o que sobrou em aberto E o resultado:
           quer dizer que o ponto e disputado de verdade, nao que falhou.

O valor nao esta em ter N respostas, esta na DIVERGENCIA entre elas: onde todos
concordam nao precisa de revisao humana, onde rachar e onde o problema e dificil.

Provedores e travas herdados de llm.py (mesmo diretorio).
"""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import le_stdin, PROVEDORES, diz  # noqa: E402

# opencode fica de fora: a conta foi bloqueada por usar o modelo gratuito FORA
# da plataforma deles — e limite de termo de uso, nao de volume, entao rodar
# menos nao resolve. Entra so se pedido a mao com --so, e sob risco conhecido.
PADRAO = ["agy", "agy2", "agy3", "codex", "codex2", "claude", "claude2",
          "openrouter", "openrouter2", "openrouter3", "alibaba",
          "commandcode", "deepseek"]

# Escrito pelo painel e lido aqui: quem entra no lote, com que modelo e que
# effort. E so um padrao — qualquer flag na linha de comando ganha.
# Config sempre em ~/.claude/ — ~/.config/opencode/ e arvore separada do
# OpenCode, nunca ler nem gravar la (dois esquadroes espelhados e
# independentes; ver memoria fronteira_opencode_separado).
def _caminho_config():
    return os.path.join(os.path.expanduser("~"), ".claude", "esquadrao.json")
CONFIG = _caminho_config()

# {provedor: (modelo, effort)} — vazio quer dizer "usa o padrao de llm.py".
AJUSTES = {}

# Gancho de acompanhamento ao vivo. Quem chama por CLI deixa None e nada muda;
# o painel poe uma funcao aqui e recebe cada evento no instante em que acontece.
#
# Existe porque `em_paralelo` so devolve quando o ULTIMO provedor termina: quem
# olha de fora ficaria minutos sem sinal nenhum, sem saber se o lote anda ou
# travou. O CLI aguenta isso (o terminal ao menos mostra que o processo vive);
# uma pagina em branco, nao.
#
# Nunca deixe um erro daqui derrubar o lote: o observador e acessorio, e a
# resposta dos modelos e o que importa.
EVENTO = None


def _ev(tipo, **dados):
    if EVENTO is None:
        return
    try:
        EVENTO(dict(dados, tipo=tipo))
    except Exception:
        pass


# Erguida pelo painel quando o usuario manda parar. O laco checa entre
# provedores e entre rodadas: interromper uma chamada ja em voo nao da (o CLI
# do provedor ja esta rodando), mas nao comecar as proximas da.
class Parado(Exception):
    pass


PARAR = None


def checa_parada():
    if PARAR is not None and PARAR():
        raise Parado()


def le_config():
    if not os.path.exists(CONFIG):
        return None
    try:
        return json.load(open(CONFIG, encoding="utf-8"))
    except Exception as e:
        diz(f"[aviso] {CONFIG} ilegivel ({e}); seguindo com os padroes")
        return None


def pergunta_a(nome, texto, imagens, effort):
    fn, modelo = PROVEDORES[nome]
    modelo, effort = AJUSTES.get(nome, (modelo, effort))
    checa_parada()
    _ev("inicio", nome=nome, modelo=modelo, effort=effort)
    t = time.time()
    try:
        resposta = fn(texto, imagens, modelo, effort).strip()
        if not resposta:
            # Resposta vazia e falha, nao participacao. Sem esta linha o
            # provedor aparece no relatorio como se tivesse respondido, entra
            # no dossie da rodada seguinte como uma entrada em branco e some do
            # radar -- ninguem vai perguntar por que aquele modelo "nao opinou".
            # Visto em 15/09/2026 com opencode-go/glm-5.3-flash, que devolve
            # string vazia sem erro nenhum. O lado das APIs ja declarava isso
            # (ver `openai_compat`); aqui vale para TODO provedor, CLI incluso.
            raise SystemExit(f"resposta vazia de {modelo} (sem erro do provedor "
                             f"-- modelo devolveu nada)")
        seg = time.time() - t
        _ev("resposta", nome=nome, modelo=modelo, texto=resposta, seg=seg, ok=True)
        return nome, resposta, seg
    except Parado:
        raise
    except BaseException as e:
        # SystemExit inclusive: um provedor fora do ar nao derruba o esquadrao.
        seg = time.time() - t
        _ev("resposta", nome=nome, modelo=modelo, texto=f"[FALHOU] {e}",
            seg=seg, ok=False)
        return nome, f"[FALHOU] {e}", seg


def em_paralelo(nomes, texto, imagens, effort):
    """Um thread por provedor. Sao chamadas de rede/subprocesso, entao o GIL
    nao atrapalha e o lote leva o tempo do mais lento, nao a soma."""
    checa_parada()
    with ThreadPoolExecutor(max_workers=len(nomes)) as ex:
        futs = [ex.submit(pergunta_a, n, texto, imagens, effort) for n in nomes]
        return [f.result() for f in futs]


def mostra(rodada, respostas):
    _ev("rodada", titulo=rodada)
    # Com observador ligado (o painel), o despejo em texto sai de cena: cada
    # resposta ja chegou la como evento proprio, no instante em que ficou
    # pronta. Repetir aqui mostraria tudo DE NOVO, em bloco, depois de o
    # usuario ja ter lido. No terminal, onde nao ha evento nenhum, este texto
    # continua sendo a unica saida que existe.
    if EVENTO is not None:
        return
    diz(f"\n{'=' * 70}\n{rodada}\n{'=' * 70}")
    for nome, resp, seg in respostas:
        diz(f"\n--- {nome}  ({seg:.0f}s) ---\n{resp}")


def dossie(respostas):
    return "\n\n".join(f"### Resposta de {n}\n{r}" for n, r, _ in respostas
                       if not r.startswith("[FALHOU]"))


# --- arquiteto: cada LLM cuida do que faz melhor -----------------------------

# O juiz decompoe o objetivo em subtarefas rotuladas; a tabela abaixo atribui
# cada uma ao primeiro slot ATIVO (toggle do painel). Toggle off = fora do
# sorteio. O juiz decide O QUE (decompoe, consolida, revisa); a tabela decide
# QUEM, de forma deterministica e sem gastar uma chamada de LLM a mais.
# Ajuste fino a mao em ~/.claude/perfis.json.
PERFIS = os.path.join(os.path.expanduser("~"), ".claude", "perfis.json")

ROTA_PADRAO = {
    "codigo":     ["codex", "codex2", "openrouter2", "commandcode", "deepseek", "claude", "claude2"],
    "frontend":   ["commandcode", "openrouter2", "codex", "codex2", "agy"],
    "visao":      ["agy", "agy2", "agy3", "alibaba", "opencode", "opencode2", "commandcode"],
    "texto":      ["claude", "claude2", "openrouter3", "openrouter", "agy"],
    "raciocinio": ["claude", "claude2", "agy", "agy2", "alibaba", "deepseek"],
    "dados":      ["alibaba", "openrouter", "deepseek", "agy"],
    "pesquisa":   ["agy", "agy2", "agy3", "commandcode", "claude", "claude2"],
    "geral":      ["agy", "agy2", "claude", "claude2", "openrouter2", "alibaba"],
}

DECOMPOR = (
    "Voce e um arquiteto de tarefas. Quebre o OBJETIVO abaixo em 2 a 6 "
    "subtarefas, cada uma na especialidade onde um LLM rende mais.\n"
    "Especialidades validas: codigo, frontend, visao, texto, raciocinio, "
    "dados, pesquisa, geral.\n"
    "- codigo: implementar, refatorar, debugar, scripts\n"
    "- frontend: UI, CSS, layout, componentes\n"
    "- visao: imagens, screenshots, diagramas\n"
    "- texto: redacao, resumo, traducao, docs\n"
    "- raciocinio: arquitetura, decisoes, trade-offs, revisao critica\n"
    "- dados: planilhas, numeros, analise, SQL\n"
    "- pesquisa: web, docs atuais, comparacao de opcoes\n"
    "- geral: resto\n"
    "Responda SO com JSON valido, sem markdown nem comentario: "
    "[{\"id\": \"curto-sem-espaco\", \"tarefa\": \"instrucao completa e "
    "autocontida\", \"especialidade\": \"...\", \"depende\": [\"ids\"]}]. "
    "\"depende\" lista ids que precisam terminar antes; [] se independente."
    "\n\nOBJETIVO:\n")


def rota_final():
    rota = {e: list(s) for e, s in ROTA_PADRAO.items()}
    try:
        if os.path.exists(PERFIS):
            for e, s in json.load(open(PERFIS, encoding="utf-8")).get("rota", {}).items():
                if isinstance(s, list) and s:
                    rota[e] = s
    except Exception as e:
        diz(f"[aviso] {PERFIS} ilegivel ({e}); usando rota padrao")
    return rota


def extrai_json(bruto):
    """Pega o primeiro [...] do texto e valida: lista de {id, tarefa}.

    O juiz e instruido a responder so JSON, mas modelo nenhum garante isso —
    quando vier cercado de markdown ou de prosa, o recorte salva a rodada; e
    quando nao der para ler, o chamador cai numa tarefa unica em vez de morrer.
    """
    try:
        plano = json.loads(bruto[bruto.index("["):bruto.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(plano, list) or not plano:
        return None
    for t in plano:
        if not isinstance(t, dict) or not str(t.get("tarefa", "")).strip():
            return None
    return plano


def arquiteto(nomes, texto, imagens, effort, juiz):
    """Decompoe pelo juiz -> roteia pela tabela -> ondas -> juiz consolida e revisa."""
    from equipe import valida, niveis, executa
    import equipe as eq
    bruto = pergunta_a(juiz, DECOMPOR + texto, imagens, effort)[1]
    plano = extrai_json(bruto)
    if plano is None:
        diz("[aviso] juiz nao devolveu JSON; tarefa unica em modo geral")
        plano = [{"id": "tudo", "tarefa": texto, "especialidade": "geral",
                  "depende": []}]
    rota = rota_final()
    for t in plano:
        esp = t.get("especialidade")
        t["especialidade"] = esp if esp in rota else "geral"
        # Primeiro preferido que esteja ativo; sem ele, o primeiro ativo.
        t["provedor"] = next((s for s in rota[t["especialidade"]] if s in nomes),
                             nomes[0])
        if not isinstance(t.get("tarefa"), str) or not t["tarefa"].strip():
            t["tarefa"] = texto
    # Id repetido do juiz quebraria o grafo de dependencia em silencio: renomeia
    # e leva o apontamento junto.
    vistos, mapa = set(), {}
    for i, t in enumerate(plano):
        base_id = str(t.get("id") or f"t{i + 1}")
        nid, k = base_id, 1
        while nid in vistos:
            k += 1
            nid = f"{base_id}-{k}"
        mapa[base_id] = nid
        t["id"] = nid
        vistos.add(nid)
    for t in plano:
        deps = t.get("depende") or []
        t["depende"] = [mapa[d] for d in deps
                        if d in mapa and mapa[d] != t["id"]]
    eq.AJUSTES.update(AJUSTES)
    valida(plano)
    diz("PLANO (" + str(len(plano)) + " etapas):")
    for t in plano:
        curta = t["tarefa"][:80] + ("…" if len(t["tarefa"]) > 80 else "")
        diz(f"  - {t['id']} -> {t['provedor']} [{t['especialidade']}] :: {curta}")
    # Marcas de fase, so para quem observa (o painel). Sem elas, plano, etapas
    # e consolidacao caem num bloco so -- e a consolidacao, que e do MESMO
    # juiz, aparecia por cima do cartao da decomposicao. No terminal nao muda
    # nada: `_ev` sem observador nao faz coisa alguma.
    _ev("rodada", titulo=f"ARQUITETO plano por {juiz}")

    quem = {t["id"]: t["provedor"] for t in plano}
    saidas = {}
    for onda in niveis(plano):
        with ThreadPoolExecutor(max_workers=len(onda)) as ex:
            res = [f.result() for f in
                   [ex.submit(executa, t, saidas) for t in onda]]
        for tid, rtexto, seg, ok in res:
            saidas[tid] = rtexto
            diz(f"\n--- {tid} ({quem[tid]})  ({seg:.0f}s) ---\n{rtexto}")

    _ev("rodada", titulo="ARQUITETO etapas executadas")
    doss = "\n\n".join(f"### Etapa '{t['id']}' ({quem[t['id']]})\n{saidas[t['id']]}"
                       for t in plano)
    fin = pergunta_a(juiz, (
        f"Objetivo original:\n{texto}\n\n"
        f"Cada etapa abaixo foi feita pelo provedor mais apto. Entregue UMA "
        f"resposta final coerente: incorpore o melhor de cada etapa, resolva "
        f"divergencias dizendo por que, e liste o que ficou pendente."
        f"\n\n{doss}"), imagens, effort)
    mostra(f"ARQUITETO consolidado por {juiz}", [fin])

    # O orquestrador tambem e o revisor final: audita a sintese contra o
    # objetivo e entrega a versao corrigida (ou a mesma, se estiver ok).
    rev = pergunta_a(juiz, (
        f"Voce e o revisor final. Objetivo original:\n{texto}\n\n"
        f"Resposta consolidada:\n{fin[1]}\n\n"
        f"Verifique: cobre todo o objetivo? divergencias entre etapas "
        f"resolvidas com motivo? algum erro de fato ou omissao que muda a "
        f"conclusao? Se estiver ok, devolva a resposta INALTERADA. Se "
        f"houver problema material, devolva a versao corrigida e ao final "
        f"liste o que mudou e por que."), imagens, effort)
    mostra(f"ARQUITETO revisao final por {juiz}", [rev])


# --- consenso: laco ate ratificacao unanime ---------------------------------

# A objecao tem que ser MATERIAL. Sem essa exigencia o modelo inventa uma
# ressalva de estilo so para nao parecer que concordou, e o laco nunca fecha.
RATIFICA = (
    "Responda com a PRIMEIRA LINHA sendo exatamente RATIFICO ou DISCORDO.\n"
    "RATIFICO se o texto abaixo esta correto e completo o bastante para ser a "
    "resposta final — nao precisa ser como voce teria escrito.\n"
    "DISCORDO so se houver erro de fato, omissao que muda a conclusao, ou "
    "afirmacao sem apoio. Preferencia de estilo, ordem ou enfase NAO conta.\n"
    "Se DISCORDO, escreva depois o que exatamente esta errado e o conserto.\n")


def voto_de(resposta):
    primeira = resposta.strip().splitlines()[0].upper() if resposta.strip() else ""
    if resposta.startswith("[FALHOU]"):
        return None                      # ausente nao conta como voto
    return "RATIFICO" in primeira and "DISCORDO" not in primeira


def consenso(nomes, texto, imagens, effort, juiz, max_rodadas):
    """Debate ate todos ratificarem a mesma sintese, ou ate acabar a paciencia.

    O criterio de parada e unanimidade de RATIFICO — nao um numero fixo de
    rodadas. Se sobrar objecao na ultima, isso e informacao, nao fracasso:
    quer dizer que o ponto e genuinamente disputado."""
    sintese, objecoes = None, ""
    for rodada in range(1, max_rodadas + 1):
        if sintese is None:
            prompt = texto
        else:
            prompt = (f"Pergunta original:\n{texto}\n\n"
                      f"Resposta consolidada ate agora:\n{sintese}\n\n"
                      f"Objecoes levantadas:\n{objecoes}\n\n"
                      f"Reescreva a resposta final resolvendo as objecoes "
                      f"validas. Se alguma objecao estiver errada, diga por que "
                      f"e mantenha o texto.")
        rs = em_paralelo(nomes, prompt, imagens, effort)
        mostra(f"RODADA {rodada} — propostas", rs)

        s = pergunta_a(juiz, (
            f"Pergunta original:\n{texto}\n\n"
            f"Varias respostas abaixo. Escreva UMA resposta final que incorpore "
            f"o que ha de melhor em todas. Onde elas se contradizem, decida e "
            f"diga por que. Entregue so a resposta, sem comentar o processo."
            f"\n\n{dossie(rs)}"), imagens, effort)
        sintese = s[1]
        mostra(f"SINTESE {rodada} (por {juiz})", [s])

        votos = em_paralelo(nomes, f"{RATIFICA}\n---\n{sintese}", imagens, effort)
        mostra(f"VOTACAO {rodada}", votos)
        contra = [(n, r) for n, r, _ in votos if voto_de(r) is False]
        sim = sum(1 for _, r, _ in votos if voto_de(r) is True)
        diz(f"\n>>> rodada {rodada}: {sim} ratificaram, {len(contra)} discordaram")

        if not contra:
            diz(f"\n{'#' * 70}\nCONSENSO na rodada {rodada}\n{'#' * 70}\n{sintese}")
            return
        objecoes = "\n\n".join(f"- {n}: {r}" for n, r in contra)

    diz(f"\n{'#' * 70}\nSEM CONSENSO em {max_rodadas} rodadas — o ponto e "
        f"disputado de verdade\n{'#' * 70}\n{sintese}\n\nEm aberto:\n{objecoes}")


def refino(nomes, texto, imagens, effort, juiz, n_rodadas):
    """Independente -> (N-2) rodadas de critica cruzada -> consolidacao.

    n_rodadas conta a consolidacao como a ultima rodada (assim n_rodadas=3
    reproduz o comportamento antigo, fixo: r1 independente, r2 critica,
    r3 consolidado). Minimo 2 (independente + consolidado, sem critica)."""
    n_rodadas = max(2, n_rodadas)
    respostas = em_paralelo(nomes, texto, imagens, effort)
    mostra("RODADA 1 — respostas independentes", respostas)

    for rodada in range(2, n_rodadas):
        p = (f"Pergunta original:\n{texto}\n\n"
             f"Outros modelos responderam o abaixo. Aponte onde eles erram ou "
             f"deixam buraco, e entao de a SUA resposta final, melhorada.\n\n"
             f"{dossie(respostas)}")
        respostas = em_paralelo(nomes, p, imagens, effort)
        mostra(f"RODADA {rodada} — critica cruzada e revisao", respostas)

    p_final = (f"Pergunta original:\n{texto}\n\n"
               f"Varios modelos ja debateram. Consolide UMA resposta final: o "
               f"que todos convergem, e onde discordam diga qual lado esta "
               f"certo e por que.\n\n{dossie(respostas)}")
    mostra(f"CONSOLIDADO por {juiz}",
           [pergunta_a(juiz, p_final, imagens, effort)])


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pergunta", nargs="?", help="ou por stdin")
    p.add_argument("-i", "--imagem", action="append", default=[])
    p.add_argument("-e", "--effort", default=None,
                   help="sobrepoe o effort do painel para todos")
    p.add_argument("--so", help="lista separada por virgula")
    p.add_argument("--modo",
                   choices=["todos", "refino", "consenso", "arquiteto"],
                   default=None)
    p.add_argument("--juiz", default=None, help="quem consolida")
    p.add_argument("--rodadas", type=int, default=None,
                   help="refino: total de rodadas (min 2). consenso: teto "
                        "(padrao 4)")
    a = p.parse_args()

    cfg = le_config() or {}
    provs = cfg.get("provedores", {})
    a.modo = a.modo or cfg.get("modo", "todos")
    a.juiz = a.juiz or cfg.get("juiz", "agy")
    a.rodadas = a.rodadas or cfg.get("rodadas", 4)
    # Juiz fora do catalogo estourava KeyError com traceback la na frente,
    # depois de o lote inteiro ja ter rodado. Falha agora, com a lista.
    if a.juiz not in PROVEDORES:
        raise SystemExit(f"juiz desconhecido: {a.juiz} "
                         f"(opcoes: {', '.join(sorted(PROVEDORES))})")

    texto = a.pergunta or le_stdin().strip()
    if not texto:
        raise SystemExit("sem pergunta")
    for x in a.imagem:
        if not os.path.exists(x):
            raise SystemExit(f"imagem nao existe: {x}")

    if a.so:
        nomes = [n.strip() for n in a.so.split(",")]
    elif provs:
        nomes = [n for n, c in provs.items() if c.get("ativo")]
    else:
        nomes = list(PADRAO)
    for n in nomes:
        if n not in PROVEDORES:
            raise SystemExit(f"provedor desconhecido: {n}")
    if not nomes:
        raise SystemExit(f"nenhum provedor ativo em {CONFIG}")

    # -e da linha de comando ganha do painel; sem ele, cada um usa o seu.
    for n in nomes:
        c = provs.get(n, {})
        AJUSTES[n] = (c.get("modelo") or PROVEDORES[n][1],
                      a.effort or c.get("effort") or "low")
    if a.juiz not in AJUSTES:
        AJUSTES[a.juiz] = (PROVEDORES[a.juiz][1], a.effort or "low")
    if cfg:
        diz("config: " + ", ".join(f"{n}({AJUSTES[n][1]})" for n in nomes)
            + f"  modo={a.modo}")

    if a.modo == "consenso":
        return consenso(nomes, texto, a.imagem, a.effort, a.juiz, a.rodadas)

    if a.modo == "arquiteto":
        return arquiteto(nomes, texto, a.imagem, a.effort, a.juiz)

    if a.modo == "todos":
        r1 = em_paralelo(nomes, texto, a.imagem, a.effort)
        mostra("RODADA 1 — respostas independentes", r1)
        return

    return refino(nomes, texto, a.imagem, a.effort, a.juiz, a.rodadas)


if __name__ == "__main__":
    main()
