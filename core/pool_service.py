"""AI-Pool headless worker pool + VirtualBrowser API shim (:9000) + admin API (:7707).

- Port 9000  (/vb API, served at root): VirtualBrowser-compatible local API so
  fpbrowser2api / batch scripts can drive this pool transparently.
- Port 7707  (admin API at root): pool management, login helpers, TOTP, jobs.

Browsers are patchright (anti-detection Playwright) headless Chromium
persistent contexts with per-worker isolated profiles and spoofed fingerprints.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import random
import re
import shutil
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyotp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fingerprint import build_fingerprint, injection_init_script

BASE_DIR = Path(__file__).resolve().parent.parent
WORKERS_DIR = BASE_DIR / "data" / "workers"
DB_PATH = BASE_DIR / "data" / "pool.db"
LOG_DIR = BASE_DIR / "logs"
SECRETS_DIR = BASE_DIR / "secrets"
OUTPUT_DIR = Path(os.environ.get("AIPOOL_OUTPUT_DIR", str(BASE_DIR / "output")))

WORKER_PORT_BASE = 9300  # worker i CDP port = 9300 + i

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "pool.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_accounts() -> List[Dict[str, Any]]:
    path = SECRETS_DIR / "accounts.txt"
    if not path.exists():
        return []
    accounts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "----" not in line:
            continue
        parts = [p.strip() for p in line.split("----")]
        if len(parts) >= 2:
            accounts.append({
                "index": len(accounts) + 1,
                "email": parts[0],
                "password": parts[1],
                "totp_secret": parts[2] if len(parts) > 2 else "",
            })
    return accounts


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

def db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            worker_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            totp_secret TEXT NOT NULL DEFAULT '',
            last_login_ok INTEGER DEFAULT 0,
            last_login_ts INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            quota_left INTEGER DEFAULT 999,
            daily_used INTEGER DEFAULT 0,
            daily_date TEXT DEFAULT '',
            total_generated INTEGER DEFAULT 0,
            fingerprint TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            worker_id INTEGER,
            prompt TEXT,
            raw_prompt TEXT,
            style TEXT DEFAULT 'concept_art',
            ratio TEXT DEFAULT '16:9',
            res TEXT DEFAULT '1k',
            status TEXT DEFAULT 'queued',
            progress INTEGER DEFAULT 0,
            image_paths TEXT DEFAULT '[]',
            image_urls TEXT DEFAULT '[]',
            error TEXT DEFAULT '',
            created_at INTEGER,
            finished_at INTEGER,
            conversation_url TEXT DEFAULT ''
        )""")
        conn.commit()


def db_exec(sql: str, params: tuple = ()) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params)
        conn.commit()


def db_query(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        return cur.fetchall()


def ensure_worker_rows() -> None:
    accounts = load_accounts()
    with sqlite3.connect(DB_PATH) as conn:
        for i, acc in enumerate(accounts, start=1):
            name = f"ChatGPT-Worker-{i:02d}"
            row = conn.execute("SELECT worker_id FROM workers WHERE worker_id=?", (i,)).fetchone()
            if not row:
                fp = build_fingerprint(i, acc["email"])
                conn.execute(
                    "INSERT INTO workers (worker_id, name, email, totp_secret, fingerprint) VALUES (?,?,?,?,?)",
                    (i, name, acc["email"], acc.get("totp_secret", ""), json.dumps(fp)),
                )
        conn.commit()


# ---------------------------------------------------------------------------
# Browser pool
# ---------------------------------------------------------------------------

_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-dev-shm-usage",
    "--disable-infobars",
]


OFFSCREEN_POS = "--window-position=-32000,0"  # headed-but-invisible window placement

_LOGIN_WIN_W, _LOGIN_WIN_H = 520, 400
_LOGIN_POS = f"--window-position=99999,99999"  # replaced at runtime with bottom-right corner


