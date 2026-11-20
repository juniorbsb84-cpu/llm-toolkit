#!/usr/bin/env python
"""Sobe o painel invisivel (sem janela) ou abre o navegador se ja no ar.

  painel              garante no ar (invisivel) e abre o navegador
  painel --nao-abre   garante no ar sem abrir o navegador (uso do agendador)
  painel --reinicia   derruba o que estiver no ar e sobe de novo

Se a porta 8777 ja responde, nao inicia outro servidor: so abre o navegador.
O servidor filho roda via pythonw, sem console e desanexado do terminal.

O --reinicia existe porque o painel.py e lido UMA VEZ, quando o processo sobe:
mexer no arquivo nao muda a pagina servida, e recarregar a aba nao adianta --
o processo velho continua com o codigo velho na memoria. Ja aconteceu de o
painel mostrar provedor como "API" depois de o TIPO virar "CLI" no codigo, e
levou um tempo ate alguem desconfiar da etiqueta em vez do codigo. Existe um
hook PostToolUse (settings.json) que chama isto sozinho quando painel.py ou
painel.html sao editados.
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser

PORTA = 8777
AQUI = os.path.dirname(os.path.abspath(__file__))


def no_ar():
    try:
        socket.create_connection(("127.0.0.1", PORTA), timeout=1).close()
        return True
    except OSError:
        return False


SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def quem_escuta():
    """(pid, imagem) de quem escuta a PORTA, ou (None, None).

    netstat + tasklist em vez de psutil: e uma dependencia a mais so para
    ler duas tabelas que o Windows ja imprime."""
    try:
        saida = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                               timeout=30, creationflags=SEM_JANELA).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None, None
    pid = None
    for linha in saida.splitlines():
        p = linha.split()
        # TCP  127.0.0.1:8777  0.0.0.0:0  LISTENING  10596   (e a forma IPv6)
        if (len(p) >= 5 and p[0].upper() == "TCP"
                and p[-2].upper() == "LISTENING" and p[1].endswith(f":{PORTA}")):
            pid = p[-1]
            break
    if pid is None:
        return None, None
    try:
        linhas = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, timeout=30,
                                creationflags=SEM_JANELA).stdout or ""
        imagem = linhas.strip().split(",")[0].strip('"') if "," in linhas else ""
    except (OSError, subprocess.SubprocessError):
        imagem = ""
    return pid, imagem


def derruba():
    """Mata o painel que estiver na porta. Devolve o que aconteceu, em texto.

    So mata se a imagem for python/pythonw: a porta e nossa por convencao, nao
    por reserva, e `taskkill /F` em processo alheio seria um estrago causado
    por um palpite."""
    pid, imagem = quem_escuta()
    if pid is None:
        return "nada no ar"
    if not imagem.lower().startswith(("python", "pythonw")):
        raise SystemExit(f"porta {PORTA} ocupada por {imagem or '?'} (PID {pid}), "
                         f"que nao e o painel -- nao vou matar processo alheio")
    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                   timeout=30, creationflags=SEM_JANELA)
    for _ in range(50):
        if not no_ar():
            return f"derrubado (PID {pid})"
        time.sleep(0.1)
    raise SystemExit(f"painel (PID {pid}) nao morreu")


def sem_console():
    """pythonw quando existir (sem console por natureza); senao python com
    flags que escondem a janela e desanexam o filho do terminal."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(w):
        return w, 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    return exe, flags


def main():
    abrir = "--nao-abre" not in sys.argv
    url = f"http://127.0.0.1:{PORTA}"
    if "--reinicia" in sys.argv:
        print(derruba())
    if no_ar():
        print(f"painel ja esta no ar em {url}")
    else:
        exe, flags = sem_console()
        subprocess.Popen(
            [exe, os.path.join(AQUI, "painel.py"), "--nao-abre"],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        for _ in range(60):
            if no_ar():
                break
            time.sleep(0.2)
        if no_ar():
            print(f"painel no ar em {url} (invisivel)")
        else:
            raise SystemExit(f"painel nao subiu em {url}")
    if abrir:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
