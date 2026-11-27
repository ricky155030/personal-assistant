---
name: herdr-coordinator
description: 透過 Herdr 開設獨立視窗派發跨專案或耗時開發/調研任務，並透過 scripts/herdr_coordinator.py 啟動背景監工、無狀態自動代答與品管驗收迴圈。
---

# Herdr 派工協調與背景品管技能 (`herdr-coordinator`)

## 1. 派工黃金鐵律
- **嚴禁阻塞主大腦**：凡涉及深度調研、多檔開發、編譯打包或耗時回測，嚴禁在主對話 turn 內直接執行或使用原生子代理，一律透過 Herdr 開設獨立視窗在背景執行，並在結案後關閉暫時視窗。
- **免確認直跑參數**：啟動新代理時務必帶入免確認旗標（`agy` 使用 `--dangerously-skip-permissions`；`omp` 預設自動執行工具，可透過 `--model <Provider/Model>` 與 `--thinking <low|medium|high>` 指定模型與推理強度）。

## 2. 開設新分頁與啟動代理
```bash
# 1. 開設不搶焦點的獨立分頁（取得回傳 JSON 的 .result.root_pane.pane_id）
herdr tab create --cwd "<專案絕對路徑>" --no-focus --label <agent-name>

# 2a. 啟動 OMP 工人（指定模型與推理強度）
herdr agent start <agent-name> --kind omp --pane <pane_id> -- --model Gemini-GW-1/gemini-4-hydrogen --thinking medium

# 2b. 或啟動 AGY 工人
herdr agent start <agent-name> --kind agy --pane <pane_id> -- --model gemini-3.8-flash-high --dangerously-skip-permissions
```

## 3. 派發任務與啟動背景品管守護
統一透過 `scripts/herdr_coordinator.py dispatch` 送出任務並登記 `TaskManager` 背景守護進程（注意：在 OMP 環境若透過背景執行啟動守護進程，需以 `o2ctl services add <name> <pid>` 納管避免回合結束被回收）：
```bash
python3 scripts/herdr_coordinator.py dispatch \
  --agent <agent-name> \
  --name "<顯示名稱，如：market-ticker · 雙分頁開發>" \
  --prompt "<自包含任務需求與驗證方式>" \
  --criteria "<具體可驗證的完工標準>" \
  --max-retries 3
```
- **可用進階參數**：`--no-review`（略過自動品管驗收）、`--batch-id <id> --batch-total <N>`（多任務併發彙總）、`--force`（強制派發）。

## 4. 生命週期、代答與 Follow-up 回溯
- **督工抽屜整合**：執行進度、卡住請示與完工報告（「✅ 完成項目 + 🧪 驗證結果」）統一收斂於 iOS App 右上角 **Herdr 派工督工抽屜（Action Queue UI）**；結案時靜默同步至主大腦記憶並發送背景未讀通知。
- **現場監工代答與打回重修**：`watch_agent` 遇到常規無害工程確認（如跑測試、檢查 lint）會自動代答解鎖；涉及產品偏好或外部副作用才進待決策佇列請示主人。完工時透過無狀態 `agy -p` 依 `--criteria` 驗收，未通過自動打回重修（預設上限 3 次）。
- **答案回寫與收尾關窗**：手動解鎖請示可執行 `python3 scripts/herdr_coordinator.py relay --agent <agent-name> --answer "<答覆>"`；任務驗收結案後執行 `herdr pane close <pane_id>` 關閉暫時視窗。
- **歷史追問回溯**：主人在主對話追問先前任務細節時，透過 `herdr agent list` 取得 `agent_session`（`agy` 的 `transcript_full.jsonl` 或 `omp` 的 `.jsonl`）精準回溯完整紀錄。