def _hidden_startupinfo():
    """子行程若自己再開主控台（例如 CLI 內部拉起的終端機），也要是隱藏的（SW_HIDE）。"""
    if os.name != "nt":
        return None
    import subprocess
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def hide_browser_window(profile_dir: Path) -> bool:
    """把背景瀏覽器的視窗整個藏起來。

    離屏（-32000）的視窗在工作列還是會留一顆 Chrome 圖示，桌面程式也會跳出來。
    用 Win32 的 SW_HIDE 藏掉後，工作列乾淨、鏡射截圖仍正常（實測 2026-10-20）。
    登入模式不能藏：Turnstile 需要視窗可見。
    """
    try:
        import subprocess
        ps = ("Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*" + str(profile_dir) + "*' } | "
              "Select-Object -ExpandProperty ProcessId")
        # pythonw 底下啟動的主控台程式會自己開一個視窗，一定要 CREATE_NO_WINDOW
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
                             capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                             startupinfo=_hidden_startupinfo()).stdout
        pids = {int(x) for x in out.split() if x.strip().isdigit()}
        if not pids:
            return False
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        hidden = []

        def cb(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
                hidden.append(hwnd)
            return True

        user32.EnumWindows(proc_t(cb), 0)
        if hidden:
            log("background browser window hidden (no taskbar icon)")
        return bool(hidden)
    except Exception as e:
        log(f"hide window failed: {e}")
        return False


def _login_window_args() -> List[str]:
    """Small window at bottom-right corner: visible to Turnstile, negligible to user."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        cx, cy = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # SM_CXSCREEN/SM_CYSCREEN
        x = max(0, cx - _LOGIN_WIN_W - 12)
        y = max(0, cy - _LOGIN_WIN_H - 60)
    except Exception:
        x, y = 900, 600
    return [f"--window-position={x},{y}", f"--window-size={_LOGIN_WIN_W},{_LOGIN_WIN_H}"]


class BrowserWorker:
    def __init__(self, worker_id: int, name: str, email: str, fingerprint: Dict[str, Any]):
        self.worker_id = worker_id
        self.name = name
        self.email = email
        self.fp = fingerprint
        self.profile_dir = WORKERS_DIR / str(worker_id)
        self.port = WORKER_PORT_BASE + worker_id
        self.context = None
        self.status = "idle"  # idle | open
        self.launch_lock = asyncio.Lock()
        self.mode = "job"

    @property
    def cdp_http(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def launch(self, pw, headless: bool = False, mode: str = "job") -> int:
        """Launch persistent context.

        mode='job'  : headed + offscreen (invisible, full runtime) or headless
        mode='login': headed + small window at bottom-right corner (Turnstile
                      requires page visibility; a tiny corner window is
                      effectively unnoticeable).
        Falls back from channel=chrome -> msedge -> bundled chromium.
        """
        self.mode = mode
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = list(_STEALTH_ARGS) + [
            f"--remote-debugging-port={self.port}",
            f"--window-size={self.fp['screen'][0]},{self.fp['screen'][1]}",
        ]
        if not headless:
            if mode == "login":
                args = [a for a in args if not a.startswith("--window-size=")] + _login_window_args()
            elif mode != "visible":
                args.append(OFFSCREEN_POS)

        channels = [None if headless else "chrome", None if headless else "msedge", None]
        last_err: Exception | None = None
        ctx = None
        for ch in channels:
            try:
                kwargs: Dict[str, Any] = dict(
                    user_data_dir=str(self.profile_dir),
                    headless=headless,
                    locale=self.fp["locale"],
                    timezone_id=self.fp["timezone_id"],
                    viewport={"width": min(1680, self.fp["screen"][0]), "height": min(1000, self.fp["screen"][1] - 72)},
                    screen={"width": self.fp["screen"][0], "height": self.fp["screen"][1]},
                    args=args,
                    ignore_default_args=["--enable-automation", "--headless=old"],
                    timeout=120000,
                )
                if ch:
                    kwargs["channel"] = ch
                if mode == "visible":
                    kwargs["viewport"] = None
                    kwargs.pop("screen", None)
                elif mode == "embed":
                    kwargs["viewport"] = dict(EMBED_VIEW)
                ctx = await pw.chromium.launch_persistent_context(**kwargs)
                if ch:
                    log(f"worker {self.worker_id} using channel={ch}")
                break
            except Exception as e:
                last_err = e
                ctx = None
                continue
        if ctx is None:
            raise RuntimeError(f"chromium launch failed: {last_err}")
        if mode == "job":
            # Fingerprint injection ONLY for job mode: Turnstile (login) runs
            # JS-tampering integrity checks and fails on patched natives.
            await ctx.add_init_script(injection_init_script(self.fp))
        def _on_close(_c, _w=self):
            _w.status = "idle"
            _w.mode = ""
        ctx.on("close", _on_close)
        self.context = ctx
        self.status = "open"
        if mode in ("job", "embed"):
            # 視窗藏起來（工作列不留圖示）；背景做，不要拖慢啟動
            asyncio.create_task(self._hide_window_later())
        log(f"worker {self.worker_id} launched headless={headless} mode={mode} cdp={self.port} inject={mode == 'job'}")
        return self.port

    async def _hide_window_later(self) -> None:
        """Chrome 開窗是漸進的，稍等一下再藏。"""
        for _ in range(8):
            await asyncio.sleep(1.0)
            try:
                if hide_browser_window(self.profile_dir):
                    return
            except Exception:
                return

    async def graceful_close(self, wait_seconds: float = 5.0) -> None:
        try:
            if self.context is not None:
                await self.context.close()
        except Exception:
            pass
        self.context = None
        self.status = "idle"
        await asyncio.sleep(wait_seconds)
        log(f"worker {self.worker_id} closed (graceful, wal flushed)")


class PoolManager:
    def __init__(self) -> None:
        self.workers: Dict[int, BrowserWorker] = {}
        self._pw = None
        self._runner: Optional[asyncio.Task] = None
        self.job_queue: "asyncio.Queue[str]" = asyncio.Queue()
        self.job_sem = asyncio.Semaphore(3)
        self.started_at = time.time()

    async def start(self) -> None:
        from patchright.async_api import async_playwright
        self._pw = await async_playwright().__aenter__()
        log("playwright manager started")

    async def stop(self) -> None:
        await self.shutdown_all()
        if self._runner:
            self._runner.cancel()
        if self._pw:
            try:
                await self._pw.__aexit__(None, None, None)
            except Exception:
                pass

    def sync_workers(self) -> None:
        rows = db_query("SELECT * FROM workers WHERE enabled=1 ORDER BY worker_id")
        for r in rows:
            wid = int(r["worker_id"])
            if wid not in self.workers:
                fp = json.loads(r["fingerprint"] or "null") or build_fingerprint(wid, r["email"])
                self.workers[wid] = BrowserWorker(wid, r["name"], r["email"], fp)
        for wid in list(self.workers):
            if not any(int(r["worker_id"]) == wid for r in rows):
                del self.workers[wid]

    # -------------------- VB-style API --------------------
    async def launch_worker(self, worker_id: int, headless: bool = False, mode: str = 'job') -> Dict[str, Any]:
        self.sync_workers()
        w = self.workers.get(worker_id)
        if w is None:
            return {"success": False, "error": f"worker {worker_id} not found"}
        async with w.launch_lock:
            if w.status == "open" and w.context is not None:
                try:
                    _ = len(w.context.pages)  # raises once the browser has died
                except Exception:
                    w.status = "idle"
                    w.context = None
            if w.status != "open" or w.context is None:
                if self._pw is None:
                    await self.start()
                try:
                    await w.launch(self._pw, headless=headless, mode=mode)
                except Exception as e:
                    log(f"worker {worker_id} launch failed: {e}")
                    return {"success": False, "error": str(e)}
        return {
            "success": True,
            "data": {
                "http": w.cdp_http,
                "ws": f"ws://127.0.0.1:{w.port}",
                "port": w.port,
                "debuggingPort": w.port,
                "dirId": str(worker_id),
            },
        }

    async def stop_worker(self, worker_id: int) -> Dict[str, Any]:
        self.sync_workers()
        w = self.workers.get(worker_id)
        if w and w.status == "open":
            await w.graceful_close()
        return {"success": True}

    def vb_list(self) -> List[Dict[str, Any]]:
        self.sync_workers()
        return [
            {"id": int(r["worker_id"]), "name": r["name"], "remark": r["email"], "group": ["AI-Pool"]}
            for r in db_query("SELECT * FROM workers ORDER BY worker_id")
        ]

    def running_list(self) -> List[Dict[str, Any]]:
        self.sync_workers()
        return [
            {"id": wid, "name": w.name, "debuggingPort": w.port, "port": w.port}
            for wid, w in self.workers.items() if w.status == "open"
        ]

    async def shutdown_all(self) -> None:
        for w in list(self.workers.values()):
            if w.status == "open":
                await w.graceful_close(3.0)
                break  # sequential for disk safety

    def worker_status(self) -> List[Dict[str, Any]]:
        self.sync_workers()
        rows = db_query("SELECT * FROM workers ORDER BY worker_id")
        out = []
        for r in rows:
            w = self.workers.get(int(r["worker_id"]))
            out.append({
                "worker_id": int(r["worker_id"]),
                "name": r["name"],
                "email": r["email"],
                "enabled": bool(r["enabled"]),
                "status": (w.status if w else "idle"),
                "mode": (w.mode if w else ""),
                "last_login_ok": bool(r["last_login_ok"]),
                "last_login_ts": int(r["last_login_ts"] or 0),
                "quota_left": int(r["quota_left"] or 0),
                "total_generated": int(r["total_generated"] or 0),
                "daily_used": int(r["daily_used"] or 0),
                "last_error": r["last_error"] or "",
                "cdp_port": (w.port if w else WORKER_PORT_BASE + int(r["worker_id"])),
            })
        return out


pool = PoolManager()


# ---------------------------------------------------------------------------
# ChatGPT page driver
# ---------------------------------------------------------------------------

STOP_BTN_SEL = "button[data-testid='stop-button'], button[aria-label*='停止'], button[aria-label*='Stop']"
SEND_BTN_SELS = [
    "button[data-testid='send-button']",
    "button[aria-label*='Send prompt' i]",
    "button[aria-label*='Send' i]",
    "button[aria-label*='发送']",
    "button[aria-label*='傳送']",
    "button[aria-label*='送出']",
]
IMG_SELS = (
    "div[data-message-author-role='assistant'] img, img[alt*='已生成'], "
    "img[alt*='Generated' i], img[src^='blob:'], img[src*='oaiusercontent']"
)


# ---------------------------------------------------------------------------
# 提示詞組成：風格與比例都只是接在文字後面的英文描述，不是 ChatGPT 的參數，
# 兩者都可以留空——空值就是原樣送出。
# ---------------------------------------------------------------------------

STYLE_PROMPTS = {
    "concept_art": "cinematic concept art, volumetric lighting, painterly detail",
    "realistic": "photorealistic, natural light, shallow depth of field, 50mm lens",
    "cyberpunk": "cyberpunk, neon lighting, rain-slick streets, high contrast",
    "anime": "anime illustration, soft cel shading, vibrant colors, studio quality",
    "oil_painting": "oil painting on canvas, visible brush strokes, rich impasto",
    "product": "studio product photography, softbox lighting, seamless backdrop",
}

RATIO_PROMPTS = {
    "16:9": "16:9 cinematic widescreen composition",
    "1:1": "1:1 square composition",
    "9:16": "9:16 vertical portrait composition",
    "4:3": "4:3 classic composition",
    "3:4": "3:4 portrait composition",
}


def compose_prompt(prompt: str, style: str = "", ratio: str = "") -> str:
    """把風格／比例描述接在提示詞後面。style 與 ratio 都是選填。

    style 可以是 STYLE_PROMPTS 的鍵，也可以是自己寫的一段描述；空字串或
    "none" 表示不套用。已經在提示詞裡的文字不會再接一次。
    """
    extras = []
    st = (style or "").strip()
    if st and st.lower() != "none":
        text = STYLE_PROMPTS.get(st, st)
        if text and text not in prompt:
            extras.append(text)
    rt = (ratio or "").strip()
    if rt and rt.lower() != "none":
        text = RATIO_PROMPTS.get(rt, rt)
        if text and text not in prompt:
            extras.append(text)
    if not extras:
        return prompt
    return prompt + "｜" + "｜".join(extras)


async def find_composer(page):
    """ChatGPT 的輸入框。"""
    for sel in ["div#prompt-textarea[contenteditable='true']", "div[contenteditable='true']",
                "#prompt-textarea", "textarea"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return el
        except Exception:
            continue
    return None


async def read_composer(page) -> str:
    try:
        return await page.evaluate("""() => {
            const el = document.querySelector('div#prompt-textarea[contenteditable="true"]')
                || document.querySelector('div[contenteditable="true"]')
                || document.querySelector('#prompt-textarea')
                || document.querySelector('textarea');
            return el ? (el.innerText || el.textContent || el.value || '') : '';
        }""")
    except Exception:
        return ""


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


async def fill_prompt(page, text: str) -> bool:
    """把提示詞確實填進輸入框，並回讀確認。

    2026-10-19 實測：`page.keyboard.type()` 在併發時會把中文吞成 '?'
    （3 併發全中，同時段單跑正常），所以改成 keyboard.insert_text 一次插入，
    送出前再回讀比對；不一致就清空重試一次。
    """
    box = await find_composer(page)
    if box is None:
        try:
            box = await page.wait_for_selector("div[contenteditable='true']", timeout=15000)
        except Exception:
            return False
    try:
        await box.click()
    except Exception:
        pass
    await asyncio.sleep(0.3)
    for attempt in range(2):
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
        except Exception:
            pass
        await asyncio.sleep(0.15)
        try:
            await page.keyboard.insert_text(text)
        except Exception as e:
            log(f"insert_text failed: {e}")
            return False
        await asyncio.sleep(0.6)
        got = await read_composer(page)
        if _norm_text(got) == _norm_text(text):
            return True
        log(f"composer mismatch (try {attempt + 1}): got {len(got)} chars, want {len(text)}")
    return False


async def set_file_input(page, path: str) -> bool:
    """把本機檔案塞進頁面上的檔案輸入框（ChatGPT 的附件按鈕）。"""
    for sel in ["input[type='file']"]:
        try:
            el = await page.query_selector(sel)
            if el is not None:
                await el.set_input_files(path)
                return True
        except Exception as e:
            log(f"set_input_files failed: {e}")
    # 沒有 input 就先點一下附件按鈕，讓它生出來
    for sel in ["button[aria-label*='附加']", "button[aria-label*='Attach' i]",
                "button[aria-label*='新增']", "button[data-testid='attach-button']"]:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await asyncio.sleep(1.2)
                el = await page.query_selector("input[type='file']")
                if el is not None:
                    await el.set_input_files(path)
                    return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# 內嵌手動台：把某個帳號的 ChatGPT 頁面鏡射回面板
# ---------------------------------------------------------------------------

AGENT_STATE = {"last_seen": 0, "label": "", "detail": "", "calls": 0}
EMBED_LOCK = asyncio.Lock()
EMBED_VIEW = {"width": 1200, "height": 800}


async def ensure_page(w: BrowserWorker, ctx):
    page = None
    for p in ctx.pages:
        if "chatgpt.com" in (p.url or ""):
            page = p
            break
    if page is None:
        page = await ctx.new_page()
    if "chatgpt.com" not in (page.url or ""):
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)
    return page


async def check_login(w: BrowserWorker, ctx) -> bool:
    try:
        cookies = await ctx.cookies("https://chatgpt.com")
        return any("session-token" in c.get("name", "") for c in cookies)
    except Exception:
        return False


DEBUG_DIR = LOG_DIR / "login_debug"


async def _snap(page, name: str) -> None:
    """Debug screenshot (headless, invisible to user)."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(DEBUG_DIR / f"{name}_{int(time.time())}.png"))
        log(f"snap {name}: url={page.url[:90]}")
    except Exception as e:
        log(f"snap {name} failed: {e}")


async def try_solve_turnstile(page, state: Optional[Dict[str, Any]] = None) -> bool:
    """Click the Cloudflare Turnstile checkbox with trusted mouse events.

    Synthetic element.click() is detected as non-genuine input; Turnstile
    only grants a token for real pointer events with natural motion. The
    checkbox sits at the iframe's left edge, vertically centered. Capped at
    3 clicks with a cooldown so a stuck widget is not hammered.
    """
    if state is None:
        state = {}
    if state.get("clicks", 0) >= 3:
        return False
    now = time.time()
    if now - state.get("last_click", 0) < 15:
        return False
    box = None
    try:
        el = await page.query_selector(
            "iframe[src*='challenges.cloudflare.com'], iframe[title*='challenge' i], iframe[title*='Cloudflare' i]")
        if el and await el.is_visible():
            box = await el.bounding_box()
    except Exception:
        box = None
    if not box or box.get("width", 0) < 60 or box.get("height", 0) < 30:
        return False
    x = box["x"] + min(30.0, box["width"] * 0.1)
    y = box["y"] + box["height"] / 2
    try:
        await page.mouse.move(
            max(1.0, x - random.uniform(80, 160)),
            max(1.0, y + random.uniform(30, 80)),
            steps=random.randint(6, 10))
        await asyncio.sleep(random.uniform(0.15, 0.4))
        await page.mouse.move(x, y, steps=random.randint(10, 16))
        await asyncio.sleep(random.uniform(0.08, 0.22))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.13))
        await page.mouse.up()
        state["clicks"] = state.get("clicks", 0) + 1
        state["last_click"] = now
        log(f"turnstile: trusted click #{state['clicks']} at ({x:.0f},{y:.0f})")
        return True
    except Exception as e:
        log(f"turnstile click err: {e}")
        return False


