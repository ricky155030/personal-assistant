# iOS Gemini 風格對話與即時語音對談隨身助理規格書 (`ios/SPEC.md`)

| 屬性 | 內容 |
| :--- | :--- |
| **專案名稱** | `MacMiniAssistant` (iOS Native App) |
| **當前版本** | `v1.0 (Build 24)` — Gemini 風格直接對話介面、即時 STT、等一下打斷、多模態傳圖、語速倍速與歷史抽屜已上線 |
| **規格書更新日期** | **`2026-11-06`** |
| **下一階段目標** | **Phase 4（架構隱患根治）** 與 **Phase 5（進階體驗升級：Markdown 程式碼渲染、耳機線控、一鍵直入 Live、動態島）** |
| **目標載體** | iOS 17+ 原生 SwiftUI App（置於本 Repo `ios/` 目錄，透過 `./package_ios.sh` 發布至 SideStore 軟體源） |

---

## 1. 📌 現況架構總覽（截至 2026-11-06 Build 24 已完成基準）

1. **Gemini 風格主視覺與直接對話流 (`HandsFreeMainView.swift`)**：
   - 待命時顯示純黑至午夜藍漸層背景、四角四色星芒 ✨ 與「**宏瑋，該你囉！**」。
   - 一旦有對話立即於主畫面直接滾動顯示雙向訊息氣泡、工具執行軌跡膠囊與即時朗讀控制。
   - 頂部導覽列整合：左上角 `=`（近期對話歷史抽屜）、`Flash 中等 ⌄`（切換思考深度 `low/med/high` 與 TTS 語速 `1.0x ~ 1.45x`）、右上角「開啟新對話」與「個人設定頁」。
2. **雙麥克風分工與多模態膠囊輸入列 (`VoiceSessionCoordinator.swift` + `AudioRecorderVAD.swift`)**：
   - **`[ + ]` 多模態拍照/選圖**：支援相機拍照與相簿選圖，自動壓縮為最長邊 1280px JPEG 附於輸入框上方，可搭配語音或文字一併送交後端 `/api/voice-chat/stream`。
   - **左側 `🎙️` 單次語音轉文字 (STT)**：採用 iOS 原生 `SFSpeechRecognizer (zh-TW)` + 專屬中英技術詞庫注入 (`contextualStrings`)，邊講邊將文字即時填入底部對話框，停頓後自動停止收音並保留文字於框內供檢視或按 `↑` 送出。
   - **右側藍色 `((|))` Gemini Live 連續語音對談**：
     - 全自動循環：「開口說話（畫面即時預覽跳字）➔ 停頓達設定秒數（1~10 秒整數可調）0 延遲自動送出（播放『咚』確認音）➔ 助理微軟曉臻神經語音逐句回答 ➔ 唸完播放『叮』提示音並自動重開麥克風」。
     - **「等一下」關鍵字語音打斷 (Keyword Voice Barge-in)**：助理回答時背景同步辨識語音文字，僅當辨識出包含「等一下 / 等下」時才瞬間切斷助理回答並接續收音，杜絕環境雜音與旁人對話誤觸。
     - **自然口語自動掛斷**：對助理說「沒事了」、「先這樣」、「結束對話」、「掰掰」時，播放柔和結束音並自動關閉 `((|))` Live 模式退回待機。
     - **背景與鎖屏常駐保活**：透過 `keepEngineAliveInBackground` 維持 `AVAudioEngine` 不中斷、搭配無聲 PCM 保活音軌 (`startBackgroundKeepAlive`)、45 秒自動續約 `SFSpeechRecognizer` 與 `UIBackgroundTask`，防止背景切換時發生 `The network connection was lost (-1005)`。

---

## 2. ⚠️ 待辦事項零：後端常駐服務重啟與真機驗收（立即可執行）

> **背景說明（2026-11-06 紀錄）**：
> `main.py` 與 `tts_service.py` 已於 Commit `5ab1923` 新增 `image_base64`（多模態圖片解碼儲存至 `data/ios_uploads/`）與 `tts_rate`（Edge-TTS 原調語速參數），但 Mac mini 上常駐的 `uvicorn main:app --host 0.0.0.0 --port 8899` 尚未重啟載入新程式碼。

