/* 角色設計：把選單內容組成提示詞（純函式，沒有 DOM，可以用 node 直接測）。
   規則來自 Character-Prompt-Studio / ai-image-prompt（MIT）：
   - 人物設定預設「四視圖」：第一格頭到肩特寫，右邊三格正／側／背全身，16:9
   - 版式圖整套反寫：均勻平光、純色背景、全清晰無景深
   - 一致性靠「逐項點名」，不靠「保持一致」這句話
   - 性別／年齡段／種族等硬資訊落到每個視圖的句子上
   - 使用者沒填的欄位直接省略，不補劇情；收尾只留 2～3 項，不寫身體部位的畫質句 */
(function (root) {
  "use strict";

  var LAYOUTS = {
    four:     { label: "四視圖", desc: "第 1 格臉部特寫，右邊三格正／側／背全身", ratio: "16:9" },
    sheet:    { label: "檔案板", desc: "左側大頭像，右側正／45°側／背全身", ratio: "16:9" },
    three:    { label: "三視圖", desc: "正／側／背全身，沒有特寫格", ratio: "3:2" },
    portrait: { label: "單張立繪", desc: "好看優先的角色立繪", ratio: "3:4" },
    expr:     { label: "表情表", desc: "同一角色六種表情的頭肩特寫", ratio: "3:2" }
  };

  var GENDER = {
    female:  { zh: "女性", en: "woman" },
    male:    { zh: "男性", en: "man" },
    neutral: { zh: "中性外型的人", en: "androgynous person" }
  };

  // 顯示順序固定：決定提示詞每一段的先後
  var CHIP_ORDER = {
    face:    ["face_shape", "eyes", "eye_color", "brows", "nose_mouth", "skin", "makeup", "expression"],
    hair:    ["hair_color", "hair_len", "hair_style", "bangs", "hair_quality"],
    outfit:  ["outfit_top", "outfit_bottom", "outfit_full", "material", "palette"],
    acc:     ["acc_head", "acc_face", "acc_body", "prop"]
  };

  function defaultForm() {
    return {
      name: "", lang: "zh", layout: "four", gender: "", bg: "淺灰色",
      chips: {}, custom: {}, extra: "", ref: ""
    };
  }

  /* lex = lexicon.json 的內容；建立 zh→en 對照與欄位索引 */
  function buildIndex(lex) {
    var en = {}, fields = {};
    (lex.fields || []).forEach(function (f) {
      fields[f.key] = f;
      f.terms.forEach(function (t) { if (!(t[0] in en)) en[t[0]] = t[1]; });
    });
    return { fields: fields, en: en };
  }

  function clean(s) { return String(s || "").replace(/\s+/g, " ").replace(/[。．.]+$/, "").trim(); }

  function chipList(form, idx, keys, lang) {
    var out = [];
    keys.forEach(function (k) {
      ((form.chips || {})[k] || []).forEach(function (zh) {
        out.push(lang === "en" ? (idx.en[zh] || zh) : zh);
      });
    });
    return out;
  }

  function join(lang, arr) { return arr.filter(Boolean).join(lang === "en" ? ", " : "、"); }

  /* 一個欄位區塊：chips + 自訂文字，合成一句。沒有內容回傳空字串 */
  function slot(form, idx, keys, customKeys, lang) {
    var parts = chipList(form, idx, keys, lang);
    (customKeys || []).forEach(function (ck) {
      var v = clean((form.custom || {})[ck]);
      if (v) parts.push(v);
    });
    return join(lang, parts);
  }

  function whoText(form, idx, lang) {
    var c = form.custom || {};
    var age = (chipList(form, idx, ["age"], lang)[0] || clean(c.age) || "").replace(/^an? /i, "");
    var g = GENDER[form.gender];
    var gz = g ? g[lang] : "";
    var eth = clean(c.ethnicity);
    var role = join(lang, chipList(form, idx, ["role"], lang).concat(clean(c.identity) ? [clean(c.identity)] : []));
    var core;
    if (lang === "en") {
      core = ["a", age, eth, gz || "character"].filter(Boolean).join(" ");
      if (role) core += ", " + role;
    } else {
      core = "一名" + [age, eth, gz || "角色"].filter(Boolean).join("") + (role ? "（" + role + "）" : "");
    }
    // 短稱呼：每個視圖的句子都帶上，避免硬資訊只在開頭出現一次就漂掉
    var short = lang === "en" ? [age, gz || "character"].filter(Boolean).join(" ") : [age, gz || "角色"].filter(Boolean).join("");
    return { full: core, short: short };
  }

  function pinList(form, idx, lang) {
    var L = lang === "en";
    var items = [];
    function add(label, val) { if (val) items.push(label + (L ? ": " : "：") + val); }
    add(L ? "hair color" : "髮色", slot(form, idx, ["hair_color"], [], lang));
    add(L ? "hairstyle" : "髮型", slot(form, idx, ["hair_len", "hair_style", "bangs"], ["hair"], lang));
    add(L ? "eyes" : "眼睛", slot(form, idx, ["eye_color", "eyes"], ["eyes"], lang));
    add(L ? "top" : "上裝", slot(form, idx, ["outfit_top"], [], lang));
    add(L ? "bottom" : "下裝", slot(form, idx, ["outfit_bottom"], [], lang));
    add(L ? "outfit" : "服裝", slot(form, idx, ["outfit_full"], ["outfit"], lang));
    add(L ? "shoes" : "鞋", slot(form, idx, [], ["shoes"], lang));
    add(L ? "accessories" : "配飾", slot(form, idx, CHIP_ORDER.acc, ["accessories"], lang));
    add(L ? "palette" : "配色", slot(form, idx, ["palette"], ["colors"], lang));
    return items;
  }

  function compose(form, lex) {
    var idx = lex && lex.fields && lex.en ? lex : buildIndex(lex || { fields: [] });
    var lang = form.lang === "en" ? "en" : "zh";
    var L = lang === "en";
    var lay = LAYOUTS[form.layout] || LAYOUTS.four;
    var who = whoText(form, idx, lang);
    var warnings = [], assumptions = [];
    var lines = [];

    if (!form.gender) warnings.push("還沒選性別：模型會自己猜，建議選一個");
    if (!who.short || who.short === (L ? "character" : "角色")) assumptions.push(L ? "gender/age not set" : "沒有指定性別與年齡段");

    var bg = clean(form.bg) || (L ? "light gray" : "淺灰色");
    var bgChip = chipList(form, idx, ["background"], lang)[0];
    var face = slot(form, idx, CHIP_ORDER.face.filter(function (k) { return k !== "expression"; }), ["face", "eyes"], lang);
    var expr = slot(form, idx, ["expression"], ["expression"], lang);
    var hair = slot(form, idx, CHIP_ORDER.hair, ["hair"], lang);
    var body = slot(form, idx, ["body"], ["body"], lang);
    var outfit = slot(form, idx, CHIP_ORDER.outfit.filter(function (k) { return k !== "material" && k !== "palette"; }), ["outfit", "shoes"], lang);
    var shoes = chipList(form, idx, ["shoes"], lang);
    if (shoes.length) outfit = join(lang, [outfit].concat(shoes));
    var mat = slot(form, idx, ["material"], ["materials"], lang);
    var pal = slot(form, idx, ["palette"], ["colors"], lang);
    var acc = slot(form, idx, CHIP_ORDER.acc, ["accessories"], lang);
    var style = slot(form, idx, ["style"], ["style"], lang);
    var pose = slot(form, idx, ["pose"], [], lang);
    var lightChip = slot(form, idx, ["light"], ["light"], lang);
    var extra = clean(form.extra);
    var ref = clean(form.ref);

    function L1(zhLabel, enLabel, val) { if (val) lines.push((L ? enLabel + ": " : zhLabel + "：") + val + (L ? "." : "。")); }

    var multiView = form.layout === "four" || form.layout === "sheet" || form.layout === "three";
    var intent, layout;

    if (form.layout === "four") {
      intent = L ? "A 16:9 landscape character design sheet (four views) for a character reference."
                 : "為角色設計製作的一張 16:9 橫幅「人物設定四視圖」。";
      layout = L
        ? "The image is divided into four equal panels side by side with narrow light-gray gutters. Panel 1 is a large head-and-shoulders close-up of " + who.full + ", the face fills the panel, no full body. The other three panels are full-body standing views of the same " + who.short + ": front view (facing the camera), side view (facing the right of the frame), back view (fully turned away). The three full-body figures are the same size, stand on the same horizontal line and are evenly spaced."
        : "畫面橫向等距分成四格，格與格之間留很窄的淺灰間隔。第一格是" + who.full + "頭部到肩部的大特寫，臉填滿這一格、不畫全身；右邊三格依序是同一名" + who.short + "的正視圖（臉朝鏡頭）、側視圖（臉朝畫面右側）、背視圖（完全背對鏡頭）全身立繪，三個立繪同樣大小、站在同一條水平線上、間距相等。";
    } else if (form.layout === "sheet") {
      intent = L ? "A 16:9 landscape character profile board." : "為角色設計製作的一張 16:9 橫幅「角色檔案板」。";
      layout = L
        ? "On the left, about 40% of the image, a large head-and-shoulders close-up of " + who.full + ". On the right, three full-body standing views of the same " + who.short + " at the same scale and on the same ground line: front, 45-degree three-quarter side, and back. Heads and feet fully inside the frame."
        : "左側約四成是" + who.full + "的頭肩大特寫；右側是同一名" + who.short + "同尺度、同地平線的三個全身立繪，依序為正面、45° 四分之三側面、背面，頭到腳完整入畫。";
    } else if (form.layout === "three") {
      intent = L ? "A 3:2 landscape character turnaround sheet (three views)." : "為角色設計製作的一張 3:2 橫幅「人物三視圖」。";
      layout = L
        ? "Three full-body standing views of " + who.full + " side by side: front (facing the camera), side (facing the right of the frame), back (fully turned away), same size, same ground line, evenly spaced. No close-up panel."
        : "三個全身立繪橫向等距並排，同一名" + who.short + "：正視圖（臉朝鏡頭）、側視圖（臉朝畫面右側）、背視圖（完全背對鏡頭），同樣大小、站在同一條水平線上。不加臉部特寫格。";
    } else if (form.layout === "expr") {
      intent = L ? "A 3:2 landscape expression sheet for one character." : "為角色設計製作的一張 3:2 橫幅「表情表」。";
      var exs = chipList(form, idx, ["expression"], lang);
      var defaults = L ? ["happy", "angry", "surprised", "sad", "shy", "calm"] : ["開心", "生氣", "驚訝", "難過", "害羞", "冷靜"];
      layout = L
        ? "Six head-and-shoulders close-ups of " + who.full + " in a 3 by 2 grid, same face and hairstyle in every panel, expressions in order: " + join(lang, (exs.length ? exs : defaults).slice(0, 6)) + "."
        : "六格頭肩特寫排成 3×2，全是" + who.full + "，每格五官與髮型完全相同，表情依序為：" + join(lang, (exs.length ? exs : defaults).slice(0, 6)) + "。";
      expr = "";
    } else {
      intent = L ? "A 3:4 portrait character illustration." : "為角色設計製作的一張 3:4 直幅「單張角色立繪」。";
      layout = L ? "A single full-body illustration of " + who.full + ", the whole figure inside the frame."
                 : "單張全身立繪，主角是" + who.full + "，整個人完整入畫。";
    }

    lines.push(intent);
    lines.push(layout);
    if (ref) lines.push((L ? "Reference: " : "參考：") + ref + (L ? ": keep the face and hairstyle, change only what is listed below." : " 的五官與髮型保持不變，只依下面的描述調整。"));
    L1("面部", "Face", face);
    L1("表情", "Expression", expr);
    L1("髮型", "Hair", hair);
    L1("體型", "Build", body);
    L1("服裝", "Outfit", outfit);
    L1("材質", "Materials", mat);
    L1("配色", "Color palette", pal);
    L1("配飾", "Accessories", acc);
    if (multiView) {
      L1("背視圖", "Back view", L ? "show clearly the hair silhouette from behind, the back collar, the cut of the back and the hem"
                                   : "清楚呈現髮型從後看的輪廓、後領、後背剪裁與下擺（背視圖看不到臉，辨識度靠這些）");
      var stance = pose || (L ? "neutral standing pose, arms hanging naturally and slightly away from the body, feet shoulder-width apart, the same pose in every view"
                               : "中性站立，雙臂自然下垂、略微離開身體，雙腳與肩同寬，三個視圖用同一個站姿");
      L1("站姿", "Pose", stance);
    } else if (pose) {
      L1("姿勢", "Pose", pose);
    }
    L1("風格", "Style", style);

    if (form.layout === "portrait") {
      L1("光線", "Lighting", lightChip || (L ? "soft key light from the front-side at 45 degrees" : "柔和主光從側前方 45° 打來，臉上有亮面與暗面"));
      L1("背景", "Background", bgChip || (L ? "simple blurred background" : "簡潔的虛化背景"));
    } else if (form.layout === "sheet") {
      L1("光線", "Lighting", lightChip || (L ? "bright soft studio light with light volume shadows, dark garments still readable" : "明亮柔和的棚拍光，輕微體積陰影，深色衣物之間仍然分得清"));
      L1("背景", "Background", (L ? "one shared " : "四處共用同一個") + bg + (L ? " solid background, no environment, no props" : "純色背景，沒有環境、沒有道具"));
    } else {
      L1("光線", "Lighting", lightChip || (L ? "even flat light, identical brightness in every panel, no directional shadows" : "均勻平光，每一格照度完全一致，沒有方向性陰影"));
      L1("背景", "Background", (L ? "one shared " : "每一格共用同一個") + bg + (L ? " solid background, no environment, no props, no cast shadows" : "純色背景，沒有環境、沒有道具、沒有投影"));
      L1("焦平面", "Focus", L ? "everything on the same focal plane, fully sharp, no depth of field" : "全部在同一焦平面、全部清晰，不要景深");
    }

    if (multiView) {
      var pins = pinList(form, idx, lang);
      if (pins.length) L1("一致性", "Consistency", (L ? "every panel shows exactly the same — " : "每一格都完全相同——") + pins.join(L ? "; " : "；"));
    }
    if (extra) L1("補充", "Extra", extra);

    var closing;
    if (multiView) closing = L ? "All panels show the same single character, no other people, no text or watermark."
                               : "所有格子畫的都是同一名角色，畫面裡沒有其他人物，沒有文字和浮水印。";
    else if (form.layout === "expr") closing = L ? "Same single character in every panel, no text or watermark." : "每格都是同一名角色，沒有文字和浮水印。";
    else closing = L ? "Only this one character in the image, no text or watermark." : "畫面裡只有這一名角色，沒有文字和浮水印。";
    lines.push(closing);

    if (!face && !hair && !outfit) warnings.push("臉、髮型、服裝都還沒填：提示詞會很空，模型會自己發揮");
    return { prompt: lines.join(L ? " " : "\n"), ratio: lay.ratio, layout: lay.label, warnings: warnings, assumptions: assumptions };
  }

  /* AI 分析結果 → 表單：自由文字放進對應的自訂欄；詞庫裡有的詞直接勾成選項 */
  function applyAnalysis(form, fields, lex) {
    var idx = lex && lex.fields && lex.en ? lex : buildIndex(lex || { fields: [] });
    var f = JSON.parse(JSON.stringify(form));
    f.chips = f.chips || {}; f.custom = f.custom || {};
    if (fields.name) f.name = fields.name;
    if (fields.gender && GENDER[fields.gender]) f.gender = fields.gender;
    ["age", "ethnicity", "identity", "face", "eyes", "hair", "body", "outfit", "shoes", "accessories", "colors", "materials", "expression", "style", "extra"]
      .forEach(function (k) { if (fields[k]) f.custom[k] = fields[k]; });
    if (fields.extra) f.extra = fields.extra;
    // 詞庫命中：敘述裡出現的詞條自動勾選（最長優先，避免「黑髮」「黑」重複）
    var textBy = {
      age: fields.age, hair_color: fields.hair, hair_len: fields.hair, hair_style: fields.hair, bangs: fields.hair,
      eye_color: fields.eyes, eyes: fields.eyes, face_shape: fields.face, brows: fields.face, nose_mouth: fields.face,
      expression: fields.expression, body: fields.body, outfit_top: fields.outfit, outfit_bottom: fields.outfit,
      outfit_full: fields.outfit, acc_head: fields.accessories, acc_face: fields.accessories, acc_body: fields.accessories,
      style: fields.style, palette: fields.colors, material: fields.materials
    };
    Object.keys(textBy).forEach(function (key) {
      var txt = textBy[key], fd = idx.fields[key];
      if (!txt || !fd) return;
      var hit = fd.terms.filter(function (t) { return t[2] !== "x" && t[0].length >= 2 && txt.indexOf(t[0]) !== -1; })
        .sort(function (a, b) { return b[0].length - a[0].length; });
      if (!hit.length) return;
      var take = fd.mode === "one" ? hit.slice(0, 1) : hit.slice(0, 4);
      f.chips[key] = take.map(function (t) { return t[0]; });
    });
    return f;
  }

  var api = { LAYOUTS: LAYOUTS, GENDER: GENDER, defaultForm: defaultForm, buildIndex: buildIndex, compose: compose, applyAnalysis: applyAnalysis };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CharCompose = api;
})(typeof window !== "undefined" ? window : globalThis);
