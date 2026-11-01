---
name: coupang-tracker
description: 酷澎 (Coupang Taiwan) 雙軌價格監控守護神技能。支援雙軌巡檢（購物車直連 100% 綠燈繞過防爬蟲盾 + 全網搜尋比價）、目標門檻降價即時通報（iOS Critical Alert 純靜音置頂 + Telegram 卡片）、口語隨時增減監控品項、調整價格門檻，以及輸出最新巡檢狀態報表。當主人詢問「酷澎價格」、「查酷澎最新狀態」、「酷澎巡檢狀況」、「幫我監控酷澎...」、「酷澎新增/修改門檻」、「停止酷澎監控」時觸發本技能。
---

# 酷澎雙軌價格守護神技能 (Coupang Tracker Skill)

本技能讓個人助理能夠隨時為主人監控台灣酷澎（Coupang Taiwan）的即時商品售價與特價破盤行情，支援口語即時查詢巡檢狀態、隨時增減監控品項與調整目標門檻價。

## 核心設計特性

1. **雙軌巡檢架構 (Dual-Track Architecture)**：
   - **軌道一：購物車直連 (Cart Track)**：
     - 直接巡檢 `https://cart.tw.coupang.com/cart` 與「保留清單 (Saved for Later)」。
     - 屬於登入會員核心路徑，**100% 綠燈秒開，完全不受 Akamai Bot Manager 防火牆攔截**。
   - **軌道二：搜尋頁廣度巡檢 (Search Query Track)**：
     - 透過首頁動態預熱、擬真 Referer 與動態 `traceId` 進行全網型號（如 iPhone 17 / 18 Pro 各色 256GB）價格比價。
2. **目標門檻價警報機制 (Target Price Alert)**：
   - **徹底摒棄「單向下修鎖死基準價」**：特價過後價格回彈、下次再度特價依然會觸發通報！
   - **智慧去重冷卻**：只要價格破更低價立刻通報；若維持低於門檻，間隔超過 6 小時進行溫馨提醒，杜絕洗版。
3. **推播通知規格**：
   - **iOS Critical Alert**：**100% 純靜音置頂**（嚴格恪守 `volume=0.0`），附帶直達購物車/結帳連結。
   - **Telegram Bot**：精緻 Markdown 卡片，即時標註型號規格、現價與設定門檻。
4. **狀態快照與歷程保留 (`recent_snapshots`)**：
   - 每輪巡檢結果自動寫入 `data/coupang_monitor_state.json`，保留最近 100 次巡檢快照，支援秒級回傳最新價格狀態。

---

## 核心檔案路徑

- **主控 CLI 腳本**：`.agents/skills/coupang-tracker/scripts/coupang_tracker.py`
- **爬蟲底層模組**：`scripts/coupang_price_tracker.py`
- **iOS 推播模組**：`scripts/ios_notify.py`
- **狀態與快照檔**：`data/coupang_monitor_state.json`
- **背景任務管理器**：`task_manager.py`

---

## 常用指令與標準操作規範 (SOP)

### 1. 查詢最新巡檢狀態與價格報表（最常用）

當主人說：「幫我看酷澎現在價格」、「酷澎最新狀態」、「酷澎巡檢日誌」：

```bash
.venv/bin/python .agents/skills/coupang-tracker/scripts/coupang_tracker.py status
```
*（若需 JSON 格式，可加上 `--json`）*

**回覆風格**：
- 直接呈現結構化狀態表格，標明「監控品項」、「目前現價」、「目標門檻」、「差額」與「狀態/來源」。

---

### 2. 新增或調整品項目標門檻價

當主人說：「酷澎幫我新增監控 [品名] [金額]」或「把 [品名] 門檻改成 [金額]」：

```bash
.venv/bin/python .agents/skills/coupang-tracker/scripts/coupang_tracker.py set --name "<品名或型號>" --threshold <金額>
```
*範例*：
```bash
.venv/bin/python .agents/skills/coupang-tracker/scripts/coupang_tracker.py set --name "Apple iPhone 17 Pro" --threshold 35000
.venv/bin/python .agents/skills/coupang-tracker/scripts/coupang_tracker.py set --name "Farmina 法米納 GC6 1.5kg 貓飼料" --threshold 900
```

---

### 3. 移除監控品項

當主人說：「酷澎不用盯 [品名] 了」、「刪除酷澎 [品名] 監控」：

```bash
.venv/bin/python .agents/skills/coupang-tracker/scripts/coupang_tracker.py remove --name "<品名或關鍵字>"
```

---

### 4. 啟動或重啟背景常駐巡檢 (TaskManager Fast Handoff)

> **重要原則**：常駐輪詢**一律透過 `task_manager.py spawn` 脫鉤啟動**，嚴禁在對話主 turn 阻塞輪詢，秒級交差釋放主大腦！

```bash
# 1. 檢查並停止舊任務（若需要）
.venv/bin/python task_manager.py list
.venv/bin/python task_manager.py kill "酷澎"

# 2. 脫鉤啟動常駐巡檢（預設 6 分鐘巡檢，TTL 10080 分鐘 = 7 天）
.venv/bin/python task_manager.py spawn \
  --name "酷澎雙軌價格守護神" \
  --script ".agents/skills/coupang-tracker/scripts/coupang_tracker.py" \
  --ttl 10080 \
  --args watch --interval 360
```

---

### 5. 立即執行單次巡檢 (run-once)

當主人說：「立刻幫我抓一次酷澎最新價格」：

```bash
.venv/bin/python .agents/skills/coupang-tracker/scripts/coupang_tracker.py run-once
```
