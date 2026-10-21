#!/usr/bin/env python3
"""Tidepool 出圖：送出 → 輪詢 → 下載，一個指令做完。只用標準函式庫（Python 3.8+，Windows / macOS / Linux 都能跑）。

用法：
  python generate_image.py "提示詞" [-o out.png] [--ratio 16:9] [--style ""] [--n 1] [--worker 0] [--timeout 300]

進度與說明印在 stderr；**stdout 的最後一行永遠是一行 JSON**（給 agent 讀）：
  成功 {"ok": true, "files": ["out.png"], "seconds": 41, "worker": 3, "task_ids": ["…"]}
  失敗 {"ok": false, "stage": "health|workers|submit|poll|download", "code": "service_down|no_login|auth|job_failed|timeout|…",
        "error": "原始錯誤", "hint": "建議怎麼處理"}
環境變數：TIDEPOOL_URL（服務網址）、TIDEPOOL_KEY（金鑰，預設 aipool-local）。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("TIDEPOOL_URL", "__BASE_URL__").rstrip("/")
KEY = os.environ.get("TIDEPOOL_KEY", "aipool-local")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Fail(Exception):
    def __init__(self, stage, code, error, hint):
        super().__init__(error)
        self.stage, self.code, self.error, self.hint = stage, code, error, hint


def http(method, path, body=None, auth=False, timeout=30, raw=False):
    req = urllib.request.Request(BASE + path, method=method)
    if body is not None:
        req.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", "Bearer " + KEY)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if raw else json.loads(data.decode("utf-8"))


def check_service():
    try:
        http("GET", "/health", timeout=8)
    except Exception as e:
        raise Fail("health", "service_down", str(e),
                   f"連不上 {BASE}。請使用者啟動 Tidepool（Windows：執行 apps\\start_service.vbs；其他系統：python core/pool_service.py），"
                   "啟動後再重試。網址或連接埠不同的話設環境變數 TIDEPOOL_URL。")
    try:
        ws = http("GET", "/api/workers", timeout=10).get("workers", [])
    except Exception as e:
        raise Fail("workers", "workers_unreadable", str(e), "服務有回應但讀不到帳號清單，請使用者看控制台是否正常。")
    ok = [w for w in ws if w.get("last_login_ok")]
    if not ok:
        raise Fail("workers", "no_login", f"{len(ws)} 個帳號，0 個已登入",
                   f"沒有已登入的帳號。請使用者開 {BASE}/panel#/accounts，按「全部重新登入」或檢查 secrets/accounts.txt。")
    return len(ws), len(ok)


def one_task(prompt, ratio, style, worker, timeout, tag):
    body = {"model": "gpt-image2-1k", "prompt": prompt, "aspect_ratio": ratio, "style": style}
    if worker:
        body["worker_id"] = worker
    try:
        sub = http("POST", "/v1/videos", body, auth=True)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise Fail("submit", "auth", "401 invalid api key",
                       "金鑰不對。預設是 aipool-local；若使用者改過，請他把金鑰放進 secrets/api_keys.txt，並用環境變數 TIDEPOOL_KEY 提供。")
        raise Fail("submit", "submit_failed", f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}", "送出被拒絕，看 error 內容。")
    except Exception as e:
        raise Fail("submit", "service_down", str(e), "送出時連線中斷，確認服務還在跑。")
    tid = sub.get("id") or sub.get("task_id")
    t0 = time.time()
    last = -1
    while time.time() - t0 < timeout:
        time.sleep(4)
        try:
            st = http("GET", f"/v1/tasks/{tid}", auth=True)
        except Exception as e:
            log(f"[{tag}] 輪詢暫時失敗：{e}")
            continue
        if st.get("progress") != last:
            last = st.get("progress")
            log(f"[{tag}] {st.get('status')} {last}%")
        if st.get("status") == "completed":
            urls = (st.get("result") or {}).get("urls") or []
            if not urls:
                raise Fail("poll", "no_image", "completed 但沒有圖片網址", "ChatGPT 沒有回圖，換一句提示詞或稍後重試。")
            return tid, urls, int(time.time() - t0)
        if st.get("status") == "failed":
            raise Fail("poll", "job_failed", str(st.get("error_message") or "unknown"),
                       "這個帳號這次沒出圖：常見原因是該帳號到了 ChatGPT 的使用上限、被暫時限流，或頁面異常。"
                       "可以用 --worker 指定別的帳號、等一會再試；連續失敗就停止並回報使用者，不要一直重試。")
    raise Fail("poll", "timeout", f"超過 {timeout} 秒", "逾時。可能所有帳號都在忙或 ChatGPT 很慢；稍後再試，或把 --timeout 加大。")


def download(url, path):
    from urllib.parse import urlparse
    data = http("GET", urlparse(url).path, raw=True, timeout=60)  # 一律走 BASE，不管服務回報的網址寫什麼
    with open(path, "wb") as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser(description="Tidepool 出圖")
    ap.add_argument("prompt")
    ap.add_argument("-o", "--out", default="tidepool.png")
    ap.add_argument("--ratio", default="16:9", choices=["16:9", "1:1", "9:16", "4:3", "3:4"])
    ap.add_argument("--style", default="")
    ap.add_argument("--n", type=int, default=1, help="一次出幾張（會平行送到不同帳號）")
    ap.add_argument("--worker", type=int, default=0, help="指定帳號編號；0＝自動")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--retry", type=int, default=1, help="失敗時重試次數（預設 1）")
    a = ap.parse_args()
    t0 = time.time()
    try:
        total, ok = check_service()
        log(f"服務正常：{total} 個帳號、{ok} 個已登入")
        try:
            http("POST", "/api/agent/ping", {"label": "tidepool-image"}, timeout=5)
        except Exception:
            pass
        n = max(1, min(a.n, ok))
        if a.n > ok:
            log(f"--n {a.n} 超過已登入帳號數，改成 {n}")
        base, ext = os.path.splitext(a.out)
        ext = ext or ".png"

        def job(i):
            tag = f"{i + 1}/{n}"
            for attempt in range(a.retry + 1):
                try:
                    return one_task(a.prompt, a.ratio, a.style, a.worker, a.timeout, tag)
                except Fail as e:
                    if e.code == "job_failed" and attempt < a.retry:
                        log(f"[{tag}] 失敗（{e.error}），5 秒後重試一次")
                        time.sleep(5)
                        continue
                    raise

        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(job, i) for i in range(n)]
            results = []
            for f in futs:
                results.append(f.result())
        files = []
        for i, (tid, urls, secs) in enumerate(results):
            path = a.out if n == 1 else f"{base}-{i + 1}{ext}"
            try:
                download(urls[0], path)
            except Exception as e:
                raise Fail("download", "download_failed", str(e), f"圖已經產生但下載失敗，可直接開 {urls[0]}")
            files.append(os.path.abspath(path))
        print(json.dumps({"ok": True, "files": files, "seconds": int(time.time() - t0),
                          "task_ids": [r[0] for r in results], "urls": [r[1][0] for r in results]}, ensure_ascii=False))
    except Fail as e:
        print(json.dumps({"ok": False, "stage": e.stage, "code": e.code, "error": e.error, "hint": e.hint}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:  # 保底：任何沒預期到的錯也要給 agent 一行 JSON
        print(json.dumps({"ok": False, "stage": "unknown", "code": "unexpected", "error": repr(e),
                          "hint": "沒預期到的錯誤，把 error 原文回報給使用者。"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
