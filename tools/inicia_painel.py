#!/usr/bin/env python
"""Sobe o painel invisivel (sem janela) ou abre o navegador se ja no ar.

  painel              garante no ar (invisivel) e abre o navegador
  painel --nao-abre   garante no ar sem abrir o navegador (uso do agendador)

Se a porta 8777 ja responde, nao inicia outro servidor: so abre o navegador.
O servidor filho roda via pythonw, sem console e desanexado do terminal.
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
