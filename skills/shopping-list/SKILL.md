---
name: shopping-list
description: 查詢、新增與管理通用待買清單（包含日常生活用品採買、旅遊代購等）。支援自動分類、地點情境過濾與批次勾銷。當主人說「幫我記要買...」、「加到待買清單」、「有什麼要買的嗎」、「...買好了」時使用本技能。
---

# 家庭通用待買清單技能 (Universal Shopping List Skill)

本技能提供**家庭全域採買行動中心**的管理能力，儲存於 Google 試算表，支援極速批次查詢、新增與勾銷。

- **腳本路徑**：`.agents/skills/shopping-list/scripts/shopping_tool.py`
- **試算表設定**：於 `.env` 中設定 `SHOPPING_SHEET_ID=<您的試算表ID>`

---

## 常用操作指令

### 1. 查詢待買項目 (`list`)
```bash
python3 .agents/skills/shopping-list/scripts/shopping_tool.py list
```

### 2. 批次新增多個待買項目 (`batch-add`)
```bash
python3 .agents/skills/shopping-list/scripts/shopping_tool.py batch-add '[
  {"item": "鮮奶", "category": "食品", "location": "全聯", "notes": "全脂"},
  {"item": "洗衣精", "category": "日用品", "location": "好市多", "notes": "補充包"}
]'
```

### 3. 採購完成勾銷 (`done` / `batch-done`)
```bash
python3 .agents/skills/shopping-list/scripts/shopping_tool.py done "鮮奶"
python3 .agents/skills/shopping-list/scripts/shopping_tool.py batch-done '["鮮奶", "洗衣精"]'
```
