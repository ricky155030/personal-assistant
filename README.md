# 🏛️ Mac mini Personal Assistant (個人 AI 私有化助理)

專為 **Mac mini 本機私有化常駐** 與 **穿戴裝置（Apple Watch、iPhone、Telegram）** 設計的高效能個人 AI 助理。

Mac mini 擔任「大腦與記憶中樞」，由常駐型 **Antigravity CLI (`agy`)** 結合 **FastAPI** 驅動，維護本地模組化的永久記憶庫，並透過 Tailscale 加密網道提供全天候秒級安全連線。

---

## 🌟 核心架構亮點

1. **⚡ 常駐大腦單例 (agy Singleton Worker)**：
   - 預熱常駐 `stream-json` 行程，消弭每次 CLI 啟動的冷啟動開銷，對話延遲由 2.4s 降至 **<0.6s**。
   - 內建 **Context 上下文防線**：對話超過閾值（30%）或閒置超時自動重啟，確保記憶體永不膨脹。
2. **🧠 秘書級模組化長效記憶庫 (`memory/`)**：
   - 解耦為 `profile.md`（身分與溝通分流）、`preferences.md`（生活與系統偏好）、`projects.md`（專案目標）與 `notes.md`（重要備忘）。
   - **Google Calendar 待辦唯一真實來源**：所有日常日程與待辦一律寫入日曆，杜絕過期文字堆積。
   - 每日夜間自動排程執行 LLM 審查與 Git 自動提交歸檔。
3. **📱 三大溝通管道與模式分流**：
   - **終端機開發模式 (CLI)**：不受長度限制，完整 Markdown 結構、代碼區塊與架構討論。
   - **Apple Watch 穿戴模式**：按下 Action Button 語音隨口交代，手錶彈出 200 字內簡短卡片，兩端 **1:1 鏡像同步**。
   - **Telegram 雙向通訊**：隨時收發文字、照片辨識、語音輸入，並即時產出報表連結。
4. **⚙️ 背景任務守護神 (`task_manager.py`) 與 Fast Handoff**：
   - 耗時任務（價格監控、社團雷達等）秒級登記脫鉤，主大腦立即釋放，背景 Worker 獨立輪詢並在命中時主動推播。
5. **🛡️ Browser Relay 真實 Chrome 自動化**：
   - 直接操控 Mac mini 本機真實 Chrome（Chrome-Assistant Profile），零外掛依賴，天然免疫 Cloudflare / Akamai 等驗證盾。

---

## 🏛️ 系統架構圖

```mermaid
flowchart LR
    subgraph Clients [終端裝置]
        AW[Apple Watch 動作按鈕]
        TG[Telegram Bot]
        CLI[Terminal CLI]
    end

    subgraph Network [Tailscale 加密網道]
        TS[Tailscale DNS :8899]
    end

    subgraph Core [Mac mini 常駐服務]
        API[FastAPI 後端]
        Fast[fast_intent 極速通道]
        Agy[agy_singleton 常駐代理]
        Mem[memory/ 模組化記憶庫]
        TM[TaskManager 背景進程]
        Reports[/reports 靜態服務]
    end

    subgraph Skills [專屬技能庫]
        BR[browser-relay]
        CP[coupang-tracker]
        FB[fb-group-monitor]
        GCal[google-calendar]
        GSheet[google-sheets]
        GMail[gmail]
        Shop[shopping-list]
        iMsg[imessage]
    end

    AW --> TS --> API
    TG <--> API
    CLI --> Agy
    API --> Fast
    API --> Agy
    Agy <--> Mem
    Agy --> TM
    Agy --> Skills
    TM --> Skills
    Reports --> TS
```

---

## 📦 模組化技能庫與載入架構 (`skills/` ➔ `.agents/skills/`)

本系統採**模組化軟連結（Symlinks）架構**，所有開發完成的技能存放於 `skills/` 總庫，僅在使用者選中時透過相對符號連結動態掛載至 `.agents/skills/`，有效節省 Prompt Token 並避免 AI 意圖誤判。

