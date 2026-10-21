---
name: tidepool-image
description: 用使用者電腦上的 Tidepool（本機 ChatGPT 網頁版出圖服務）生成圖片。使用者要你畫圖、生圖、出圖、做插圖／角色設定圖／海報／封面，或提到 Tidepool、ChatGPT 出圖、gpt-image 時使用。需要 Tidepool 服務正在這台電腦上執行。
---

# Tidepool 出圖

Tidepool 把使用者已登入的 ChatGPT 網頁版帳號變成本機出圖服務：**圖由 ChatGPT 產生**，服務只在本機（`__BASE_URL__`）。

## 怎麼用

```bash
python scripts/generate_image.py "提示詞" -o out.png
```

選用參數：`--ratio 16:9|1:1|9:16|4:3|3:4`、`--style ""`、`--n 3`（一次出多張，會平行送到不同帳號）、`--worker 3`（指定帳號，一般不要指定）、`--timeout 300`。

腳本做完整件事：檢查服務與帳號 → 送出 → 輪詢 → 下載。進度在 stderr，**stdout 最後一行是 JSON**：

- 成功：`{"ok": true, "files": ["…/out.png"], "seconds": 41}` → 把圖給使用者看（能顯示圖片就顯示，否則給完整路徑）。
- 失敗：`{"ok": false, "stage": "…", "code": "…", "error": "…", "hint": "…"}` → 照 `hint` 處理，並把「哪一步、原因、建議」回報使用者。

## 失敗怎麼辦

| code | 意思 | 做法 |
| --- | --- | --- |
| `service_down` | 連不上服務 | 請使用者啟動 Tidepool（Windows：`apps\start_service.vbs`；其他：`python core/pool_service.py`）；網址不同就設 `TIDEPOOL_URL` |
| `no_login` | 沒有已登入的帳號 | 請使用者開 `__BASE_URL__/panel#/accounts` 重新登入 |
| `auth` | 金鑰不對 | 預設 `aipool-local`；使用者改過就用環境變數 `TIDEPOOL_KEY` |
| `job_failed` | 該帳號這次沒出圖 | 多半是帳號到了 ChatGPT 的使用上限或被暫時限流。腳本已自動重試一次；再失敗就停下回報，**不要狂重試** |
| `timeout` | 等太久 | 稍後再試，或加大 `--timeout` |

## 要知道的限制（不要向使用者承諾做不到的事）

- **解析度由 ChatGPT 決定**，這裡無法指定 2k／4k。`--ratio` 只是一句文字提示，不保證輸出剛好是該比例。
- 控制台上的「剩餘配額」只是本機計數，不是 ChatGPT 的官方額度；實際上限依各帳號方案，免費帳號很快會到上限。
- 單張通常 30～90 秒。每次 `--n` 不要超過 3，除非使用者要求；這是使用者自己的額度，沒被要求就不要大量連續出圖。

## 寫提示詞

- 寫完整的畫面描述（主體、動作、場景、光線、風格、鏡頭），中文英文都可以；不要加「請幫我畫」之類的客套話。
- 需要保持同一角色或參考圖時：使用者在控制台「資產」區放的圖，提示詞裡寫 `@圖1`、`@圖2` 會自動附給 ChatGPT（編號見 `GET __BASE_URL__/api/assets` 的 `label`）。
- **角色設定圖**（四視圖）照這個結構寫最穩：16:9 橫幅，四格等距；第一格頭到肩特寫，右邊三格正／側／背全身；均勻平光、純色背景、全部清晰；把髮色、髮型、瞳色、每件衣服逐項寫死（不要只寫「保持一致」）；收尾寫「四格是同一個角色，沒有其他人物，沒有文字和浮水印」。
- 也可以請使用者用控制台「角色設計」頁（`__BASE_URL__/panel#/character`）用選單組提示詞。

## 直接呼叫 API（沒辦法跑 Python 時）

```bash
curl -X POST __BASE_URL__/v1/videos -H "Authorization: Bearer aipool-local" -H "Content-Type: application/json" -d '{"model":"gpt-image2-1k","prompt":"提示詞","aspect_ratio":"16:9"}'
curl __BASE_URL__/v1/tasks/<id> -H "Authorization: Bearer aipool-local"   # 每 5 秒一次，completed 後 result.urls[0] 就是 PNG
```
