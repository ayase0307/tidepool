"""角色設計頁的後端：角色紀錄、AI 分析／潤飾、出圖連結與角色資產區。

詞庫與版式規則來自 Character-Prompt-Studio（MIT，見 character/LICENSE）。
這個模組不 import pool_service（避免循環），由 pool_service 呼叫 install() 把需要的東西傳進來。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "character"
STATIC_OK = {"compose.js", "character.js", "character.css", "lexicon.json", "presets.json", "NOTICE.md", "LICENSE"}
MAX_TEXT = 20000


class CharSave(BaseModel):
    id: str = ""
    name: str = ""
    form: Dict[str, Any] = {}
    prompt: str = ""
    note: str = ""


class CharId(BaseModel):
    id: str = ""
    file: str = ""
    name: str = ""
    worker_id: Optional[int] = None
    prompt: str = ""


class CharText(BaseModel):
    text: str = ""
    prompt: str = ""
    note: str = ""
    provider: str = ""


# 給 AI 的規則：精簡自 ai-image-prompt 的 SKILL.md（硬資訊不丟、不加戲、一致性逐條釘死、收尾只留 2~3 項）
POLISH_SYSTEM = (
    "You refine image-generation prompts for AI character reference sheets (GPT-Image-2 style natural language).\n"
    "Rules:\n"
    "1. Keep the layout block exactly as the user structured it (e.g. four views: a head-and-shoulders close-up, then front, side and back full body, "
    "16:9, even flat light, one solid background, everything in focus). Never turn it into a single illustration.\n"
    "2. Hard facts (gender, age, ethnicity/species, number of characters, named clothing parts, colors) must stay, and must be repeated for each view, "
    "not stated only once.\n"
    "3. Pin every recognisable trait concretely (hair color/style, eye color, each garment, accessories, shoes) so the views stay consistent. "
    "Do not rely on the phrase 'keep consistent'.\n"
    "4. Do not invent plot, mood, props or background elements the user did not give. If the user left a slot empty, leave it out.\n"
    "5. One sentence per slot. No hype words (masterpiece, stunning, 8K, ultra detailed). No body-part quality phrases (e.g. perfect hands).\n"
    "6. End with only 2-3 constraints (same character in all views, no other people, no text or watermark).\n"
    "7. Keep the user's language. If an extra instruction is given, apply only that change.\n"
    "Reply with the final prompt wrapped exactly as <prompt>...</prompt> and nothing else."
)

ANALYZE_SYSTEM = (
    "You extract a character description into structured fields for a character design form.\n"
    "Reply with ONE JSON object only (no markdown, no commentary). Use these string keys; value is \"\" when the text does not say it. "
    "Never invent details that are not in the text. Write values in Traditional Chinese, short phrases.\n"
    "{\"name\":\"\",\"gender\":\"female|male|neutral|\",\"age\":\"\",\"ethnicity\":\"\",\"identity\":\"\",\"face\":\"\",\"eyes\":\"\","
    "\"hair\":\"\",\"body\":\"\",\"outfit\":\"\",\"shoes\":\"\",\"accessories\":\"\",\"colors\":\"\",\"materials\":\"\","
    "\"expression\":\"\",\"style\":\"\",\"extra\":\"\"}\n"
    "gender must be exactly one of female, male, neutral or empty. outfit lists each garment; accessories lists each item."
)

FIELD_KEYS = ["name", "gender", "age", "ethnicity", "identity", "face", "eyes", "hair", "body", "outfit", "shoes",
              "accessories", "colors", "materials", "expression", "style", "extra"]


def extract_json(text: str) -> Dict[str, Any]:
    """從可能夾雜雜訊的輸出裡取第一個括號平衡的 {…}。"""
    s = text or ""
    i = s.find("{")
    while i != -1:
        depth, in_str, esc = 0, False, False
        for j in range(i, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(s[i:j + 1])
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        pass
                    break
        i = s.find("{", i + 1)
    return {}


def extract_prompt(text: str) -> str:
    m = re.search(r"<prompt>(.*?)</prompt>", text or "", re.S)
    return (m.group(1) if m else "").strip()


def install(app, ctx: Dict[str, Callable]) -> None:
    char_dir: Path = ctx["data_dir"] / "characters"
    img_dir = char_dir / "images"
    index = char_dir / "index.json"

    def load() -> List[Dict[str, Any]]:
        try:
            return json.loads(index.read_text(encoding="utf-8"))
        except Exception:
            return []

    def save(items: List[Dict[str, Any]]) -> None:
        char_dir.mkdir(parents=True, exist_ok=True)
        tmp = index.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(index)

    def find(items, cid):
        return next((c for c in items if c["id"] == cid), None)

    def adopt(items: List[Dict[str, Any]]) -> bool:
        """把完成的出圖作業複製進角色自己的資產區（複製，圖庫刪圖不會影響這裡）。"""
        changed = False
        for c in items:
            for j in c.get("jobs", []):
                if j.get("state") in ("done", "failed"):
                    continue
                rows = ctx["db_query"]("SELECT status, progress, image_paths, error FROM jobs WHERE job_id=?", (j["job_id"],))
                if not rows:
                    j["state"], j["error"], changed = "failed", "找不到出圖作業", True
                    continue
                r = rows[0]
                j["status"], j["progress"] = r["status"], r["progress"]
                if r["status"] == "completed":
                    img_dir.mkdir(parents=True, exist_ok=True)
                    for p in json.loads(r["image_paths"] or "[]"):
                        src = Path(p)
                        if not src.exists():
                            src = ctx["output_dir"] / src.name
                        if src.exists():
                            dest = img_dir / f"{c['id']}-{uuid.uuid4().hex[:6]}{src.suffix.lower() or '.png'}"
                            shutil.copy2(src, dest)
                            c.setdefault("images", []).append({"file": dest.name, "job_id": j["job_id"], "ts": int(time.time())})
                    j["state"], changed = "done", True
                elif r["status"] == "failed":
                    j["state"], j["error"], changed = "failed", (r["error"] or "出圖失敗")[:200], True
        return changed

    @app.get("/character/{name}")
    async def character_static(name: str):
        if name not in STATIC_OK or not (STATIC_DIR / name).exists():
            raise HTTPException(404, "not found")
        return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})

    @app.get("/api/character/list")
    async def character_list():
        items = load()
        if adopt(items):
            save(items)
        items.sort(key=lambda c: c.get("updated", 0), reverse=True)
        return {"characters": items}

    @app.post("/api/character/save")
    async def character_save(req: CharSave):
        items = load()
        now = int(time.time())
        c = find(items, req.id) if req.id else None
        if c is None:
            c = {"id": uuid.uuid4().hex[:10], "created": now, "images": [], "jobs": []}
            items.append(c)
        c.update({"name": (req.name or "未命名角色")[:60], "form": req.form, "prompt": req.prompt,
                  "note": req.note[:500], "updated": now})
        save(items)
        return {"success": True, "character": c}

    @app.post("/api/character/delete")
    async def character_delete(req: CharId):
        items = load()
        c = find(items, req.id)
        if c is None:
            return {"success": False, "error": "找不到角色"}
        for im in c.get("images", []):
            try:
                (img_dir / im["file"]).unlink()
            except Exception:
                pass
        save([x for x in items if x["id"] != req.id])
        return {"success": True}

    @app.post("/api/character/image/delete")
    async def character_image_delete(req: CharId):
        items = load()
        c = find(items, req.id)
        if c is None:
            return {"success": False, "error": "找不到角色"}
        c["images"] = [im for im in c.get("images", []) if im["file"] != req.file]
        try:
            (img_dir / Path(req.file).name).unlink()
        except Exception:
            pass
        save(items)
        return {"success": True, "character": c}

    @app.get("/api/character/file/{name}")
    async def character_file(name: str):
        p = img_dir / Path(name).name
        if not p.exists():
            raise HTTPException(404, "not found")
        return FileResponse(p)

    @app.post("/api/character/generate")
    async def character_generate(req: CharId):
        items = load()
        c = find(items, req.id)
        if c is None:
            return {"success": False, "error": "請先儲存角色"}
        prompt = (req.prompt or c.get("prompt") or "").strip()
        if not prompt:
            return {"success": False, "error": "提示詞是空的"}
        # ratio 留空：版式（16:9 四視圖）已經寫在提示詞裡，不要再接一句比例
        job_id = await ctx["submit_job"](prompt, req.worker_id, "", "", "")
        c.setdefault("jobs", []).append({"job_id": job_id, "ts": int(time.time()), "state": "queued"})
        c["prompt"] = prompt
        c["updated"] = int(time.time())
        save(items)
        return {"success": True, "job_id": job_id}

    @app.post("/api/character/image/to_asset")
    async def character_to_asset(req: CharId):
        """把角色圖加進一般資產區，之後在提示詞裡用 @圖N 當參考圖（同一個角色換裝的迭代用）。"""
        p = img_dir / Path(req.file).name
        if not p.exists():
            return {"success": False, "error": "找不到圖檔"}
        label = ctx["add_asset"](p, req.name or p.name)
        return {"success": True, "label": label}

    async def run_text(system: str, user: str, provider: str) -> tuple:
        state = ctx["agent_state"]()
        order = ([provider] if provider else []) + [ctx["active_id"]()] + [
            p["id"] for p in ctx["listed"]() if (state.get(p["id"]) or {}).get("connected")]
        seen, errs = set(), []
        for pid in order:
            if not pid or pid in seen:
                continue
            seen.add(pid)
            try:
                out = await ctx["run_provider"](pid, system, user, state)
                if out and out.strip():
                    return out, pid
            except Exception as e:
                errs.append(f"{pid}：{str(e)[:120]}")
        raise RuntimeError(" → ".join(errs) if errs else "還沒有連接任何 agent，先到「Agent 設定」連一個")

    @app.post("/api/character/analyze")
    async def character_analyze(req: CharText):
        text = (req.text or "").strip()
        if not text:
            return {"success": False, "error": "請先貼上或匯入一段角色敘述"}
        if len(text) > MAX_TEXT:
            return {"success": False, "error": f"敘述太長（上限 {MAX_TEXT} 字）"}
        try:
            out, pid = await run_text(ANALYZE_SYSTEM, "Character description:\n" + text, req.provider)
        except Exception as e:
            return {"success": False, "error": str(e)[:400]}
        data = extract_json(out)
        fields = {k: str(data.get(k) or "").strip()[:400] for k in FIELD_KEYS if data.get(k)}
        if fields.get("gender") not in (None, "female", "male", "neutral"):
            fields.pop("gender")
        if not fields:
            return {"success": False, "error": "AI 沒有回傳可用的欄位，換個模型商或再試一次", "raw": out[:300]}
        return {"success": True, "fields": fields, "provider": pid}

    @app.post("/api/character/polish")
    async def character_polish(req: CharText):
        prompt = (req.prompt or "").strip()
        if not prompt:
            return {"success": False, "error": "沒有可潤飾的提示詞"}
        if len(prompt) > MAX_TEXT:
            return {"success": False, "error": f"提示詞太長（上限 {MAX_TEXT} 字）"}
        user = "Prompt to refine:\n" + prompt + (("\n\nExtra instruction (apply only this): " + req.note.strip()) if req.note.strip() else "")
        try:
            out, pid = await run_text(POLISH_SYSTEM, user, req.provider)
        except Exception as e:
            return {"success": False, "error": str(e)[:400]}
        res = extract_prompt(out)
        if not res:
            return {"success": False, "error": "AI 回覆格式不對，再試一次", "raw": out[:300]}
        return {"success": True, "prompt": res, "provider": pid}
