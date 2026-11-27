# 開發交付、資安防護與維運規範 (`docs/DEV_WORKFLOW.md`)

> 凡涉及程式碼修改、Git 提交推送、CI 追蹤、跨專案派工或常駐服務重啟時，必須遵照本規範執行。

---

## 1. 程式碼交付與 CI 綠燈閉環原則 (Definition of Done: CI Verification Policy)

- **本機先行驗證 (Pre-flight Check)**：任何程式碼變更在 `git commit` 前，必須先於本地執行 `uv run pytest`，確保本機測試 100% 通過，絕不將低階語法或單元測試錯誤提交推送到遠端。
- **CI 綠燈才算完工 (CI Green as Definition of Done)**：
  - `git push` 不代表任務交付完成。
  - 若透過常駐助理 (`agy_singleton` / `omp_singleton` / App / Telegram) 執行，Push 後應立即透過 `python3 task_manager.py spawn-cmd --name "CI 監控 <run_id>" --cmd "gh run watch <run_id> --exit-status" --notify telegram --ttl 30` 脫鉤追蹤並立刻結束當前 turn，讓 CI 結果直接推播給主人，不阻塞或混入下次對話。若於終端機 CLI 互動開發中，則可直接等待 `gh run watch` 綠燈回報。
  - **紅燈自動自癒 (Auto-Heal on CI Failure)**：
    - 若 CI 失敗，主動擷取 `gh run view <run_id> --log-failed` 深入診斷跨平台（如 Windows、Ubuntu、各 Python 版本）的失敗根因。
    - 自動就地修復程式碼、提交並再次推送，循環驗證直到 CI 全綠燈為止。
    - **嚴禁在 CI 仍在跑或紅燈時向主人回報「已完成」**，確保 `main` 分支永遠保持全綠健康狀態。

---

## 2. 零機敏資訊外洩天條 (Zero-Data-Leak Policy & Commit Guard)

- **核心鐵律**：本專案為開源專案與公開/共享代碼庫，**絕對 100% 嚴禁將任何主人個人機敏資訊、私有網路架構或正式環境憑證寫入或 Commit 至 Git 倉庫**。
- **嚴格禁止寫入代碼與文件之機敏項目（Zero-Tolerance Blacklist）**：
  1. **正式金鑰與密鑰**：如 `TELEGRAM_BOT_TOKEN`、`HASS_TOKEN`、`ASSISTANT_API_KEY`、`OPENAI_API_KEY`、`GEMINI_API_KEY`、身分證字號 (`USER_ID_NO`)、生日密碼 (`USER_BIRTH_MMDD`)、各平台帳號密碼等。此類資訊僅能存在於本地未追蹤之 `.env`。
  2. **真實個人身分與姓名**：主人姓名、家人姓名、真實手機號碼、個人真實 Email。文檔、註解與測試一律使用 `<您的稱呼>`、`Alex`、`person.user` 或公開範例。
  3. **內部網路與設備實體 IP**：Tailscale 網段 IP（如 `100.x.y.z`）、區域網路 IP（`192.168.x.x`）、特定 Tailscale 節點私有域名。代碼與範例一律使用環境變數、`http://homeassistant.local:8123` 或 `<your-device-domain>.ts.net`。
  4. **真實家電硬體實體代碼**：特定硬體 Switch/Sensor MAC ID（如 `0xa4c138...`、`sonoff_...`、`158d00...` 等）。真實設備對照一律由本機未追蹤之 `data/devices.json` 載入，代碼範例僅能使用通用命名（如 `switch.living_room_light`）。
  5. **私人雲端資產 ID 與網址**：真實 Google 試算表 ID、私有 Google Drive 連結、私人 Facebook 社團網址。範例一律使用 `<YOUR_SPREADSHEET_ID>` 或公開 Dummy ID。
- **Commit 前強制防護查驗 (Pre-Commit Zero-Leak Gate)**：
  - 在執行 `git commit` 前，除了 `uv run pytest` 本機綠燈外，**必須主動執行機敏關鍵字與 IP 模式掃描**（檢查 `git diff --cached` 是否含有 IP、人名、實體硬體標識或真實金鑰）。
  - 若檢測到任何非 Dummy 假資料的真實私有標識，**嚴禁 Commit**，必須就地脫敏抽離至未追蹤的本機配置後方可提交。

---

## 3. 本機開發與交付核心原則 (Local Development & Delivery Discipline)

- **小步原子推進 (Small Atomic Commits)**：一次只完成單一功能或修復單一 Bug，絕不將重構與新功能混成一大包一次提交，確保每筆改動都能獨立測試與快速回滾。
- **改動必補測試 (Test-With-Change)**：新增業務邏輯或修復 Bug 時，必須同步補上對應單元測試與邊界條件，避免後續回歸破壞。
- **本機先驗再推送 (Local Pre-Push Gate)**：每次 Commit 與 Push 前，Python 必跑全量 `uv run pytest`，修改 `ios/` 必執行 `./package_ios.sh --local-only` 驗證編譯無誤與無新增警告，並通過機敏資訊掃描。
- **改完自測即提交 (Auto-Commit on Green)**：每次程式碼改完，助理必須自行跑對應 build/test（Python 跑全量 `uv run pytest`；改 `ios/` 先跑 `./package_ios.sh --local-only` 驗證編譯）加機敏掃描，全綠後直接 `git commit`，免等主人確認；一事一 commit，commit message 一律使用英文（Conventional Commits、祈使句、範圍明確，如 `feat(calendar): add retry on timeout`）；commit 後依第 1 節決定是否 push 並跟 CI 到綠燈。
- **iOS 版號與 GitHub Release 發布規範 (SemVer + Build + SideStore)**：修改 `ios/` 並完成測試與提交準備對外發布時，依改動性質決定語意化版號（`fix`/`perf` 升 `patch`、`feat` 升 `minor`、重大架構不相容變更升 `major`），執行 `./package_ios.sh --bump <patch|minor|major> "<更新摘要>"` 遞增 `Build` 號、更新 `ios/apps.json`（託管於公開 GitHub 倉庫供 SideStore 讀取）並自動建立 GitHub Release 上傳 `.ipa`（詳見 `ios-app-release` 技能）。
- **嚴守修改範圍 (Minimal Diff & Scope Discipline)**：只改當前任務相關程式碼，不順手重構無關模組或刪除既有防呆與註解；發現其他問題應另外排單處理。
- **前後端契約相容 (Contract-First Compatibility)**：後端 API 與 iOS App 有欄位連動時，必須保留向下相容性，確保舊版 App 尚未更新也不會閃退。
- **服務安全重啟 (Safe Service Reload)**：修改常駐後端後，嚴禁在語音串流進行中直接強殺服務；但完成開發、通過本機測試與 GitHub CI 驗證且判定需要重載套用時，助理可直接主動透過 `scripts/idle_safe_reload.py --quiet 8 --interval 2` 排程安全重載（免再反問主人確認，允許服務短暫重啟數秒）。
