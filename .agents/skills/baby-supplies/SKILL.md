---
name: baby-supplies
description: 待產與嬰幼兒用品備品清單專屬管理技能。支援一鍵新增備品、自動將實品照片複製並上傳至 Google Drive 專屬資料夾、自動寫入 =HYPERLINK 超連結至試算表，以及關鍵字/分類/收納位置查詢。當主人說「新增...到嬰幼兒備品」、「查嬰兒備品」、「待產包有什麼」時使用本技能。
---

# 待產與嬰幼兒用品備品管理技能 (Baby Supplies Checklist Skill)

本技能專為《嬰兒用品與待產備品清單》Google 試算表打造，封裝了**實品照片自動上傳 Google Drive + 試算表超連結公式 (`=HYPERLINK`) 寫入**的完整管線，確保每次傳圖新增備品都不會遺漏照片連結。

- **腳本路徑**：`.agents/skills/baby-supplies/scripts/baby_supplies_tool.py`
- **環境變數配置（於 `.env` 設定）**：
  - `BABY_SUPPLIES_SHEET_ID=<YOUR_SPREADSHEET_ID>`
  - `BABY_SUPPLIES_DRIVE_FOLDER_ID=<YOUR_DRIVE_FOLDER_ID>`

---

## 核心操作指令 (CLI Usage)

### 1. 新增備品（含實品照片自動上傳雲端硬碟與超連結寫入）(`add`)
當主人傳送商品照片要求加入嬰幼兒備品時，務必傳入 `--photo <圖片路徑>` 與 `--photo-label <短標籤>`：

```bash
python3 .agents/skills/baby-supplies/scripts/baby_supplies_tool.py add \
  --category "哺育餵養" \
  --item "奶瓶奶嘴" \
  --spec "0m+ (SS號圓孔)｜Pigeon 貝親 母乳實感矽膠 (1入盒裝)" \
  --qty "1" \
  --note "已有 (0個月起新生兒適用)" \
  --location "ikea 層架籃子第三層" \
  --photo "/path/to/uploaded_image.jpg" \
  --photo-label "貝親母乳實感奶嘴SS號(0m+)"
```

> **內部防呆機制**：腳本會自動將工作目錄外的圖片（如 `.gemini` 上傳暫存區）暫存複製至專案內 `data/temp_images/` 後調用 `gws drive files create` 上傳至專屬雲端硬碟資料夾，並以 `USER_ENTERED` 模式寫入 `=HYPERLINK("https://drive.google.com/file/d/<ID>/view", "📸 #1 <標籤>")`，上傳完畢自動清除暫存檔。

### 2. 搜尋備品 (`search`)
```bash
python3 .agents/skills/baby-supplies/scripts/baby_supplies_tool.py search "貝親"
```

### 3. 依類別或收納位置列出備品 (`list`)
```bash
python3 .agents/skills/baby-supplies/scripts/baby_supplies_tool.py list --category "哺育餵養"
python3 .agents/skills/baby-supplies/scripts/baby_supplies_tool.py list --location "ikea"
```
