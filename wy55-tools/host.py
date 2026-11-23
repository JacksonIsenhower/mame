#!/usr/bin/env python3
"""Pretend to be the host computer on the emulated WY-55's main port.

    host.py PORT [DELAY SEQUENCE]...

Listens on 127.0.0.1:PORT for MAME's socket bitbanger (-bitb socket.127.0.0.1:PORT), then for each
DELAY SEQUENCE pair waits DELAY seconds and sends SEQUENCE. Sequences use Python escapes, e.g.
'\\x1b~5'. Everything the terminal sends back is printed with repr().
"""
import socket
import sys
import threading
import time


def main():
    port = int(sys.argv[1])
    steps = list(zip(sys.argv[2::2], sys.argv[3::2]))
    srv = socket.create_server(("127.0.0.1", port))
    conn, _ = srv.accept()

    def reader():
        while data := conn.recv(4096):
            print("terminal sent", repr(data), flush=True)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    for delay, seq in steps:
        time.sleep(float(delay))
        data = seq.encode("latin-1").decode("unicode_escape").encode("latin-1")
        print("host sends", repr(data), flush=True)
        conn.sendall(data)
    # Stay connected until the terminal side goes away; closing first kills MAME with SIGPIPE
    t.join()


if __name__ == "__main__":
    main()
