"""把 Character-Prompt-Studio 的詞庫（assets/terms.json）濾成「角色設計」頁用的選單資料。

用法（需要 opencc，只在建置時用，執行時不依賴）：
    uvx --from opencc-python-reimplemented python tools/build_character_lexicon.py <terms.json 路徑>

來源：https://github.com/joshesye/Character-Prompt-Studio（MIT，改自 AI绘梦师葉子的 ai-image-prompt）
輸出：core/character/lexicon.json —— 簡體詞條轉成繁體顯示用，英文原樣保留（英文模式的提示詞用）。
"""
import json
import sys
from pathlib import Path

from opencc import OpenCC

cc = OpenCC("s2twp")
src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("terms.json")
out = Path(__file__).resolve().parent.parent / "core" / "character" / "lexicon.json"
terms = json.loads(src.read_text(encoding="utf-8"))["terms"]

# 欄位 → 取自詞庫的哪些 (域, 要素)。section 決定在選單裡放哪一區。
FIELDS = [
    # key, 標籤, 區, 取自 [(域, 要素)], 單選 or 多選
    ("age", "年齡段", "identity", [("人像", "年龄段")], "one"),
    ("role", "角色類型", "identity", [("人像", "主体类型"), ("旧库补充-主体", "主体、角色")], "multi"),
    ("body", "身形", "identity", [("人像", "身形"), ("人像", "肩颈"), ("人像", "腰腹"), ("人像", "四肢")], "multi"),
    ("face_shape", "臉型", "face", [("人像", "脸型")], "one"),
    ("eyes", "眼睛", "face", [("人像", "眼睛"), ("人像", "瞳孔"), ("人像", "睫毛")], "multi"),
    ("eye_color", "瞳色", "face", [("人像", "眼睛颜色")], "one"),
    ("brows", "眉毛", "face", [("人像", "眉毛")], "one"),
    ("nose_mouth", "鼻與嘴", "face", [("人像", "鼻型"), ("人像", "嘴部与牙齿")], "multi"),
    ("skin", "皮膚質感", "face", [("人像", "皮肤质感"), ("人像", "面孔特征")], "multi"),
    ("makeup", "妝容", "face", [("人像", "妆容")], "one"),
    ("expression", "表情與眼神", "face", [("人像", "中性表情"), ("人像", "正面情绪"), ("人像", "眼神")], "one"),
    ("hair_color", "髮色", "hair", [("人像", "发色")], "one"),
    ("hair_len", "髮長", "hair", [("人像", "发长")], "one"),
    ("hair_style", "髮型", "hair", [("人像", "短发造型"), ("人像", "束发与编发"), ("人像", "卷发与直发"), ("旧库补充-主体", "发型")], "one"),
    ("bangs", "瀏海", "hair", [("人像", "刘海")], "one"),
    ("hair_quality", "髮質", "hair", [("人像", "发质与状态")], "one"),
    ("outfit_top", "上裝", "outfit", [("人像", "上装"), ("旧库补充-服装", "上衣")], "multi"),
    ("outfit_bottom", "下裝", "outfit", [("人像", "裙装"), ("人像", "裤装"), ("旧库补充-服装", "下裤")], "multi"),
    ("outfit_full", "連身／禮服／套裝", "outfit", [("人像", "连身与礼服"), ("人像", "传统服饰"), ("旧库补充-服装", "套装")], "multi"),
    ("shoes", "鞋襪", "outfit", [("人像", "鞋袜"), ("旧库补充-服装", "鞋子"), ("旧库补充-服装", "袜子")], "multi"),
    ("material", "材質", "outfit", [("通用-色彩材质", "材料"), ("通用-色彩材质", "表面质感")], "multi"),
    ("palette", "配色", "outfit", [("通用-色彩材质", "基础色"), ("通用-色彩材质", "经典色"), ("通用-色彩材质", "配色方案")], "multi"),
    ("acc_head", "頭部配飾", "accessory", [("人像", "帽子"), ("人像", "发饰"), ("旧库补充-饰品", "头饰"), ("旧库补充-饰品", "帽子")], "multi"),
    ("acc_face", "臉部配飾", "accessory", [("人像", "面部配饰"), ("人像", "耳饰"), ("旧库补充-饰品", "眼部"), ("旧库补充-饰品", "面部")], "multi"),
    ("acc_body", "頸／手／腰配飾", "accessory", [("人像", "颈饰"), ("人像", "手饰"), ("人像", "腰部与包"), ("旧库补充-饰品", "项链"), ("旧库补充-饰品", "手饰")], "multi"),
    ("prop", "手持道具", "accessory", [("人像", "手持道具")], "multi"),
    ("pose", "站姿", "layout", [("人像", "腿部与站姿"), ("人像", "手部动作"), ("人像", "全身动作"), ("旧库补充-姿势", "整体姿势")], "one"),
    ("style", "風格", "style", [("通用-风格", "写实程度"), ("通用-风格", "动漫游戏"), ("通用-风格", "3D 与渲染"), ("通用-风格", "艺术流派"), ("通用-风格", "摄影风格"), ("通用-风格", "视觉效果"), ("通用-风格", "媒介与画种")], "multi"),
    ("light", "光線", "style", [("通用-光线", "光位"), ("通用-光线", "光质"), ("通用-光线", "照明风格"), ("通用-光线", "影调")], "one"),
    ("background", "背景", "style", [("通用-背景收尾", "背景")], "one"),
]

def group_terms(pairs):
    seen, items = set(), []
    for dom, grp in pairs:
        for t in terms:
            if t["s"] == dom and t["g"] == grp and t["zh"] not in seen:
                seen.add(t["zh"])
                items.append([cc.convert(t["zh"]), t["en"], t.get("st", ""), cc.convert(t.get("note", ""))])
    # 常用 / 可用在前，少見次之，慎用放最後（介面預設折疊）
    rank = {"常用": 0, "可用": 1, "少见": 2, "慎用": 3}
    items.sort(key=lambda x: rank.get(x[2], 9))
    for x in items:
        x[2] = {"常用": "c", "可用": "u", "少见": "r", "慎用": "x"}.get(x[2], "u")
    return items

fields = []
for key, label, section, pairs, mode in FIELDS:
    items = group_terms(pairs)
    if items:
        fields.append({"key": key, "label": label, "section": section, "mode": mode, "terms": items})

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"source": "Character-Prompt-Studio (MIT) / ai-image-prompt", "fields": fields},
                          ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print("fields", len(fields), "terms", sum(len(f["terms"]) for f in fields), "->", out, out.stat().st_size, "bytes")
for f in fields:
    print(f["key"], len(f["terms"]), f["terms"][0][:2])