async def do_login(w: BrowserWorker, ctx, email: str, password: str, totp_secret: str) -> bool:
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    ts_state: Dict[str, Any] = {"last_click": time.time() - 5}
    log(f"worker {w.worker_id} login: opening auth page")
    try:
        await page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log(f"worker {w.worker_id} nav warn: {e}")
    await asyncio.sleep(5)
    await _snap(page, "01_arrived")

    email_input = None
    for _ in range(35):
        await asyncio.sleep(1)
        await try_solve_turnstile(page, ts_state)
        try:
            email_input = await page.query_selector(
                "input[type='email'], input[name='email'], input[name='username'], input[autocomplete='username'], input[placeholder*='Email' i]")
            if email_input and await email_input.is_visible():
                break
            email_input = None
        except Exception:
            email_input = None
    if email_input is None:
        await _snap(page, "02a_no_email_field")
        return False
    await email_input.click()
    await page.keyboard.type(email, delay=12)
    await asyncio.sleep(1.2)
    try:
        submit = await page.wait_for_selector("button[type='submit'], button.btn-primary", timeout=8000)
        await submit.click()
    except Exception:
        await page.keyboard.press("Enter")
    await asyncio.sleep(3)
    await _snap(page, "02_email_sent")

    pwd_input = None
    for _ in range(40):
        await asyncio.sleep(1)
        await try_solve_turnstile(page, ts_state)
        try:
            p = await page.query_selector("input[type='password'], input[name='password'], #password")
            if p and await p.is_visible():
                pwd_input = p
                break
        except Exception:
            pass
    if pwd_input is None:
        await _snap(page, "03_no_password_field")
        return False
    await pwd_input.click()
    await page.keyboard.type(password, delay=12)
    await asyncio.sleep(1.2)
    try:
        submit2 = await page.wait_for_selector("button[type='submit'], button.btn-primary", timeout=8000)
        await submit2.click()
    except Exception:
        await page.keyboard.press("Enter")
    await asyncio.sleep(3)
    await _snap(page, "04_password_sent")

    if totp_secret:
        code_input = None
        for _ in range(35):
            await asyncio.sleep(1)
            await try_solve_turnstile(page, ts_state)
            cur = page.url or ""
            if "auth" not in cur and "mfa" not in cur and "code" not in cur:
                if "chatgpt.com" in cur:
                    break
                continue
            try:
                el = await page.query_selector(
                    "input[name='code'], input[autocomplete='one-time-code'], input[inputmode='numeric']")
                if el and await el.is_visible():
                    code_input = el
                    break
            except Exception:
                pass
        if code_input:
            totp = pyotp.TOTP(totp_secret).now()
            log(f"worker {w.worker_id} filling TOTP {totp}")
            await code_input.click()
            await page.keyboard.type(totp, delay=80)
            await asyncio.sleep(1)
            try:
                submit3 = await page.wait_for_selector(
                    "button[type='submit'], button.btn-primary", timeout=8000)
                await submit3.click()
            except Exception:
                pass
            await _snap(page, "05_totp_sent")
        else:
            await _snap(page, "06_no_totp_field")

    ok = False
    for _ in range(45):
        await asyncio.sleep(1)
        await try_solve_turnstile(page, ts_state)
        cur = page.url or ""
        if "chatgpt.com" in cur and "auth" not in cur and "mfa" not in cur:
            ok = True
            break
    await asyncio.sleep(6)
    await _snap(page, "07_final")
    has_session = await check_login(w, ctx)
    log(f"worker {w.worker_id} login: nav_ok={ok} session={has_session}")
    return has_session


async def fetch_b64(page, url: str):
    return await page.evaluate(
        """async (url) => {
            const resp = await fetch(url);
            const blob = await resp.blob();
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result.split(',')[1]);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        }""", url)


async def generate_image_inpage(page, prompt: str, ref_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    final_prompt = prompt
    if "立即生成图片" not in prompt and "Generate image directly" not in prompt:
        final_prompt = (
            f"立即生成图片：{prompt}。请直接调用画图工具立即生成图片，"
            "不要输出任何询问文字或反问。"
        )

    if not await fill_prompt(page, final_prompt):
        return {"ok": False, "error": "prompt did not reach the input box (text mismatch)",
                "conversation_url": page.url or ""}
    if ref_paths:
        await attach_assets(page, ref_paths)

    sent = False
    for sel in SEND_BTN_SELS:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_enabled():
                await btn.click()
                sent = True
                break
        except Exception:
            continue
    if not sent:
        await page.keyboard.press("Enter")

    urls: List[str] = []
    seen: Dict[str, bool] = {}
    deadline = time.time() + 300
    while time.time() < deadline:
        await asyncio.sleep(3)
        imgs = await page.query_selector_all(IMG_SELS)
        found = []
        for im in imgs:
            try:
                src = await im.get_attribute("src")
                if not src or "avatar" in src:
                    continue
                if ("oaiusercontent" in src) or src.startswith("blob:"):
                    found.append(src)
            except Exception:
                continue
        still_running = bool(await page.query_selector(STOP_BTN_SEL))
        if found and not still_running:
            urls = list(reversed(found))
            break
        if found and still_running:
            for s in found:
                seen[s] = True
    if not urls:
        urls = [s for s in seen if "oaiusercontent" in s or s.startswith("blob:")]
        if urls:
            urls = list(reversed(urls))

    conv = page.url or ""
    if not urls:
        return {"ok": False, "error": "generation timed out / no image found", "conversation_url": conv}

    downloaded: Dict[str, int] = {}
    for src in urls:
        try:
            b64 = await fetch_b64(page, src)
            if b64:
                downloaded[src] = len(base64.b64decode(b64))
        except Exception as e:
            log(f"image fetch failed {src[:60]}: {e}")
    if not downloaded:
        return {"ok": False, "error": "downloaded 0 images", "urls": urls, "conversation_url": conv}
    return {"ok": True, "sizes": downloaded, "conversation_url": conv}


async def run_generate_job(job_id: str) -> None:
    rows = db_query("SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not rows:
        return
    j = dict(rows[0])
    wid = int(j["worker_id"])
    pool.sync_workers()
    w = pool.workers.get(wid)
    if w is None:
        db_exec("UPDATE jobs SET status='failed', error='worker not found', finished_at=? WHERE job_id=?",
                (int(time.time()), job_id))
        return
    try:
        db_exec("UPDATE jobs SET status='processing', progress=10 WHERE job_id=?", (job_id,))
        if w.status != "open" or w.context is None:
            res = await pool.launch_worker(wid)
            if not res.get("success"):
                raise RuntimeError(res.get("error") or "launch failed")
        ctx = w.context

        if not await check_login(w, ctx):
            accs = {a["email"]: a for a in load_accounts()}
            acc = accs.get(w.email)
            if acc is None:
                raise RuntimeError(f"no credentials for {w.email}")
            db_exec("UPDATE jobs SET progress=20 WHERE job_id=?", (job_id,))
            ok = await do_login(w, ctx, acc["email"], acc["password"], acc["totp_secret"])
            db_exec("UPDATE workers SET last_login_ok=?, last_login_ts=?, last_error=? WHERE worker_id=?",
                    (1 if ok else 0, int(time.time()), "" if ok else "login failed during job", wid))
            if not ok:
                raise RuntimeError("auto-login failed")
        db_exec("UPDATE jobs SET progress=40 WHERE job_id=?", (job_id,))
        page = await ensure_page(w, ctx)
        job_prompt, ref_paths, _used = resolve_refs(j["prompt"])
        result = await generate_image_inpage(page, job_prompt, ref_paths)
        if not result.get("ok"):
            if result.get("conversation_url"):
                db_exec("UPDATE jobs SET conversation_url=? WHERE job_id=?",
                        (result["conversation_url"], job_id))
            raise RuntimeError(result.get("error") or "generation failed")
        db_exec("UPDATE jobs SET progress=85 WHERE job_id=?", (job_id,))

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        saved: List[str] = []
        for idx, (url, size) in enumerate(sorted(result["sizes"].items(), key=lambda kv: -kv[1])):
            b64 = await fetch_b64(page, url)
            if not b64:
                continue
            fname = f"{job_id}_{idx+1}.png"
            (OUTPUT_DIR / fname).write_bytes(base64.b64decode(b64))
            saved.append(str(OUTPUT_DIR / fname))
            log(f"job {job_id} saved {fname} ({size // 1024} KB)")
        db_exec("UPDATE jobs SET status='completed', progress=100, image_paths=?, finished_at=?, conversation_url=? WHERE job_id=?",
                (json.dumps(saved), int(time.time()), result.get("conversation_url", ""), job_id))
        db_exec("UPDATE workers SET daily_used=daily_used+1, total_generated=total_generated+?, quota_left=max(quota_left-1,0) WHERE worker_id=?",
                (len(saved), wid))
    except Exception as e:
        log(f"job {job_id} failed: {e}")
        db_exec("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE job_id=?",
                (str(e)[:500], int(time.time()), job_id))
    finally:
        if w.status == "open" and w.context is not None:
            try:
                await w.graceful_close(4.0)
            except Exception:
                pass


async def _run_with_slot(job_id: str) -> None:
    async with pool.job_sem:
        await run_generate_job(job_id)


async def job_runner_loop() -> None:
    log("job runner ready")
    while True:
        job_id = await pool.job_queue.get()
        asyncio.create_task(_run_with_slot(job_id))


# ---------------------------------------------------------------------------
# API apps
# ---------------------------------------------------------------------------

vb_api = FastAPI()
admin_api = FastAPI()


class VBIdReq(BaseModel):
    id: int
    args: Optional[List[str]] = None
    headless: Optional[bool] = None


class GenerateReq(BaseModel):
    prompt: str
    worker_id: Optional[int] = None
    ratio: Optional[str] = "16:9"
    res: Optional[str] = "1k"
    style: Optional[str] = "concept_art"
    raw_prompt: Optional[bool] = False
    wait: Optional[bool] = False
    timeout: Optional[int] = 300


class LoginReq(BaseModel):
    worker_id: int


class GrabReq(BaseModel):
    worker_id: int


def totp_now(secret: str) -> Dict[str, Any]:
    if not secret:
        return {"code": "", "remaining": 0}
    t = pyotp.TOTP(secret)
    remaining = 30 - int(time.time()) % 30
    return {"code": t.now(), "remaining": remaining}


@vb_api.post("/api/getBrowserList")
async def vb_get_browser_list():
    return {"success": True, "data": pool.vb_list()}


@vb_api.get("/api/getBrowserList")
async def vb_get_browser_list_get():
    return {"success": True, "data": pool.vb_list()}


@vb_api.get("/api/getBrowserRunningList")
async def vb_get_running():
    return {"success": True, "data": pool.running_list()}


@vb_api.post("/api/getBrowserRunningList")
async def vb_get_running_post():
    return {"success": True, "data": pool.running_list()}


@vb_api.post("/api/launchBrowser")
async def vb_launch(req: VBIdReq):
    headless = True if req.headless is None else bool(req.headless)
    return await pool.launch_worker(int(req.id), headless=headless)


@vb_api.post("/api/stopBrowser")
async def vb_stop(req: VBIdReq):
    return await pool.stop_worker(int(req.id))


@vb_api.post("/api/addCrx")
async def vb_add_crx():
    return {"success": True, "msg": "shim: extension logic handled by core driver"}


def _vb_lifespan(main):
    @asynccontextmanager
    async def lifespan(a):
        db_init()
        ensure_worker_rows()
        await pool.start()
        pool.sync_workers()
        pool._runner = asyncio.create_task(job_runner_loop())
        log("AI-Pool core ready (vb :9000 + admin :7707)")
        yield
        await pool.stop()
    return lifespan


vb_api.router.lifespan_context = _vb_lifespan(vb_api)


# admin API endpoints -------------------------------------------------------

@admin_api.get("/api/workers")
async def admin_workers():
    return {"workers": pool.worker_status()}


@admin_api.get("/api/accounts")
async def admin_accounts():
    """Full credential + TOTP info for the manual login support page (local only)."""
    out = []
    for w in pool.worker_status():
        rows = db_query("SELECT totp_secret FROM workers WHERE worker_id=?", (w["worker_id"],))
        secret = rows[0]["totp_secret"] if rows else ""
        t = totp_now(secret)
        accs = {a["email"]: a for a in load_accounts()}
        out.append({**w, "totp": t, "password": (accs.get(w["email"]) or {}).get("password", "")})
    return {"accounts": out}


@admin_api.get("/assistant", response_class=HTMLResponse)
async def admin_assistant():
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).resolve().parent / "helper.html", media_type="text/html")


