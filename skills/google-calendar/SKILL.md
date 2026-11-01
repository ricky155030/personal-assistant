---
name: google-calendar
description: 查詢、新增與管理主人的 Google Calendar 行事曆。主人交代記錄的所有項目（生活瑣事、繳費、會議、待辦任務等）一律固定寫入「待辦事項」日曆中。
---

# Google Calendar 行事曆管理技能

本技能專為個人助理設計，透過後台工具對接 Google Calendar。

> **核心準則**：
> 主人交代的所有日程、待辦、繳費、會議、活動或提醒，**一律固定寫入「待辦事項」行事曆**，統一集中管理，不再分散到其他日曆。

本技能腳本路徑：`.agents/skills/google-calendar/scripts/calendar_tool.py`

## 預設固定行事曆

* **行事曆名稱**：`待辦事項`（支援環境變數 `GOOGLE_CALENDAR_TODO_ID`，或預設動態比對/使用 `primary`）
* 主人所有提及的事件（不論是工作、開會、生活、繳款、看診）**一律存入本行事曆**。

---

## 常用操作指令

### 1. 查詢待辦排程 (List Events)
```bash
python3 .agents/skills/google-calendar/scripts/calendar_tool.py list 待辦事項 <today|week|upcoming|YYYY-MM-DD>
```
* 查詢今天所有待辦：`python3 .agents/skills/google-calendar/scripts/calendar_tool.py list 待辦事項 today`
* 查詢本週待辦排程：`python3 .agents/skills/google-calendar/scripts/calendar_tool.py list 待辦事項 week`
* 查詢近期所有待辦：`python3 .agents/skills/google-calendar/scripts/calendar_tool.py list 待辦事項 upcoming`

### 2. 新增排程（一律存入「待辦事項」）
```bash
python3 .agents/skills/google-calendar/scripts/calendar_tool.py add 待辦事項 <標題> <開始時間> [結束時間] [地點]
```
* **全天事件範例**：
  ```bash
  python3 .agents/skills/google-calendar/scripts/calendar_tool.py add 待辦事項 "健康檢查" "2026-11-15"
  ```
* **指定時間範例**（若未指定結束時間，腳本會自動設為 1 小時後）：
  ```bash
  python3 .agents/skills/google-calendar/scripts/calendar_tool.py add 待辦事項 "團隊週會" "2026-11-10 09:30"
  ```
* **繳費提醒範例**：
  ```bash
  python3 .agents/skills/google-calendar/scripts/calendar_tool.py add 待辦事項 "水電費繳費截止" "2026-11-20"
  ```
* **工作會議範例**：
  ```bash
  python3 .agents/skills/google-calendar/scripts/calendar_tool.py add 待辦事項 "專案進度會議" "2026-11-12 14:00" "2026-11-12 15:30" "線上會議"
  ```

### 3. 刪除排程 (Delete Event)
```bash
python3 .agents/skills/google-calendar/scripts/calendar_tool.py delete 待辦事項 <event_id>
```

---

## 口語轉換與防呆規則 (Parsing Rules)

1. **固定目標行事曆**：
   - 所有的項目**一律寫入「待辦事項」**，不需要去分流個人、Work 或繳費，統一集中收攏。
2. **地點拆解**：
   - 若主人說「*在某餐廳跟朋友吃飯*」，拆解為標題「*與朋友用餐*」、地點「*某餐廳*」。
3. **標題精煉**：
   - 自動轉為精簡名詞（例如「*去醫院洗牙*」➔ 標題「*牙醫門診*」）。
4. **預設提醒設定**：
   - 腳本已內建預設提醒（**活動前 1 天 + 前 1 小時** 雙重彈窗提醒）。

## 回覆規則：
- 執行完成後，向主人簡要回報（例如：「已為你在待辦事項行事曆中記錄 11 月 10 日早上九點半的『團隊週會』。」）。
- 保持 1~2 句話，不要輸出任何 Markdown 標記，確保手錶與 Telegram 朗讀無雜音。
