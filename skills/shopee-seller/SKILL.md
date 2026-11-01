---
name: shopee-seller
description: 蝦皮賣家自動上架技能。支援主人單次對話傳送商品圖片（可單品多圖）及「數量」與「金額」，由 LLM 視覺辨識商品、自動配對台灣蝦皮官方分類 ID、撰寫 SEO 標題（10~60字）與結構化描述，自動壓縮圖片上傳直連圖床、生成獨立批次 Excel、透過 Chrome-Assistant CDP 零 Token 自動上傳至賣家中心，並在當前對話立即回傳商品後台專屬連結與公開網址；亦支援多商品分次暫存累積（stage-add / stage-export）。
---

# 蝦皮賣家批次自動上架技能 (Shopee Seller Upload Skill)

本技能專門處理主人的**蝦皮商品上架需求**。
**預設流程為「Fast Handoff 非同步秒級上架」**：主人傳照片 + 數量 + 金額，助理在當前對話 turn 內完成視覺辨識、分類匹配、文案生成，並透過 `async-upload` 於 1 秒內委派 TaskManager 背景 Worker 執行瀏覽器上架管線，秒級回覆主人確認；背景完成後主動透過 Telegram 推播前台商品連結！

- **核心腳本**：`.agents/skills/shopee-seller/scripts/shopee_tool.py`
- **背景工作腳本**：`.agents/skills/shopee-seller/scripts/shopee_worker.py`
- **官方母本**：`.agents/skills/shopee-seller/resources/Shopee_Template.xlsx`（賣場 ID 可透過 `.env` 的 `SHOPEE_SHOP_ID` 自訂）
- **官方分類庫**：`.agents/skills/shopee-seller/resources/categories.json`（內建 2,088 筆台灣蝦皮官方分類）
- **任務快取目錄**：`data/shopee_tasks/`
- **任務日誌目錄**：`data/task_logs/`
- **暫存隊列檔**：`data/shopee_staging_queue.json`
- **預設輸出目錄**：`~/Desktop/Shopee_Batch_<YYYYMMDD_HHMMSS>.xlsx`

---

## 核心黃金守則 (Core Rules)

1. **預設 Fast Handoff 非同步脫鉤（秒級交差釋放，嚴禁阻塞對話通道）**：
   - 當主人傳送商品照片、金額與數量時，**無需等待第二次確認，直接在該 turn 一步到位執行 `async-upload`**！
   - 腳本在 1 秒內返回 `task_id`，主大腦**立即結束當前 turn 並向主人回覆確認**，絕不阻塞等待 CDP 操作。
   - 背景 Worker 完成發布後，會**自動透過 Telegram 推播前台商品公開網址與後台管理連結**給主人。
2. **預設物流通道配置（硬性鎖定）**：
   - **開啟 (`開啟`)**：`7-ELEVEN` (`AG`)、`全家` (`AH`)、`萊爾富` (`AI`)、`蝦皮店到店` (`AK`)、`蝦皮店到店 - 隔日到貨` (`AL`)。
   - **關閉 (`關閉`)**：`黑貓宅急便` (`AF`)、`宅配通` (`AJ`)。
   - `shopee_tool.py` 已將上述設定寫為硬性預設值，無須額外手動指定。
3. **圖片黃金排版：官方白底高清首圖 + 主人實拍物況佐證（極重要）**：
   - 賣場吸引買家點擊的核心在於「乾淨、白底、高畫質的官方宣傳照」；而買家決定下單的安心關鍵在於「實物保存狀況與吊牌細節」。
   - 因此，**當主人傳送隨手拍攝的照片時，本技能流程一律自動執行「首圖官方升級」**：
     1. 由 LLM 視覺精準辨識商品的官方全名（品牌、系列、角色、規格型號）。
     2. 自動至網路檢索抓取 1~2 張該物品之「官方正版宣傳照 / 白底目錄照 / 高解析正方形商品照」，下載至 `data/downloaded_images/`。
     3. **自動重構圖片佇列 (Slot 排序)**：
        - **Slot 1（蝦皮首圖 / 封面）**：官方高畫質白底宣傳照（負責搜尋版面吸睛與提升點閱率）。
        - **Slot 2（選配）**：官方陣容合照或包裝盒照（若有找到）。
        - **Slot 3..N（實況照）**：主人上傳的真實手機拍攝照片（展示真實保存狀況、未拆封袋、蛋紙/吊牌、細節特寫，提供買家最高信任度）。
4. **支援單一商品多張圖片平行轉為直連 URL**：
   - 在 `images` 陣列傳入包含官方首圖與主人實拍照的路徑清單，背景 Worker 會自動進行多線程平行轉檔並上傳至直連圖床，取得直連網址填入 Excel。
