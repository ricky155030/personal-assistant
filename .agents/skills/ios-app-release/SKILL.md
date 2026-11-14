---
name: ios-app-release
description: MacMiniAssistant iOS App 自動編譯打包與 SideStore 遠端軟體源發布技能。每當修改 `ios/` 目錄下任何程式碼（SwiftUI 介面、API Client、Widget 等）並確認編譯通過後，或主人要求「打包 iOS App」、「發布新版 App」、「放到 static」、「更新 SideStore」時觸發，自動執行 `./package_ios.sh` 產出新版 `.ipa` 並更新 `static/apps.json`。
---

# iOS App 自動打包與 SideStore 發布技能 (iOS App Release Skill)

> **核心原則**：只要修改了 `ios/MacMiniAssistant/` 或 `ios/MacMiniAssistantWidget/` 內的任何程式碼並確認無誤，**必須主動完成打包與軟體源更新**，無需等主人事後提醒「放到 static」。

## 標準執行流程

1. **執行打包腳本**（帶入本次改動的簡潔中文說明作為 Changelog）：
   ```bash
   ./package_ios.sh "<本次更新重點摘要>"
   ```
   該腳本會自動完成：
   - 遞增 `CFBundleVersion`（同步寫入主 App 與 Widget 的 `Info.plist`）
   - 執行 `xcodegen generate` 與 `xcodebuild` 編譯（無須接上實機）
   - 打包輸出至 `static/MacMiniAssistant.ipa`
   - 更新 `static/apps.json` 與已訂閱的外部 SideStore 軟體源

2. **驗證輸出結果**：
   - 確認指令退出碼為 `0`，且輸出包含新的 `Build <N>`、`mac-mini-assistant 軟體源已更新` 與檔案大小。

3. **版本控管邊界（Zero-Data-Leak / Clean Repo）**：
   - **嚴禁將 `static/MacMiniAssistant.ipa` 或含私有 Tailscale 域名的 `static/apps.json` 加入 `git commit`**；僅提交 `ios/` 原始碼變更。

4. **回報主人**：
   - 在最終回覆中明確附上**最新 Build 編號**與更新摘要，並提醒主人可直接在 iPhone 的 **SideStore** 下拉重新整理軟體源進行遠端更新。