### 執行步驟
1. 於適當時機（確認無正在執行的 Apple Watch / HomePod 請求時）重啟 `uvicorn` 服務。
2. 使用 curl 驗證新欄位：
   ```bash
   curl -N -X POST http://your-tailscale-device.ts.net:8899/api/voice-chat/stream \
     -H "Content-Type: application/json" \
     -H "X-Agent-Key: your-api-key" \
     -d '{"query": "用一句話測試語速", "client": "ios-voice", "tts_rate": "+25%"}'
   ```

---

## 3. 🛠️ Phase 4：四大底層架構隱患根治規格（優先級：最高 P0）

後續接手實作的開發者／Agent 請依序完成以下 4 項底層穩定性改造：

### 4.1 【隱患一】切換歷史舊對話時，後端 `agy_singleton` 上下文未同步對齊
- **問題根因**：
  使用者從左上角 `=` 抽屜（`ConversationDrawerView.swift`）點選一筆舊的 `ChatSession` 後，手機畫面雖然載入了舊對話的 `messages`，但後端 `agy_singleton` 是單一常駐 Session（或者已經被其他管道如 Telegram / Apple Watch 洗掉上下文，甚至重啟過）。此時使用者接著講「*那第二個方案怎麼改？*」，後端根本不知道這筆歷史對話的前文，會答非所問。
- **目標檔案**：
  - `ios/MacMiniAssistant/Services/VoiceSessionCoordinator.swift`
- **具體實作方案**：
  1. 在 `VoiceSessionCoordinator` 新增狀態旗標：
     ```swift
     private var needsHistoryContextInjection: Bool = false
     ```
  2. 當呼叫 `selectSession(_ session: ChatSession)` 載入歷史對話時，若 `session.messages` 非空，設定 `needsHistoryContextInjection = true`。
  3. 在 `processTranscript(_ text: String)` 組裝 `fullQuery` 時，若 `needsHistoryContextInjection == true`：
     - 取出 `messages` 中該場對話最近 6 則（最多 3 輪 user/assistant）歷史訊息（每則截斷至前 300 字以節省 Token）。
     - 自動在發送給後端的 `fullQuery` 前方注入結構化前情提要（但 UI 氣泡依然只顯示乾淨的 `text`）：
       ```swift
       var queryToSend = fullQuery
       if needsHistoryContextInjection {
           needsHistoryContextInjection = false
           let recentContext = buildRecentHistoryContext(limit: 6)
           if !recentContext.isEmpty {
               queryToSend = "[系統提示：主人剛從手機切換回先前的歷史對話，以下為該場對話的前情提要供你銜接上下文]\n\(recentContext)\n\n[主人當前最新提問]\n\(fullQuery)"
           }
       }
       ```
- **驗收標準**：
  - 點開三天前的歷史對話並直接追問代名詞問題（如「剛剛說的那個設定檔在哪？」），助理能精準根據該場歷史對話回答。

---

### 4.2 【隱患二】iOS `SFSpeechRecognizer` 雲端配額限制與 45 秒背景斷流瞬盲
- **問題根因**：
  1. `SFSpeechAudioBufferRecognitionRequest` 預設走 Apple 雲端伺服器辨識，Apple 對單一設備有每小時請求次數與單次 60 秒硬上限，長時間散步可能遭遇配額限流。
  2. 目前每 45 秒呼叫 `startSpeechRecognitionStream()` 重建 Request 時，若使用者剛好在第 45.0 秒開口講第一個字，重建瞬間會有約 150ms 的空窗吃字。
- **目標檔案**：
  - `ios/MacMiniAssistant/Services/AudioRecorderVAD.swift`
- **具體實作方案**：
  1. **優先啟用 iOS 裝置端離線神經網路辨識 (`requiresOnDeviceRecognition`)**：
     在 `startSpeechRecognitionStream()` 中檢查 `recognizer.supportsOnDeviceRecognition`：
     ```swift
     if recognizer.supportsOnDeviceRecognition {
         request.requiresOnDeviceRecognition = true
     }
     ```
     *優勢*：iPhone Neural Engine 本地運算 **0 網路延遲、不受 Apple 雲端 60 秒與每小時次數限制、戶外基地台切換完全不影響辨識**！
  2. **45 秒續約時回灌 `preRollBuffers` 消除切換空窗**：
     當定時器觸發自動續約重建 `SFSpeechAudioBufferRecognitionRequest` 時，將記憶體中最近 400ms 的 `preRollBuffers` 立即 `append` 進新 Request，確保就算使用者剛好在續約瞬間開口，第一個音節也 100% 不遺失。