5. **未知必填欄位 agy 自主演進與自愈機制 (Self-Healing & Auto-Updating)**：
   - 進入草稿編輯頁時，腳本自動掃描所有必填與未選欄位（下拉、單選 radio、文字輸入皆涵蓋），dirty-flag 多輪直到穩定（上限 5 輪，處理選 A 才長出 B 的連動欄位）。
   - 優先查閱 `resources/attribute_rules.json` 本地規則庫（**分類命名空間**：worker 會把 `title→category_id` 對照傳進上架流程，規則按分類命中；如：「限制級 ➔ 否」、「禁運品 ➔ 否」、「是否有BSMI字號 ➔ 否」）。
   - 若遭遇全新未知必填欄位，背景自動調用本機 `agy -p`（`gemini-3.8-flash-low`）即時推理決策：選項編號化回傳索引，後備值排除「新增選項」類彈窗值、偏好「其他/否/通用」；點選後**驗證真的選上才固化**寫入 `attribute_rules.json`。
   - 發布被擋時自動收集**欄位級證據**：每個欄位旁紅字錯誤 + 未填欄位清單 + 失敗截圖（`data/task_logs/<excel>_<item_id>.png`），寫入 task JSON 並透過 Telegram 明確告知主人卡在哪幾個欄位。
6. **狀態即時可查 (`task-status`)**：
   - 若主人詢問「上架好了嗎？」或查詢進度，直接呼叫 `task-status <task_id>` 秒級讀取本地快取（< 0.1s），無須重複啟動瀏覽器。
7. **可選的分批暫存模式（僅當主人明確要求時）**：
   - 若主人明確表示「先幫我暫存，晚點一起上傳」或「我要累積多件商品」，才切換為 `stage-add` ➔ `stage-export --upload` 模式。

---

## 維運備忘（穩定事實，不易腐爛；函式級細節以 code comment 為準）

- **執行環境**：一律使用專案 `.venv/bin/python` 執行 `shopee_tool.py` / `shopee_worker.py`（系統 `python3` 缺 `pydantic` 等依賴會直接炸）。
- **分類 ID 貫穿鏈**：worker 以 `expected_meta`（`[{title, category_id}]`）傳入 uploader，草稿頁自愈依此回查分類；`attribute_rules.json` 結構為 `{分類ID: {欄位名: 選項}}` + `_global` 通用，未命中才走 LLM。
- **失敗證據三件套**：欄位級紅字（`field_errors`）+ 未填清單（`unresolved_fields`）寫進 `data/shopee_tasks/<task_id>.json`，截圖存 `data/task_logs/<excel_stem>_<item_id>.png`；主人問進度一律 `task-status <task_id>` 秒查，不要重開瀏覽器。
- **文字輸入型必填不臆測**：無規則時留待人工，寧可進草稿具名通知，不亂填污染。
---

## 標準執行工作流 (Standard Workflow)

### 預設模式：非同步秒級上架 (`async-upload`)

當收到主人傳送的商品照片 + 數量 + 金額時：

1. **視覺檢視商品圖片 (`view_file`)**：
   - 確認商品外觀、品牌、型號、規格與新舊保存狀況。
   - 萃取最精準的搜尋關鍵字（品牌 + 系列 + 角色/型號）。
   - 若缺少金額或數量，立即簡潔反問主人確認（遵守反問確認優先原則）。
2. **自動檢索官方/清晰宣傳照作為首圖**：
   - 運用商品關鍵字聯網搜尋該商品官方發售頁、白底產品圖或高清晰商品照。
   - 下載最佳正方形/白底圖片至 `data/downloaded_images/`。
   - **重構圖片陣列**：以該官方照為第一張首圖，主人的手機實拍照依序排在其後：
     `images = ["data/downloaded_images/official_cover.jpg", "/path/to/user_photo1.jpg", ...]`
3. **查詢官方分類 ID (`search-category`) 與避坑指南**：
   - **避坑原則（極重要）**：
     - 所有動漫與 IP 角色（如寶可夢、迪士尼、哆啦A夢、吉伊卡哇、航海王等）之玩偶、娃娃、公仔、周邊，**一律優先選擇 `101392`（愛好與收藏品 > 收藏品 > 動漫周邊）或 `101408`（扭蛋盒玩）**，切勿選入「母嬰用品/玩具」，徹底避開 BSMI 玩具檢驗字號阻擋！
     - 遊戲片/卡匣：走 `100742`（電玩遊戲 > 主機遊戲 > Switch遊戲）。
   - 查詢指令範例：
     ```bash
     .venv/bin/python .agents/skills/shopee-seller/scripts/shopee_tool.py search-category "動漫" "周邊" --limit 5
     ```