@admin_api.get("/api/workers/{worker_id}/totp")
async def admin_totp(worker_id: int):
    rows = db_query("SELECT totp_secret FROM workers WHERE worker_id=?", (worker_id,))
    if not rows:
        raise HTTPException(404, "no such worker")
    return totp_now(rows[0]["totp_secret"])


@admin_api.post("/api/login")
async def admin_login(req: LoginReq):
    pool.sync_workers()
    w = pool.workers.get(int(req.worker_id))
    if w is None:
        return {"success": False, "error": "worker not found"}
    accs = {a["email"]: a for a in load_accounts()}
    acc = accs.get(w.email)
    if acc is None:
        return {"success": False, "error": "no credentials on file"}
    res = await pool.launch_worker(w.worker_id, mode='login')
    if not res.get("success"):
        return {"success": False, "error": res.get("error", "launch failed")}
    ctx = w.context
    if await check_login(w, ctx):
        return {"success": True, "already_logged_in": True}
    ok = await do_login(w, ctx, acc["email"], acc["password"], acc["totp_secret"])
    db_exec("UPDATE workers SET last_login_ok=?, last_login_ts=?, last_error=? WHERE worker_id=?",
            (1 if ok else 0, int(time.time()), "" if ok else "login failed", w.worker_id))
    await w.graceful_close(5.0)
    return {"success": ok}


@admin_api.post("/api/close")
async def admin_close(req: VBIdReq):
    return await pool.stop_worker(int(req.id))


@admin_api.post("/api/open")
async def admin_open(req: LoginReq):
    """Open a worker's real Chrome window on screen for manual use."""
    pool.sync_workers()
    res = await pool.launch_worker(int(req.worker_id), headless=False, mode="visible")
    if not res.get("success"):
        return res
    w = pool.workers.get(int(req.worker_id))
    try:
        page = await ensure_page(w, w.context)
        await page.bring_to_front()
    except Exception as e:
        log(f"open visible warn: {e}")
    return res


@admin_api.post("/api/stop_all")
async def admin_stop_all():
    await pool.shutdown_all()
    return {"success": True}


GRAB_JS = """
() => {
    const ok = (s) => !!s && !s.includes("avatar") &&
        (s.includes("oaiusercontent") || s.includes("estuary") ||
         s.startsWith("blob:") || s.startsWith("data:image"));
    const srcOf = (i) => i.currentSrc || i.src || "";
    const msgs = document.querySelectorAll("div[data-message-author-role='assistant']");
    const last = msgs.length ? msgs[msgs.length - 1] : null;
    let srcs = last ? [...last.querySelectorAll("img")].map(srcOf).filter(ok) : [];
    if (!srcs.length) {
        srcs = [...document.querySelectorAll("img")].map(srcOf).filter(ok).slice(-1);
    }
    return [...new Set(srcs)];
}
"""


@admin_api.post("/api/grab")
async def admin_grab(req: GrabReq):
    """Save the newest image(s) from a worker's live ChatGPT page into the gallery."""
    pool.sync_workers()
    w = pool.workers.get(int(req.worker_id))
    if w is None or w.status != "open" or w.context is None:
        return {"success": False, "error": "此 Worker 尚未開啟，請先按「開啟」"}
    page = None
    try:
        for p in reversed(w.context.pages):
            if "chatgpt.com" in (p.url or ""):
                page = p
                break
    except Exception:
        page = None
    if page is None:
        return {"success": False, "error": "視窗中找不到 ChatGPT 頁面"}
    try:
        srcs = await page.evaluate(GRAB_JS)
    except Exception as e:
        return {"success": False, "error": f"掃描頁面失敗：{e}"}
    if not srcs:
        return {"success": False, "error": "頁面上找不到圖片，請先在視窗中出圖"}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    saved: List[str] = []
    for i, src in enumerate(srcs):
        try:
            if src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
            else:
                b64 = await fetch_b64(page, src)
            if not b64:
                continue
            fname = f"manual_w{w.worker_id:02d}_{ts}_{i + 1}.png"
            (OUTPUT_DIR / fname).write_bytes(base64.b64decode(b64))
            saved.append(fname)
            log(f"grab w{w.worker_id}: saved {fname}")
        except Exception as e:
            log(f"grab w{w.worker_id} img {i} failed: {e}")
    if not saved:
        return {"success": False, "error": "找到圖片但下載失敗"}
    return {"success": True, "files": saved,
            "urls": [f"/api/outputs/file/{n}" for n in saved]}


@admin_api.post("/api/open_folder")
async def admin_open_folder():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(OUTPUT_DIR))  # noqa: S606 (local desktop app)
        return {"success": True, "path": str(OUTPUT_DIR)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@admin_api.get("/api/outputs")
async def admin_outputs(limit: int = 60):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(OUTPUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return {"files": [
        {"name": f.name, "size_kb": int(f.stat().st_size / 1024), "mtime": int(f.stat().st_mtime)}
        for f in files
    ]}


@admin_api.get("/api/outputs/file/{name}")
async def admin_output_file(name: str):
    safe = Path(name).name
    if not safe or Path(safe).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "bad name")
    p = OUTPUT_DIR / safe
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p)


@admin_api.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    return FileResponse(Path(__file__).resolve().parent / "panel.html", media_type="text/html")


# the panel's own webfont (Public Sans), served locally so the console works offline
admin_api.mount("/fonts", StaticFiles(directory=Path(__file__).resolve().parent / "fonts"), name="fonts")
# 品牌素材（logo / banner）
admin_api.mount("/brand", StaticFiles(directory=Path(__file__).resolve().parent.parent / "brand"), name="brand")


