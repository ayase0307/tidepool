"""把 onetake 錄好的 footage（webm + frames + info.json）轉成 README 用的 GIF。

用法：python demo/build_gifs.py <footage 目錄> [輸出目錄=docs/demo]
需要 ffmpeg、Pillow、numpy。

- 從 onetake 的開始標記（左下洋紅方塊）消失後才切起，標記本身不入鏡。
- 劇本在等 AI / 偵測時會在右下角放一個 20px 綠色方塊（__d.fast(1)），
  這裡把有綠塊的畫格（前後各多 1 格）整段剪掉 → 等待只留開頭那一下光條，直接接到結果。
- 寬 960、12fps、128 色。Playwright 錄的 webm 每格都有細小雜訊，GIF 會因此整張重畫；
  所以先做「區塊保持」：16×16 區塊和上一張輸出差不到門檻就沿用舊的，只有真的在動的地方會更新。
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

src = Path(sys.argv[1])
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "docs" / "demo"
dst.mkdir(parents=True, exist_ok=True)
info = json.loads((src / "info.json").read_text(encoding="utf-8"))
NAMES = {"01-generate": "generate", "02-character": "character", "03-agents": "agents", "04-gallery": "gallery"}
FPS, WIDTH, COLORS = 12, 960, 128
BLOCK, THRESH, MEAN_T = 16, 24, 2.5  # 區塊大小、最大色差門檻、平均色差門檻（0–255）
FULL = 0.25  # 超過這個比例的區塊在動，就整張更新


def corner(f, right):
    im = Image.open(f)
    w, h = im.size
    return im.getpixel((w - 8, h - 8) if right else (6, h - 8))


def is_green(px):
    return px[1] > 180 and px[0] < 110 and px[2] < 110


def is_magenta(px):
    return px[0] > 180 and px[2] > 180 and px[1] < 90


def cut_ranges(frames):
    """回傳要剪掉的畫格區間 [(a, b)]（含頭尾），依右下角綠塊判斷。"""
    ranges = []
    for i, f in enumerate(frames):
        if not is_green(corner(f, True)):
            continue
        if ranges and i <= ranges[-1][1] + 2:
            ranges[-1][1] = i
        else:
            ranges.append([i, i])
    return [(a - 1, b + 1) for a, b in ranges]


def hold_blocks(prev, cur):
    """cur 中和 prev 差異小於門檻的 16×16 區塊改用 prev 的內容。"""
    h, w, _ = cur.shape
    d = np.abs(cur.astype(np.int16) - prev.astype(np.int16)).max(axis=2)
    hb, wb = -(-h // BLOCK), -(-w // BLOCK)
    pad = np.zeros((hb * BLOCK, wb * BLOCK), np.int16)
    pad[:h, :w] = d
    blocks = pad.reshape(hb, BLOCK, wb, BLOCK)
    # 尖峰大（游標、文字）或整塊平均偏移（淡入淡出殘留）都算在動；雜訊兩者都很小
    moving = (blocks.max(axis=(1, 3)) > THRESH) | (blocks.mean(axis=(1, 3)) > MEAN_T)
    if moving.mean() > FULL:  # 捲動、淡入淡出：整張換新，避免殘留舊區塊
        return cur.copy()
    mask = np.repeat(np.repeat(moving, BLOCK, 0), BLOCK, 1)[:h, :w]
    out = prev.copy()
    out[mask] = cur[mask]
    return out


# 自我檢查：小雜訊被吃掉、真的變化保留
_a = np.zeros((32, 32, 3), np.uint8)
_b = _a.copy(); _b[0, 0] = 10; _b[20, 20] = 200
_o = hold_blocks(_a, _b)
assert _o[0, 0, 0] == 0 and _o[20, 20, 0] == 200
assert hold_blocks(_a, _a + 10)[0, 0, 0] == 10  # 整張淡入：全部更新

for case, meta in info.items():
    frames = sorted((src / "frames" / case).glob("*.jpg"))
    start = meta.get("mark") or 0
    while start < len(frames) and is_magenta(corner(frames[start], False)):
        start += 1
    start += 2
    cuts = cut_ranges(frames)
    keep = f"gte(n\\,{start})" + "".join(f"*not(between(n\\,{a}\\,{b}))" for a, b in cuts)
    w0, h0 = Image.open(frames[0]).size
    W, H = WIDTH, int(round(h0 * WIDTH / w0 / 2)) * 2
    out = dst / (NAMES.get(case, case) + ".gif")
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(src / f"{case}.webm"), "-vf",
         f"fps=30,select='{keep}',setpts=N/30/TB,fps={FPS},scale={W}:{H}:flags=lanczos",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-vf", f"split[a][b];[a]palettegen=max_colors={COLORS}:stats_mode=diff[p];"
                f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
         "-loop", "0", str(out)], stdin=subprocess.PIPE)
    prev, n = None, 0
    while True:
        buf = dec.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        cur = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        prev = cur.copy() if prev is None else hold_blocks(prev, cur)
        enc.stdin.write(prev.tobytes())
        n += 1
    enc.stdin.close()
    if dec.wait() or enc.wait():
        sys.exit(f"ffmpeg failed on {case}")
    print(f"{out.name}: {n / FPS:.1f}s, start {start}, cut {[(round(a / 30, 1), round(b / 30, 1)) for a, b in cuts]}, "
          f"{out.stat().st_size / 1e6:.2f} MB")
