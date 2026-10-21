"""AI-Pool 桌面控制台 — native window wrapping the local pool panel.

Double-click AI-Pool.vbs (or run this file directly) to:
  1. health-check the pool service on :7707, auto-start it hidden if needed
  2. open a native desktop window showing the 10-account control panel
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
HEALTH = "http://127.0.0.1:7707/health"
PANEL = "http://127.0.0.1:7707/panel"

CREATE_NO_WINDOW = 0x08000000


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=2) as r:
            return b"ok" in r.read()
    except Exception:
        return False


def ensure_service() -> bool:
    """Start the pool service hidden if it is not running. Returns True if we started it."""
    if healthy():
        return False
    pythonw = BASE / "venv" / "Scripts" / "pythonw.exe"
    py = str(pythonw) if pythonw.exists() else sys.executable
    subprocess.Popen(
        [py, str(BASE / "core" / "pool_service.py")],
        cwd=str(BASE / "core"),
        creationflags=CREATE_NO_WINDOW,
        close_fds=True,
    )
    for _ in range(30):
        time.sleep(1)
        if healthy():
            return True
    raise SystemExit("pool service failed to start within 30s")


def main() -> None:
    ensure_service()
    import webview  # pywebview

    webview.create_window(
        "AI-Pool 控制台",
        PANEL,
        width=1380,
        height=920,
        min_size=(1080, 700),
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
