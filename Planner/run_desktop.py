"""Daily Planner — Desktop App Launcher
====================================
Starts the Flask backend and opens the planner in a native macOS
pywebview window. If webview fails, falls back to the default browser.

Run:  python3 run_desktop.py    (or double-click the .command file)
"""

import os
import subprocess
import sys
import time
import traceback
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(BASE_DIR, "app.py")
FLASK_URL = "http://127.0.0.1:5000"
PORT = 5000
LOG_FILE = os.path.join(BASE_DIR, "error.log")

try:
    from version import __version__ as APP_VERSION
except ImportError:
    APP_VERSION = "unknown"

log_fp = open(LOG_FILE, "a", buffering=1)


def _log(message):
    print(message, file=sys.stderr)
    print(message, file=log_fp)


def _free_port():
    # Cross-platform port cleanup: prefer psutil (Windows/macOS/Linux),
    # fall back to lsof on Unix, otherwise just continue.
    try:
        import psutil
        killed = []
        for conn in psutil.net_connections(kind="inet"):
            try:
                if conn.laddr and conn.laddr.port == PORT and conn.pid:
                    killed.append(conn.pid)
            except Exception:
                continue
        for pid in set(killed):
            if pid == os.getpid():
                continue
            _log(f"[planner] freeing port {PORT}: {pid}")
            try:
                psutil.Process(pid).terminate()
            except Exception:
                pass
        if killed:
            time.sleep(1)
        return
    except ImportError:
        pass
    except Exception:
        pass
    try:
        pids = subprocess.run(
            ["lsof", "-ti", f"tcp:{PORT}"],
            capture_output=True, text=True, timeout=2,
        ).stdout.split()
        if pids:
            _log(f"[planner] freeing port {PORT}: {', '.join(pids)}")
            for pid in pids:
                try:
                    os.kill(int(pid), 15)
                except Exception:
                    pass
            time.sleep(1)
    except Exception:
        pass


def _server_ready(attempts=60, delay=0.25):
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(FLASK_URL, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(delay)
    return False


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(APP_VERSION)
        sys.exit(0)

    _free_port()

    _log(f"[planner] Oztudy v{APP_VERSION} starting {APP_PY} on 127.0.0.1:{PORT}")
    server = subprocess.Popen(
        [sys.executable, APP_PY],
        stdout=log_fp, stderr=log_fp, cwd=BASE_DIR,
    )

    if not _server_ready():
        _log(f"[planner] server did not become ready at {FLASK_URL} — exiting")
        server.terminate()
        sys.exit(1)

    _log(f"[planner] flask bound to {FLASK_URL}")

    try:
        import webview
        webview.create_window(
            "Daily Planner", FLASK_URL,
            width=1180, height=820, min_size=(980, 700),
            background_color="#F9FAFB",
        )
        webview.start()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        traceback.print_exc(file=log_fp)
        _log("[planner] webview unavailable — opening in the default browser")
        try:
            import webbrowser
            webbrowser.open(FLASK_URL)
        except Exception:
            # Last-resort OS-specific openers (macOS `open`, Windows `start`).
            try:
                if sys.platform.startswith("win"):
                    subprocess.run(["cmd", "/c", "start", "", FLASK_URL], capture_output=True)
                elif sys.platform == "darwin":
                    subprocess.run(["open", FLASK_URL], capture_output=True)
                else:
                    subprocess.run(["xdg-open", FLASK_URL], capture_output=True)
            except Exception:
                pass

    try:
        server.wait()
    except KeyboardInterrupt:
        server.terminate()