"""Daily Planner — Desktop App Launcher
====================================
Starts the Flask backend and opens the planner in a native macOS
pywebview window. If webview fails, falls back to the default browser.

Run:  python3 run_desktop.py    (or double-click the .command file)
"""

import logging
import logging.handlers
import os
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(BASE_DIR, "app.py")
FLASK_URL = "http://127.0.0.1:5000"
PORT = 5000
# Logs follow the data directory when overridden (e.g. Homebrew installs keep
# everything under OZTUDY_DATA_DIR instead of the read-only Cellar).
LOG_DIR = os.environ.get("OZTUDY_DATA_DIR", BASE_DIR)
LOG_FILE = os.path.join(LOG_DIR, "error.log")
# Rotating log bounds: keep ~1.5 MB total so a runaway backend cannot fill disk.
LOG_MAX_BYTES = 512 * 1024
LOG_BACKUPS = 3

try:
    from version import __version__ as APP_VERSION
except ImportError:
    APP_VERSION = "unknown"

logger = logging.getLogger("oztudy.launcher")
log_fp = None


def _log(message: str) -> None:
    """Emit a launcher status line to stderr and the rotating log."""
    logger.info(message)


def _open_log() -> None:
    """Configure rotating-file + stderr logging. Degrades to stderr only."""
    global log_fp
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s [planner] %(message)s")
    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setFormatter(fmt)
    logger.addHandler(stderr_h)
    try:
        if LOG_DIR != BASE_DIR:
            os.makedirs(LOG_DIR, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
            encoding="utf-8",
        )
        rotating.setFormatter(fmt)
        logger.addHandler(rotating)
        # Child Flask output still streams to the same path in append mode so
        # backend tracebacks are preserved alongside launcher logs.
        log_fp = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    except OSError as exc:
        print(f"[planner] cannot open log file {LOG_FILE}: {exc}", file=sys.stderr)


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


def _server_ready(attempts: int = 60, delay: float = 0.25) -> bool:
    """Poll ``FLASK_URL`` until HTTP 200 or the attempt budget runs out.

    Total wait is ``attempts * (request timeout + delay)`` ≈ 15 s by default.
    Each request has its own 2 s timeout so a hung socket cannot block the
    launcher indefinitely.
    """
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

    _open_log()
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
        logger.exception("webview failed")
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