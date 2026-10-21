/* 角色設計頁：繪製進 #charApp，路由 #/character。
   表單形狀與提示詞組裝由 compose.js（window.CharCompose）負責；資料走 /api/character/*。
   事件全部委派在 #charApp 與詳情視窗上，只就地更新需要變的部分，不整頁重繪。 */
(function () {
  'use strict';

  var DRAFT_KEY = 'tidepool.character.draft.v1';
  var CHIP_LIMIT = 8;
  var POLL_MS = 3000;
  var FACE_KEYS = ['face_shape', 'eyes', 'eye_color', 'brows', 'nose_mouth', 'skin', 'makeup', 'expression'];
  var BG_PRESETS = ['淺灰色', '中灰色', '白色', '米白色', '深灰色'];
  var SECTIONS = [
    { id: 'layout', title: '版式', open: true, keys: [], customs: [] },
    { id: 'identity', title: '身份', open: true, keys: ['age', 'role'], customs: ['age', 'ethnicity', 'identity'] },
    { id: 'outfit', title: '服裝', open: true, keys: ['outfit_top', 'outfit_bottom', 'outfit_full', 'shoes', 'material', 'palette'], customs: ['outfit', 'shoes', 'materials', 'colors'] },
    { id: 'style', title: '風格與光線', open: true, keys: ['style', 'light', 'pose', 'background'], customs: ['style'] },
    { id: 'face', title: '臉部', open: false, keys: FACE_KEYS, customs: ['face', 'eyes', 'expression'] },
    { id: 'hair', title: '髮型', open: false, keys: ['hair_color', 'hair_len', 'hair_style', 'bangs', 'hair_quality'], customs: ['hair'] },
    { id: 'body', title: '體型', open: false, keys: ['body'], customs: ['body'] },
    { id: 'acc', title: '配飾', open: false, keys: ['acc_head', 'acc_face', 'acc_body', 'prop'], customs: ['accessories'] },
    { id: 'extra', title: '補充', open: false, keys: [], customs: [], extra: true }
  ];
  var LAYOUT_ICON = {
    four: '<rect x="4" y="10" width="12" height="12" rx="1"/><rect x="20" y="5" width="9" height="26" rx="1"/><rect x="33" y="5" width="9" height="26" rx="1"/><rect x="46" y="5" width="9" height="26" rx="1"/>',
    sheet: '<rect x="4" y="4" width="22" height="28" rx="1"/><rect x="31" y="5" width="7" height="26" rx="1"/><rect x="40" y="5" width="7" height="26" rx="1"/><rect x="49" y="5" width="7" height="26" rx="1"/>',
    three: '<rect x="5" y="4" width="15" height="28" rx="1"/><rect x="22" y="4" width="15" height="28" rx="1"/><rect x="39" y="4" width="15" height="28" rx="1"/>',
    portrait: '<rect x="18" y="2" width="24" height="32" rx="1"/>',
    expr: '<rect x="4" y="4" width="16" height="12" rx="1"/><rect x="22" y="4" width="16" height="12" rx="1"/><rect x="40" y="4" width="16" height="12" rx="1"/><rect x="4" y="20" width="16" height="12" rx="1"/><rect x="22" y="20" width="16" height="12" rx="1"/><rect x="40" y="20" width="16" height="12" rx="1"/>'
  };
  var ICON_MORE = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.1"/><circle cx="8" cy="8" r="1.1"/><circle cx="12.5" cy="8" r="1.1"/></svg>';
  var ICON_PLUS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>';
  var ICON_X = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6"/></svg>';
  var PH_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/></svg>';

  var $root = document.getElementById('charApp');
  if (!$root) return;
  var $modal = null;

  var S = {
    booted: false, booting: false,
    lex: null, idx: null, presets: [],
    form: CharCompose.defaultForm(), prompt: '', manual: false, polishPrev: null,
    meta: { ratio: '', layout: '', warnings: [], assumptions: [] },
    charId: null, note: '', chars: [],
    showX: {}, expand: {}, open: {}, just: null,
    pollTimer: null, jobState: {}, jobsInit: false, lastFailed: null,
    pickSig: null, cardSig: null, detailId: null
  };
  SECTIONS.forEach(function (s) { S.open[s.id] = s.open; });
  S.open.ai = false;

  /* ---------- 小工具 ---------- */
  function h(v) { return esc(v == null ? '' : String(v)); }
  function q(sel, root) { return (root || $root).querySelector(sel); }
  function qa(sel, root) { return Array.prototype.slice.call((root || $root).querySelectorAll(sel)); }
  function setText(sel, text) { var el = q(sel); if (el) el.textContent = text; }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function errorText(e) {
    var m = e && e.message ? e.message : e;
    return typeof m === 'string' ? m : JSON.stringify(m);
  }
  function isOn() { return (location.hash || '') === '#/character'; }
  function imgUrl(file) { return '/api/character/file/' + encodeURIComponent(file); }
  function findChar(id) {
    for (var i = 0; i < S.chars.length; i++) if (S.chars[i].id === id) return S.chars[i];
    return null;
  }
  function byUpdated(a, b) { return (Number(b.updated) || 0) - (Number(a.updated) || 0); }
  function sortedImages(c) {
    return (c.images || []).slice().sort(function (a, b) { return (Number(b.ts) || 0) - (Number(a.ts) || 0); });
  }
  function relTime(ts) {
    if (!ts) return '—';
    var d = Math.floor(Date.now() / 1000) - Number(ts);
    if (d < 60) return '剛剛';
    if (d < 3600) return Math.floor(d / 60) + ' 分前';
    if (d < 86400) return Math.floor(d / 3600) + ' 小時前';
    return Math.floor(d / 86400) + ' 天前';
  }
  function activeJob(c) {
    var list = (c.jobs || []).filter(function (j) { return j.state !== 'done' && j.state !== 'failed'; });
    list.sort(function (a, b) { return (Number(b.ts) || 0) - (Number(a.ts) || 0); });
    return list[0] || null;
  }
  function jobPct(j) { return Math.max(0, Math.min(100, Number(j.progress) || 0)); }
  function jobLabel(j) { var p = jobPct(j); return p > 0 ? '生成中 ' + Math.round(p) + '%' : '排隊中'; }
  function anyActive() { return S.chars.some(function (c) { return !!activeJob(c); }); }
  function flash(el, cls) {
    if (!el) return;
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
  }
  function setBusy(btn, on, label) {
    if (!btn) return;
    if (on) {
      if (btn._orig == null) btn._orig = btn.innerHTML;
      btn.innerHTML = '<span class="spin"></span>' + h(label || '處理中…');
    } else if (btn._orig != null) {
      btn.innerHTML = btn._orig; btn._orig = null;
    }
    btn.disabled = !!on;
    btn.classList.toggle('is-busy', !!on);
  }
  /* 兩段確認：第一次按變「確認」字樣，3 秒內再按才執行 */
  function twoStep(btn, armedText, onConfirm) {
    if (btn.dataset.armed !== '1') {
      btn._orig = btn.innerHTML;
      btn.dataset.armed = '1';
      btn.classList.add('armed');
      btn.textContent = armedText;
      setTimeout(function () { if (btn.dataset.armed === '1') disarm(btn); }, 3000);
      return;
    }
    disarm(btn);
    onConfirm();
  }
  function disarm(btn) {
    btn.dataset.armed = '0';
    btn.classList.remove('armed');
    if (btn._orig != null) btn.innerHTML = btn._orig;
    btn._orig = null;
  }
  function run(fn, msg) {
    return Promise.resolve().then(fn).catch(function (e) { toast(msg + '：' + errorText(e), 'err'); });
  }

  /* 所有後端呼叫都經過這裡：non-2xx 或 success:false 都 throw，帶上後端的 error 文字 */
  async function req(path, body) {
    var init = {};
    if (body !== undefined) init = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    var res = await fetch(path, init);
    var j = null;
    try { j = await res.json(); } catch (e) { j = null; }
    if (!res.ok || (j && j.success === false)) {
      var d = j && (j.error || j.detail);
      throw new Error(d ? (typeof d === 'string' ? d : JSON.stringify(d)) : ('HTTP ' + res.status));
    }
    return j || {};
  }
  function failAI(e) {
    var msg = errorText(e);
    toast(msg, 'err');
    if (msg.indexOf('還沒有連接任何 agent') !== -1 && typeof openAgentSettings === 'function') openAgentSettings();
  }

  /* ---------- 草稿（localStorage，全部包 try/catch） ---------- */
  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ form: S.form, prompt: S.prompt, manual: S.manual, charId: S.charId, note: S.note }));
    } catch (e) { /* 無痕模式或被封鎖：忽略 */ }
  }
  function restoreDraft() {
    var d = null;
    try { var raw = localStorage.getItem(DRAFT_KEY); d = raw ? JSON.parse(raw) : null; } catch (e) { d = null; }
    if (!d || !d.form) return;
    S.form = normForm(d.form);
    S.charId = d.charId || null;
    S.note = d.note || '';
    S.manual = !!d.manual;
    S.prompt = d.prompt || '';
  }
  function normForm(f) {
    var x = Object.assign(CharCompose.defaultForm(), clone(f || {}));
    if (!CharCompose.LAYOUTS[x.layout]) x.layout = 'four';
    if (x.lang !== 'en') x.lang = 'zh';
    x.chips = x.chips || {};
    x.custom = x.custom || {};
    return x;
  }

  /* ---------- 提示詞與計數 ---------- */
  function refreshPreview(anim) {
    var r = CharCompose.compose(S.form, S.idx);
    S.meta = r;
    if (!S.manual || !S.prompt) S.prompt = r.prompt;
    renderPromptBox(anim);
  }
  function renderPromptBox(anim) {
    var ta = q('#chPrompt');
    if (!ta) return;
    var changed = ta.value !== S.prompt;
    if (changed) ta.value = S.prompt;
    ta.readOnly = !S.manual;
    if (changed && anim) flash(ta, 'landed');
    setText('#chLayoutBadge', S.meta.layout || '');
    setText('#chRatioBadge', S.meta.ratio ? '比例 ' + S.meta.ratio : '');
    q('#chManualBadge').hidden = !S.manual;
    q('#chRestore').hidden = !S.manual;
    q('[data-ca="manual"]').setAttribute('aria-pressed', String(S.manual));
    q('[data-ca="undo"]').hidden = !S.polishPrev;
    var items = (S.meta.warnings || []).concat(S.meta.assumptions || []);
    q('#chWarn').innerHTML = items.map(function (x) { return '<li>' + h(x) + '</li>'; }).join('');
    updatePromptCount();
  }
  function updatePromptCount() {
    setText('#chChars', S.prompt.length + ' 字' + (S.manual ? '（手動編輯中）' : ''));
  }
  function sectionFill(sec) {
    var f = S.form, n = 0;
    sec.keys.forEach(function (k) { n += ((f.chips || {})[k] || []).length; });
    sec.customs.forEach(function (k) { if (String((f.custom || {})[k] || '').trim()) n++; });
    if (sec.id === 'identity' && f.gender) n++;
    if (sec.extra) { if (f.extra) n++; if (f.ref) n++; }
    return n;
  }
  function updateCounts() {
    SECTIONS.forEach(function (s) {
      if (s.id === 'layout') return;
      var el = q('[data-count="' + s.id + '"]');
      if (!el) return;
      var n = sectionFill(s);
      el.textContent = n ? String(n) : '';
      el.classList.toggle('on', n > 0);
    });
    qa('.ch-group').forEach(function (g) {
      var n = ((S.form.chips || {})[g.dataset.group] || []).length;
      var el = g.querySelector('.ch-gcount');
      if (el) el.textContent = n ? String(n) : '';
    });
  }
  function setVal(el, v) {
    if (document.activeElement !== el && el.value !== v) el.value = v;
  }
  /* 同步不會重繪的靜態控制項（按鈕狀態、輸入框值）。輸入框有焦點時不覆蓋，避免游標跳動 */
  function syncStatics() {
    var f = S.form;
    qa('[data-ca="layout"]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.v === f.layout)); });
    qa('[data-ca="gender"]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.v === f.gender)); });
    qa('[data-ca="lang"]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.v === f.lang)); });
    qa('[data-ca="bg"]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.v === f.bg)); });
    qa('[data-ca="ref"]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.v === f.ref)); });
    var L = CharCompose.LAYOUTS[f.layout] || CharCompose.LAYOUTS.four;
    setText('#chLDesc', L.desc + '　比例 ' + L.ratio);
    var ln = q('#chLangNote');
    if (ln) ln.hidden = f.lang !== 'en';
    qa('[data-field]').forEach(function (el) { setVal(el, f[el.dataset.field] || ''); });
    qa('[data-custom]').forEach(function (el) { setVal(el, (f.custom || {})[el.dataset.custom] || ''); });
  }
  function afterFormChange(anim) {
    refreshPreview(anim);
    syncStatics();
    updateCounts();
    saveDraft();
  }

  /* ---------- 表單：區段與欄位 ---------- */
  function secHTML(id, title, hasCount, open, body) {
    return '<div class="ch-sec' + (open ? ' open' : '') + '" data-sec="' + h(id) + '">'
      + '<button type="button" class="ch-sec-head" data-ca="sec" data-sec="' + h(id) + '" aria-expanded="' + String(!!open) + '" aria-controls="chsec-' + h(id) + '">'
      + '<span class="ch-sec-title">' + h(title) + '</span>'
      + (hasCount ? '<span class="ch-count mono" data-count="' + h(id) + '"></span>' : '')
      + '<svg class="ch-chev" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5"/></svg>'
      + '</button>'
      + '<div class="ch-sec-body" id="chsec-' + h(id) + '"><div class="ch-sec-inner"><div class="ch-sec-pad">' + body + '</div></div></div>'
      + '</div>';
  }
  function formSectionHTML(sec) {
    return secHTML(sec.id, sec.title, sec.id !== 'layout', S.open[sec.id], sectionBody(sec.id));
  }
  function sectionBody(id) {
    var G = function (k) { return groupHTML(k); };
    switch (id) {
      case 'layout':
        return layoutHTML();
      case 'identity':
        return nameHTML() + genderHTML() + G('age')
          + customInput('age', '自訂年齡', '沒有合適的選項時再寫，例如：約二十八歲')
          + customInput('ethnicity', '種族／物種', '例如：東亞人、精靈、獸人')
          + customInput('identity', '身份／職業', '例如：機能風偵察員')
          + '<details class="ch-more"><summary>更多角色類型</summary><div class="ch-more-body">' + G('role') + '</div></details>';
      case 'outfit':
        return G('outfit_top') + G('outfit_bottom') + G('outfit_full') + G('shoes')
          + customInput('outfit', '自訂服裝', '完整描述衣服，例如：深灰機能風短外套')
          + customInput('shoes', '自訂鞋襪', '例如：磨白的深棕色靴子')
          + G('material') + G('palette')
          + customInput('materials', '自訂材質', '例如：舊皮革、羅紋針織')
          + customInput('colors', '自訂配色', '例如：墨綠為主、少量銅金');
      case 'style':
        return G('style')
          + customInput('style', '自訂風格', '例如：日系卡通渲染，硬邊陰影')
          + groupHTML('light', '版式圖預設均勻平光，只有想改時才選')
          + customInput('light', '自訂光線', '例如：明亮的宴會暖調光影，亮面與暗面分明')
          + G('pose')
          + groupHTML('background', '選了會覆蓋預設背景描述');
      case 'face':
        return FACE_KEYS.map(function (k) { return G(k); }).join('')
          + customInput('face', '自訂臉部', '例如：偏寫實的風格化 CG 美型臉')
          + customInput('eyes', '自訂眼睛', '例如：眼尾微微上挑')
          + customInput('expression', '自訂表情與眼神', '例如：冷靜、警覺、嘴唇閉合');
      case 'hair':
        return G('hair_color') + G('hair_len') + G('hair_style') + G('bangs') + G('hair_quality')
          + customInput('hair', '自訂髮型', '例如：深棕色短髮，整齊後梳');
      case 'body':
        return G('body') + customInput('body', '自訂體型', '例如：肩寬、腰身纖細');
      case 'acc':
        return G('acc_head') + G('acc_face') + G('acc_body') + G('prop')
          + customInput('accessories', '自訂配飾', '例如：腰側長劍與劍鞘');
      case 'extra':
        return '<label class="ch-field"><span class="field-label">補充說明</span>'
          + '<textarea class="ch-input" data-field="extra" rows="3" placeholder="上面沒有的細節，例如：左手戴一只銀色戒指"></textarea></label>'
          + '<div class="ch-field"><span class="field-label">參考圖</span>'
          + '<p class="set-note">這張圖的五官與髮型會被保留，只改下面填的內容。點一張選用，再點一次取消。</p>'
          + '<div id="chRefs" class="ch-refs"></div></div>';
    }
    return '';
  }
  function renderForm() {
    var box = q('#chForm');
    if (!box) return;
    box.innerHTML = '<div class="panel-head"><h2>設定</h2><span class="meta">留空的欄位不會寫進提示詞</span></div>'
      + SECTIONS.map(formSectionHTML).join('');
    qa('.ch-group').forEach(function (g) { renderGroup(g.dataset.group); });
    renderRefs();
    syncStatics();
    updateCounts();
  }
  function nameHTML() {
    return '<label class="ch-field"><span class="field-label">角色名稱</span>'
      + '<input class="ch-input" data-field="name" autocomplete="off" placeholder="例如：Mira">'
      + '<span class="set-note">只用於資產區命名，不會進提示詞。</span></label>';
  }
  function genderHTML() {
    var opts = [['female', '女性'], ['male', '男性'], ['neutral', '中性']];
    return '<div class="ch-field"><span class="field-label" id="chGenderLbl">性別</span>'
      + '<div class="ch-seg" role="group" aria-labelledby="chGenderLbl">'
      + opts.map(function (o) { return '<button type="button" aria-pressed="false" data-ca="gender" data-v="' + o[0] + '">' + o[1] + '</button>'; }).join('')
      + '</div></div>';
  }
  function layoutHTML() {
    var L = CharCompose.LAYOUTS;
    var cards = Object.keys(L).map(function (k) {
      return '<button type="button" class="ch-layout" aria-pressed="false" data-ca="layout" data-v="' + k + '">'
        + '<svg viewBox="0 0 60 36" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + LAYOUT_ICON[k] + '</svg>'
        + '<span class="ch-lname">' + h(L[k].label) + '</span></button>';
    }).join('');
    var bg = '<div class="ch-field"><span class="field-label" id="chBgLbl">背景色</span>'
      + '<div class="ch-pills" role="group" aria-labelledby="chBgLbl">'
      + BG_PRESETS.map(function (v) { return '<button type="button" class="ch-pill" aria-pressed="false" data-ca="bg" data-v="' + h(v) + '">' + h(v) + '</button>'; }).join('')
      + '</div>'
      + '<input class="ch-input" data-field="bg" autocomplete="off" placeholder="自訂背景色，例如：暖灰色"></div>';
    var lang = '<div class="ch-field"><span class="field-label" id="chLangLbl">輸出語言</span>'
      + '<div class="ch-seg" role="group" aria-labelledby="chLangLbl">'
      + '<button type="button" aria-pressed="false" data-ca="lang" data-v="zh">中文</button>'
      + '<button type="button" aria-pressed="false" data-ca="lang" data-v="en">English</button></div>'
      + '<p class="set-note" id="chLangNote" hidden>English 時，自訂欄位的文字不會被翻譯。</p></div>';
    return '<div class="ch-layouts" role="group" aria-label="版式">' + cards + '</div>'
      + '<p class="ch-ldesc" id="chLDesc"></p>' + bg + lang;
  }
  function customInput(key, label, ph) {
    return '<label class="ch-field"><span class="field-label">' + h(label || '自訂') + '</span>'
      + '<input class="ch-input" data-custom="' + h(key) + '" autocomplete="off" placeholder="' + h(ph || '') + '"></label>';
  }
  function groupHTML(key, hint) {
    var fd = S.idx.fields[key];
    if (!fd) return '';
    var lbl = h(fd.label);
    var search = fd.terms.length > 30
      ? '<input type="search" class="ch-input ch-search" hidden data-search="' + h(key) + '" placeholder="搜尋' + lbl + '" aria-label="搜尋' + lbl + '">'
      : '';
    return '<div class="ch-group" data-group="' + h(key) + '">'
      + '<div class="ch-ghead"><span class="ch-glabel">' + lbl + '</span>'
      + (fd.mode === 'one' ? '<span class="ch-gmode">單選</span>' : '')
      + '<span class="ch-gcount mono"></span></div>'
      + (hint ? '<p class="set-note">' + h(hint) + '</p>' : '')
      + search
      + '<div class="ch-pills" role="group" aria-label="' + lbl + '"></div>'
      + '<div class="ch-gfoot"></div>'
      + '</div>';
  }
  function groupEl(key) { return q('.ch-group[data-group="' + key + '"]'); }
  function pillHTML(key, o) {
    var cls = 'ch-pill' + (o.t[2] === 'x' ? ' is-x' : '') + (S.just === key + ':' + o.i ? ' just' : '');
    var title = o.t[1] + (o.t[3] ? ' · ' + o.t[3] : '');
    return '<button type="button" class="' + cls + '" aria-pressed="' + String(o.on) + '" data-ca="chip" data-key="' + h(key) + '" data-i="' + o.i + '" title="' + h(title) + '">' + h(o.t[0]) + '</button>';
  }
  /* 詞條 pill：預設只畫前 18 個（已選的一律保留），慎用（x）收在「顯示慎用項」，超過 30 個有搜尋框 */
  function renderGroup(key) {
    var g = groupEl(key);
    var fd = S.idx.fields[key];
    if (!g || !fd) return;
    var sel = (S.form.chips || {})[key] || [];
    var qEl = g.querySelector('.ch-search');
    var query = qEl ? qEl.value.trim().toLowerCase() : '';
    var expand = !!S.expand[key], showX = !!S.showX[key];
    if (qEl) qEl.hidden = !expand && !query;  // 先看常用的；要找才展開，展開後才有搜尋框
    var all = fd.terms.map(function (t, i) { return { t: t, i: i, on: sel.indexOf(t[0]) !== -1 }; });
    var cand = all.filter(function (o) { return o.t[2] !== 'x' || showX || o.on; });
    var xCount = all.filter(function (o) { return o.t[2] === 'x'; }).length;
    var shown;
    if (query) shown = cand.filter(function (o) { return o.on || (o.t[0] + ' ' + o.t[1]).toLowerCase().indexOf(query) !== -1; });
    else if (expand) shown = cand;
    else shown = cand.filter(function (o, n) { return n < CHIP_LIMIT || o.on; });
    var rest = cand.length - shown.length;
    g.querySelector('.ch-pills').innerHTML = shown.length
      ? shown.map(function (o) { return pillHTML(key, o); }).join('')
      : '<span class="ch-none">沒有符合的詞條</span>';
    var foot = '';
    if (!query && cand.length > CHIP_LIMIT) {
      foot += '<button type="button" class="ch-link" data-ca="more" data-key="' + h(key) + '">' + (expand ? '收起' : '再顯示 ' + rest + ' 個') + '</button>';
    }
    if (xCount) {
      foot += '<button type="button" class="ch-link" data-ca="showx" data-key="' + h(key) + '">' + (showX ? '隱藏慎用項' : '顯示慎用項（' + xCount + '）') + '</button>';
    }
    g.querySelector('.ch-gfoot').innerHTML = foot;
    var cnt = g.querySelector('.ch-gcount');
    if (cnt) cnt.textContent = sel.length ? String(sel.length) : '';
  }
  function toggleChip(key, i) {
    var fd = S.idx.fields[key];
    var t = fd && fd.terms[i];
    if (!t) return;
    var zh = t[0];
    S.form.chips = S.form.chips || {};
    var arr = (S.form.chips[key] || []).slice();
    var pos = arr.indexOf(zh);
    if (fd.mode === 'one') arr = pos !== -1 ? [] : [zh];
    else if (pos !== -1) arr.splice(pos, 1);
    else arr.push(zh);
    S.form.chips[key] = arr;
    S.just = key + ':' + i;
    renderGroup(key);
    S.just = null;
    afterFormChange(true);
  }
  function toggleSec(btn) {
    var sec = btn.closest('.ch-sec');
    var id = btn.dataset.sec;
    var open = !sec.classList.contains('open');
    sec.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
    S.open[id] = open;
  }

  /* ---------- 參考圖（全域 assets，由 panel.html 維護） ---------- */
  function renderRefs() {
    var box = q('#chRefs');
    if (!box) return;
    var list = (typeof assets !== 'undefined' && assets) ? assets : [];
    if (!list.length) {
      box.innerHTML = '<div class="empty ch-empty">資產區是空的，到手動台上傳</div>';
      return;
    }
    box.innerHTML = list.map(function (a) {
      var v = '@' + a.label;
      return '<button type="button" class="ch-ref" aria-pressed="' + String(S.form.ref === v) + '" data-ca="ref" data-v="' + h(v) + '" title="' + h(a.name) + '">'
        + '<img src="/api/assets/file/' + encodeURIComponent(a.id) + '" alt="" loading="lazy">'
        + '<span>' + h(a.label) + '</span></button>';
    }).join('');
  }

  /* ---------- 快速開始：預設、新角色、載入 ---------- */
  function renderPresets() {
    var box = q('#chPresets');
    if (!box) return;
    box.innerHTML = S.presets.map(function (p) {
      return '<button type="button" class="btn sm ghost" data-ca="preset" data-id="' + h(p.id) + '" title="' + h(p.desc) + '">' + h(p.name) + '</button>';
    }).join('');
  }
  function applyPreset(id) {
    var p = S.presets.filter(function (x) { return x.id === id; })[0];
    if (!p) return;
    var f = CharCompose.defaultForm();
    Object.assign(f, clone(p.form || {}));
    if (!f.name && p.id !== 'blank') f.name = p.name;
    S.form = normForm(f);
    S.charId = null; S.note = ''; S.manual = false; S.polishPrev = null; S.stageFile = null;
    renderForm(); refreshPreview(true); saveDraft(); renderPicker(); renderCards(); renderJobStrip();
    toast('已套用：' + p.name, 'ok');
  }
  function newChar() {
    S.form = CharCompose.defaultForm();
    S.charId = null; S.note = ''; S.manual = false; S.polishPrev = null; S.stageFile = null;
    renderForm(); refreshPreview(true); saveDraft(); renderPicker(); renderCards(); renderJobStrip();
    toast('已開新角色', 'ok');
  }
  function loadChar(id) {
    var c = findChar(id);
    if (!c) return;
    S.form = normForm(c.form);
    S.charId = c.id;
    S.stageFile = null;
    S.note = c.note || '';
    S.polishPrev = null;
    var auto = CharCompose.compose(S.form, S.idx).prompt;
    S.manual = !!c.prompt && c.prompt !== auto;
    S.prompt = c.prompt || auto;
    renderForm(); refreshPreview(true); saveDraft(); renderPicker(); renderCards(); renderJobStrip();
    toast('已載入：' + (c.name || '未命名角色'), 'ok');
  }
  function renderPicker() {
    var sel = q('#chPicker');
    if (!sel) return;
    var list = S.chars.slice().sort(byUpdated);
    var sig = list.map(function (c) { return c.id + '\u0001' + (c.name || ''); }).join('\u0002');
    if (sig !== S.pickSig) {
      S.pickSig = sig;
      sel.innerHTML = '<option value="">我的角色…</option>' + list.map(function (c) {
        return '<option value="' + h(c.id) + '">' + h(c.name || '未命名角色') + '</option>';
      }).join('');
    }
    sel.value = S.charId && findChar(S.charId) ? S.charId : '';
  }

  /* ---------- 儲存與出圖 ---------- */
  function upsert(c) {
    if (!c || !c.id) return;
    for (var i = 0; i < S.chars.length; i++) {
      if (S.chars[i].id === c.id) { S.chars[i] = Object.assign({}, S.chars[i], c); return; }
    }
    S.chars.push(c);
  }
  async function saveEditor() {
    var name = String(S.form.name || '').trim() || '未命名角色';
    S.form.name = name;
    var body = { name: name, form: S.form, prompt: S.prompt, note: S.note };
    if (S.charId) body.id = S.charId;
    var r = await req('/api/character/save', body);
    var c = r.character;
    S.charId = c.id;
    if (c.note != null) S.note = c.note;
    upsert(c);
    saveDraft();
    syncStatics();
    renderPicker(); renderCards(); renderJobStrip();
    return c;
  }
  async function saveClick(btn) {
    setBusy(btn, true, '儲存中…');
    try {
      var c = await saveEditor();
      toast('已儲存：' + c.name, 'ok');
    } catch (e) {
      toast('儲存失敗：' + errorText(e), 'err');
    } finally { setBusy(btn, false); }
  }
  async function generate() {
    var btn = q('[data-ca="generate"]');
    setBusy(btn, true, '排入中…');
    try {
      var c = await saveEditor();
      await req('/api/character/generate', { id: c.id, prompt: S.prompt });
      S.lastFailed = null;
      toast('已排入出圖佇列', 'ok');
      await refreshList(false);
    } catch (e) {
      toast('出圖失敗：' + errorText(e), 'err');
    } finally { setBusy(btn, false); }
  }

  /* ---------- AI：潤飾、分析、匯入 ---------- */
  async function polish() {
    var ta = q('#chPrompt');
    var btn = q('[data-ca="polish"]');
    if (!S.prompt.trim()) { toast('提示詞是空的，先填寫或組合再潤飾', 'err'); return; }
    setBusy(btn, true, '潤飾中…');
    ta.classList.add('thinking');
    try {
      var r = await req('/api/character/polish', { prompt: S.prompt, note: q('#chNote').value.trim() });
      if (!r.prompt) throw new Error('沒有回傳內容');
      S.polishPrev = { prompt: S.prompt, manual: S.manual };
      S.prompt = r.prompt;
      S.manual = true;
      renderPromptBox(true);
      saveDraft();
      toast('已潤飾（' + (r.provider || 'AI') + '）', 'ok');
    } catch (e) {
      failAI(e);
    } finally {
      ta.classList.remove('thinking');
      setBusy(btn, false);
    }
  }
  function undoPolish() {
    if (!S.polishPrev) return;
    S.prompt = S.polishPrev.prompt;
    S.manual = S.polishPrev.manual;
    S.polishPrev = null;
    refreshPreview(true);
    saveDraft();
  }
  function toggleManual() {
    if (S.manual) { restoreAuto(); return; }
    S.manual = true;
    renderPromptBox(false);
    var ta = q('#chPrompt');
    if (ta) ta.focus();
    saveDraft();
  }
  function restoreAuto() {
    S.manual = false;
    S.polishPrev = null;
    refreshPreview(true);
    saveDraft();
  }
  async function analyze() {
    var ta = q('#chAnalyze');
    var btn = q('[data-ca="analyze"]');
    var text = ta.value.trim();
    if (!text) { toast('先貼上一段角色敘述，或匯入文字檔', 'err'); return; }
    setBusy(btn, true, '分析中…');
    try {
      var r = await req('/api/character/analyze', { text: text });
      var fields = r.fields || {};
      var before = {};
      SECTIONS.forEach(function (s) { before[s.id] = sectionFill(s); });
      S.form = CharCompose.applyAnalysis(S.form, fields, S.idx);
      SECTIONS.forEach(function (s) { if (sectionFill(s) !== before[s.id]) S.open[s.id] = true; });
      var n = Object.keys(fields).filter(function (k) { return String(fields[k] == null ? '' : fields[k]).trim(); }).length;
      renderForm(); refreshPreview(true); saveDraft();
      toast('已填入 ' + n + ' 個欄位（來自 ' + (r.provider || 'AI') + '）', 'ok');
    } catch (e) {
      failAI(e);
    } finally { setBusy(btn, false); }
  }
  function onFile(input) {
    var f = input.files && input.files[0];
    if (!f) return;
    if (f.size > 200 * 1024) {
      toast('檔案超過 200KB，請縮短後再匯入', 'err');
      input.value = '';
      return;
    }
    var rd = new FileReader();
    rd.onload = function () {
      q('#chAnalyze').value = String(rd.result || '');
      toast('已匯入：' + f.name, 'ok');
    };
    rd.onerror = function () { toast('讀取檔案失敗', 'err'); };
    rd.readAsText(f);
    input.value = '';
  }

  /* ---------- 角色列表、出圖狀態、輪詢 ---------- */
  async function refreshList(quiet) {
    if (!isOn()) { stopPoll(); return; }
    try {
      var r = await req('/api/character/list');
      S.chars = r.characters || [];
      trackJobs();
      renderPicker(); renderCards(); renderJobStrip();
      if (anyActive()) startPoll(); else stopPoll();
    } catch (e) {
      if (!quiet) toast('讀取角色列表失敗：' + errorText(e), 'err');
      if (anyActive()) startPoll();
    }
  }
  /* 只在某個 job 的狀態「從進行中變成完成／失敗」時提示一次；第一次載入時不提示 */
  function trackJobs() {
    var first = !S.jobsInit;
    S.jobsInit = true;
    var fresh = false;
    S.chars.forEach(function (c) {
      (c.jobs || []).forEach(function (j) {
        var prev = S.jobState[j.job_id];
        S.jobState[j.job_id] = j.state;
        if (first || prev === undefined || prev === j.state) return;
        if (j.state === 'done') { fresh = true; if (S.charId === c.id) S.stageFile = null; toast('角色圖完成，已放進資產區', 'ok'); }
        else if (j.state === 'failed') {
          S.lastFailed = { charId: c.id, msg: j.error || '未知錯誤' };
          toast('出圖失敗：' + (j.error || '未知錯誤'), 'err');
        }
      });
    });
    if (fresh && typeof loadAssets === 'function') loadAssets().then(renderRefs);
  }
  function startPoll() {
    if (S.pollTimer || !isOn()) return;
    S.pollTimer = setTimeout(function () { S.pollTimer = null; refreshList(true); }, POLL_MS);
  }
  function stopPoll() {
    if (S.pollTimer) { clearTimeout(S.pollTimer); S.pollTimer = null; }
  }
  function renderJobStrip() {
    var box = q('#chJob');
    if (!box) return;
    var c = S.charId ? findChar(S.charId) : null;
    var act = c ? activeJob(c) : null;
    if (act) {
      var p = jobPct(act);
      box.hidden = false;
      box.innerHTML = '<div class="ch-job-row"><div class="ch-bar"><i style="width:' + p + '%"></i></div><span class="meta">' + h(jobLabel(act)) + '</span></div>';
    } else if (c && S.lastFailed && S.lastFailed.charId === c.id) {
      box.hidden = false;
      box.innerHTML = '<p class="ch-err">上一張出圖失敗：' + h(S.lastFailed.msg) + '</p>';
    } else {
      box.hidden = true;
      box.innerHTML = '';
    }
  }
  function tileHTML(c) {
    var imgs = sortedImages(c);
    var a = activeJob(c);
    var thumb = imgs.length
      ? '<img src="' + h(imgUrl(imgs[0].file)) + '" alt="" loading="lazy">'
      : '<span class="ch-ph">' + PH_ICON + '尚未出圖</span>';
    var tag = a ? '<span class="badge ch-busy"><i class="spin"></i>' + h(jobLabel(a)) + '</span>' : '';
    var nm = c.name || '未命名角色';
    return '<div class="ch-tile' + (S.charId === c.id ? ' is-current' : '') + '">'
      + '<button type="button" class="ch-tilemain" data-ca="pick" data-id="' + h(c.id) + '" aria-label="選用 ' + h(nm) + '">'
      + '<span class="ch-thumb">' + thumb + tag + '</span>'
      + '<span class="ch-cardbody"><span class="ch-cname">' + h(nm) + '</span>'
      + '<span class="meta">' + imgs.length + ' 張 · ' + h(relTime(c.updated)) + '</span></span></button>'
      + '<button type="button" class="ch-tilemenu" data-ca="open" data-id="' + h(c.id) + '" aria-label="管理 ' + h(nm) + '" title="改名、備註、複製、刪除">' + ICON_MORE + '</button></div>';
  }
  function renderCards() {
    var box = q('#chCards');
    if (!box) return;
    var list = S.chars.slice().sort(byUpdated);
    setText('#chCount', list.length + ' 個角色');
    var sig = list.map(function (c) {
      var a = activeJob(c);
      return [c.id, c.name, c.updated, S.charId === c.id ? 1 : 0,
        (c.images || []).map(function (im) { return im.file; }).join(','),
        a ? a.job_id + ':' + jobPct(a) : ''].join('|');
    }).join('\n');
    if (sig !== S.cardSig) {
      S.cardSig = sig;
      var add = '<button type="button" class="ch-tile ch-new" data-ca="new" aria-label="新角色"><span class="ch-thumb"><span class="ch-ph">' + ICON_PLUS + '新角色</span></span></button>';
      box.innerHTML = add + list.map(tileHTML).join('');
    }
    renderStage();
  }

  /* 舞台：目前這個角色的圖，完整顯示（直式、橫式都看得到整張），不用開視窗 */
  function renderStage() {
    var box = q('#chStage');
    if (!box) return;
    var c = S.charId ? findChar(S.charId) : null;
    var imgs = c ? sortedImages(c) : [];
    var cur = imgs.filter(function (im) { return im.file === S.stageFile; })[0] || imgs[0] || null;
    var act = c ? activeJob(c) : null;
    var sig = [c ? c.id : '', cur ? cur.file : '', imgs.length, act ? act.job_id + ':' + jobPct(act) : ''].join('|');
    if (sig === S.stageSig) return;
    S.stageSig = sig;
    var head = '<div class="ch-shead"><span class="ch-sname">' + h(c ? (c.name || '未命名角色') : '預覽') + '</span>'
      + '<span class="meta">' + (cur ? imgs.length + ' 張圖' : '') + '</span></div>';
    var body;
    if (cur) {
      var f = h(cur.file);
      body = '<button type="button" class="ch-sbox" data-ca="s-zoom" data-file="' + f + '" aria-label="放大查看">'
        + '<img src="' + h(imgUrl(cur.file)) + '" alt="' + h(c.name || '') + '">'
        + (act ? '<span class="ch-sbusy"><i class="spin"></i>' + h(jobLabel(act)) + '</span>' : '') + '</button>'
        + (imgs.length > 1 ? '<div class="ch-sthumbs">' + imgs.map(function (im) {
          return '<button type="button" class="ch-sthumb' + (im.file === cur.file ? ' on' : '') + '" data-ca="s-pick" data-file="' + h(im.file) + '" aria-label="切換到這張"><img src="' + h(imgUrl(im.file)) + '" alt="" loading="lazy"></button>';
        }).join('') + '</div>' : '')
        + '<div class="ch-stools">'
        + '<button type="button" class="btn sm" data-ca="s-iter" data-file="' + f + '" title="加入參考圖、載入設定，改你接著填的內容後再出圖">以這張為參考換裝</button>'
        + '<button type="button" class="btn sm ghost" data-ca="s-ref" data-file="' + f + '">設為參考圖</button>'
        + '<button type="button" class="x-btn" data-ca="s-del" data-file="' + f + '" aria-label="刪除這張圖" title="刪除這張圖">' + ICON_X + '</button></div>';
    } else {
      body = '<div class="ch-sbox ch-sempty">' + (act
        ? '<span class="ch-sbusy on"><i class="spin"></i>' + h(jobLabel(act)) + '</span>'
        : '<span class="ch-ph">' + PH_ICON + (c ? '這個角色還沒有圖，按「出圖」' : (S.chars.length ? '從上方挑一個角色，或選預設開始' : '選好設定按「出圖」，圖會出現在這裡')) + '</span>') + '</div>';
    }
    box.innerHTML = head + body;
  }

  /* ---------- 角色詳情視窗（沿用 .modal；panel.html 的 Esc 與背景點擊會關閉它） ---------- */
  function ensureModal() {
    if ($modal) return $modal;
    $modal = document.createElement('div');
    $modal.className = 'modal';
    $modal.id = 'chModal';
    $modal.hidden = true;
    $modal.innerHTML = '<div class="modal-box ch-detail" role="dialog" aria-modal="true" aria-labelledby="chDetailTitle">'
      + '<div class="modal-head"><h2 id="chDetailTitle">角色詳情</h2>'
      + '<button type="button" class="icon-btn" data-close-modal aria-label="關閉">' + ICON_CLOSE + '</button></div>'
      + '<div id="chDetailBody"></div></div>';
    document.body.appendChild($modal);
    $modal.addEventListener('click', onClick);
    $modal.addEventListener('change', onModalChange);
    $modal.addEventListener('keydown', onModalKey);
    return $modal;
  }
  function closeModal() { if ($modal) $modal.hidden = true; }
  function openDetail(id) {
    var c = findChar(id);
    if (!c) return;
    ensureModal();
    S.detailId = id;
    renderDetail(c);
    $modal.hidden = false;
    var b = $modal.querySelector('[data-close-modal]');
    if (b) b.focus();
  }
  function renderDetail(c) {
    var body = $modal.querySelector('#chDetailBody');
    var n = (c.images || []).length;
    body.innerHTML = '<div class="ch-dhead">'
      + '<label class="ch-field"><span class="field-label">角色名稱（按 Enter 儲存）</span><input class="ch-input ch-dname" id="chDName" autocomplete="off" value="' + h(c.name) + '"></label>'
      + '<label class="ch-field"><span class="field-label">備註</span><input class="ch-input" id="chDNote" autocomplete="off" placeholder="例如：給小說第二章用" value="' + h(c.note) + '"></label>'
      + '<p class="meta">更新 ' + h(relTime(c.updated)) + ' · 共 ' + n + ' 張圖</p></div>'
      + '<div class="ch-dimgs" id="chDImgs"></div>'
      + '<div class="ch-dactions">'
      + '<button type="button" class="btn sm" data-ca="d-load">載入設定到編輯器</button>'
      + '<button type="button" class="btn sm ghost" data-ca="d-copy">複製提示詞</button>'
      + '<button type="button" class="btn sm ghost" data-ca="d-dup">複製一份</button>'
      + '<button type="button" class="btn sm ghost ch-del" data-ca="d-del">刪除角色</button>'
      + '</div>';
    renderDetailImgs(c);
  }
  function renderDetailImgs(c) {
    var box = $modal && $modal.querySelector('#chDImgs');
    if (!box || !c) return;
    var imgs = sortedImages(c);
    if (!imgs.length) {
      box.innerHTML = '<div class="empty ch-empty">還沒有圖。出圖完成後會出現在這裡。</div>';
      return;
    }
    box.innerHTML = imgs.map(function (im, i) {
      var f = h(im.file);
      return '<figure class="ch-dimg">'
        + '<button type="button" class="ch-dimgbtn" data-ca="zoom" data-file="' + f + '" aria-label="放大第 ' + (imgs.length - i) + ' 張">'
        + '<img src="' + h(imgUrl(im.file)) + '" alt="" loading="lazy"></button>'
        + '<figcaption class="ch-dtools">'
        + '<button type="button" class="btn sm" data-ca="d-iter" data-file="' + f + '" title="加入參考圖、載入這個角色的設定，只改你接著填的內容">以這張為參考換裝</button>'
        + '<button type="button" class="btn sm ghost" data-ca="d-ref" data-file="' + f + '">設為參考圖</button>'
        + '<button type="button" class="x-btn" data-ca="img-del" data-file="' + f + '" aria-label="刪除這張圖" title="刪除這張圖">' + ICON_X + '</button>'
        + '</figcaption></figure>';
    }).join('');
  }
  async function setAsRef(btn) {
    var c = findChar(S.detailId);
    if (!c) return;
    setBusy(btn, true, '加入中…');
    try {
      var r = await req('/api/character/image/to_asset', { file: btn.dataset.file, name: (c.name || '角色') + ' 參考' });
      var lbl = r.label || '';
      toast('已加入資產 ' + lbl + '，在提示詞輸入 @' + lbl + ' 即可當參考圖', 'ok');
      if (typeof loadAssets === 'function') await loadAssets();
      renderRefs();
    } catch (e) {
      toast('加入資產失敗：' + errorText(e), 'err');
    } finally { setBusy(btn, false); }
  }
  /* 同一個角色換裝的迭代：圖進資產區 → 載入設定 → 用 @圖N 當參考，關視窗回到編輯器 */
  async function iterate(btn) {
    var c = findChar(S.detailId);
    if (!c) return;
    setBusy(btn, true, '處理中…');
    try {
      var r = await req('/api/character/image/to_asset', { file: btn.dataset.file, name: (c.name || '角色') + ' 參考' });
      if (typeof loadAssets === 'function') await loadAssets();
      loadChar(c.id);
      S.form.ref = '@' + (r.label || '');
      renderRefs();
      afterFormChange(true);
      closeModal();
      window.scrollTo(0, 0);
      toast('已用 ' + r.label + ' 當參考：五官與髮型保留，改下面的服裝或風格後再出圖', 'ok');
    } catch (e) {
      toast('失敗：' + errorText(e), 'err');
    } finally { setBusy(btn, false); }
  }
  async function deleteImage(btn) {
    var c = findChar(S.detailId);
    if (!c) return;
    btn.disabled = true;
    try {
      var r = await req('/api/character/image/delete', { id: c.id, file: btn.dataset.file });
      upsert(r.character);
      renderDetailImgs(findChar(c.id));
      renderCards();
      toast('已刪除這張圖', 'ok');
    } catch (e) {
      btn.disabled = false;
      toast('刪除失敗：' + errorText(e), 'err');
    }
  }
  async function deleteChar(id) {
    await req('/api/character/delete', { id: id });
    S.chars = S.chars.filter(function (c) { return c.id !== id; });
    if (S.charId === id) { S.charId = null; S.note = ''; saveDraft(); }
    closeModal();
    renderPicker(); renderCards(); renderJobStrip();
    toast('已刪除角色', 'ok');
  }
  async function duplicateChar(id) {
    var c = findChar(id);
    if (!c) return;
    var name = (c.name || '未命名角色') + '（副本）';
    var r = await req('/api/character/save', { name: name, form: Object.assign(clone(c.form || {}), { name: name }), prompt: c.prompt || '', note: c.note || '' });
    upsert(r.character);
    renderPicker(); renderCards();
    toast('已複製一份：' + name, 'ok');
  }
  async function saveName(raw, input) {
    var c = findChar(S.detailId);
    if (!c) return;
    var name = String(raw || '').trim() || '未命名角色';
    input.value = name;
    if (name === c.name) return;
    try {
      var r = await req('/api/character/save', { id: c.id, name: name, form: Object.assign(clone(c.form || {}), { name: name }), prompt: c.prompt || '', note: c.note || '' });
      upsert(r.character);
      if (S.charId === c.id) { S.form.name = name; syncStatics(); saveDraft(); }
      renderPicker(); renderCards();
      toast('已改名為 ' + name, 'ok');
    } catch (e) {
      toast('改名失敗：' + errorText(e), 'err');
    }
  }
  async function saveNote(raw) {
    var c = findChar(S.detailId);
    if (!c) return;
    var note = String(raw || '');
    if (note === (c.note || '')) return;
    try {
      var r = await req('/api/character/save', { id: c.id, name: c.name, form: c.form || {}, prompt: c.prompt || '', note: note });
      upsert(r.character);
      if (S.charId === c.id) { S.note = note; saveDraft(); }
      toast('已儲存備註', 'ok');
    } catch (e) {
      toast('儲存備註失敗：' + errorText(e), 'err');
    }
  }
  function onModalChange(e) {
    var t = e.target;
    if (t.id === 'chDName') saveName(t.value, t);
    else if (t.id === 'chDNote') saveNote(t.value);
  }
  function onModalKey(e) {
    if (e.key === 'Enter' && e.target.id === 'chDName') { e.preventDefault(); e.target.blur(); }
  }

  /* ---------- 事件委派 ---------- */
  function onClick(e) {
    var t = e.target.closest('[data-ca]');
    if (!t) return;
    var a = t.dataset.ca;
    switch (a) {
      case 'sec': toggleSec(t); break;
      case 'layout': S.form.layout = t.dataset.v; afterFormChange(true); break;
      case 'gender': S.form.gender = S.form.gender === t.dataset.v ? '' : t.dataset.v; afterFormChange(true); break;
      case 'lang': S.form.lang = t.dataset.v; afterFormChange(true); break;
      case 'bg': S.form.bg = t.dataset.v; afterFormChange(true); break;
      case 'chip': toggleChip(t.dataset.key, Number(t.dataset.i)); break;
      case 'more': S.expand[t.dataset.key] = !S.expand[t.dataset.key]; renderGroup(t.dataset.key); break;
      case 'showx': S.showX[t.dataset.key] = !S.showX[t.dataset.key]; renderGroup(t.dataset.key); break;
      case 'ref': S.form.ref = S.form.ref === t.dataset.v ? '' : t.dataset.v; renderRefs(); afterFormChange(true); break;
      case 'preset': applyPreset(t.dataset.id); break;
      case 'new': newChar(); break;
      case 'import': q('#chFile').click(); break;
      case 'analyze': analyze(); break;
      case 'manual': toggleManual(); break;
      case 'restore': restoreAuto(); break;
      case 'copy': copyText(S.prompt, '已複製提示詞'); break;
      case 'polish': polish(); break;
      case 'undo': undoPolish(); break;
      case 'save': saveClick(t); break;
      case 'generate': generate(); break;
      case 'open': openDetail(t.dataset.id); break;
      case 'pick': loadChar(t.dataset.id); break;
      case 's-pick': S.stageFile = t.dataset.file; renderStage(); break;
      case 's-zoom': {
        var sc = findChar(S.charId);
        showLightbox(imgUrl(t.dataset.file), (sc && sc.name) || '角色圖', '角色設計');
        break;
      }
      case 's-ref': S.detailId = S.charId; setAsRef(t); break;
      case 's-iter': S.detailId = S.charId; iterate(t); break;
      case 's-del': twoStep(t, '確認刪除', function () { S.detailId = S.charId; deleteImage(t); }); break;
      case 'zoom': {
        var zc = findChar(S.detailId);
        showLightbox(imgUrl(t.dataset.file), (zc && zc.name) || '角色圖', '角色設計');
        break;
      }
      case 'd-ref': setAsRef(t); break;
      case 'd-iter': iterate(t); break;
      case 'img-del': twoStep(t, '確認刪除', function () { deleteImage(t); }); break;
      case 'd-load': loadChar(S.detailId); closeModal(); window.scrollTo(0, 0); break;
      case 'd-copy': {
        var dc = findChar(S.detailId);
        if (dc && dc.prompt) copyText(dc.prompt, '已複製提示詞');
        else toast('這個角色還沒有提示詞', 'err');
        break;
      }
      case 'd-dup': run(function () { return duplicateChar(S.detailId); }, '複製失敗'); break;
      case 'd-del': twoStep(t, '再按一次確定刪除', function () { run(function () { return deleteChar(S.detailId); }, '刪除失敗'); }); break;
    }
  }
  function onInput(e) {
    var t = e.target;
    if (t.id === 'chPrompt') {
      if (S.manual) { S.prompt = t.value; updatePromptCount(); saveDraft(); }
      return;
    }
    if (t.dataset.search) { renderGroup(t.dataset.search); return; }
    if (t.dataset.field) { S.form[t.dataset.field] = t.value; afterFormChange(false); return; }
    if (t.dataset.custom) { S.form.custom[t.dataset.custom] = t.value; afterFormChange(false); }
  }
  function onChange(e) {
    var t = e.target;
    if (t.id === 'chFile') onFile(t);
    else if (t.id === 'chPicker' && t.value) loadChar(t.value);
  }

  /* ---------- 版面骨架 ---------- */
  function sideHTML() {
    return '<div class="panel-head ch-phead"><h2>提示詞</h2><div class="ch-badges">'
      + '<span class="badge" id="chLayoutBadge"></span><span class="badge" id="chRatioBadge"></span>'
      + '<span class="badge ok" id="chManualBadge" hidden>已手動修改</span></div></div>'
      + '<div class="ch-promptbar">'
      + '<button type="button" class="ch-toggle" data-ca="manual" aria-pressed="false"><i></i>手動編輯</button>'
      + '<button type="button" class="ch-link" id="chRestore" data-ca="restore" hidden>還原為自動組合</button></div>'
      + '<textarea class="ch-prompt" id="chPrompt" readonly spellcheck="false" aria-label="提示詞"></textarea>'
      + '<ul class="ch-warn" id="chWarn"></ul>'
      + '<div class="meta" id="chChars"></div>'
      + '<div class="ch-actions">'
      + '<button type="button" class="btn sm ghost" data-ca="copy">複製</button>'
      + '<button type="button" class="btn sm" data-ca="save">儲存角色</button>'
      + '<button type="button" class="btn primary" data-ca="generate">出圖</button></div>'
      + '<div class="ch-job" id="chJob" hidden></div>'
      + '<div class="ch-polish">'
      + '<input class="ch-input" id="chNote" autocomplete="off" placeholder="AI 潤飾的額外指示（可空），例如：少一點配飾" aria-label="AI 潤飾的額外指示">'
      + '<button type="button" class="btn sm" data-ca="polish">AI 潤飾</button>'
      + '<button type="button" class="btn sm ghost" data-ca="undo" hidden>撤銷潤飾</button></div>';
  }
  function aiBodyHTML() {
    return '<label class="ch-field"><span class="field-label">角色敘述</span>'
      + '<textarea class="ch-input" id="chAnalyze" rows="6" placeholder="貼上一段角色敘述，例如小說段落、設定文字…"></textarea></label>'
      + '<div class="ch-actions">'
      + '<button type="button" class="btn sm ghost" data-ca="import">匯入文字檔</button>'
      + '<button type="button" class="btn sm" data-ca="analyze">分析並填入</button></div>'
      + '<p class="set-note">匯入的檔案不超過 200KB，內容會放進上方輸入框。</p>';
  }
  function shellHTML() {
    return '<div class="title-row"><div><h1>角色設計</h1>'
      + '<p class="sub">先在上面挑一個角色，或選預設開始；左邊調整設定，右邊直接看圖、出圖。</p></div></div>'
      + '<section class="panel ch-library" aria-labelledby="chAssetsTitle">'
      + '<div class="panel-head"><h2 id="chAssetsTitle">角色資產</h2><span class="meta" id="chCount"></span></div>'
      + '<div class="ch-strip" id="chCards"></div></section>'
      + '<div class="ch-cols">'
      + '<section class="panel ch-form" aria-label="角色設定">'
      + '<div class="ch-start">'
      + '<div class="ch-row"><span class="ch-label">快速開始</span><div class="ch-presets" id="chPresets"></div></div>'
      + '<div class="ch-aiwrap">' + secHTML('ai', '從一段敘述自動填表', false, S.open.ai, aiBodyHTML()) + '</div>'
      + '<select class="ch-picker-hidden" id="chPicker" hidden></select></div>'
      + '<div id="chForm"></div></section>'
      + '<aside class="panel ch-side" aria-label="預覽與出圖">' + stageShellHTML() + sideHTML() + '</aside>'
      + '</div>'
      + '<input type="file" id="chFile" hidden accept=".txt,.md,.json,text/plain">';
  }
  function stageShellHTML() {
    return '<div class="ch-stage" id="chStage"></div>';
  }
  function mount() {
    $root.innerHTML = shellHTML();
    renderPresets();
    renderForm();
    refreshPreview(false);
    renderPicker();
    renderCards();
    renderJobStrip();
  }

  /* ---------- 啟動與路由 ---------- */
  async function boot() {
    if (S.booting || S.booted) return;
    S.booting = true;
    try {
      var lexRes = await fetch('/character/lexicon.json');
      if (!lexRes.ok) throw new Error('詞庫讀取失敗 HTTP ' + lexRes.status);
      var presRes = await fetch('/character/presets.json');
      if (!presRes.ok) throw new Error('預設讀取失敗 HTTP ' + presRes.status);
      S.lex = await lexRes.json();
      S.idx = CharCompose.buildIndex(S.lex);
      S.presets = (await presRes.json()).presets || [];
      restoreDraft();
      S.booted = true;
      mount();
      await refreshList(false);
    } catch (e) {
      $root.innerHTML = '<div class="empty">角色設計載入失敗：' + h(errorText(e)) + '</div>';
    } finally {
      S.booting = false;
    }
  }
  function onRoute() {
    if (!isOn()) { stopPoll(); return; }
    if (!S.booted) { boot(); return; }
    refreshList(false);
    renderRefs();
  }

  $root.addEventListener('click', onClick);
  $root.addEventListener('input', onInput);
  $root.addEventListener('change', onChange);
  window.addEventListener('hashchange', onRoute);
  onRoute();
})();
