---
name: current-location
description: 取得主人的即時 GPS 定位、所在國家、城市、區域、地標與地址。當主人詢問「我現在在哪裡」、「目前所在位置」、「我附近有什麼」、「附近景點/餐廳/店家」、「我現在位置去...要多久」等需要即時物理位置的情境時使用本技能。
---

# 主人即時定位技能 (Current Location Skill)

本技能提供**零猜測、秒級取得主人目前精準物理位置**的能力。
資料來源直連 Home Assistant 的 person 實體（由隨身配戴的 Apple Watch 與 iPhone 即時向 HA 回報經緯度），並自動進行逆向地理編碼解析出國家、城市、行政區、商圈地標與地址。

腳本路徑：`.agents/skills/current-location/scripts/get_location.py`

---

## 執行指令格式

### 1. 取得結構化 JSON（推薦大腦調用）：
```bash
python3 .agents/skills/current-location/scripts/get_location.py --json
```

### 2. 取得純文字摘要：
```bash
python3 .agents/skills/current-location/scripts/get_location.py
```

---

## 大腦調用核心規範 (Agent Behavior Rules)

1. **嚴禁盲猜或翻閱記憶庫找地址**：
   - 當主人問「我目前位置有什麼好逛的？」、「我附近有什麼賣電器的嗎？」、「我現在去太宰府要多久？」：
   - **第一步一律先執行本技能取得即時位置**，切勿翻閱 `memory/profile.md` 或猜測主人在台北住家。
2. **快速銜接搜尋工具**：
   - 執行本技能獲得城市、行政區與地標（例如：`福岡市中央區天神`）後，**直接以該地名作為關鍵字進行 Google Search 或週邊探索**（例如：`福岡 天神 電器行 推薦`、`天神 逛街 景點`）。
3. **回覆時明確標註基準點**：
   - 回覆開頭可先告知主人目前基準定位，讓主人清楚距離計算的依據（例如：「根據您目前在天神三丁目附近的定位，周邊步行可達的電器行推薦如下：...」）。
