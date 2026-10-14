"""
JournalScope launcher (no console window - run with pythonw.exe).

Serves the static app in ./docs on 127.0.0.1 and opens it in the default browser.
Why a server at all: the app loads its data files with fetch(), which browsers refuse for file:// pages.

  - Clicking the shortcut again while it is running just opens the browser (no second server).
  - The server stops by itself after IDLE_HOURS without a request; click the shortcut to start it again.
  - Files are always served with Cache-Control: no-cache, so a rebuild shows up on the next page load.

Optional flags:  --no-browser   start the server only
"""
import ctypes
import http.server
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "docs")
HOST = "127.0.0.1"
PORTS = (8431, 8432, 8433, 8434)  # first one that is free (or already ours)
IDLE_HOURS = 3
MARKER = "JournalScope"  # appears in index.html; used to recognise our own server


def alert(msg):
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "JournalScope", 0x10)
    except Exception:
        pass


def is_open(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, port)) == 0


def is_ours(port):
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/index.html", timeout=2) as r:
            return MARKER in r.read(20000).decode("utf-8", "ignore")
    except Exception:
        return False


class Handler(http.server.SimpleHTTPRequestHandler):
    last_hit = time.time()

    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        Handler.last_hit = time.time()
        super().do_GET()

    def log_message(self, *args):  # pythonw has no stderr
        pass


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows SO_REUSEADDR would let a second server steal the port
    daemon_threads = True


def main():
    open_browser = "--no-browser" not in sys.argv
    if not os.path.isfile(os.path.join(ROOT, "data", "journals.json")):
        alert("The journal data was not found:\n\n" + os.path.join(ROOT, "data", "journals.json") +
              "\n\nRun build\\build_data.py --offline in the journal-finder folder, then try again.")
        return

    port = None
    for p in PORTS:
        if is_open(p):
            if is_ours(p):  # already running: just open it
                if open_browser:
                    webbrowser.open(f"http://{HOST}:{p}/")
                return
            continue  # taken by something else
        port = p
        break
    if port is None:
        alert("Ports %s are all in use by other programs. Close one and try again." % ", ".join(map(str, PORTS)))
        return

    try:
        srv = Server((HOST, port), Handler)
    except OSError as e:
        alert(f"Could not start the local server on port {port}:\n\n{e}")
        return

    def watchdog():
        while True:
            time.sleep(60)
            if time.time() - Handler.last_hit > IDLE_HOURS * 3600:
                srv.shutdown()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(f"http://{HOST}:{port}/")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