async def submit_job(prompt: str, worker_id: Optional[int], style: str, ratio: str,
                     res: str, wrap: bool = True) -> str:
    """Shared job submission used by admin /api/generate and the /v1 bridge."""
    pool.sync_workers()
    target = worker_id
    if target is None:
        rows = db_query(
            "SELECT worker_id FROM workers WHERE enabled=1 AND last_login_ok=1 "
            "ORDER BY quota_left DESC, daily_used ASC")
        cands = []
        for r in rows:
            wobj = pool.workers.get(int(r["worker_id"]))
            if wobj is not None and wobj.mode == "embed":
                continue          # 使用者正在手動台操作這個帳號，先不排給它
            cands.append(int(r["worker_id"]))
        target = cands[0] if cands else 1
    final_prompt = prompt
    final_prompt = compose_prompt(prompt, style, ratio)
    if wrap and "立即生成图片" not in final_prompt:
        final_prompt = (f"立即生成图片：{final_prompt}。请直接调用画图工具立即生成图片，"
                        "不要输出任何询问文字或反问。")
    job_id = uuid.uuid4().hex[:22]
    db_exec(
        "INSERT INTO jobs (job_id, worker_id, prompt, raw_prompt, style, ratio, res, status, progress, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (job_id, target, final_prompt, prompt, style, ratio, res, "queued", 0, int(time.time())))
    await pool.job_queue.put(job_id)
    return job_id


@admin_api.post("/api/generate")
async def admin_generate(req: GenerateReq):
    job_id = await submit_job(req.prompt, req.worker_id, req.style, req.ratio, req.res,
                              wrap=not req.raw_prompt)
    if req.wait:
        deadline = time.time() + int(req.timeout or 300)
        while time.time() < deadline:
            rows = db_query("SELECT status FROM jobs WHERE job_id=?", (job_id,))
            st = rows[0]["status"] if rows else "failed"
            if st in ("completed", "failed"):
                break
            await asyncio.sleep(3)
    rows = db_query("SELECT * FROM jobs WHERE job_id=?", (job_id,))
    j = {}
    if rows:
        j = dict(rows[0])
        j["image_paths"] = json.loads(j.get("image_paths") or "[]")
    return {"job_id": job_id, **j}


# ---------------------------------------------------------------------------
# NewAPI-compatible bridge (/v1/videos + /v1/tasks) — agent-facing
# ---------------------------------------------------------------------------

def _load_v1_keys() -> set:
    """/v1/* 的存取金鑰：內建本機預設 + secrets/api_keys.txt（一行一個，自己加）+ 環境變數 AIPOOL_API_KEYS（逗號分隔）。"""
    keys = {"aipool-local"}
    try:
        keys |= {ln.strip() for ln in (SECRETS_DIR / "api_keys.txt").read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")}
    except Exception:
        pass
    keys |= {k.strip() for k in os.environ.get("AIPOOL_API_KEYS", "").split(",") if k.strip()}
    return keys


V1_API_KEYS = _load_v1_keys()


def _auth_v1(request: Request) -> None:
    auth = request.headers.get("authorization", "") or ""
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    if key not in V1_API_KEYS:
        raise HTTPException(401, "invalid api key")


class VideosReq(BaseModel):
    model: str = "gpt-image2-1k"
    prompt: str = ""
    aspect_ratio: str = "16:9"
    duration: Optional[int] = None
    worker_id: Optional[int] = None
    style: str = ""          # 選填：STYLE_PROMPTS 的鍵或自寫的描述，空值＝不套用


def _png_size(path: str) -> str:
    try:
        import struct as _struct
        with open(path, "rb") as f:
            d = f.read(24)
        w, h = _struct.unpack(">II", d[16:24])
        return f"{w}x{h}"
    except Exception:
        return ""


@admin_api.post("/v1/videos")
async def v1_videos(req: VideosReq, request: Request):
    """NewAPI-compatible task creation endpoint backed by the local pool."""
    _auth_v1(request)
    if not req.prompt.strip():
        raise HTTPException(400, "prompt is empty")
    res = req.model.replace("gpt-image2-", "").strip() or "1k"
    if res not in ("1k", "2k", "4k"):
        res = "1k"
    ratio = req.aspect_ratio if req.aspect_ratio in ("16:9", "1:1", "9:16", "4:3", "3:4") else "16:9"
    job_id = await submit_job(req.prompt, req.worker_id, req.style, ratio, res, wrap=True)
    return {"id": job_id, "task_id": job_id, "object": "video", "status": "queued",
            "progress": 0, "model": req.model, "aspect_ratio": ratio}


@admin_api.get("/v1/tasks/{task_id}")
async def v1_task(task_id: str, request: Request):
    _auth_v1(request)
    rows = db_query("SELECT * FROM jobs WHERE job_id=?", (task_id,))
    if not rows:
        raise HTTPException(404, "task not found")
    j = dict(rows[0])
    status = j.get("status") or "queued"
    out: Dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "progress": int(j.get("progress") or 0),
        "result": None,
        "error_message": j.get("error") or None,
    }
    if status == "completed":
        paths = json.loads(j.get("image_paths") or "[]")
        urls = [f"http://127.0.0.1:7707/api/outputs/file/{Path(p).name}" for p in paths]
        out["result"] = {
            "type": "gpt_workflow_image",
            "workflow_kind": "image",
            "conversation_id": (j.get("conversation_url") or "").rstrip("/").split("/")[-1],
            "urls": urls,
            "image_url": urls[0] if urls else "",
            "model": f"gpt-image2-{j.get('res') or '1k'}",
            "resolution": j.get("res") or "1k",
            "aspect_ratio": j.get("ratio") or "16:9",
            "size": _png_size(paths[0]) if paths else "",
        }
    return out



# ---------------------------------------------------------------------------
# 內嵌手動台 + agent 連線狀態
# ---------------------------------------------------------------------------

class EmbedReq(BaseModel):
    worker_id: int = 0
    x: float = 0
    y: float = 0
    text: str = ""
    worker_ids: List[int] = []
    asset_ids: List[str] = []


class AgentPing(BaseModel):
    label: str = "agent"
    detail: str = ""


class UploadReq(BaseModel):
    worker_id: int = 0
    filename: str = "reference.png"
    data_b64: str = ""


class AgentCfg(BaseModel):
    provider: str = ""
    name: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    extra_args: str = ""
    enabled: bool = True
    mode: str = ""


class OptimizeReq(BaseModel):
    prompt: str = ""
    refs: List[str] = []


async def _embed_target(worker_id: int):
    """取得（必要時開啟）某個帳號的瀏覽器與 ChatGPT 頁面。"""
    pool.sync_workers()
    w = pool.workers.get(worker_id)
    if w is None:
        raise HTTPException(404, "worker not found")
    if w.status != "open" or w.context is None:
        res = await pool.launch_worker(worker_id, mode="embed")
        if not res.get("success"):
            raise HTTPException(500, res.get("error") or "launch failed")
    page = await ensure_page(w, w.context)
    if page is None:
        raise HTTPException(500, "no chatgpt page")
    if "chatgpt.com" not in (page.url or ""):
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
    return w, page


@admin_api.post("/api/embed/open")
async def embed_open(req: EmbedReq):
    async with EMBED_LOCK:
        return await _embed_open_locked(req)


async def _embed_open_locked(req: EmbedReq):
    pool.sync_workers()
    # 切換帳號：先把手動台上一個帳號關掉（同一時間只留一個背景瀏覽器）
    for wid, obj in pool.workers.items():
        if wid != req.worker_id and obj.status == "open" and obj.mode == "embed":
            await pool.stop_worker(wid)
    w = pool.workers.get(req.worker_id)
    # 已經是實體視窗的話先關掉，改成離屏的內嵌模式（手動台不會在桌面上留視窗）
    if w is not None and w.status == "open" and w.mode != "embed":
        await pool.stop_worker(req.worker_id)
    w, page = await _embed_target(req.worker_id)
    return {"success": True, "worker_id": req.worker_id, "url": page.url,
            "viewport": page.viewport_size}


@admin_api.get("/api/embed/frame")
async def embed_frame(worker_id: int):
    pool.sync_workers()
    w = pool.workers.get(worker_id)
    if w is None or w.context is None:
        raise HTTPException(404, "worker not open")
    pages = [p for p in w.context.pages if "chatgpt.com" in (p.url or "")]
    if not pages:
        raise HTTPException(404, "no chatgpt page")
    page = pages[-1]
    shot = await page.screenshot(type="jpeg", quality=62, timeout=10000)
    return Response(content=shot, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@admin_api.post("/api/embed/click")
async def embed_click(req: EmbedReq):
    w, page = await _embed_target(req.worker_id)
    await page.mouse.click(req.x, req.y)
    return {"success": True}


@admin_api.post("/api/embed/type")
async def embed_type(req: EmbedReq):
    w, page = await _embed_target(req.worker_id)
    text, paths, _used = resolve_refs(req.text)
    ok = await fill_prompt(page, text)
    if ok and paths:
        await attach_assets(page, paths)
    return {"success": ok, "error": "" if ok else "text mismatch"}


@admin_api.post("/api/embed/send")
async def embed_send(req: EmbedReq):
    w, page = await _embed_target(req.worker_id)
    sent = False
    for sel in SEND_BTN_SELS:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_enabled():
                await btn.click()
                sent = True
                break
        except Exception:
            continue
    if not sent:
        await page.keyboard.press("Enter")
    return {"success": True, "sent": sent}


@admin_api.post("/api/embed/upload")
async def embed_upload(req: UploadReq):
    w, page = await _embed_target(req.worker_id)
    if not req.data_b64:
        raise HTTPException(400, "no file")
    raw = base64.b64decode(req.data_b64.split(",")[-1])
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", req.filename or "reference.png")[:60]
    dest = OUTPUT_DIR / ("_upload_" + safe)
    dest.write_bytes(raw)
    ok = await set_file_input(page, str(dest))
    return {"success": ok, "path": str(dest), "size": len(raw)}


@admin_api.post("/api/embed/close")
async def embed_close(req: EmbedReq):
    async with EMBED_LOCK:
        pool.sync_workers()
        w = pool.workers.get(req.worker_id)
        if w is None:
            raise HTTPException(404, "worker not found")
        await pool.stop_worker(req.worker_id)
        return {"success": True}


@admin_api.post("/api/agent/ping")
async def agent_ping(req: AgentPing):
    AGENT_STATE["last_seen"] = int(time.time())
    AGENT_STATE["label"] = (req.label or "agent")[:40]
    if req.detail:
        AGENT_STATE["detail"] = req.detail[:200]
    AGENT_STATE["calls"] += 1
    return {"ok": True, "ts": AGENT_STATE["last_seen"], "calls": AGENT_STATE["calls"]}


ACCOUNT_SEP = "----"

# ---------------------------------------------------------------------------
# 資產區：使用者上傳的參考圖，依上傳順序編號（圖1、圖2…），可排序、可刪除
# ---------------------------------------------------------------------------

ASSETS_DIR = BASE_DIR / "data" / "assets"
ASSETS_META = ASSETS_DIR / "assets.json"


def _assets_load() -> List[Dict[str, Any]]:
    try:
        return json.loads(ASSETS_META.read_text(encoding="utf-8"))
    except Exception:
        return []


def _assets_save(items: List[Dict[str, Any]]) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_META.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _assets_relabel(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, it in enumerate(items, start=1):
        it = dict(it)
        it["label"] = f"圖{i}"
        it["index"] = i
        out.append(it)
    return out


@admin_api.get("/api/assets")
async def assets_list():
    return {"assets": _assets_relabel(_assets_load())}


@admin_api.post("/api/assets/upload")
async def assets_upload(req: UploadReq):
    if not req.data_b64:
        raise HTTPException(400, "no file")
    raw = base64.b64decode(req.data_b64.split(",")[-1])
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    items = _assets_load()
    aid = uuid.uuid4().hex[:10]
    ext = pathlib.Path(req.filename or "ref.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".png"
    dest = ASSETS_DIR / (aid + ext)
    dest.write_bytes(raw)
    items.append({"id": aid, "name": req.filename or (aid + ext), "file": dest.name,
                   "size": len(raw), "added": int(time.time())})
    _assets_save(items)
    return {"success": True, "assets": _assets_relabel(items)}


@admin_api.get("/api/assets/file/{asset_id}")
async def assets_file(asset_id: str):
    for it in _assets_load():
        if it["id"] == asset_id:
            p = ASSETS_DIR / it["file"]
            if p.exists():
                return FileResponse(p)
    raise HTTPException(404, "not found")


@admin_api.post("/api/assets/reorder")
async def assets_reorder(req: EmbedReq):
    order = req.asset_ids or []
    items = _assets_load()
    by_id = {i["id"]: i for i in items}
    new = [by_id[i] for i in order if i in by_id]
    new += [i for i in items if i["id"] not in order]
    _assets_save(new)
    return {"success": True, "assets": _assets_relabel(new)}


@admin_api.post("/api/assets/delete")
async def assets_delete(req: EmbedReq):
    items = _assets_load()
    keep = []
    removed = None
    for it in items:
        if it["id"] == req.text:
            removed = it
        else:
            keep.append(it)
    if removed is None:
        return {"success": False, "error": "not found"}
    try:
        (ASSETS_DIR / removed["file"]).unlink()
    except Exception:
        pass
    _assets_save(keep)
    return {"success": True, "assets": _assets_relabel(keep)}


def resolve_refs(text: str) -> tuple:
    """把提示詞裡的 @圖1 / @圖2 換成模型看得懂的說法，並回傳要附上的檔案。"""
    items = _assets_relabel(_assets_load())
    paths = []
    used = []

    def repl(m):
        n = int(m.group(1))
        if 1 <= n <= len(items):
            it = items[n - 1]
            used.append(it)
            paths.append(ASSETS_DIR / it["file"])
            return f"第{n}張參考圖"
        return m.group(0)

    out = re.sub(r"@圖(\d+)", repl, text or "")
    return out, paths, used


async def attach_assets(page, paths) -> int:
    """把參考圖附加到 ChatGPT 的輸入框。"""
    n = 0
    for p in paths:
        try:
            if await set_file_input(page, str(p)):
                n += 1
                await asyncio.sleep(1.0)
        except Exception as e:
            log(f"attach asset failed: {e}")
    return n


# ---------------------------------------------------------------------------
# 圖庫管理
# ---------------------------------------------------------------------------

@admin_api.post("/api/outputs/delete")
async def outputs_delete(req: EmbedReq):
    name = pathlib.Path(req.text or "").name
    if not name:
        raise HTTPException(400, "no name")
    p = OUTPUT_DIR / name
    if not p.exists():
        return {"success": False, "error": "not found"}
    try:
        p.unlink()
    except Exception as e:
        return {"success": False, "error": str(e)}
    with sqlite3.connect(DB_PATH) as conn:
        for r in conn.execute("SELECT job_id, image_paths FROM jobs").fetchall():
            try:
                paths = json.loads(r[1] or "[]")
            except Exception:
                paths = []
            if any(pathlib.Path(x).name == name for x in paths):
                conn.execute("UPDATE jobs SET image_paths=?, image_urls='[]' WHERE job_id=?",
                             (json.dumps([x for x in paths if pathlib.Path(x).name != name]), r[0]))
        conn.commit()
    return {"success": True, "remaining": len(list(OUTPUT_DIR.glob("*.png")))}


# ---------------------------------------------------------------------------
# 提示詞庫（awesome-gpt-image-2 的案例庫，離線快取）
# ---------------------------------------------------------------------------

LIBRARY_URL = "https://gpt-image2.canghe.ai/cases.json"
LIBRARY_CACHE = BASE_DIR / "data" / "prompts.json"
_LIBRARY: Dict[str, Any] = {"cases": [], "ts": 0}


async def _load_library() -> List[Dict[str, Any]]:
    if _LIBRARY["cases"]:
        return _LIBRARY["cases"]
    try:
        if LIBRARY_CACHE.exists():
            data = json.loads(LIBRARY_CACHE.read_text(encoding="utf-8"))
            _LIBRARY["cases"] = data.get("cases") or []
            _LIBRARY["ts"] = int(LIBRARY_CACHE.stat().st_mtime)
            return _LIBRARY["cases"]
    except Exception:
        pass
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(LIBRARY_URL)
            r.raise_for_status()
            data = r.json()
        LIBRARY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        LIBRARY_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _LIBRARY["cases"] = data.get("cases") or []
        _LIBRARY["ts"] = int(time.time())
    except Exception as e:
        log(f"prompt library fetch failed: {e}")
        _LIBRARY["cases"] = []
    return _LIBRARY["cases"]


@admin_api.get("/api/prompts")
async def api_prompts(q: str = "", category: str = "", limit: int = 40):
    cases = await _load_library()
    cats = sorted({c.get("category") or "" for c in cases if c.get("category")})
    qq = (q or "").strip().lower()
    out = []
    for c in cases:
        if category and c.get("category") != category:
            continue
        if qq:
            hay = ((c.get("title") or "") + " " + (c.get("prompt") or "")).lower()
            if qq not in hay:
                continue
        out.append({
            "id": c.get("id"), "title": c.get("title") or c.get("promptPreview") or "",
            "prompt": c.get("prompt") or "", "category": c.get("category") or "",
            "styles": c.get("styles") or [], "image": c.get("image") or "",
            "sourceUrl": c.get("sourceUrl") or "",
        })
        if len(out) >= limit:
            break
    return {"cases": out, "categories": cats, "total": len(cases), "cached_at": _LIBRARY["ts"]}


# ---------------------------------------------------------------------------
# Agent 設定與「AI 優化提示詞」
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 模型商註冊表
#
# 兩種接法，介面也分開：
#   cli          本機 CLI —— 自動偵測 PATH 上裝了哪些 agent CLI，背景靜默執行
#                （codex / claude 的模型清單會用它們 CLI 的本機憑證查上游目錄，只讀、不複製 token）
#   api          API 金鑰 —— 選一個服務商預設（或自訂），填 key，模型清單從 /models 偵測
# 每個模型商都會自動偵測可用模型；偵測失敗會回報原因（不是靜默給空清單）。
# ---------------------------------------------------------------------------

AGENT_CONFIG_PATH = SECRETS_DIR / "agent.json"

AGENT_PROVIDER_DEFS = [
    {"id": "codex", "name": "Codex 憑證", "kind": "cred", "wire": "responses",
     "base_url": "https://chatgpt.com/backend-api/codex", "path": "/responses",
     "cred": "~/.codex/auth.json", "token_path": ["tokens", "access_token"],
     "account_path": ["tokens", "account_id"], "account_header": "chatgpt-account-id",
     # client_version 太舊會回空清單；新版本號才會列出目前方案可用的模型
     "models_url": "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0",
     "headers": {"OpenAI-Beta": "responses=experimental", "originator": "codex_cli_rs"},
     "login": "codex login", "note": "ChatGPT / Codex 訂閱"},
    {"id": "claude", "name": "Claude 憑證", "kind": "cred", "wire": "anthropic",
     "base_url": "https://api.anthropic.com/v1", "path": "/messages",
     "cred": "~/.claude/.credentials.json", "token_path": ["claudeAiOauth", "accessToken"],
     "models_url": "https://api.anthropic.com/v1/models",
     "headers": {"anthropic-version": "2023-06-01", "anthropic-beta": "oauth-2025-04-20"},
     "login": "claude auth login", "note": "Claude Pro / Max 訂閱"},
    {"id": "codex-cli", "name": "Codex CLI", "kind": "cli", "cmd": "codex",
     "args": ["exec", "--json", "--ephemeral", "--skip-git-repo-check",
              "--sandbox", "read-only", "{prompt}"],
     "model_arg": "-m", "inherit": "codex", "install": "npm i -g @openai/codex",
     "login": "codex login"},
    {"id": "claude-cli", "name": "Claude Code CLI", "kind": "cli", "cmd": "claude",
     "args": ["-p", "{prompt}", "--output-format", "json", "--tools", "",
              "--no-session-persistence"],
     "model_arg": "--model", "inherit": "claude", "install": "npm i -g @anthropic-ai/claude-code",
     "login": "claude auth login"},
    {"id": "opencode-cli", "name": "OpenCode CLI", "kind": "cli", "cmd": "opencode",
     "args": ["run", "--format", "json", "{prompt}"], "model_arg": "-m",
     "models_cmd": ["models"], "install": "npm i -g opencode-ai", "login": "opencode auth login"},
    {"id": "agy-cli", "name": "Antigravity CLI", "kind": "cli", "cmd": "agy",
     "args": ["-p", "{prompt}", "--output-format", "json"], "model_arg": "--model",
     "models_cmd": ["models"], "install": "irm https://antigravity.google/cli/install.ps1 | iex",
     "login": "agy"},
    {"id": "api-openai", "name": "OpenAI", "kind": "api", "wire": "chat",
     "base_url": "https://api.openai.com/v1", "note": "官方 API key"},
    {"id": "api-opencode-go", "name": "OpenCode Go", "kind": "api", "wire": "chat",
     "base_url": "https://opencode.ai/inference/go/v1", "note": "OpenCode Go 訂閱附的 API key"},
    {"id": "api-commandcode", "name": "Command Code", "kind": "api", "wire": "chat",
     "base_url": "https://api.commandcode.ai/provider/v1", "messages_for": "claude",
     "note": "Command Code 帳號的 API key（claude 系模型自動走 /messages）"},
]

SCAN_TIMEOUT = 25


def _all_defs() -> List[Dict[str, Any]]:
    custom = [{"id": c["id"], "name": c["name"], "kind": "api", "wire": "chat", "custom": True,
               "base_url": c.get("base_url") or "", "note": "自訂服務商"}
              for c in (load_agent_config().get("custom") or [])]
    return AGENT_PROVIDER_DEFS + custom


def _listed() -> List[Dict[str, Any]]:
    """介面上列出的：扣掉憑證來源（cred）與使用者刪掉的。"""
    hidden = set(load_agent_config().get("hidden") or [])
    return [p for p in _all_defs() if p["kind"] != "cred" and p["id"] not in hidden]


def _listing(state: Dict[str, Any]) -> Dict[str, Any]:
    hidden = set(load_agent_config().get("hidden") or [])
    return {"providers": [_item(p, state.get(p["id"]) or {}) for p in _listed()],
            "active": _active_id(),
            "hidden": [{"id": p["id"], "name": p["name"], "kind": p["kind"]}
                       for p in AGENT_PROVIDER_DEFS if p["id"] in hidden and p["kind"] != "cred"]}


def _active_id() -> str:
    """選用中的模型商；舊設定存的是已移除的訂閱制 id，自動對應到同家的 CLI。"""
    a = load_agent_config().get("active") or ""
    a = {"claude": "claude-cli", "codex": "codex-cli"}.get(a, a)
    return a if any(p["id"] == a for p in _listed()) else ""


def _prov_def(pid: str) -> Optional[Dict[str, Any]]:
    return next((p for p in _all_defs() if p["id"] == pid), None)


def _dig(data: Any, path: List[str]) -> str:
    cur = data
    for k in path:
        cur = cur.get(k) if isinstance(cur, dict) else None
    return str(cur or "")


def _read_cred(prov: Dict[str, Any]) -> str:
    """每次呼叫現讀官方 CLI 的憑證檔（CLI 會自己 refresh，所以不快取、不存進 agent.json）。"""
    if not prov.get("cred"):
        return ""
    try:
        data = json.loads(Path(os.path.expanduser(prov["cred"])).read_text(encoding="utf-8"))
        return _dig(data, prov.get("token_path") or [])
    except Exception:
        return ""


def _sub_headers(prov: Dict[str, Any], token: str) -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
    h.update(prov.get("headers") or {})
    if prov.get("account_path"):
        try:
            data = json.loads(Path(os.path.expanduser(prov["cred"])).read_text(encoding="utf-8"))
            acc = _dig(data, prov["account_path"])
            if acc:
                h[prov["account_header"]] = acc
        except Exception:
            pass
    return h


def _agent_state() -> Dict[str, Any]:
    return load_agent_config().get("providers") or {}


def _agent_save_state(state: Dict[str, Any], active: str = "") -> None:
    cfg = load_agent_config()
    cfg["providers"] = state
    if active:
        cfg["active"] = active
    save_agent_config(cfg)


def _installed(prov: Dict[str, Any]) -> Optional[bool]:
    return bool(shutil.which(prov["cmd"])) if prov.get("cmd") else None


async def _models_from_url(url: str, headers: Dict[str, str]) -> List[str]:
    import httpx
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"模型目錄回應 {r.status_code}")
    data = r.json()
    items = data
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or data.get("model_list") or []
    out: List[str] = []
    for it in items or []:
        if isinstance(it, dict):
            if str(it.get("visibility") or "list") != "list":
                continue
            mid = it.get("id") or it.get("slug") or it.get("name") or it.get("model")
            if mid:
                out.append(str(mid))
        elif isinstance(it, str):
            out.append(it)
    return out


async def _fetch_models(prov: Dict[str, Any], base_url: str = "", key: str = "") -> tuple:
    """自動偵測可用模型 → (模型清單, 失敗原因, 是否為靜態清單)。"""
    kind = prov.get("kind")
    try:
        if kind == "cli":
            if not _installed(prov):
                return [], "這台電腦還沒裝", False
            inherit = _prov_def(prov.get("inherit") or "")
            if inherit:
                tok = _read_cred(inherit)
                if tok:
                    models = await _models_from_url(inherit["models_url"], _sub_headers(inherit, tok))
                    if models:
                        return models, "", False
            if prov.get("models_cmd"):
                raw = await _run_agent_cli(resolve_agent_cmd(prov["cmd"]) + prov["models_cmd"], timeout=40)
                models = []
                for line in (raw or "").splitlines():
                    slug = line.strip().split("\t")[0].strip()
                    if slug and " " not in slug and not slug.lower().startswith("fetching"):
                        models.append(slug)
                return models, ("" if models else "CLI 沒有回傳模型"), False
            if prov.get("static_models"):
                return list(prov["static_models"]), "", True
            return [], "這個 CLI 沒有提供模型清單", False
        base = (base_url or prov.get("base_url") or "").rstrip("/")
        if not base:
            return [], "請先填 base_url", False
        if not key:
            return [], "請先填 API key", False
        models = await _models_from_url(base + "/models", {"Authorization": "Bearer " + key})
        return models, ("" if models else "服務商回傳空清單"), False
    except Exception as e:
        return [], str(e)[:200] or e.__class__.__name__, False


def _item(prov: Dict[str, Any], st: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": prov["id"], "name": prov["name"], "kind": prov["kind"],
        "note": prov.get("note") or "", "install": prov.get("install") or "",
        "login": prov.get("login") or "",
        "installed": _installed(prov),
        "has_cred": bool(_read_cred(prov)) if prov.get("cred") else None,
        "has_key": bool(st.get("api_key")),
        "base_url": st.get("base_url") or prov.get("base_url") or "",
        "connected": bool(st.get("connected")),
        "model": st.get("model") or "", "models": st.get("models") or [],
        "static": bool(st.get("static")), "error": st.get("error") or "",
    }


def _apply_models(st: Dict[str, Any], models: List[str], err: str, static: bool, auto: bool = True) -> None:
    st["error"] = err
    if models:
        st["models"], st["static"] = models, static
        if st.get("model") not in models:
            st["model"] = models[0] if auto else ""


async def _detect(prov: Dict[str, Any], st: Dict[str, Any]) -> Dict[str, Any]:
    """偵測一個模型商並回傳更新後的 state。「已連接」＝模型清單拿得到。"""
    st = dict(st)
    try:
        models, err, static = await asyncio.wait_for(
            _fetch_models(prov, st.get("base_url", ""), st.get("api_key", "")), SCAN_TIMEOUT)
    except asyncio.TimeoutError:
        models, err, static = [], "偵測逾時", False
    # CLI 的清單來自上游目錄，可能含帳號不能用的型號；預設不指定，讓 CLI 用自己的預設
    _apply_models(st, models, err, static, auto=prov["kind"] != "cli")
    st["connected"] = bool(models)
    st["checked"] = time.time()
    return st


def _parse_jsonl(text: str) -> List[str]:
    """從 codex 的 JSONL 事件流（或 claude 的 stream-json）取出最終回覆。"""
    out: List[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "item.completed":
            it = ev.get("item") or {}
            if it.get("type") == "agent_message" and it.get("text"):
                out.append(str(it["text"]))
        elif ev.get("type") == "result" and ev.get("result"):
            out.append(str(ev["result"]))
        elif ev.get("type") == "text" and (ev.get("part") or {}).get("text"):  # opencode
            out.append(str(ev["part"]["text"]))
    return out


def load_agent_config() -> Dict[str, Any]:
    try:
        return json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_agent_config(cfg: Dict[str, Any]) -> None:
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def resolve_agent_cmd(name: str) -> List[str]:
    """把 agent CLI 解析成可以直接執行的指令。

    Windows 上 npm 的 shim 是 .cmd（subprocess 不能直接跑），而且 .cmd 會走 cmd 的字串解析，
    提示詞裡的 & | < > % 會被吃掉。所以這裡繞開 shim：
      1. PATH 上第一個同名指令本身若是 .exe 就直接用（agy.exe）
      2. 否則解析 .cmd 裡指的 .exe 或 node + .js（codex / claude-code 都是這樣）
      3. 再退回同名的 .exe，最後才是 cmd.exe（此時提示詞要自己小心）
    """
    import os
    import re
    import shutil
    # 以使用者在終端機打 `name` 會跑到的那一個為準（可能是比 PATH 上另一個 .exe 更新的 npm 版本，
    # 例如 codex 桌面版內附 0.143、npm 是 0.163，舊的會被伺服器拒絕新模型）
    shim = shutil.which(name)
    if not shim:
        raise RuntimeError(f"找不到 {name}（先在「Agent 設定」裡看安裝指令）")
    if shim.lower().endswith((".exe",)):
        return [shim]
    try:
        txt = Path(shim).with_suffix(".cmd").read_text(encoding="utf-8", errors="replace")
        base = str(Path(shim).parent)
        for e in re.findall(r'"([^"]+\.exe)"', txt, re.I):
            e = e.replace("%dp0%", base).replace("%~dp0", base)
            if Path(e).exists():
                return [e]
        for j in re.findall(r'"([^"]+\.js)"', txt, re.I):
            j = j.replace("%dp0%", base).replace("%~dp0", base)
            if Path(j).exists():
                return ["node", j]
    except Exception:
        pass
    exe = shutil.which(name + ".exe")
    if exe:
        return [exe]
    return ["cmd.exe", "/d", "/c", shim]


def _extract_agent_text(raw: str) -> str:
    """從 agent 的輸出裡取出最終回覆（支援 codex 的 JSONL 與各家的 JSON）。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    lines = _parse_jsonl(raw)
    if lines:
        return lines[-1].strip()
    # 事件流裡若有錯誤要如實回報；頂層的 error／turn.failed 才是真正失敗，item 裡的 error 多半只是警告
    top_err = item_err = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "turn.failed":
            err = ev.get("error") or {}
            top_err = top_err or str(err.get("message") if isinstance(err, dict) else err)
        elif t == "item.completed":
            it = ev.get("item") or {}
            if it.get("type") == "error" and it.get("message"):
                item_err = item_err or str(it["message"])
        elif t == "error":
            e = ev.get("error") or {}
            top_err = top_err or str(ev.get("message") or (e.get("data") or {}).get("message") or e.get("message") or "")
    if top_err or item_err:
        msg = top_err or item_err
        try:  # message 有時是整段 JSON 字串
            inner = json.loads(msg)
            msg = (inner.get("error") or {}).get("message") or msg
        except Exception:
            pass
        raise RuntimeError(msg[:400])
    try:
        data = json.loads(raw)
    except Exception:
        for line in raw.splitlines():
            if "ERROR" in line or "error" in line.lower():
                return ""
        return raw
    if isinstance(data, dict):
        if data.get("is_error"):
            raise RuntimeError(str(data.get("result") or "agent 回報錯誤")[:400])
        status = str(data.get("status") or "").upper()
        if status in ("ERROR", "FAILED"):
            raise RuntimeError(str(data.get("error") or data.get("response") or "agent 回報錯誤")[:400])
        for k in ("result", "response", "text", "content"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    return raw


async def _run_agent_cli(cmd: List[str], timeout: int = 180) -> str:
    import subprocess
    # CREATE_NO_WINDOW：背景跑 CLI 時不要跳出黑視窗；stdin 關掉，否則 opencode 之類會一直等輸入
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    # 丟到執行緒：subprocess.run 會卡住整個事件迴圈（出圖與兩個 API 都共用同一個）
    p = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
        encoding="utf-8", errors="replace", creationflags=flags, startupinfo=_hidden_startupinfo())
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0 and not out:
        raise RuntimeError(f"agent 執行失敗（{p.returncode}）：{err[-400:]}")
    if not out:
        raise RuntimeError("agent 沒有輸出：" + err[-300:])
    return out


async def _call_http(wire: str, url: str, headers: Dict[str, str], model: str,
                     system: str, user: str) -> str:
    import httpx
    if wire == "anthropic":
        body: Dict[str, Any] = {"model": model, "max_tokens": 2048,
                                "messages": [{"role": "user", "content": user}]}
        if system:
            body["system"] = system
    elif wire == "responses":
        # Codex 後端強制 stream=true 且一定要有 instructions
        body = {"model": model, "store": False, "stream": True,
                "instructions": system or "You are a helpful assistant.",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": user}]}]}
    else:
        body = {"model": model, "messages": ([{"role": "system", "content": system}] if system else [])
                                            + [{"role": "user", "content": user}]}
    if wire == "responses":
        parts: List[str] = []
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"回應 {r.status_code}：{(await r.aread()).decode('utf-8', 'replace')[:220]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "response.output_text.delta":
                        parts.append(ev.get("delta") or "")
                    elif ev.get("type") in ("response.failed", "error"):
                        raise RuntimeError(json.dumps(ev, ensure_ascii=False)[:300])
        return "".join(parts).strip()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, headers=headers, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"回應 {r.status_code}：{r.text[:220]}")
    data = r.json()
    if wire == "anthropic":
        return "".join(b.get("text") or "" for b in data.get("content") or [] if b.get("type") == "text").strip()
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise RuntimeError("回應格式不符：" + json.dumps(data, ensure_ascii=False)[:200])


async def run_provider(pid: str, system: str, user: str, state: Dict[str, Any]) -> str:
    """用指定的模型商產生文字。"""
    prov = _prov_def(pid)
    if prov is None:
        raise RuntimeError("未知的模型商：" + pid)
    st = (state or {}).get(pid) or {}
    model = st.get("model") or ""
    kind = prov.get("kind")
    if kind == "cred":
        raise RuntimeError("這個項目只用來查模型清單，不能直接呼叫")
    if kind == "api":
        if not model:
            models, err, _ = await _fetch_models(prov, st.get("base_url", ""), st.get("api_key", ""))
            if not models:
                raise RuntimeError(err or "沒有可用模型")
            model = models[0]
        base = (st.get("base_url") or prov.get("base_url") or "").rstrip("/")
        key = st.get("api_key") or ""
        if not base or not key:
            raise RuntimeError("還沒填 base_url / API key")
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        if prov.get("messages_for") and model.startswith(prov["messages_for"]):
            headers["anthropic-version"] = "2023-06-01"
            return await _call_http("anthropic", base + "/messages", headers, model, system, user)
        return await _call_http("chat", base + "/chat/completions", headers, model, system, user)
    # CLI 沒有 system 欄位，把兩段併成一個訊息
    cli_prompt = (system + "\n\n" + user).strip() if system else user
    cmd = resolve_agent_cmd(prov["cmd"]) + [a.replace("{prompt}", cli_prompt) for a in prov["args"]]
    if model and prov.get("model_arg"):
        cmd += [prov["model_arg"], model]
    log(f"agent {pid}: {' '.join(c[:50] for c in cmd[:3])} …")
    raw = await _run_agent_cli(cmd, timeout=120)
    text = _extract_agent_text(raw)
    if not text:
        err = ""
        for line in (raw or "").splitlines():
            if "ERROR" in line or "error" in line.lower():
                err = line.strip()
        raise RuntimeError(err[:400] or "agent 沒有輸出")
    return text


@admin_api.get("/api/agent/providers")
async def agent_providers():
    """列出所有模型商：狀態、可用模型、目前選用的模型（用上次偵測的快取，不連網）。"""
    state = _agent_state()
    return _listing(state)


@admin_api.post("/api/agent/scan")
async def agent_scan(force: bool = False):
    """一次偵測全部：PATH 上裝了的 CLI、填過 key 的 API。並行、各自逾時。"""
    state = _agent_state()
    now = time.time()

    def fresh(p):  # CLI 的模型清單 15 分鐘內不重查（少跑 CLI，視窗也開得快）；按「重新偵測全部」才強制
        st = state.get(p["id"]) or {}
        return p["kind"] == "cli" and not force and st.get("connected") and st.get("models") and now - st.get("checked", 0) < 900

    todo = [p for p in _listed()
            if ((p["kind"] == "cli" and _installed(p))
                or (p["kind"] == "api" and (state.get(p["id"]) or {}).get("api_key"))) and not fresh(p)]
    results = await asyncio.gather(*[_detect(p, state.get(p["id"]) or {}) for p in todo])
    for p, st in zip(todo, results):
        state[p["id"]] = st
    for p in _listed():  # 沒裝 / 沒填 key 的：清掉過期的已連接狀態（快取還新鮮而略過的不算）
        if p not in todo and p["id"] in state and not fresh(p):
            state[p["id"]]["connected"] = False
    _agent_save_state(state)
    return _listing(state)


@admin_api.post("/api/agent/provider/save")
async def agent_provider_save(req: AgentCfg):
    """存 base_url / api_key / model（API 類），然後重新偵測模型。"""
    prov = _prov_def(req.provider)
    if prov is None or prov["kind"] == "cred" or prov not in _listed():
        return {"success": False, "error": "未知的模型商"}
    state = _agent_state()
    st = dict(state.get(prov["id"]) or {})
    if prov["kind"] == "api":
        if req.base_url:
            st["base_url"] = req.base_url.strip()
        if req.api_key:
            st["api_key"] = req.api_key.strip()
    if req.model:
        st["model"] = req.model
    st = await _detect(prov, st)
    state[prov["id"]] = st
    _agent_save_state(state)
    return {"success": bool(st["connected"]), "error": st.get("error") or "",
            "provider": _item(prov, st)}


@admin_api.post("/api/agent/provider/detect")
async def agent_provider_detect(req: AgentCfg):
    """重新偵測單一模型商的可用模型（訂閱／CLI 的「連接」也是這個）。"""
    return await agent_provider_save(AgentCfg(provider=req.provider))


@admin_api.post("/api/agent/provider/test")
async def agent_provider_test(req: AgentCfg):
    """真的送一句話測試。成功才算連得通。"""
    prov = _prov_def(req.provider)
    if prov is None:
        return {"success": False, "error": "未知的模型商"}
    state = _agent_state()
    st = dict(state.get(prov["id"]) or {})
    if req.model:
        st["model"] = req.model
    state[prov["id"]] = st
    try:
        out = await run_provider(prov["id"], "", "Reply with the single word: OK", state)
    except Exception as e:
        return {"success": False, "error": str(e)[:400], "provider": _item(prov, st)}
    st["connected"], st["error"] = True, ""
    _agent_save_state(state)
    return {"success": True, "reply": out[:200], "error": "", "provider": _item(prov, st)}


@admin_api.post("/api/agent/provider/delete")
async def agent_provider_delete(req: AgentCfg):
    """刪除：自訂的整個移除；內建的收進「已移除」（可還原）。連同 key、模型設定一起清掉。"""
    prov = _prov_def(req.provider)
    if prov is None or prov["kind"] == "cred":
        return {"success": False, "error": "未知的模型商"}
    cfg = load_agent_config()
    if prov.get("custom"):
        cfg["custom"] = [c for c in (cfg.get("custom") or []) if c["id"] != prov["id"]]
    else:
        cfg["hidden"] = sorted(set(cfg.get("hidden") or []) | {prov["id"]})
    (cfg.get("providers") or {}).pop(prov["id"], None)
    if cfg.get("active") == prov["id"]:
        cfg["active"] = ""
    save_agent_config(cfg)
    return {"success": True, **_listing(cfg.get("providers") or {})}


@admin_api.post("/api/agent/provider/add")
async def agent_provider_add(req: AgentCfg):
    """新增一個自訂的 OpenAI 相容服務商（名稱 + base_url，key 之後在卡片裡填）。"""
    name, base = req.name.strip(), req.base_url.strip()
    if not name or not base.startswith(("http://", "https://")):
        return {"success": False, "error": "請填名稱與 http(s):// 開頭的 base_url"}
    cfg = load_agent_config()
    ids = {p["id"] for p in _all_defs()}
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "custom"
    pid, i = "api-" + slug, 2
    while pid in ids:
        pid, i = f"api-{slug}-{i}", i + 1
    cfg["custom"] = (cfg.get("custom") or []) + [{"id": pid, "name": name, "base_url": base.rstrip("/")}]
    save_agent_config(cfg)
    return {"success": True, "id": pid, **_listing(cfg.get("providers") or {})}


@admin_api.post("/api/agent/provider/restore")
async def agent_provider_restore(req: AgentCfg):
    """還原已刪除的內建項目（provider 留空＝全部還原）。"""
    cfg = load_agent_config()
    cfg["hidden"] = [h for h in (cfg.get("hidden") or []) if req.provider and h != req.provider]
    save_agent_config(cfg)
    return {"success": True, **_listing(cfg.get("providers") or {})}


@admin_api.post("/api/agent/select")
async def agent_select(req: AgentCfg):
    """指定 AI 優化要用的模型商。"""
    if _prov_def(req.provider) is None or _prov_def(req.provider) not in _listed():
        return {"success": False, "error": "未知的模型商"}
    state = _agent_state()
    if req.model:  # 下拉選了模型就記住（enabled=false 代表只記模型、不切換選用）；__default__＝回到 CLI 預設
        state[req.provider] = {**(state.get(req.provider) or {}), "model": "" if req.model == "__default__" else req.model}
    _agent_save_state(state, req.provider if req.enabled else "")
    return {"success": True, "active": req.provider if req.enabled else load_agent_config().get("active", "")}


def _scrub_agent_config() -> None:
    """舊版把訂閱的 OAuth token 抄進 agent.json；現在一律現讀，把殘留的刪掉。"""
    cfg = load_agent_config()
    dirty = bool(cfg.pop("subscriptions", None))
    known = {p["id"] for p in _all_defs()}
    for pid in [k for k in (cfg.get("providers") or {}) if k not in known]:
        cfg["providers"].pop(pid)
        dirty = True
    for st in (cfg.get("providers") or {}).values():
        if isinstance(st, dict) and st.pop("token", None):
            dirty = True
    if dirty:
        save_agent_config(cfg)


_scrub_agent_config()

OPTIMIZE_SYSTEM = (
    "You are a prompt engineer for gpt-image-2. Rewrite the user's rough idea into one vivid "
    "English image prompt: subject, composition, lighting, materials, style and lens. "
    "Reply with the prompt text only - no quotes, no explanation, no markdown."
)


@admin_api.post("/api/prompt/optimize")
async def prompt_optimize(req: OptimizeReq):
    raw = (req.prompt or "").strip()
    if not raw:
        raise HTTPException(400, "prompt is empty")
    text, _paths, used = resolve_refs(raw)
    hint = ""
    if used:
        hint = "\n\nThe user attached these reference images (in order): " + ", ".join(
            f"image {i+1}" for i in range(len(used)))
    cfg = load_agent_config()
    state = cfg.get("providers") or {}
    active = _active_id()
    # 只試「選用的」＋「已連接的」，沒連接的不浪費逾時
    order = ([active] if active else []) + [
        p["id"] for p in _listed()
        if p["id"] != active and (state.get(p["id"]) or {}).get("connected")]
    if not order:
        return {"success": False, "error": "還沒有可用的模型商，先到「Agent 設定」連接一個"}
    errs = []
    for pid in order:
        try:
            out = await run_provider(pid, OPTIMIZE_SYSTEM, text + hint, state)
            if out and out.strip():
                return {"success": True, "prompt": out.strip(), "provider": pid,
                        "model": (state.get(pid) or {}).get("model", "")}
        except Exception as e:
            errs.append(f"{pid}：{str(e)[:150]}")
    return {"success": False, "error": " → ".join(errs)[:600]}

LOGIN_STATE = {"running": False, "done": 0, "total": 0, "current": 0, "message": ""}


def _parse_accounts(text: str) -> List[Dict[str, str]]:
    """解析帳號檔：每行 email----password----2FA_secret，# 開頭是註解。"""
    out: List[Dict[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(ACCOUNT_SEP)
        if len(parts) < 3:
            continue
        email, password, secret = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not email or not password or not secret:
            continue
        out.append({"email": email, "password": password, "totp_secret": secret})
    return out


@admin_api.post("/api/accounts/import")
async def accounts_import(req: EmbedReq):
    """匯入帳號檔（email----password----2FA_secret，每行一個）。"""
    text = req.text or ""
    accs = _parse_accounts(text)
    if not accs:
        return {"success": False, "error": "沒有解析到任何帳號，請確認格式"}
    path = SECRETS_DIR / "accounts.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".txt.bak"))
    path.write_text("\n".join(
        f"{a['email']}{ACCOUNT_SEP}{a['password']}{ACCOUNT_SEP}{a['totp_secret']}" for a in accs
    ) + "\n", encoding="utf-8")
    # 同步 worker 資料（email / 2FA），指紋不動（profile 已經存在）
    with sqlite3.connect(DB_PATH) as conn:
        for i, a in enumerate(accs, start=1):
            conn.execute("UPDATE workers SET email=?, totp_secret=? WHERE worker_id=?", (a["email"], a["totp_secret"], i))
        conn.commit()
    pool.sync_workers()
    log(f"imported {len(accs)} accounts from panel")
    return {"success": True, "count": len(accs), "backup": str(path.with_suffix('.txt.bak'))}


async def _auto_login_worker(wid: int) -> bool:
    pool.sync_workers()
    w = pool.workers.get(wid)
    if w is None:
        return False
    accs = {a["email"]: a for a in load_accounts()}
    row = db_query("SELECT email FROM workers WHERE worker_id=?", (wid,))
    email = row[0]["email"] if row else ""
    acc = accs.get(email)
    if acc is None:
        db_exec("UPDATE workers SET last_login_ok=0, last_error='no credentials' WHERE worker_id=?", (wid,))
        return False
    try:
        res = await pool.launch_worker(wid, mode="login")
        if not res.get("success"):
            raise RuntimeError(res.get("error") or "launch failed")
        ok = await check_login(w, w.context)
        if not ok:
            ok = await do_login(w, w.context, acc["email"], acc["password"], acc["totp_secret"])
        db_exec("UPDATE workers SET last_login_ok=?, last_login_ts=?, last_error=? WHERE worker_id=?",
                (1 if ok else 0, int(time.time()), "" if ok else "login failed", wid))
        return bool(ok)
    except Exception as e:
        log(f"auto login worker {wid} failed: {e}")
        db_exec("UPDATE workers SET last_login_ok=0, last_error=? WHERE worker_id=?", (str(e)[:120], wid))
        return False
    finally:
        try:
            await w.graceful_close(wait_seconds=1.0)
        except Exception:
            pass


async def _auto_login_all(worker_ids: List[int]) -> None:
    LOGIN_STATE["running"] = True
    LOGIN_STATE["total"] = len(worker_ids)
    LOGIN_STATE["done"] = 0
    try:
        for wid in worker_ids:
            LOGIN_STATE["current"] = wid
            LOGIN_STATE["message"] = f"登入中 W{wid:02d}"
            await _auto_login_worker(wid)
            LOGIN_STATE["done"] += 1
    finally:
        LOGIN_STATE["running"] = False
        LOGIN_STATE["current"] = 0
        LOGIN_STATE["message"] = ""


@admin_api.post("/api/login/all")
async def login_all(req: EmbedReq):
    """依序把沒登入的帳號自動登入（Turnstile + 密碼 + TOTP），每個約 40-60 秒。"""
    if LOGIN_STATE["running"]:
        return {"success": False, "error": "已經在登入中了"}
    ids = req.worker_ids or []
    if not ids:
        rows = db_query("SELECT worker_id FROM workers WHERE last_login_ok=0 ORDER BY worker_id")
        ids = [int(r["worker_id"]) for r in rows]
    if not ids:
        return {"success": True, "message": "所有帳號都已登入", "count": 0}
    asyncio.create_task(_auto_login_all(ids))
    return {"success": True, "count": len(ids), "message": f"開始登入 {len(ids)} 個帳號"}


@admin_api.get("/api/login/state")
async def login_state():
    return dict(LOGIN_STATE)


@admin_api.get("/api/agent/status")
async def agent_status():
    last = int(AGENT_STATE["last_seen"] or 0)
    return {"online": bool(last and time.time() - last < 90), "last_seen": last,
            "label": AGENT_STATE["label"], "detail": AGENT_STATE.get("detail", ""),
            "calls": AGENT_STATE["calls"]}

@admin_api.get("/api/jobs")
async def admin_jobs(limit: int = 30):
    rows = db_query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    return {"jobs": [dict(r) for r in rows]}


@admin_api.get("/api/jobs/{job_id}")
async def admin_job(job_id: str):
    rows = db_query("SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not rows:
        raise HTTPException(404, "no such job")
    j = dict(rows[0])
    j["image_paths"] = json.loads(j.get("image_paths") or "[]")
    j["image_urls"] = json.loads(j.get("image_urls") or "[]")
    return j


@admin_api.get("/health")
async def admin_health():
    return {"ok": True, "uptime_s": int(time.time() - pool.started_at)}


def _asset_add_file(src: Path, name: str) -> str:
    """把一個本機圖檔加進資產區，回傳它的標籤（圖N）。"""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    items = _assets_load()
    aid = uuid.uuid4().hex[:10]
    dest = ASSETS_DIR / (aid + (src.suffix.lower() or ".png"))
    shutil.copy2(src, dest)
    items.append({"id": aid, "name": name, "file": dest.name, "size": dest.stat().st_size, "added": int(time.time())})
    _assets_save(items)
    return _assets_relabel(items)[-1]["label"]


# 角色設計頁（詞庫、角色紀錄、AI 分析／潤飾、角色資產區）
import character_api  # noqa: E402

character_api.install(admin_api, {
    "data_dir": BASE_DIR / "data", "output_dir": OUTPUT_DIR,
    "db_query": db_query, "submit_job": submit_job, "add_asset": _asset_add_file,
    "run_provider": run_provider, "agent_state": _agent_state, "active_id": _active_id, "listed": _listed,
})


if __name__ == "__main__":
    db_init()
    ensure_worker_rows()
    import uvicorn
    config_vb = uvicorn.Config(vb_api, host="127.0.0.1", port=9000, log_level="warning")
    config_ad = uvicorn.Config(admin_api, host="127.0.0.1", port=7707, log_level="warning")
    s_vb = uvicorn.Server(config_vb)
    s_ad = uvicorn.Server(config_ad)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(asyncio.gather(s_vb.serve(), s_ad.serve()))
