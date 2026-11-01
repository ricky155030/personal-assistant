---
name: google-sheets
description: 查詢與管理 Google Sheets 雲端試算表。支援讀取指定範圍、追加資料行、批次寫入、搜尋儲存格。嚴禁直接執行裸 gws 指令，一律透過本腳本進行安全防護操作。
---

# Google Sheets 雲端試算表管理技能

本技能透過安全包裝的 `sheets_tool.py` 查詢與管理 Google Sheets 試算表，避免直接執行 gws 可能造成的憑證刪除問題。

- **腳本路徑**：`.agents/skills/google-sheets/scripts/sheets_tool.py`

---

## 核心操作指令 (CLI Usage)

### 1. 讀取試算表內容 (`read`)
```bash
# 讀取指定試算表工作表範圍
python3 .agents/skills/google-sheets/scripts/sheets_tool.py read <SPREADSHEET_ID_OR_ALIAS> "Sheet1!A1:E20"
```

### 2. 追加一列或多列資料 (`append`)
```bash
python3 .agents/skills/google-sheets/scripts/sheets_tool.py append <SPREADSHEET_ID_OR_ALIAS> "Sheet1!A:E" '[["2026-10-31", "日用品", "洗衣精", 2, "特價採買"]]'
```

### 3. 搜尋試算表 (`search`)
```bash
python3 .agents/skills/google-sheets/scripts/sheets_tool.py search <SPREADSHEET_ID_OR_ALIAS> "關鍵字"
```

### 4. 取得試算表中繼資料與分頁清單 (`metadata`)
```bash
python3 .agents/skills/google-sheets/scripts/sheets_tool.py metadata <SPREADSHEET_ID_OR_ALIAS>
```

---

## 設定別名 (Spreadsheet Aliases)
您可在 `.env` 中指定通用試算表 ID：
```env
SHOPPING_SHEET_ID=<您的待買清單試算表ID>
SHEET_ALIASES_JSON={"work": "<工作試算表ID>", "finance": "<財務試算表ID>"}
```
設定後，即可直接以別名代替長字串 ID 進行操作。
