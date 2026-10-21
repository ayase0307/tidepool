"""AI-Pool CLI generator (agent-facing).

Submits a generation job to the local pool and saves returned images.

Usage:
  python generate.py "一只赛博朋克橘猫飞行员" --style anime --ratio 16:9 -o cat.png
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

POOL = "http://127.0.0.1:7707"  # admin API

STYLE_PROMPTS = {
    "concept_art": "cinematic concept art, volumetric lighting, painterly detail",
    "realistic": "photorealistic, 85mm lens, shallow depth of field, natural lighting",
    "cyberpunk": "cyberpunk, neon glow, rain-slick streets, blade-runner mood",
    "anime": "anime illustration, soft cel shading, vibrant colors, studio quality",
    "oil_painting": "classical oil painting, thick brush texture, chiaroscuro",
    "product": "commercial product photography, studio softbox lighting, clean background",
}

RATIO_PROMPTS = {
    "16:9": "16:9 cinematic widescreen composition",
    "1:1": "1:1 square composition",
    "9:16": "9:16 vertical portrait composition",
    "4:3": "4:3 composition",
    "3:4": "3:4 vertical composition",
}


def http_json(method: str, url: str, data: Optional[Dict[str, Any]] = None,
              timeout: int = 330) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        url, data=json.dumps(data or {}).encode("utf-8") if data is not None else None,
        headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def optimize_prompt(prompt: str, style: str, ratio: str) -> str:
    extras = []
    if style in STYLE_PROMPTS:
        extras.append(STYLE_PROMPTS[style])
    if ratio in RATIO_PROMPTS:
        extras.append(RATIO_PROMPTS[ratio])
    if extras:
        prompt = f"{prompt}，{('，').join(extras)}"
    return prompt


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="AI-Pool ChatGPT image generator")
    ap.add_argument("prompt")
    ap.add_argument("-o", "--output", default="", help="output image path")
    ap.add_argument("-s", "--style", default="concept_art",
                    choices=list(STYLE_PROMPTS.keys()))
    ap.add_argument("-r", "--ratio", default="16:9",
                    choices=list(RATIO_PROMPTS.keys()))
    ap.add_argument("--res", default="1k", choices=["1k", "2k", "4k"])
    ap.add_argument("-w", "--worker", type=int, default=None, help="pin to a specific worker id")
    ap.add_argument("--raw-prompt", action="store_true", help="skip wrapper/optimizer")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--pool", default=POOL)
    args = ap.parse_args()

    health = http_json("GET", f"{args.pool}/health", timeout=10)
    if not health.get("ok"):
        print("[!] pool not serving correctly")
        return 2

    prompt = args.prompt
    if not args.raw_prompt:
        prompt = optimize_prompt(prompt, args.style, args.ratio)
    print("=" * 64)
    print(f"[*] prompt: {prompt}")
    print("=" * 64)

    res = http_json("POST", f"{args.pool}/api/generate", {
        "prompt": prompt,
        "worker_id": args.worker,
        "ratio": args.ratio,
        "res": args.res,
        "style": args.style,
        "raw_prompt": True,
        "wait": True,
        "timeout": args.timeout,
    }, timeout=args.timeout + 60)

    job_id = res.get("job_id")
    status = res.get("status")
    if status != "completed":
        print(f"[!] job {job_id} ended as {status}: {res.get('error')}")
        print(f"    see: {args.pool}/api/jobs/{job_id}")
        return 3

    paths: List[str] = res.get("image_paths") or []
    if not paths:
        print("[!] completed but no image paths")
        return 4

    out_path = Path(args.output) if args.output else Path(f"ai-collection/{job_id}_1.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = Path(paths[0])
    if src.resolve() != out_path.resolve():
        shutil.copy2(src, out_path)
    for extra in paths[1:]:
        extra_dst = out_path.with_name(f"{out_path.stem}_{len(paths)}{out_path.suffix}")
        shutil.copy2(extra, extra_dst)
        print(f"[+] extra image: {extra_dst}")

    kb = out_path.stat().st_size / 1024
    print(f"[SUCCESS] {out_path.resolve()} ({kb:.0f} KB, worker {res.get('worker_id')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