- **驗收標準**：
  - 連續開啟 Live 模式散步 30 分鐘以上，語音辨識不因配額中斷，且任意時刻開口第一個字皆完整捕捉。

---

### 4.3 【隱患三】對話歷史照片直接將 Base64 `Data` 塞入單一 JSON 造成載入膨脹
- **問題根因**：
  目前 `ChatMessage.attachedImageData: Data?` 直接隨 `sessionHistory` 編碼進 `Documents/macmini_chat_sessions.json`。若累積傳送 20~30 張照片，單一 JSON 檔會膨脹至數十 MB，導致每次新增訊息 `saveSessionHistory()` 與啟動 App `loadSessionHistory()` 產生主執行緒 I/O 卡頓。
- **目標檔案**：
  - `ios/MacMiniAssistant/Models/AssistantModels.swift`
  - `ios/MacMiniAssistant/Services/VoiceSessionCoordinator.swift`
  - `ios/MacMiniAssistant/Views/HandsFreeMainView.swift`
- **具體實作方案**：
  1. 在 `Documents/chat_images/` 建立專屬圖片快取目錄。
  2. 將 `ChatMessage` 改為儲存相對檔名 `attachedImageFilename: String?`（並保留計算屬性或快取載入 `UIImage`），避免將數百 KB 的二進位圖片寫入 JSON。
  3. 在 `deleteSession()` 與 `clearAllHistory()` 時，同步清理對應的本地圖片檔案，避免空間洩漏。
- **驗收標準**：
  - `macmini_chat_sessions.json` 檔案大小永遠維持在數百 KB 以內，啟動與存檔維持 0 卡頓。

---

### 4.4 【隱患四】iPhone 喇叭外放時的硬體級 AEC（回音消除）路由整合
- **問題根因**：
  目前收音走 `AVAudioEngine.inputNode`（已開啟 `setVoiceProcessingEnabled(true)`），但助理語音播放走的是獨立的 `AVAudioPlayer(data: mp3Data)`。在 iOS CoreAudio 架構下，當不戴耳機、使用 iPhone 底部喇叭大聲外放時，`AVAudioPlayer` 的音訊流並未直接掛載於同一個 `AVAudioEngine` 的 `outputNode` 參考鏈路上，因此硬體 AEC 消除率不如同引擎內部路由，外放音量開到最大時仍可能干擾 `SFSpeechRecognizer` 聽取「等一下」。
- **目標檔案**：
  - `ios/MacMiniAssistant/Services/NativeSpeechPlayer.swift`
  - `ios/MacMiniAssistant/Services/AudioRecorderVAD.swift`
- **具體實作方案**：
  1. 將 TTS 音訊播放改為透過 `AVAudioPlayerNode` 掛載（attach & connect）至同一個開啟了 `setVoiceProcessingEnabled(true)` 的 `AVAudioEngine.mainMixerNode`（或於外放模式下在 `isBargeInMonitoring` 期間將播放增益控制在 `0.85` 並比對剔除 `currentSentence` 重疊字詞）。
  2. **雙重回音文字過濾器（Text-Level Echo Rejection）**：
     在 `AudioRecorderVAD.swift` 的 `isBargeInMonitoring` 判斷中，若目前助理正在唸的句子本身就包含「等一下」三個字，暫時忽略該句期間的同詞觸發，防止助理自己唸出「等一下」時把自己打斷。
- **驗收標準**：
  - 不戴耳機、iPhone 喇叭外放 80% 音量下，助理朗讀長篇內容不會自我打斷；使用者在 40 公分距離正常音量說「等一下」可在 0.3 秒內精準打斷。

---

## 4. 🚀 Phase 5：五大進階使用者體驗升級規格（優先級：P1 ~ P2）

### 5.1 【體驗升級一】區塊級 Markdown 與程式碼語法高亮卡片 + 一鍵複製 (P1)
- **現況痛點**：
  SwiftUI 原生的 `Text(LocalizedStringKey(message.content))` 僅支援行內粗體與連結，遇到三反引號程式碼區塊（`` ```python ... ``` ``）、標題（`###`）或表格時，會全部擠在同一行或失去換行與等寬字體排版，對 Vibe Coding 檢視程式碼極不友善。
