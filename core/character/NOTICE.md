# 角色設計：來源與授權

詞庫（`lexicon.json`）與版式規則取自 **Character-Prompt-Studio**
<https://github.com/joshesye/Character-Prompt-Studio>（作者 joshesye），
其內容基於 **AI绘梦師葉子** 的 `ai-image-prompt`
<https://github.com/huimengshiyezi/ai-image-prompt>，兩者皆採 MIT 授權，授權全文見同目錄的 `LICENSE`。

本專案的改動：
- 詞條由簡體轉為繁體顯示，英文原樣保留；只挑出與角色設計有關的 30 個欄位（`tools/build_character_lexicon.py`）。
- 提示詞組裝規則（`compose.js`）改寫自該技能的「人物設定四視圖」「冷酷科幻人物」「韓式財閥千金」等章節。