| 技能模組 | 類別 | 預設狀態 | 核心功能 |
| :--- | :--- | :--- | :--- |
| **`setup-wizard`** | 核心 | 🔒 **必備** | 互動式與 LLM 對話式設定嚮導，管理身分、金鑰與技能開關 |
| **`browser-relay`** | 核心 | 🔒 **必備** | 操控本機真實 Chrome 進行 DOM 辨識、填表、點擊與批次動作（抗驗證盾） |
| **`google-calendar`** | 生產力 | 🟢 **啟用** | Google 行事曆日程查詢、新增與「待辦事項」日曆動態維護 |
| **`google-sheets`** | 生產力 | 🟢 **啟用** | Google Sheets 試算表查詢、寫入、追加與資料庫管理 |
| **`gmail`** | 生產力 | 🟢 **啟用** | 透過 Google Workspace CLI 安全搜尋與讀取郵件摘要 |
| **`shopping-list`** | 生產力 | 🟢 **啟用** | 家庭通用待買清單，支援地點分類、批次新增與採買雙向勾銷 |
| **`imessage`** | 通訊 | ⚪ *可選* | 透過 macOS 原生 AppleScript 安全發送 iMessage（白名單防誤發） |
| **`coupang-tracker`** | 電商 | ⚪ *可選* | 酷澎雙軌價格監控守護神（購物車直連 + 搜尋比價），破盤自動通報 |
| **`fb-group-monitor`** | 社群 | ⚪ *可選* | 監控 FB 社團最新貼文，以 LLM 語意條件自動篩選並即時推播 |
| **`shopee-seller`** | 電商 | ⚪ *可選* | 拍照自動視覺辨識商品、產生文案、圖床壓縮、CDP 批次自動上架 |

### 🛠️ 技能管理指令 (CLI & LLM 均支援)

```bash
# 列出所有技能與當前啟用狀態
python3 setup.py skills list

# 技能前置相依性與外部授權健檢 (Doctor)
python3 setup.py skills doctor
python3 setup.py skills doctor google-calendar

# 檢視或更新特定技能設定參數
python3 setup.py skills config google-calendar --set GOOGLE_CALENDAR_TODO_ID="primary"

# 啟用/停用指定技能
python3 setup.py skills enable imessage shopee-seller
python3 setup.py skills disable coupang-tracker

# 批量設定啟用清單（其餘選配自動停用）
python3 setup.py skills set google-calendar,google-sheets,imessage
```

> 💡 **LLM 對話隨時開關與健檢**：您可直接在對話中說「**幫我啟用 iMessage 技能**」、「**把待辦日曆 ID 設為 primary**」或「**幫我檢查行事曆技能狀態 (doctor)**」，助理會自動執行相應指令並即時回報！

---

## 🚀 快速上手指南

### 1. 系統需求
- **硬體**：Mac mini (Apple Silicon M 系列推薦) 或 macOS 設備
- **軟體**：
  - macOS 14+ (Sonoma / Sequoia)
  - Python 3.11+（推薦使用 [uv](https://github.com/astral-sh/uv) 管理）
  - [Google Antigravity CLI (`agy`)](https://github.com)
  - [Tailscale](https://tailscale.com/)（用於裝置間安全內網連線）

### 2. 安裝與設定虛擬環境
```bash
# 複製專案
git clone <your-repo-url> personal-assistant
cd personal-assistant

# 使用 uv 建立虛擬環境並安裝相依套件
uv venv
source .venv/bin/activate
uv pip install -e .
```

### 3. 一鍵初始化設定嚮導 (推薦)
我們提供了全自動的互動式設定嚮導，自動引導填寫主人身分、Telegram Token、自動偵測 Tailscale 網域並自動生成安全金鑰：

```bash
# 啟動互動式設定嚮導
python3 setup.py
# 或使用 ./start.sh setup
```

> 💡 **對話式設定**：服務啟動後，您亦可隨時在 Antigravity CLI 或 Telegram 對話中說「**幫我設定個人資訊**」或「**初始化設定**」，直接呼叫內建的 `setup-wizard` 技能以對話方式完成配置！

### 4. 啟動服務
```bash
# 開發前台啟動（若尚未設定，將自動提示啟動嚮導）
./start.sh

# 或以背景守護模式啟動
nohup ./start.sh > data/assistant.log 2>&1 &
```

啟動後，API 預設監聽於 `http://0.0.0.0:8899`。

---

## ⌚ Apple Watch 與穿戴裝置設定
請參閱 [`SHORTCUT_SETUP.md`](SHORTCUT_SETUP.md) 依序完成：
1. iPhone / Mac 捷徑設定（取得 URL 內容 ➜ 彈出卡片回覆）。
2. 綁定 Apple Watch Ultra 側邊「動作按鈕 (Action Button)」。
3. 享受抬手一鍵隨口交代助理的極速體驗！

---

## 🛠️ 常見維護指令
```bash
# 查看背景任務列表 (TaskManager)
python3 task_manager.py list

# 停止背景任務
python3 task_manager.py kill "<任務名稱>"

# 手動同步與審查今日記憶
python3 memory_scheduler.py --once
```

---

## 📄 License
MIT License
