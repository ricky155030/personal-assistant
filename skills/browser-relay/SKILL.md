---
name: browser-relay
description: 直接操控 Mac mini 本機真實 Google Chrome 應用程式進行網頁自動化（Chrome-Assistant 引擎）。零外掛依賴、天然抗防爬蟲盾（PerimeterX / Cloudflare 自動自愈）、專屬永久保存 Profile（Cookies 與 Sessions 永不遺失）、與主人日常使用之 Chrome 完全互不干擾。內建高階通用 AI 介面：可互動元素語意快照 (inspect)、自然文字與標籤定位 (smart click/type)、多步驟批次動作管線 (batch)，適用所有訂位、表單、搶票與動態渲染頁面。
---

# Chrome 瀏覽器自動化技能 (Chrome-Assistant Engine)

本技能讓 Antigravity (`agy`) 能夠直接以 **Mac mini 本機真實 Google Chrome 應用程式** 進行網頁自動化操作，使用專屬持久化設定檔（`Chrome-Assistant` Profile）。

## 核心價值與通用設計

1. **零外掛依賴（Zero-Extension）**：
   - 彻底免除 Chrome Extension 擴充套件依賴，不再受 Service Worker 休眠、MV3 權限或擴充套件協定版本過期問題困擾。
2. **專屬持久化 Profile（Persistent Sessions & Cookies）**：
   - 設定檔永久儲存於：`~/Library/Application Support/Google/Chrome-Assistant`。
   - 所有登入狀態、Cookies、會員 Session 永久保留，下次重啟依然有效。
   - 與主人日常使用的預設 Chrome 完全隔離，**兩者可同時運行，互不搶畫面、互不衝突**。
3. **天然抗防爬蟲盾與自動自愈（Anti-Bot Auto-Bypass）**：
   - 使用真實 Chrome 二進位核心（`/Applications/Google Chrome.app`），完全具備真實 GPU 渲染、Canvas 與真人硬體指紋。
   - 啟動時自動抹除 `--disable-blink-features=AutomationControlled`，`navigator.webdriver = false`。
   - **底層自動偵測通用驗證盾**：遇到 PerimeterX「按住不放 (Press & Hold)」時，底層自動精準計算中心座標並執行 11 秒物理模擬長按；遇到 Cloudflare Turnstile 自動點擊核取，完全不需上層模型手動摸索。
4. **高階通用 AI 介面（AI-Friendly Primitives）**：
   - **`inspect`（元素語意快照）**：1 秒列出畫面所有按鈕、輸入欄位、選單與建議定位器，彻底告別盲寫 JavaScript 試探。
   - **`click` / `type`（智慧語意定位）**：支援人類可讀的文字（`--text`）、標籤（`--label`）、預設提示（`--placeholder`），免猜動態 CSS class。
   - **`batch`（批次動作管線）**：一次傳入連續操作清單（選日期 $\rightarrow$ 選時段 $\rightarrow$ 填資料 $\rightarrow$ 送出），2~3 秒內流水線跑完，減少 80% 的模型往返與等待。

---

## 執行環境與腳本路徑

- **控制腳本**：`.agents/skills/browser-relay/scripts/browser_relay.py`
- **虛擬環境 Python**：`.venv/bin/python`（已內建 `playwright`）
- **專屬 Profile 目錄**：`~/Library/Application Support/Google/Chrome-Assistant`
- **CDP 連線端口**：`http://127.0.0.1:9223`（由 Chrome 原生提供，不需外部轉發服務）

---

## 常用操作指令

### 1. 檢查引擎狀態 (`status`)
確認 Chrome-Assistant 運行狀態：
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py status
```

### 2. 開啟新分頁 (`open`)
在 Chrome-Assistant 中開啟分頁（底層自動監聽並自愈驗證盾）：
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py open "https://inline.app/booking/<店家代碼>"
```

