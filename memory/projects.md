# 專案目標與持續追蹤 (`memory/projects.md`)

## 1. 核心專案進度 (Core Projects)

### Mac mini 個人秘書助理架構 (Mac Mini Assistant)
- **架構願景**：打造個人專屬的執行秘書，整合 Apple Watch 語音輸入、Telegram 隨身助理與 Google 日曆待辦排程。
- **里程碑狀態**：
  - [x] **常駐代理單例 (agy singleton)**：預熱常駐 `stream-json` 進程，大幅降低冷啟動延遲。
  - [x] **Context 上下文防線**：建立上下文閾值自動重啟保護機制，搭配 Telegram 即時狀態告警。
  - [x] **三大溝通模式分流**：終端機開發模式 (全 Markdown)、語音手錶模式 (簡潔純文字)、Telegram 機器人模式 (卡片排版+圖表報告)。
  - [x] **秘書級模組化記憶庫**：將記憶庫解耦為 `profile`, `preferences`, `projects`, `notes`，並全權對接 Google Calendar「待辦事項」。
  - [x] **模組化技能與工具鏈擴充**：支援豐富的日常與自動化技能擴充，並持續維護與自測。

### 家庭通用待買清單 (Universal Shopping List)
- **試算表 ID**：`<YOUR_SPREADSHEET_ID>`
- **用途**：家庭全域採買行動中心（日常日用品、採買代購），由專屬技能 `shopping-list` 進行維護。
- **狀態**：[運作中]

### 嬰兒用品與待產備品清單 (Baby & Newborn Checklist)
- **試算表別名**：`baby`、`newborn`
- **試算表 ID**：`<YOUR_SPREADSHEET_ID>`
- **線上網址**：https://docs.google.com/spreadsheets/d/<YOUR_SPREADSHEET_ID>/edit
- **用途**：追蹤與管理新生兒備品、衣物尺寸、大型用品（汽座、推車、澡盆、消毒鍋等）現況與採買狀態。
- **存取方式**：透過通用 `google-sheets` 技能（例如：`sheets_tool.py search baby "汽座"` 或 `read baby`）進行動態查詢與更新。
- **狀態**：[運作中]

---

## 2. 待辦與排程原則
- **唯一真實來源 (Single Source of Truth)**：不在 Markdown 文件堆積靜態過期待辦。
- **Google Calendar 待辦事項**：所有日常任務、繳費、預約、提醒與會議，一律直接寫入 Google Calendar「待辦事項」日曆，透過技能動態檢索與管理。