- **目標檔案**：
  - 新增 `ios/MacMiniAssistant/Views/MarkdownMessageView.swift`
  - 修改 `ios/MacMiniAssistant/Views/HandsFreeMainView.swift`（替換 `GeminiMessageRow` 內的純 `Text`）
- **具體實作方案**：
  1. 實作輕量無第三方套件依賴的 `MarkdownBlockParser`，將訊息文字依序切分為：
     - `.paragraph(String)`：一般文字與條列（支援行內 `LocalizedStringKey` 渲染）。
     - `.heading(level: Int, text: String)`：以對應字級粗體與上下間距渲染。
     - `.codeBlock(language: String, code: String)`：
       - 以深色圓角卡片（`Color(red: 0.07, green: 0.08, blue: 0.11)`）渲染。
       - 卡片頂部 Header 顯示語言標籤（如 `python`、`swift`、`bash`）與右上角 **「📋 複製程式碼」按鈕**（點擊寫入 `UIPasteboard.general.string` 並觸發 Haptic 與「已複製 ✓」提示）。
       - 卡片內容使用 `.font(.system(size: 13.5, design: .monospaced))` 並支援水平滾動（`ScrollView(.horizontal)`），確保程式碼絕不破版折行。
- **驗收標準**：
  - 當助理回覆包含 Python / Swift 程式碼區塊時，以獨立等寬字體卡片呈現，點擊右上角可一鍵複製整段程式碼。

---

### 5.2 【體驗升級二】消除思考等待空白感（工具執行與長思考「微呼吸引導音」與訊息重試按鈕）(P1)
- **現況痛點**：
  1. 當使用者問了一個需要查行事曆、讀寫檔案或跨網搜尋的問題，從「咚（送出）」到助理吐出第一句話之間常有 3~8 秒的等待期。若手機放口袋且關閉了工具語音播報，使用者會以為斷線了。
  2. 若在電梯或地下室網路瞬斷導致該則訊息 `.error`，目前必須重新再講一遍。
- **目標檔案**：
  - `ios/MacMiniAssistant/Services/VoiceSessionCoordinator.swift`
  - `ios/MacMiniAssistant/Views/HandsFreeMainView.swift`
- **具體實作方案**：
  1. **超過 2.5 秒未出聲時自動墊音提示**：
     在 `processTranscript()` 啟動一個 2.5 秒 Timer：若 2.5 秒後仍處於 `.thinkingAndStreaming` 且尚未收到第一個 `audio_chunk` 或 `status` 播報，自動輕聲播放一個極簡短的柔和思考音（或輕聲說「*稍等喔*」），消除口袋盲操時的等待焦慮。
  2. **訊息失敗一鍵「🔄 重試」按鈕**：
     在 `GeminiMessageRow` 中，若 `message.status == .error`，在氣泡下方直接顯示「🔄 點擊重試」膠囊按鈕，點擊即直接拿上一則 User Message 重新發起串流，無需重講。
- **驗收標準**：
  - 複雜查詢等待超過 2.5 秒有明確聽覺反饋；網路異常失敗時可一鍵點擊重試。

---

### 5.3 【體驗升級三】AirPods 耳機柄按壓 ／ 車用方向盤按鍵控制 (`MPRemoteCommandCenter`) (P1)
- **現況痛點**：
  戴著 AirPods 散步或開車連藍牙時，手機鎖屏放在口袋裡，若想「立即送出剛講完的話（不等停頓秒數）」或「安靜打斷助理（不想喊等一下）」，無法透過捏耳機柄或按方向盤按鍵操作。
- **目標檔案**：
  - `ios/MacMiniAssistant/Services/VoiceSessionCoordinator.swift`（新增 `import MediaPlayer`）
