---
name: personal-briefing
description: 主人專屬的個人簡報技能。當主人要求「今日簡報」、「進度彙整」、「我今天要做什麼」時觸發，查詢 Google Calendar 待辦事項與 memory/ 模組專案進度。
---

# 個人晨間簡報與進度彙整技能 (Personal Briefing Skill)

當主人詢問今天的工作、日程、需求進度或要求今日簡報時：
1. **查詢今日待辦排程**：
   執行 `python3 .agents/skills/google-calendar/scripts/calendar_tool.py list 待辦事項 today` 與 `python3 .agents/skills/google-calendar/scripts/calendar_tool.py list primary today` 獲取真實即時行程。
2. **參考核心專案目標**：
   閱讀 `memory/projects.md` 與 `memory/notes.md`（或聚合檔 `MEMORY.md`）了解進行中的架構與里程碑。
3. **依溝通管道輸出**：
   - **語音朗讀 (Apple Watch / HomePod)**：統整為 1~2 句純口語精要，直球回覆。嚴禁 Markdown 與 Emoji（符合 HomePod 朗讀規範）。
   - **Telegram / 終端機**：條列今日日程、重要提醒與專案目標。
