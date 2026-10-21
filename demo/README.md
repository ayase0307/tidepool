# README 動畫

README 的動畫是用 [onetake](https://github.com/feitangyuan/onetake) 的 `record_footage.py` 實際操作本機控制台錄的
（Playwright 真實開瀏覽器、真的點、真的等 AI 回覆，不是合成的）。

```powershell
# 1. 先把服務開著（http://127.0.0.1:7707/panel），並確認 Agent 設定裡至少有一個 CLI 是連上的（出圖段的 AI 優化要用）
# 2. 錄製（Windows 要加 PYTHONUTF8=1，不然 footage.json 的中文會被當 cp950 讀）
$env:PYTHONUTF8 = "1"
uvx --with playwright==1.64.0 --with pillow python <onetake>\scripts\record_footage.py demo\footage.json --out footage
# 3. 轉成 GIF → docs/demo/（需要 ffmpeg、Pillow、numpy）
python demo\build_gifs.py footage
```

- `footage.json`：四段劇本（出圖＋AI 優化、角色設計、Agent 設定、圖庫），畫面 1280×800。
  - 游標是頁面內注入的箭頭（Playwright 的錄影本身不含游標），跟著真實的滑鼠事件走，按下時有一圈漣漪。
  - 點擊都用座標上的真實滑鼠點擊；座標是依 1280×800 的版面量的，改了版面要重量。
  - 劇本只打字、按「AI 優化」、切分頁、開燈箱，**不會按「出圖」**，也不會在 API key 欄位輸入任何東西。
- `build_gifs.py`：
  - 從 onetake 的開始標記消失後切起。
  - 等 AI 回覆、等偵測的時候，劇本會在右下角放一個綠色小方塊；有綠塊的畫格整段剪掉，所以等待只留開頭一下光條。
  - 寬 960、12fps、128 色；Playwright 的 webm 有細雜訊，先把「沒真的在動」的 16×16 區塊沿用上一張，GIF 才不會每格整張重畫。
- 已知陷阱（2026-10-21 實測）：`/api/agent/scan` 在上次完整偵測後 15 分鐘內再掃一次，會把被略過的 CLI 也標成未連接
  （`pool_service.py` 的清理迴圈），於是掃描結果會「連上 / 全灰」交替。後果：
  - Agent 設定那段可能錄到全灰的燈；
  - 出圖段的「AI 優化」在全灰狀態下會直接報「還沒連接任何 agent」。
  
  最穩的做法：燈是綠的、而且距離上次偵測超過 15 分鐘再錄。若 agents 段錄到全灰，就在全灰狀態下單獨重錄那一段（劇本只留 `03-agents`）——這時會重新偵測，燈會變綠。
- onetake 的授權是 PolyForm Noncommercial 1.0.0；這裡只用它的錄製腳本拍示範動畫，沒有把它放進本專案。
