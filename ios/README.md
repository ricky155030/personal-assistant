# MacMiniAssistant iOS App (`ios/`)

專為 **邊走路散步、邊開車時免持連續語音聊天與口語 Vibe Coding** 打造的 iOS 原生 SwiftUI 客戶端。

## 📄 施工規格書 (Specification)
- 完整需求、架構紅線、後端 SSE 串流協定、SwiftUI 狀態機設計與驗收標準請見：
  👉 **[`ios/SPEC.md`](./SPEC.md)**

## 🚀 給後續動工 Agent 的快速指引
1. 請先完整閱讀 [`ios/SPEC.md`](./SPEC.md) 與根目錄 [`AGENTS.md`](../AGENTS.md)。
2. 依序完成：
   - **Phase 1**：擴充 [`agent.py`](../agent.py) 與 [`main.py`](../main.py) 新增 `POST /api/voice-chat/stream` (SSE 串流 + 非程式碼口語自動分句 `tts_sentence`)。
   - **Phase 2**：於本目錄 (`ios/`) 建立 `MacMiniAssistant` SwiftUI 專案，實作全自動 VAD 免持收音、iOS 原生 `AVSpeechSynthesizer` 零延遲朗讀佇列、盲操打斷手勢與背景音訊常駐。
   - **Phase 3**：建立根目錄 `package_ios.sh`，通過 `xcodebuild` 無簽名編譯並打包 `.ipa` 發布至 SideStore 軟體源。