4. **一步到位非同步啟動上架 (`async-upload`)**：
   - **`condition`（商品新舊，語意判定，沒說二手就是全新）**：主對話 LLM 看主人 prompt + 照片當下判定，只傳 `全新` 或 `二手`：
     - 主人說了 `二手/出清/9成/9.5成/近新/用過/僅拆封/展示品/中古/個人收藏` 任一，或照片明顯有使用痕跡 → `二手`。
     - 沒提以上字眼、且照片像全新未拆 → `全新`（預設）。
     - **標題前綴必須與 condition 一致**：`全新` → `【全新現貨】`；`二手` → `【二手近新】/【二手出清】`，絕不許二手品掛全新前綴。
     - 草稿頁 `商品狀況` radio 由 worker 依此值強制點選，不經小模型臆測、不寫入 `attribute_rules.json`（同分類新舊混賣會污染）。
   - **`title`（商品名稱）**：嚴格控制在 **15 ~ 60 字**（蝦皮規定 10~60 字），格式：`【狀態/亮點】品牌 + 型號 + 商品核心名稱 + 關鍵規格/特色 + 熱搜同義詞`。
   - **`description`（商品描述）**：結構化分段（【商品特色與物況說明】、【規格資訊】、【出貨與購買須知】）。二手品必須在首段寫明成色（如 9.5成新、使用痕跡位置）。
   - 執行指令（1 秒內立即返回）：
   ```bash
   .venv/bin/python .agents/skills/shopee-seller/scripts/shopee_tool.py async-upload --payload '{
    "category_id": "100578",
    "condition": "二手",
    "title": "【二手近新】Sony WF-1000XM5 真無線降噪藍牙耳機 黑色 盒裝配件齊全",
    "description": "【商品特色與物況說明】\n・品牌型號：Sony WF-1000XM5 真無線主動式降噪耳機（黑色）\n・商品狀況：二手近新，功能正常、蓄電良好，外觀保存極佳，附完整原廠充電盒與耳塞配件。\n\n【出貨與購買須知】\n・支援 7-ELEVEN、全家、萊爾富、蝦皮店到店（含隔日到貨）。\n・實品拍攝，歡迎聊聊詢問細節！",
    "price": 4500,
    "stock": 1,
    "weight": "0.3",
    "images": [
      "/path/to/official_cover.jpg",
      "/path/to/user_real_photo1.jpg",
      "/path/to/user_real_photo2.jpg"
    ]
  }'
   ```
5. **當前 turn 立即回覆主人確認訊息（秒級釋放）**：
   - 明確向主人列出圖片配置（標註哪張是官方首圖、哪些是實拍物況），範例格式：
   ```markdown
   👌 **已為您啟動蝦皮自動上架作業（背景任務：`task_20261018_xxxxxx`）！**

  - 📦 **商品名稱**：【二手近新】Sony WF-1000XM5 真無線降噪藍牙耳機 黑色 盒裝配件齊全
   - 🏷️ **分類**：3C與筆電 > 耳機、音響與擴大機 > 入耳式耳機
   - 💰 **售價**：NT$ 4,500
   - 🔢 **數量**：1
   - 📸 **圖片配置**：
     1. 首圖：官方高畫質白底宣傳照（已自動檢索取得）
     2. 次圖起：您拍攝的商品實拍與配件特寫
   - 🚚 **物流**：7-11 / 全家 / 萊爾富 / 蝦皮店到店（含隔日到貨）
   - ⏳ **預估耗時**：約 15 ~ 20 秒

   系統正在背景自動處理圖片轉檔、Excel 批次組裝、賣家中心發布與非限制級核取。
   完成後將自動透過 Telegram 推播前台商品連結給您，您無需在線上等待！
   ```

---

## 備用模式：多商品分批暫存累積（主人明確要求時）

```bash
# 1. 加入暫存區
.venv/bin/python .agents/skills/shopee-seller/scripts/shopee_tool.py stage-add '{"category_id": "...", "title": "...", "price": 100, "stock": 1, "images": [...] }'

# 2. 查看暫存
.venv/bin/python .agents/skills/shopee-seller/scripts/shopee_tool.py stage-list

# 3. 一鍵打包並上傳
.venv/bin/python .agents/skills/shopee-seller/scripts/shopee_tool.py stage-export --upload
```

---

## 進階支援：單一商品多規格（如多顏色/多尺寸）

在商品物件中帶入 `variations` 陣列：
```json
{
  "category_id": "101022",
  "title": "【全新現貨】日本純棉寶寶包屁衣 透氣短袖連身衣 多色可選",
  "description": "純棉親膚材質，全新現貨。",
  "weight": "0.2",
  "images": ["/path/to/main_cover.jpg"],
  "variations": [
    {"name1": "顏色", "option1": "奶油白", "price": 250, "stock": 2, "image": "/path/to/white.jpg"},
    {"name1": "顏色", "option1": "寶寶藍", "price": 250, "stock": 1, "image": "/path/to/blue.jpg"}
  ]
}
```