- **具體實作方案**：
  1. 在進入 `((|))` Live 模式時，註冊 `MPRemoteCommandCenter.shared()` 的三個核心指令：
     - `togglePlayPauseCommand`（AirPods 單擊耳機柄 / 車機播放暫停鍵）：
       - 若助理正在說話 (`.speaking`) ➔ **立即打斷閉嘴並切換為 `.listening`**！
       - 若正在聽使用者說話且已有辨識文字 (`!liveTranscript.isEmpty`) ➔ **0 秒立即送出該句話**！
       - 若尚未說話 ➔ **切換麥克風暫停 (`.paused`) / 恢復聆聽 (`.listening`)**，並播放對應提示音。
     - `nextTrackCommand`（AirPods 連按兩下耳機柄 / 方向盤下一首鍵）：
       - 強制立即打斷或立即送出當前語句。
  2. 同步更新 `MPNowPlayingInfoCenter.default().nowPlayingInfo`：
     - `MPMediaItemPropertyTitle`: 即時顯示當前狀態（如 `"🎙️ 正在聆聽您說話..."`、`"🧠 助理思考中..."` 或當前朗讀句子）。
     - `MPMediaItemPropertyArtist`: `"Mac mini 隨身助理 (Live)"`。
     - 讓 iPhone **鎖定畫面（Lock Screen）與 Apple Watch 播放中畫面**直接即時顯示當前對話狀態與字幕！
- **驗收標準**：
  - 手機鎖屏放口袋時，捏一下 AirPods 耳機柄即可打斷助理說話或立即送出語句；抬起手機看鎖屏即可看到即時狀態字幕。

---

### 5.4 【體驗升級四】iPhone「動作按鈕 (Action Button) ／ 鎖屏捷徑」一鍵直入 Live 對談 (`macmini://live`) (P2)
- **現況痛點**：
  目前要開始語音聊天需「解鎖手機 ➔ 點開 App ➔ 點右下角藍色 `((|))`」共 3 步。
- **目標檔案**：
  - `ios/project.yml` & `ios/MacMiniAssistant/Info.plist`（新增 `CFBundleURLTypes` 註冊 `macmini://`）
  - `ios/MacMiniAssistant/MacMiniAssistantApp.swift`（新增 `.onOpenURL` 處理）
  - 新增 `ios/MacMiniAssistant/Services/AssistantAppShortcuts.swift`（實作 `AppIntent` 與 `AppShortcutsProvider`）
- **具體實作方案**：
  1. **註冊 URL Scheme (`macmini://`)**：
     - `macmini://live`：打開 App 並立即自動啟動 `coordinator.toggleLiveVoiceMode()`（若已在 Live 則保持聆聽）。
     - `macmini://new`：清空開啟新對話並啟動 Live 對談。
     - `macmini://dictate`：打開 App 並立即啟動左側 `🎙️` 語音轉文字。
  2. **內建 `AppShortcutsProvider`（免設定自動出現在 iOS「捷徑」與「動作按鈕」選單）**：
     - 提供「**開始與 Mac mini 語音對談**」捷徑（`openAppWhenRun = true`），使用者可在 iPhone「設定 ➔ 動作按鈕 ➔ 捷徑」直接綁定，按一下實體動作按鈕即 0 秒進入 Live 語音聊天！
- **驗收標準**：
  - 在 Safari 或捷徑執行 `macmini://live`，或按 iPhone 動作按鈕，App 立即跳出並直接處於藍色 `((|))` Live 聆聽狀態。

---

### 5.5 【體驗升級五】iOS 動態島 (Dynamic Island) 與鎖定畫面即時動態 (`ActivityKit`) (P2)
- **目標場景**：
  當使用者在開啟 `((|))` Live 連續語音模式後切去滑 LINE、看 Google Maps 導航或鎖定螢幕時，在 iPhone 頂部**動態島（Dynamic Island）**與**鎖定畫面置頂橫幅**即時顯示：
  - 左側：Gemini 四色星芒與當前狀態燈（🟢 聆聽中 / 🟡 思考中 / 🔵 朗讀中）。
  - 右側：即時滾動的辨識字幕或助理回覆摘要，以及一鍵「暫停 / 結束對談」按鈕。
- **施工注意**：
  - 需於 `ios/project.yml` 新增 `WidgetExtension` Target 並於主 App `Info.plist` 加入 `NSSupportsLiveActivities = YES`。

---

## 5. 📦 統一編譯、打包與發布標準流程

任何階段修改完畢後，一律透過專案根目錄的自動化腳本驗證與發布：

```bash
# 1. 若有修改後端 Python 檔案，先執行語法檢查
.venv/bin/python -m py_compile agent.py main.py tts_service.py

# 2. 一鍵重新生成 Xcode 專案、無簽名編譯驗證、打包 IPA 並雙軌同步至 SideStore 軟體源
./package_ios.sh "<本次更新說明>"
```
