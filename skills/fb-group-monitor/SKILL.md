---
name: fb-group-monitor
description: Facebook 社團新貼文智慧監控技能。透過 Chrome-Assistant 真實瀏覽器 Session 定期巡檢特定 FB 社團「最新貼文（CHRONOLOGICAL）」，經本地去重快取後，僅針對未見過的新貼文調用 `agy -p` Inline Prompt 進行自然語言條件判讀，命中時立即推播 Telegram 通知並附帶直達連結。當主人說「幫我監控 FB 社團...」、「盯一下社團有沒有人賣...」、「停止 FB 社團監控」、「看一下社團最新貼文」時觸發本技能。
---

# Facebook 社團新貼文智慧監控技能 (FB Group Monitor)

本技能讓個人助理能夠依據主人的口語指令，透過 `task_manager.py` 在背景脫鉤監控指定的 Facebook 社團最新貼文，並使用 `agy -p` 語意篩選目標貼文，命中時即時發送 Telegram 通知。

## 核心設計特性

1. **穩定可靠的純首頁巡檢架構（預設每 60 秒巡檢）**：
   - 每 60 秒（1 分鐘）透過 Chrome-Assistant 開啟社團最新貼文（`?sorting_setting=CHRONOLOGICAL`），模擬自然滾動 4 段展開貼文並提取 DOM。
   - 巡檢完畢後立即關閉分頁與 Worker 連線，待機期間 0% CPU、0 連線，天然抗防爬蟲盾，已穩定驗證數週從未遭遇風控封鎖。
2. **零 Token 浪費（本地雙重去重快取）**：
   - 已讀貼文的 `post_id` 與內文雜湊指紋 (`text_hash`) 永久記錄於 `data/fb_seen_posts.json`。
   - 僅對未曾看過的新貼文調用 LLM，舊文瞬間識別略過，**0 次 LLM 呼叫、0 重複推播**。
3. **無狀態極速 Inline Prompt (`agy -p`) ＋ 自動圖片視覺辨識**：
   - 發現新貼文時，自動下載貼文附圖至 `/tmp/fb_monitor_imgs`（判讀後自動閱後即焚），於 `/tmp` 輕量環境呼叫 `agy --dangerously-skip-permissions -p` 進行批次 JSON 判讀；遇到只寫「贈」字未寫品名的貼文也能直接看圖辨識。
4. **完整巡檢歷史與結構化日誌 (`history`)**：
   - 每輪巡檢時間、掃描篇數、以及每篇貼文的內容預覽、命中狀態與判讀原因均自動記錄於 `data/fb_seen_posts.json` 與 `data/task_logs/<task_id>.log`，隨時支援主人口語查詢「近期的巡檢有些什麼？」。
5. **常用社團自動記憶 (`data/fb_group_aliases.json`)**：
   - 只要主人提供過一次社團網址，系統自動將其設為 `default`（預設社團），後續主人只需口語說「幫我監控 FB 社團有沒有二手推車」，免重複貼網址即可直接啟動！

---

## 執行環境與腳本路徑

- **核心腳本**：`.agents/skills/fb-group-monitor/scripts/fb_group_monitor.py`
- **任務管理器**：`task_manager.py`
- **已讀與歷史快取檔**：`data/fb_seen_posts.json`
- **社團別名檔**：`data/fb_group_aliases.json`

---

## 標準操作規範 (SOP)

### 情境 1：主人口語要求「啟動/開始監控 FB 社團」（最常用）

> **重要鐵律（Fast Handoff）**：背景輪詢監控**一律透過 `task_manager.py spawn` 啟動**，嚴禁在主對話直接裸跑 `watch` 阻塞等待！若已有舊的 FB 社團監控任務在跑，先 `kill` 舊任務再啟動新任務。

1. **檢查或清理舊監控任務（若需要更新條件）**：
   ```bash
   .venv/bin/python task_manager.py list
   # 若已有舊的 FB 社團監控且主人想更換條件：
   .venv/bin/python task_manager.py kill "FB社團監控"
   ```

2. **透過 `task_manager.py` 脫鉤啟動背景監控（每 60 秒巡檢，TTL 預設 10080 分鐘 = 7 天）**：
   ```bash
   .venv/bin/python task_manager.py spawn \
     --name "FB社團監控: <簡短目標描述>" \
     --script ".agents/skills/fb-group-monitor/scripts/fb_group_monitor.py" \
     --ttl 10080 \
     --args watch --group "<社團網址或default>" --criteria "<主人指定的自然語言篩選條件>" --interval 60
   ```
   *(註：若主人沒給網址，且 `data/fb_group_aliases.json` 已有 `default`，`--group "default"` 會自動套用預設社團；若從未設定過任何社團網址，請先向主人反問確認目標社團網址。)*
   *(註：若主人沒給網址，且 `data/fb_group_aliases.json` 已有 `default`，`--group "default"` 會自動套用預設社團；若從未設定過任何社團網址，請先向主人反問確認目標社團網址。)*

3. **秒級回覆主人並結束當前 Turn**：
   啟動成功後立即回覆主人確認任務 ID、巡檢頻率（1 分鐘）與篩選條件。

---

### 情境 2：主人詢問「近期的巡檢有些什麼？」、查看監控狀態或停止監控

- **快速查看近期巡檢結果與貼文清單 (`--recent` / `--status` / `status` / `history`)**：
  當主人問「最近巡檢到了什麼貼文？」、「剛剛社團發了什麼？」、「監控狀態如何？」時，執行：
  ```bash
  # 預設快速查看當前監控守護狀態、近期輪次與最近 5 篇貼文（終端美化排版）
  .venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py --recent

  # 指定查看最近 10 篇貼文（支援簡寫 -r 10）
  .venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py --recent 10

  # 輸出機器可讀之 JSON 結構（方便程式或 Agent 萃取）
  .venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py --recent --json
  ```
- **查看目前背景任務運行狀態與即時 Log**：
  ```bash
  .venv/bin/python task_manager.py list
  .venv/bin/python task_manager.py log "FB社團監控" --lines 30
  ```
- **停止 FB 社團監控**：
  ```bash
  .venv/bin/python task_manager.py kill "FB社團監控"
  ```

---

### 情境 3：單次即時掃描社團最新貼文 (`scan`)

當主人只是想問「現在社團最新貼文有什麼？」或「幫我測一下社團抓不抓得到」，不需掛背景長任務，直接執行 `scan`：

```bash
# 僅抓取最新 6 篇貼文原始內容（不呼叫 agy -p）
.venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py scan --group "default" --max-posts 6

# 抓取並立即用 agy -p 依條件篩選（加上 --notify 會在命中時同步發 Telegram）
.venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py scan \
  --group "default" \
  --criteria "<篩選條件>" \
  --max-posts 6 \
  --notify
```

---

### 情境 4：初次登入 Facebook 或管理社團別名 (`login` / `alias`)

- **喚起 `Chrome-Assistant` 讓主人登入 Facebook**：
  ```bash
  .venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py login
  ```
- **設定或查看常用社團別名**：
  ```bash
  # 設定別名（同時會自動更新 default）
  .venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py alias set --name "恩典牌" --url "https://www.facebook.com/groups/xxxxxx/"

  # 列出已儲存的社團別名
  .venv/bin/python .agents/skills/fb-group-monitor/scripts/fb_group_monitor.py alias list
  ```
