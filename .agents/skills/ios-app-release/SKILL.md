---
name: ios-app-release
description: MacMiniAssistant iOS App 自動編譯打包、語意化升版與 GitHub Releases / SideStore 發布；修改 ios/ 程式碼或要求打包發布新版時觸發。
---

# iOS App 自動打包與 GitHub Release / SideStore 發布技能

> **核心原則**：只要修改了 `ios/` 內的任何程式碼並確認無誤，**必須主動完成打包、更新 `ios/apps.json` 與 GitHub Release 發布**，無需等主人事後提醒。

## 語意化版號升級判定原則 (SemVer + Build)

- **Build 號 (`CFBundleVersion`)**：每次打包嚴格遞增 1（主 App、Widget 與 Share Extension 同步），作為 SideStore 與 App 內更新檢查的唯一比對依據。
- **版號 (`CFBundleShortVersionString` / `MARKETING_VERSION`)**：
  - `patch`（如 `1.0.0` → `1.0.1`）：Bug 修復、效能優化、UI 微調（對應 `fix` / `perf` / `style`）。
  - `minor`（如 `1.0.1` → `1.1.0`）：新增完整功能或新頁面（對應 `feat`）。
  - `major`（如 `1.x.x` → `2.0.0`）：整體架構或介面重大翻新、前後端 API 不相容變更。
  - `--local-only`：僅本機驗證編譯，只加 Build 號、不升版號、不發 GitHub Release。

## 標準執行流程

1. **本機預檢編譯**（於 `git commit` 程式碼改動前）：
   ```bash
   ./package_ios.sh --local-only "<測試摘要>"
   ```
2. **正式發布（升版 + 更新 `ios/apps.json` + 建立 GitHub Release）**：
   ```bash
   ./package_ios.sh --bump <patch|minor|major> "<本次更新重點摘要>"
   ```
   （若需指定特定版號如 `1.0.0`，可使用 `--version 1.0.0`）
   該腳本會自動完成：
   - 依 `--bump` 或 `--version` 更新 `ios/project.yml` 與三支 `Info.plist` 的 `CFBundleShortVersionString` 及遞增 `CFBundleVersion`
   - 執行 `xcodegen generate` 與 `xcodebuild` 編譯並打包 `static/MacMiniAssistant.ipa`
   - 更新追蹤於倉庫的 `ios/apps.json`（含零機敏 GitHub Raw `sourceURL` / `iconURL` 與 GitHub Releases `downloadURL`）
   - 同步更新本機 `static/apps.json` 與外部訂閱軟體源（若存在）
   - 自動提交 `ios/apps.json`、`ios/project.yml` 與 `Info.plist` 版本異動、推送 `main`，並透過 `gh release create v<VERSION>` 上傳 `MacMiniAssistant.ipa`

3. **版本控管邊界（Zero-Data-Leak / Clean Repo）**：
   - **嚴禁將 `static/MacMiniAssistant.ipa` 或含私有 Tailscale 域名的 `static/apps.json` 加入 `git commit`**。
   - 公開託管於倉庫的 `ios/apps.json` 僅能包含公開 GitHub Raw 與 GitHub Releases 連結，絕對不可出現任何私有 IP 或域名。

4. **回報主人**：
   - 在最終回覆中附上**最新版號與 Build 編號**（如 `v1.0.0 (Build 134)`）與更新摘要，並提醒主人可直接在 iPhone 的 **SideStore** 重新整理軟體源更新。