### 3. 可互動元素快照 (`inspect`) —— 操作頁面前首選
快速掌握當前畫面有哪些按鈕、欄位與選項：
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py inspect
# 指定分頁或輸出完整 JSON
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py inspect --tab 0
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py inspect --json
```

### 4. 智慧點擊 (`click`) —— 支援純文字模糊匹配
直接用肉眼看到的字點擊，不需要去查 CSS selector：
```bash
# 以文字點擊
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py click --text "確定取消訂位"
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py click --text "19:00"

# 以標準 selector 點擊
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py click "[data-cy='submit']"
```

### 5. 智慧填寫文字 (`type`)
支援以 Label 或 Placeholder 自動定位輸入框：
```bash
# 以表單 Label 定位
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py type "王小明" --label "姓名"

# 以 Placeholder 定位
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py type "0912345678" --placeholder "手機號碼"
```

### 6. 下拉選單選擇 (`select`)
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py select "2" --selector "#adult-picker"
```

### 7. 連鎖動作批次執行管線 (`batch`) —— 速度最大化
將多個動作整合為單一指令執行，大幅減少模型思考輪詢：
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py batch '[
  {"action": "select", "selector": "#adult-picker", "value": "2"},
  {"action": "click", "text": "19:00"},
  {"action": "click", "text": "完成預訂"},
  {"action": "fill", "selector": "[data-cy=\"name\"]", "value": "王小明"},
  {"action": "click", "selector": "#gender-male"},
  {"action": "fill", "selector": "[data-cy=\"phone\"]", "value": "0912345678"},
  {"action": "fill", "selector": "[data-cy=\"email\"]", "value": "user@example.com"}
]'
```

### 8. 讀取頁面乾淨內文 (`read`)
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py read
```

### 9. 截圖畫面 (`screenshot`)
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py screenshot "status.png"
```

### 10. 分頁管理、殭屍分頁回收與關閉 (`tabs` / `gc` / `close` / `quit`)
```bash
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py tabs
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py gc --max-tabs 8
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py close 0
.venv/bin/python .agents/skills/browser-relay/scripts/browser_relay.py quit
```

---

## 🚦 通用標準操作節奏 (Recommended Workflow)

當需要操作任何動態網頁（訂位、搶票、表單填寫）時，**標準最高效步驟**如下：

1. **開啟頁面**：`browser_relay.py open "<網址>"`
2. **拍下快照**：`browser_relay.py inspect`（一秒掌握所有欄位與按鈕）
3. **批次執行或文字點擊**：
   - 簡單操作：直接 `click --text "..."` 或 `type "..." --label "..."`
   - 複雜表單：直接組裝成 `batch '[...]'` 2 秒內一次跑完！
4. **結束釋放**：確認結果後關閉該分頁 `close <tab>`。

---

## ⚠️ 執行鐵律與反模式禁止 (Strict Constraints & Anti-Patterns)

1. **嚴禁自行撰寫多輪 `.venv/bin/python -c` 逐一連線 CDP 試探！**
   - 每次自行撰寫 Python 試探會導致 15~20 輪往返、引發 600 秒超時。
   - 所有網頁互動**必須且只能**呼叫 `browser_relay.py inspect` 看清欄位後，使用 `browser_relay.py batch` 或高階 CLI 一次性完成。
2. **通用搜尋直達網址，禁止多輪反覆跳轉**：
   - 尋找訂位或服務網址時，直接以 `<店家名> <分店/地區> 線上訂位` 或 `<店家名> 訂位` 檢索。從搜尋結果摘要或網址中直接挑選官方訂位連結（無論是 Inline、OpenTable、Ocard、EZTABLE 或品牌自營訂位系統），1 步取得目標 URL 即刻開啟，禁止在多個無關頁面反覆跳轉。
   - 若搜尋結果顯示該店家僅開放電話預約或無線上系統，應直接告知主人門店電話，避免盲目點擊無效網頁。
