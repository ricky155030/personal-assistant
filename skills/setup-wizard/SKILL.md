---
name: setup-wizard
description: 個人 AI 助理初始化、隱私金鑰設定、技能模組前置相依檢驗 (Doctor) 與配置管理技能。當主人要求「初始化個人助理」、「設定個人資訊」、「設定帳號密碼」、「修改 Telegram token」、「更新通訊白名單」、「開啟/啟用技能」、「關閉/停用技能」、「技能健檢/Doctor」、「日曆ID設定」、「/setup」時觸發本技能。引導配置主人稱呼、聯絡資訊、Telegram Token、Tailscale 網域、安全性 API Key、技能專屬參數與相依檢驗，並自動同步全域模組化記憶庫 (memory/ 與 MEMORY.md)。
---

# 個人助理設定嚮導與技能調度引擎 (Setup Wizard & Skills Engine)

本技能提供全互動式或口語化的配置流程，協助主人或新使用者一鍵設定 Personal Assistant 運作所需的所有個人檔案、通訊憑證、安全性金鑰，並透過**自包含清單 (Manifest-Driven)** 架構，自由啟用、停用與自我診斷 (Doctor) 各項專屬擴充技能。

## 🎯 支援配置範疇

1. **個人檔案與身分定位 (Profile & Identity)**：
   - 主人稱呼與全名（自動替換 `memory/profile.md` 與 `AGENTS.md` 中的預設佔位符）
   - 電子郵件、所在城市與隨身穿戴設備清單
2. **通訊與連線憑證 (Credentials & Keys)**：
   - 捷徑認證金鑰 (`ASSISTANT_API_KEY`)：支援自動生成 32 字元隨機高強度金鑰
   - Tailscale 網域名稱 (`TAILSCALE_DOMAIN`)：自動偵測本機 MagicDNS
   - Telegram 機器人憑證 (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)：支援連線測試
3. **自包含清單驅動之技能模組選配 (Manifest-Driven Skills)**：
   - 每個技能目錄 (`skills/<name>/`) 獨立維護 `manifest.json`，宣告自身的：
     - `prerequisites`: 作業系統限制、二進位工具 (`binaries`)、外部帳號授權 (`auth`)、瀏覽器 Session (`browser_session`)
     - `config`: 專屬環境變數清單（名稱、中文說明、預設值、是否必填）
   - 核心必備（常駐）：`setup-wizard`, `browser-relay`
   - 生產力選配（預設）：`google-calendar`, `google-sheets`, `gmail`, `shopping-list`
   - 進階個人選配：`imessage`, `coupang-tracker`, `fb-group-monitor`, `shopee-seller`
4. **技能診斷與自癒 (Skill Doctor)**：
   - 啟用前自動檢查 OS、CLI 工具與授權，回報缺失並導引安裝或自動修復。

---

## 🛠️ 調用方式與工作流程 (SOP)

### 模式一：對話式即時設定 (Conversational / LLM-Driven Mode) —— 優先推薦
當主人在 Antigravity CLI 或 Telegram 對話中口語交代時，助理直接在後台執行對應指令完成設定並秒級回報：

#### 1. 個人檔案與金鑰配置
* 主人口語：「把我的稱呼改成 Alex」、「設定我的 Telegram Token 為 xxx」、「我的電話是 +886912345678」
* **助理執行**：
  ```bash
  python3 scripts/setup_wizard.py --non-interactive \
    --owner "<主人稱呼>" \
    --full-name "<主人全名>" \
    --tg-token "<TOKEN>" \
    --tg-chat-id "<CHAT_ID>" \
    --tailscale "<DOMAIN>" \
    --owner-phone "<手機號碼>"
  ```

#### 2. 技能清單檢視 (List Skills)
* 主人口語：「目前開啟了哪些技能？」、「有哪些技能可以用？」
* **助理執行**：
  ```bash
  python3 setup.py skills list --json
  ```
  *取得 JSON 後向主人條理呈現「已啟用」與「可選擴充」清單。*

#### 3. 技能前置條件健檢 (Doctor)
* 主人口語：「幫我檢查技能狀態」、「行事曆技能就緒了嗎？」、「為什麼 iMessage 不能用？」
* **助理執行**：
  ```bash
  # 檢驗特定技能
  python3 setup.py skills doctor google-calendar --json

  # 檢驗所有技能
  python3 setup.py skills doctor --json
  ```
  *若回報 `ready: false`，助理可精準指出缺少的 CLI 工具或未授權命令，並引導主人完成。*

#### 4. 啟用或停用技能 (Enable / Disable Skills)
* 主人口語：「幫我啟用 iMessage 技能」、「開啟酷澎價格追蹤」
* **助理執行**：
  ```bash
  python3 setup.py skills enable imessage
  # 或批量啟用
  python3 setup.py skills enable imessage shopee-seller
  ```
* 主人口語：「幫我關閉酷澎追蹤」、「停用 iMessage」
* **助理執行**：
  ```bash
  python3 setup.py skills disable coupang-tracker
  ```

#### 5. 技能專屬參數設定 (Config)
* 主人口語：「把待辦行事曆 ID 設為 primary」、「把日曆 ID 設為 xxx@group.calendar.google.com」
* **助理執行**：
  ```bash
  python3 setup.py skills config google-calendar --set GOOGLE_CALENDAR_TODO_ID="primary" --json
  ```
* 主人口語：「設定待買清單的試算表 ID 為 1AbC...」
* **助理執行**：
  ```bash
  python3 setup.py skills config shopping-list --set SHOPPING_SHEET_ID="1AbC..." --json
  ```

---

### 模式二：終端機全互動式步驟精靈 (Terminal Interactive Mode)
當使用者在命令列終端機想要以步驟式引導選單逐步填寫與勾選：

```bash
python3 setup.py
# 或使用 start.sh
./start.sh setup
```
包含四個完整步驟：
1. **【步驟 1/4】** 設定主人個人資訊與身分識別
2. **【步驟 2/4】** 設定通訊管道與安全連線密鑰（含 Telegram 自動連線驗證）
3. **【步驟 3/4】** 選擇啟用之專屬技能模組（支援輸入序號自由勾選/切換、全選或只保留核心）
4. **【步驟 4/4】** 自動讀取選中技能的 `manifest.json`，執行前置相依健檢，並逐一詢問各技能專屬配置（如行事曆 ID、試算表 ID、電話白名單等）

---

## 🔒 安全與透明度原則
1. **本地檔案權限保護**：寫入的 `.env` 檔案權限嚴格設為 `600`（僅本機使用者可讀）。
2. **機敏資訊不進 Git**：Telegram Token、API Key 等均只存在於本機 `.env`，嚴禁寫入任何 Git 追蹤檔案。
3. **記憶同步透明化**：任何身分資訊變更均即時記錄於 `memory/profile.md` 並聚合編譯至 `MEMORY.md`。
