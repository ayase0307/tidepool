<div align="center">

<p><a href="README.md">繁體中文</a> · <a href="README.en.md">English</a></p>

<img src="brand/logo.png" width="88" alt="Tidepool logo" />

# Tidepool

**把你已經在付費的 ChatGPT 網頁版額度，用到滿。**

排隊、並行、自動登入、給 agent 呼叫 —— 全部跑在你自己的電腦上，不需要 API key。

[![Python](https://img.shields.io/badge/python-3.10%2B-171717?logo=python&logoColor=3776AB&style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-171717?style=flat-square)](#快速開始)
[![License](https://img.shields.io/badge/license-MIT-45b396?style=flat-square)](LICENSE)
[![API](https://img.shields.io/badge/api-NewAPI%20compatible-45b396?style=flat-square)](#給-agent-用)

</div>

<p align="center">
  <img src="brand/banner.png" alt="Tidepool" width="100%" />
</p>

---

## 為什麼是 Tidepool

ChatGPT 網頁版每個帳號都有一份出圖額度，但它只能一張一張、手動在網頁上按。
Tidepool 把這份**本來就在付的額度**變成一個可以排程的本機服務：

- **額度用滿** — 多個帳號各自獨立的瀏覽器 profile，任務依剩餘額度自動分配、並行出圖
- **不用 API key、不另外付 API 費** — 走的是網頁版本身，登入、驗證碼、2FA 全部自動處理
- **給人用，也給 agent 用** — 網頁控制台 + NewAPI 相容端點 + 一行 CLI
- **全程本機** — 服務只綁 `127.0.0.1`；圖由 ChatGPT 產生，除此之外不經過任何第三方轉手
- **背景靜默** — 瀏覽器與 CLI 都在背景跑，不跳視窗、不搶焦點

> 多帳號的取得與管理方式不在本專案範圍內，請自行尋找合適的管道。
> 請遵守 ChatGPT 的服務條款，只用於你自己的帳號。

## 看看它怎麼用

### 出圖與 AI 優化

寫一句粗略的想法，交給你電腦上的 agent 改寫成專業提示詞，再一鍵出圖。
風格與比例都是選填，留空就原樣送出。

<img src="docs/demo/generate.gif" alt="出圖與 AI 優化" width="100%" />

### 角色設計

用選單挑角色的版式、身份、服裝、風格，提示詞即時組好；
也可以貼一段角色敘述讓 AI 自動填表。出完的圖直接進「角色資產」，隨時挑出來換裝再出。

<img src="docs/demo/character.gif" alt="角色設計" width="100%" />

### Agent 設定：自動偵測，逐家連接

開視窗就自動掃描本機的 agent CLI 與可用模型；沒有 CLI 的就填 API key。
每一項都能測試、刪除，也能新增任何 OpenAI 相容的服務商。

<img src="docs/demo/agents.gif" alt="Agent 設定" width="100%" />

### 圖庫

所有產出一個地方看，直式、橫式都完整顯示；點圖放大，附上當初的提示詞。

<img src="docs/demo/gallery.gif" alt="圖庫" width="100%" />

> 動畫裡等 AI 回覆的空白時間已剪掉（實際 AI 優化約 10 秒）。
> 以上動畫用 [onetake](https://github.com/feitangyuan/onetake) 的錄製腳本實際操作本機控制台拍攝，
> 腳本在 [`demo/footage.json`](demo/footage.json)，轉檔指令在 [`demo/README.md`](demo/README.md)。

## 功能

| | |
| --- | --- |
| **出圖** | 佇列、並行、依配額自動分配帳號；`@圖1` 指定參考圖會自動附到 ChatGPT |
| **角色設計** | 30 個欄位約 1200 個詞條可挑可填；四視圖／檔案板／三視圖／單張立繪／表情表；AI 分析與潤飾；角色資產區 |
| **AI 優化** | 用你已裝好的 Codex / Claude Code / OpenCode / Antigravity CLI，或任何 OpenAI 相容 API |
| **手動台** | 把某個帳號的 ChatGPT 頁面內嵌進控制台，自己打字、上傳參考圖、按送出 |
| **資產與提示詞庫** | 參考圖資產區；內建 541 筆 [awesome-gpt-image-2](https://github.com/freestylefly/awesome-gpt-image-2) 案例 |
| **帳號** | 狀態、剩餘配額、2FA 驗證碼一鍵複製、匯入帳號檔、一鍵全部重新登入 |
| **給 agent** | `/v1/videos` + `/v1/tasks`（NewAPI 相容）、CLI、內附 skill |

## 快速開始

### 全新電腦（Windows）

**0. 先裝兩個東西**

| 需要 | 下載 | 備註 |
| --- | --- | --- |
| Python 3.10+ | <https://www.python.org/downloads/> | 安裝時**務必勾選「Add Python to PATH」** |
| Google Chrome | <https://www.google.com/chrome/> | 沒有也會退回 Edge / 內建 Chromium |

**1. 取得程式**

```powershell
git clone https://github.com/ayase0307/tidepool.git
cd tidepool
```

**2. 建立環境**

```powershell
python -m venv venv
venv\Scripts\pip install -U pip
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m patchright install chromium
```

**3. 放好帳號檔**

建立 `secrets\accounts.txt`，每行一個帳號（格式見[下面](#帳號檔格式)）：

```
you@example.com----your-password----BASE32 2FA 密鑰
```

**4. 啟動**

```powershell
start apps\start_service.vbs
```

開機自啟（選用）：

```powershell
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-ItemProperty -Path $run -Name "Tidepool" -Value "wscript.exe `"$PWD\apps\start_service.vbs`"" -PropertyType String -Force
```

**5. 打開控制台** → <http://127.0.0.1:7707/panel>

就這樣。服務在背景跑，視窗不會跳出來。

<details>
<summary>macOS / Linux</summary>

```bash
git clone https://github.com/ayase0307/tidepool.git && cd tidepool
python3 -m venv venv && source venv/bin/activate
pip install -U pip && pip install -r requirements.txt
python -m patchright install chromium
python core/pool_service.py          # 直接跑前景
# 瀏覽器開 http://127.0.0.1:7707/panel
```

`apps\start_service.vbs` 與 `apps\AI-Pool.vbs` 是 Windows 專用的；macOS / Linux 請直接跑 `core/pool_service.py`。
</details>

## 給 agent 用

### 一步到位：貼給 agent，它自己測試並裝成技能

控制台左下角按「**複製用法給 agent**」，把整段貼給你的 agent（Claude Code、Codex 或其他）。它會自己：

1. 下載已設好網址的技能檔（`/api/agent/skill/…`）
2. 跑一次真的出圖測試（檢查服務與帳號 → 送出 → 等待 → 下載）
3. 用固定格式回報：✅ 成功（耗時、圖片路徑、把圖顯示給你）或 ❌ 失敗（哪一步、原因、建議）
4. 成功後，把它建立成全域的 **tidepool-image** 技能，之後你說「幫我畫…」它就會用

技能腳本只用 Python 標準函式庫，Windows / macOS / Linux 都能跑；原始檔在 [`core/agent_skill/`](core/agent_skill/)。

### 直接呼叫 HTTP API（NewAPI 相容）

```bash
curl -X POST http://127.0.0.1:7707/v1/videos \
  -H "Authorization: Bearer aipool-local" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image2-1k","prompt":"一隻柴犬在草地上奔跑，陽光","aspect_ratio":"16:9"}'
# → {"id":"<task_id>","status":"queued"}

curl http://127.0.0.1:7707/v1/tasks/<task_id> -H "Authorization: Bearer aipool-local"
# → {"status":"completed","result":{"urls":["http://127.0.0.1:7707/api/outputs/file/xxx.png"]}}
```

`style` 選填：留空＝不套用，或填 `concept_art` / `realistic` / `cyberpunk` / `anime` /
`oil_painting` / `product`，也可以直接寫一段英文描述。`aspect_ratio` 會被接成一句英文描述送進提示詞，
解析度由 ChatGPT 決定（`model` 的 2k／4k 後綴不會改變輸出）。

### CLI

```bash
venv\Scripts\python core\generate.py "一隻柴犬在草地上奔跑，陽光" -o out.png -w 3
```

## Agent 設定

控制台左下角的「**Agent 設定**」是一個模型商清單，挑一個當作「AI 優化」與「角色設計」背後的引擎。
每個模型商都會列出**自己可用的模型（自動偵測）**、連線狀態與目前選用的模型；每一項都能刪除（內建的可還原）。

| 分頁 | 內容 | 憑證從哪來 |
| --- | --- | --- |
| 本機 CLI | 自動掃描 PATH 上的 Codex / Claude Code / OpenCode / Antigravity CLI，背景靜默執行、不跳視窗 | 各自的 `login` |
| API 金鑰 | OpenAI、OpenCode Go、Command Code，或自己新增任何 OpenAI 相容服務（名稱 + base_url + key） | 你自己填 |

用法：開視窗會自動偵測 → 從下拉選模型（CLI 可選「CLI 預設」）→ 按「**測試**」確認會回話 → 勾「**優化用**」。
失敗原因會原樣顯示。

## 角色設計

側欄的「**角色設計**」：上方是角色資產（永遠看得到），左邊調設定，右邊直接看圖、出圖。

- **版式**：四視圖（臉部特寫＋正／側／背全身）、檔案板、三視圖、單張立繪、表情表
- **選單**：身份、服裝、風格光線，以及臉、髮、體型、配飾，共 30 個欄位約 1200 個詞條；常用在前、慎用預設收起，每組都能挑或自己填
- **提示詞**：照「人物設定」的規則即時組好 —— 均勻平光、純色背景、全清晰，性別與年齡寫進每個視圖，一致性逐項點名；可手動編輯，也可請 AI 潤飾（可撤銷）
- **從敘述自動填表**：貼上或匯入一段角色敘述，AI 分析後填進表單
- **角色資產**：改名、備註、載入設定、複製、刪除；**以這張為參考換裝**一鍵把圖加進資產、載入設定並用 `@圖N` 當參考

詞庫與四視圖規則取自 [Character-Prompt-Studio](https://github.com/joshesye/Character-Prompt-Studio)
（MIT，基於 AI绘梦師葉子的 ai-image-prompt），授權與來源見 [`core/character/NOTICE.md`](core/character/NOTICE.md)。

## 架構

```
┌─ pool_service.py（單一 Python 進程，背景隱藏執行）──────────┐
│                                                             │
│  :9000  VirtualBrowser 相容 shim                            │
│         （讓 fpbrowser2api / 批次腳本無縫接入）              │
│                                                             │
│  :7707  Admin API + 控制台 + 助手頁                          │
│         /api/generate  /v1/videos  /api/embed/*  /panel     │
│                                                             │
│  BrowserWorker × N：patchright（反偵測 Playwright）          │
│    各自獨立的 profile、UA、canvas/WebGL 噪點指紋             │
│    worker i 的 CDP port = 9300 + i                          │
└─────────────────────────────────────────────────────────────┘
```

- **出圖走頁面內直驅**：送提示詞 → 等待 → 頁面內 fetch 圖片轉 base64（繞開外部 URL 的 403）
- **指紋**：真機身份 + canvas / WebGL / audio 噪點。UA 絕不造假，否則會被當場抓到
- **背景執行**：瀏覽器視窗開起來的瞬間就被 Win32 藏起來，工作列不留圖示；CLI 子行程也一律隱藏
- **中文安全**：提示詞用 `insert_text` 一次插入並在送出前回讀比對，併發時不會把中文吞成 `?`

## 帳號檔格式

`secrets\accounts.txt`，一行一個帳號，三段用四個連字號 `----` 分隔：

```
you@example.com----your-password----BASE32 2FA 密鑰
```

- 2FA 密鑰是 TOTP 的那串種子（匯入時不會驗證，錯了會在登入時失敗）
- `#` 開頭的行會被當成註解略過
- 控制台的「匯入帳號密碼」按鈕可以直接貼上檔案，會自動備份舊檔成 `accounts.txt.bak`
- 每個帳號的 2FA 驗證碼可以在控制台直接點一下複製

## 端點

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| `GET` | `/health` | 服務狀態 |
| `GET` | `/api/workers` · `/api/jobs` · `/api/outputs` | 帳號、工作紀錄、圖檔清單 |
| `POST` | `/api/generate` | 送一筆出圖（`prompt` / `aspect_ratio` / `style` / `worker_id`） |
| `POST` | `/api/open` · `/api/close` · `/api/grab` | 開關帳號瀏覽器、把對話裡最新的圖存進圖庫 |
| `POST` | `/api/accounts/import` · `/api/login/all` | 匯入帳號檔、依序自動登入 |
| `GET`/`POST` | `/api/assets/*` | 資產（上傳、排序、刪除） |
| `GET` | `/api/prompts?q=` | 提示詞庫查詢 |
| `POST` | `/api/prompt/optimize` | AI 優化提示詞 |
| `GET`/`POST` | `/api/agent/providers` · `/scan` · `/provider/*` | Agent 設定（偵測、儲存、測試、刪除、新增） |
| `GET`/`POST` | `/api/character/*` | 角色設計（角色紀錄、AI 分析／潤飾、出圖、角色資產） |
| `POST` | `/api/embed/*` | 內嵌手動台（open / frame / click / type / send / upload） |
| `POST` | `/v1/videos` · `GET` `/v1/tasks/{id}` | NewAPI 相容（給 agent） |

## 注意

- 控制台的「剩餘配額」是本機計數（初始 999，每出一張 -1），**不是 ChatGPT 的官方額度**。實際能出幾張取決於各帳號的方案（免費帳號通常很快到上限、要等官方重置）；到了上限出圖會失敗，換帳號或稍後再試
- 解析度由 ChatGPT 決定；`aspect_ratio` 只是一句文字提示，不保證輸出剛好是該比例
- 服務與所有 API 都只綁 `127.0.0.1`；`/v1/*` 內建的本機金鑰請自行在程式裡改掉再對外暴露
- `secrets/`、`data/`、`output/` 不會進版本控制（帳號、角色、產出都在本機）

## 致謝

[awesome-gpt-image-2](https://github.com/freestylefly/awesome-gpt-image-2)（提示詞庫案例）·
[Character-Prompt-Studio](https://github.com/joshesye/Character-Prompt-Studio)（角色設計詞庫與規則，MIT）·
[onetake](https://github.com/feitangyuan/onetake)（錄製 README 動畫的腳本）

## License

MIT © Tidepool
