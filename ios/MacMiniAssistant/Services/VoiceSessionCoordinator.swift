import Foundation
import SwiftUI
import Combine
import AVFoundation
import WidgetKit

@MainActor
public final class VoiceSessionCoordinator: ObservableObject {
    @Published public var state: SessionState = .idle
    @Published public var voiceInputMode: VoiceInputMode = .none
    @Published public var selectedModelFamily: ModelFamily = {
        let saved = UserDefaults.standard.string(forKey: "macmini_model_family") ?? "flash"
        return ModelFamily(rawValue: saved) ?? .flash
    }() {
        didSet {
            UserDefaults.standard.set(selectedModelFamily.rawValue, forKey: "macmini_model_family")
        }
    }
    @Published public var thinkingLevel: ThinkingLevel = .med

    // 車載藍牙 / 手機揚聲器 / 聽筒音訊路由狀態
    @Published public var currentAudioRouteName: String = "手機揚聲器"
    @Published public var currentAudioRouteIcon: String = "speaker.wave.3.fill"
    @Published public var isBluetoothOrCarConnected: Bool = false
    @Published public var isForcedSpeaker: Bool = true

    public var effectiveLevel: String {
        switch selectedModelFamily {
        case .flash:
            return thinkingLevel.rawValue
        case .argon:
            return "argon"
        case .claude:
            return "claude"
        }
    }
    @Published public var sttMode: STTRecognitionMode = {
        let saved = UserDefaults.standard.string(forKey: "macmini_stt_mode") ?? "hybrid"
        return STTRecognitionMode(rawValue: saved) ?? .hybrid
    }() {
        didSet {
            UserDefaults.standard.set(sttMode.rawValue, forKey: "macmini_stt_mode")
            vad.sttMode = sttMode
        }
    }
    @Published public var currentDb: Float = -60.0
    @Published public var silenceProgress: Float = 0.0
    @Published public var silenceElapsed: TimeInterval = 0.0
    @Published public var pauseSeconds: Int = {
        let saved = UserDefaults.standard.integer(forKey: "macmini_pause_seconds")
        return saved > 0 ? saved : 2
    }() {
        didSet {
            UserDefaults.standard.set(pauseSeconds, forKey: "macmini_pause_seconds")
            vad.silenceDuration = TimeInterval(pauseSeconds)
        }
    }
    @Published public var ttsSpeed: TTSSpeedOption = {
        let saved = UserDefaults.standard.string(forKey: "macmini_tts_speed_rate") ?? "+0%"
        return TTSSpeedOption(rawValue: saved) ?? .normal
    }() {
        didSet {
            UserDefaults.standard.set(ttsSpeed.rawValue, forKey: "macmini_tts_speed_rate")
            speechPlayer.speechRate = AVSpeechUtteranceDefaultSpeechRate * ttsSpeed.localRateMultiplier
        }
    }
    @Published public var earconSoundEnabled: Bool = {
        if UserDefaults.standard.object(forKey: "macmini_earcon_enabled") == nil {
            return true
        }
        return UserDefaults.standard.bool(forKey: "macmini_earcon_enabled")
    }() {
        didSet {
            UserDefaults.standard.set(earconSoundEnabled, forKey: "macmini_earcon_enabled")
        }
    }
    @Published public var pendingImageData: Data? = nil
    @Published public var messages: [ChatMessage] = []
    @Published public var sessionHistory: [ChatSession] = []
    @Published public var currentSessionId: UUID = UUID()
    @Published public var liveTranscript: String = ""
    @Published public var textInput: String = ""
    @Published public var currentStreamingText: String = ""
    @Published public var currentToolStatus: String? = nil
    @Published public var speakToolStatusEnabled: Bool = true
    @Published public var errorMessage: String? = nil

    // 伺服器健康診斷與 AGY 配額快照
    @Published public var isServerReachable: Bool = false
    @Published public var serverStatusText: String = "連線檢查中..."
    @Published public var showDebugHUD: Bool = false
    @Published public var quotaSnapshot: QuotaSnapshot? = nil
    @Published public var isRefreshingQuota: Bool = false
    @Published public var isCompactingContext: Bool = false
    @Published public var compactStatusMessage: String? = nil

    public let vad = AudioRecorderVAD()
    public let speechPlayer = NativeSpeechPlayer()
    public let apiClient = MacMiniAPIClient.shared

    private var activeStreamTask: Task<Void, Never>?
    private var healthCheckTimer: Timer?
    private var backgroundTaskID: UIBackgroundTaskIdentifier = .invalid
    private var silenceWarned = false
    private var shouldSpeakCurrentTurn = true
    private var dictationBaseText: String = ""

    private let selectionHaptic = UISelectionFeedbackGenerator()
    private let rigidHaptic = UIImpactFeedbackGenerator(style: .rigid)
    private let softHaptic = UIImpactFeedbackGenerator(style: .soft)
    private let notifyHaptic = UINotificationFeedbackGenerator()

    public var isLiveMode: Bool {
        voiceInputMode == .liveContinuous
    }

    public var isSingleDictationMode: Bool {
        voiceInputMode == .singleTurn
    }

    public init() {
        vad.silenceDuration = TimeInterval(pauseSeconds)
        vad.sttMode = sttMode
        speechPlayer.speechRate = AVSpeechUtteranceDefaultSpeechRate * ttsSpeed.localRateMultiplier
        loadSessionHistory()
        setupCallbacks()
        setupAudioNotifications()
        startHealthMonitoring()
    }

    private func setupAudioNotifications() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let userInfo = notification.userInfo,
                  let typeValue = userInfo[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }

            Task { @MainActor [weak self] in
                guard let self = self else { return }
                if type == .began {
                    self.speechPlayer.stopImmediately()
                } else if type == .ended {
                    if let optionsValue = userInfo[AVAudioSessionInterruptionOptionKey] as? UInt,
                       AVAudioSession.InterruptionOptions(rawValue: optionsValue).contains(.shouldResume) {
                        try? AVAudioSession.sharedInstance().setActive(true)
                        if self.voiceInputMode != .none, case .listening = self.state {
                            try? self.vad.startListening()
                        }
                    }
                }
            }
        }

        NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let userInfo = notification.userInfo,
                  let reasonValue = userInfo[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue) else { return }

            Task { @MainActor [weak self] in
                guard let self = self else { return }
                self.updateCurrentAudioRoute()
                if reason == .oldDeviceUnavailable {
                    self.speechPlayer.stopImmediately()
                }
            }
        }
        updateCurrentAudioRoute()

        NotificationCenter.default.addObserver(
            forName: UIApplication.willEnterForegroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                self.checkServerHealth()
                if self.voiceInputMode != .none, case .listening = self.state {
                    try? AVAudioSession.sharedInstance().setActive(true)
                    try? self.vad.startListening()
                }
            }
        }
    }

    private func setupCallbacks() {
        vad.onDbUpdate = { [weak self] db in
            guard let self = self else { return }
            self.currentDb = db
            if case .listening(let isSpeaking, _) = self.state {
                self.state = .listening(isSpeaking: isSpeaking, currentDb: db)
            }
        }

        vad.onSpeechStarted = { [weak self] in
            guard let self = self else { return }
            self.silenceProgress = 0.0
            self.silenceElapsed = 0.0
            self.silenceWarned = false
            self.state = .listening(isSpeaking: true, currentDb: self.currentDb)
            self.selectionHaptic.selectionChanged()
        }

        // 當助理思考或回答中，偵測到主人說「等一下」打斷 (Voice Barge-in)
        vad.onBargeInDetected = { [weak self] in
            guard let self = self else { return }
            self.speechPlayer.stopImmediately()
            self.activeStreamTask?.cancel()
            self.activeStreamTask = nil

            let lvl = self.effectiveLevel
            Task {
                await self.apiClient.cancelVoiceChat(level: lvl)
            }

            // 將被中途打斷的助理訊息標記為完成，保留目前已生成的文字
            if let idx = self.messages.lastIndex(where: { $0.role == .assistant && $0.status == .streaming }) {
                let currentText = self.messages[idx].content.trimmingCharacters(in: .whitespacesAndNewlines)
                if currentText.isEmpty {
                    self.messages[idx].content = "⏹️ 已中止思考"
                } else if !currentText.hasSuffix("⏹️") {
                    self.messages[idx].content += " ⏹️"
                }
                self.messages[idx].status = .completed
                self.syncCurrentSessionToHistory()
            }

            self.currentToolStatus = nil
            self.currentStreamingText = ""
            self.silenceProgress = 0.0
            self.silenceElapsed = 0.0
            self.silenceWarned = false
            self.state = .listening(isSpeaking: true, currentDb: self.currentDb)
            self.rigidHaptic.impactOccurred()
        }

        vad.onPartialTranscript = { [weak self] text in
            guard let self = self else { return }
            self.liveTranscript = text
            if self.voiceInputMode == .singleTurn {
                self.textInput = self.dictationBaseText.isEmpty ? text : "\(self.dictationBaseText) \(text)"
            }
            if case .listening(let isSpeaking, _) = self.state, !isSpeaking {
                self.state = .listening(isSpeaking: true, currentDb: self.currentDb)
            }
        }

        vad.onSilenceProgress = { [weak self] ratio, elapsed in
            guard let self = self else { return }
            self.silenceProgress = ratio
            self.silenceElapsed = elapsed
            if ratio >= 0.75 && !self.silenceWarned {
                self.silenceWarned = true
                self.rigidHaptic.impactOccurred()
            }
        }

        // 做法 A 主通道：iOS 原生 SFSpeechRecognizer 即時完成斷句
        vad.onTranscriptFinished = { [weak self] transcript in
            guard let self = self else { return }
            self.silenceProgress = 0.0
            self.silenceElapsed = 0.0
            self.silenceWarned = false
            self.liveTranscript = ""
            self.notifyHaptic.notificationOccurred(.success)

            if self.voiceInputMode == .singleTurn {
                // 🎙️ 左側麥克風：僅將 STT 文字放入輸入框，不自動送出
                let clean = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                if !clean.isEmpty {
                    self.textInput = self.dictationBaseText.isEmpty ? clean : "\(self.dictationBaseText) \(clean)"
                }
                self.voiceInputMode = .none
                self.state = .idle
                return
            }

            self.handleDirectTranscript(transcript)
        }

        // 備援通道：當 SFSpeechRecognizer 離線或未授權時，上傳音檔交給後端 Whisper
        vad.onSpeechFinished = { [weak self] audioURL in
            guard let self = self else { return }
            self.silenceProgress = 0.0
            self.silenceElapsed = 0.0
            self.silenceWarned = false
            self.liveTranscript = ""
            self.notifyHaptic.notificationOccurred(.success)
            self.handleSpeechFinished(audioURL: audioURL)
        }

        speechPlayer.onSentenceStarted = { [weak self] sentence in
            guard let self = self else { return }
            self.state = .speaking(currentSentence: sentence)
            self.softHaptic.impactOccurred()
            // 在 Live 模式中，助理一開口即自動啟動 Voice Barge-in 監聽，主人隨時發聲即可打斷
            if self.voiceInputMode == .liveContinuous && !self.vad.isBargeInMonitoring {
                self.vad.startBargeInMonitoring()
            }
        }

        speechPlayer.onQueueFinished = { [weak self] in
            guard let self = self else { return }
            self.handleQueueFinished()
        }
    }

    public func startHealthMonitoring() {
        checkServerHealth()
        healthCheckTimer?.invalidate()
        healthCheckTimer = Timer.scheduledTimer(withTimeInterval: 12.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.checkServerHealth()
            }
        }
    }

    public func checkServerHealth() {
        Task {
            let (ok, ms, detail) = await apiClient.checkHealth()
            self.isServerReachable = ok
            self.serverStatusText = ok ? "Mac mini (\(ms)ms)" : "離線: \(detail)"
            if ok {
                if let snap = await apiClient.fetchQuotaStatus(forceRefresh: false) {
                    self.quotaSnapshot = snap
                }
            }
            AppGroupStore.shared.updateSnapshot(
                isReachable: ok,
                latency: ms,
                message: self.serverStatusText,
                flashPercent: self.quotaSnapshot?.rolling5hRemainingPct,
                proPercent: self.quotaSnapshot?.weeklyRemainingPct
            )
            WidgetCenter.shared.reloadAllTimelines()
        }
    }

    public func refreshQuotaStatus(force: Bool = true) {
        guard !isRefreshingQuota else { return }
        isRefreshingQuota = true
        Task {
            defer { self.isRefreshingQuota = false }
            if let snap = await apiClient.fetchQuotaStatus(forceRefresh: force) {
                self.quotaSnapshot = snap
                AppGroupStore.shared.updateSnapshot(
                    isReachable: self.isServerReachable,
                    latency: 0,
                    message: self.serverStatusText,
                    flashPercent: snap.rolling5hRemainingPct,
                    proPercent: snap.weeklyRemainingPct
                )
                WidgetCenter.shared.reloadAllTimelines()
            }
        }
    }

    /// 觸發 ACP 原生 `/compact` 無損壓縮對話記憶
    public func compactBrainContext() {
        guard !isCompactingContext else { return }
        isCompactingContext = true
        compactStatusMessage = "正在執行 ACP /compact 無損壓縮..."
        rigidHaptic.impactOccurred()
        Task {
            defer { self.isCompactingContext = false }
            let (ok, msg) = await apiClient.compactContext()
            self.compactStatusMessage = (ok ? "✅ " : "⚠️ ") + msg
            if ok {
                self.notifyHaptic.notificationOccurred(.success)
            }
            if let snap = await apiClient.fetchQuotaStatus(forceRefresh: true) {
                self.quotaSnapshot = snap
            }
        }
    }

    /// 點擊快速回覆膠囊 (Quick-Reply Chip) 一鍵送出選項
    public func sendQuickReply(_ option: String) {
        let trimmed = option.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        selectionHaptic.selectionChanged()

        // 清除該則訊息上的快速回覆膠囊，避免重複點擊
        if let idx = messages.lastIndex(where: { $0.role == .assistant && !$0.quickReplies.isEmpty }) {
            messages[idx].quickReplies = []
        }

        if case .listening = state {
            vad.stopListening()
        }
        speechPlayer.stopImmediately()
        shouldSpeakCurrentTurn = (voiceInputMode == .liveContinuous)
        processTranscript(trimmed)
    }

    // MARK: - 語音與對話控制入口

    /// 切換 🎙️ 單次語音轉文字 (STT 填入對話框，不自動送出)
    public func toggleSingleTurnVoice() {
        if voiceInputMode == .singleTurn {
            // 再次點擊 🎙️：停止收音並保留目前對話框內的文字
            vad.stopListening()
            liveTranscript = ""
            silenceProgress = 0.0
            silenceElapsed = 0.0
            voiceInputMode = .none
            state = .idle
            return
        }

        speechPlayer.stopImmediately()
        dictationBaseText = textInput.trimmingCharacters(in: .whitespacesAndNewlines)
        voiceInputMode = .singleTurn
        vad.keepEngineAliveInBackground = false
        shouldSpeakCurrentTurn = false
        vad.silenceDuration = TimeInterval(pauseSeconds)
        startListeningWithPermissions()
    }

    /// 切換 ((|)) Gemini Live 即時連續語音對談模式
    public func toggleLiveVoiceMode() {
        if voiceInputMode == .liveContinuous {
            stopSession()
            return
        }

        speechPlayer.stopImmediately()
        voiceInputMode = .liveContinuous
        vad.keepEngineAliveInBackground = true
        speechPlayer.startBackgroundKeepAlive()
        shouldSpeakCurrentTurn = true
        vad.silenceDuration = TimeInterval(pauseSeconds)
        startListeningWithPermissions()
    }

    /// 是否正處於大腦思考、工具執行或語音朗讀中（供畫面顯示 ⏹️ 停止按鈕）
    public var isProcessingOrSpeaking: Bool {
        switch state {
        case .thinkingAndStreaming, .speaking, .transcribing:
            return true
        default:
            return false
        }
    }

    /// 手動或語音中途取消當前 Turn（立即通知後端 ACP session/cancel 強制煞車）
    public func cancelCurrentTurn() {
        activeStreamTask?.cancel()
        activeStreamTask = nil
        speechPlayer.stopImmediately()

        let lvl = effectiveLevel
        Task {
            await apiClient.cancelVoiceChat(level: lvl)
        }

        if let idx = messages.lastIndex(where: { $0.role == .assistant && $0.status == .streaming }) {
            let trimmed = messages[idx].content.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                messages[idx].content = "⏹️ 已中止思考"
            } else if !trimmed.hasSuffix("⏹️") {
                messages[idx].content += " ⏹️"
            }
            messages[idx].status = .completed
            syncCurrentSessionToHistory()
        }

        currentToolStatus = nil
        currentStreamingText = ""
        errorMessage = nil
        rigidHaptic.impactOccurred()

        if voiceInputMode == .liveContinuous {
            if earconSoundEnabled {
                speechPlayer.playListeningReadyEarcon()
            }
            state = .listening(isSpeaking: false, currentDb: currentDb)
            if vad.isBargeInMonitoring {
                vad.transitionFromBargeInToNormalListening()
            } else {
                try? vad.startListening()
            }
        } else {
            endBackgroundExecutionTask()
            speechPlayer.stopBackgroundKeepAlive()
            state = .idle
        }
    }

    /// 在 Live 模式中點擊聲波區域：說話中立即送出 / 思考或朗讀中立即中止 (Cancel / Barge-in)
    public func handleLiveActionTap() {
        switch state {
        case .idle, .paused:
            if voiceInputMode == .none {
                toggleLiveVoiceMode()
            } else {
                startListeningWithPermissions()
            }
        case .listening:
            if !liveTranscript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                vad.forceFinishSpeech()
            } else if voiceInputMode == .singleTurn {
                stopSession()
            } else {
                togglePause()
            }
        case .speaking, .thinkingAndStreaming, .transcribing:
            // 一鍵中止大腦思考與朗讀，在 Live 模式下立即重新開啟麥克風聽主人重講
            cancelCurrentTurn()
        }
    }

    /// 直接送出鍵盤或語音轉文字輸入框的訊息（或單獨送出附加圖片）
    public func sendTextMessage() {
        let trimmed = textInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty || pendingImageData != nil else { return }
        let finalQuery = trimmed.isEmpty ? "請幫我分析這張圖片" : trimmed
        textInput = ""
        dictationBaseText = ""
        liveTranscript = ""
        silenceProgress = 0.0
        silenceElapsed = 0.0

        if case .listening = state {
            vad.stopListening()
        }
        speechPlayer.stopImmediately()

        if voiceInputMode == .singleTurn {
            voiceInputMode = .none
        }
        shouldSpeakCurrentTurn = (voiceInputMode == .liveContinuous)
        processTranscript(finalQuery)
    }

    /// 開啟新對話（先保存目前對話至歷史，再清空畫面）
    public func startNewChat() {
        if !messages.isEmpty {
            syncCurrentSessionToHistory()
        }
        activeStreamTask?.cancel()
        activeStreamTask = nil
        speechPlayer.stopImmediately()
        vad.stopListening()
        currentSessionId = UUID()
        messages.removeAll()
        pendingImageData = nil
        liveTranscript = ""
        currentStreamingText = ""
        currentToolStatus = nil
        errorMessage = nil
        voiceInputMode = .none
        state = .idle
    }

    // MARK: - 歷史對話持久化管理 (Recent Conversation History)
    private var historyFileURL: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        return docs.appendingPathComponent("macmini_chat_sessions.json")
    }

    private func loadSessionHistory() {
        guard let data = try? Data(contentsOf: historyFileURL),
              let decoded = try? JSONDecoder().decode([ChatSession].self, from: data) else {
            return
        }
        sessionHistory = decoded.sorted { $0.updatedAt > $1.updatedAt }
    }

    private func saveSessionHistory() {
        guard let data = try? JSONEncoder().encode(sessionHistory) else { return }
        try? data.write(to: historyFileURL, options: .atomic)
    }

    public func syncCurrentSessionToHistory() {
        guard !messages.isEmpty else { return }
        let firstUserText = messages.first(where: { $0.role == .user })?.content.trimmingCharacters(in: .whitespacesAndNewlines) ?? "語音對話"
        let title = String(firstUserText.prefix(28))

        if let idx = sessionHistory.firstIndex(where: { $0.id == currentSessionId }) {
            sessionHistory[idx].title = title
            sessionHistory[idx].updatedAt = Date()
            sessionHistory[idx].messages = messages
        } else {
            let newSession = ChatSession(
                id: currentSessionId,
                title: title,
                updatedAt: Date(),
                messages: messages
            )
            sessionHistory.insert(newSession, at: 0)
        }

        // 依最後更新時間排序，最多保留最近 50 筆對話紀錄
        sessionHistory.sort { $0.updatedAt > $1.updatedAt }
        if sessionHistory.count > 50 {
            sessionHistory = Array(sessionHistory.prefix(50))
        }
        saveSessionHistory()
    }

    public func selectSession(_ session: ChatSession) {
        if !messages.isEmpty {
            syncCurrentSessionToHistory()
        }
        activeStreamTask?.cancel()
        activeStreamTask = nil
        speechPlayer.stopImmediately()
        currentSessionId = session.id
        messages = session.messages
        errorMessage = nil
    }

    public func deleteSession(_ session: ChatSession) {
        sessionHistory.removeAll { $0.id == session.id }
        saveSessionHistory()
        if currentSessionId == session.id {
            currentSessionId = UUID()
            messages.removeAll()
        }
    }

    public func clearAllHistory() {
        sessionHistory.removeAll()
        saveSessionHistory()
        currentSessionId = UUID()
        messages.removeAll()
    }

    public func startSession() {
        if voiceInputMode == .none {
            voiceInputMode = .liveContinuous
            vad.keepEngineAliveInBackground = true
            shouldSpeakCurrentTurn = true
            vad.silenceDuration = TimeInterval(pauseSeconds)
        }
        startListeningWithPermissions()
    }

    private func startListeningWithPermissions() {
        AudioRecorderVAD.requestPermissions { [weak self] micGranted, _ in
            Task { @MainActor in
                self?.handlePermissionResult(micGranted)
            }
        }
    }

    private func handlePermissionResult(_ granted: Bool) {
        guard granted else {
            errorMessage = "請至 iPhone 設定允許麥克風與語音辨識權限"
            state = .idle
            voiceInputMode = .none
            return
        }
        do {
            liveTranscript = ""
            try vad.startListening()
            state = .listening(isSpeaking: false, currentDb: -60.0)
            errorMessage = nil
        } catch {
            errorMessage = "無法啟動收音: \(error.localizedDescription)"
            state = .idle
            voiceInputMode = .none
        }
    }

    public func stopSession() {
        activeStreamTask?.cancel()
        activeStreamTask = nil
        endBackgroundExecutionTask()
        vad.keepEngineAliveInBackground = false
        vad.stopListening()
        speechPlayer.stopBackgroundKeepAlive()
        speechPlayer.stopImmediately()
        liveTranscript = ""
        silenceProgress = 0.0
        silenceElapsed = 0.0
        voiceInputMode = .none
        state = .idle
    }

    private func beginBackgroundExecutionTask() {
        endBackgroundExecutionTask()
        backgroundTaskID = UIApplication.shared.beginBackgroundTask(withName: "MacMiniVoiceStream") { [weak self] in
            Task { @MainActor in
                self?.endBackgroundExecutionTask()
            }
        }
    }

    private func endBackgroundExecutionTask() {
        if backgroundTaskID != .invalid {
            UIApplication.shared.endBackgroundTask(backgroundTaskID)
            backgroundTaskID = .invalid
        }
    }

    public func togglePause() {
        if case .paused = state {
            startListeningWithPermissions()
        } else {
            vad.stopListening()
            liveTranscript = ""
            state = .paused
        }
    }

    // 純停止與禮貌應答詞（不觸發 LLM 回覆，保持安靜聆聽）
    private let pureStopWords: Set<String> = [
        "等一下", "等下", "停", "停止", "閉嘴", "不用了", "夠了", "暫停",
        "好", "好的", "知道了", "好知道了", "了解", "謝謝", "好謝謝"
    ]

    // 自然口語掛斷詞（自動關閉 ((|)) Live 連續語音模式退回待機）
    private let hangUpPhrases: [String] = [
        "沒事了", "先這樣", "結束對話", "結束對談", "關閉語音", "謝謝掰掰", "謝謝拜拜", "再見", "掰掰", "拜拜"
    ]

    private func isHangUpCommand(_ stripped: String) -> Bool {
        guard !stripped.isEmpty else { return false }
        if hangUpPhrases.contains(stripped) {
            return true
        }
        // 例如「好先這樣」、「好沒事了」、「好的掰掰」（短句結尾為掛斷詞）
        if stripped.count <= 8 {
            return hangUpPhrases.contains(where: { stripped.hasSuffix($0) })
        }
        return false
    }

    private func handleDirectTranscript(_ text: String) {
        let cleanTranscript = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let strippedPunctuation = cleanTranscript
            .components(separatedBy: .punctuationCharacters.union(.symbols).union(.whitespacesAndNewlines))
            .joined()

        guard !cleanTranscript.isEmpty else {
            resumeOrFinishAfterEmptyInput()
            return
        }

        // 1. 檢查是否為口語自動掛斷指令（如「沒事了」、「先這樣」、「掰掰」）
        if isHangUpCommand(strippedPunctuation) {
            if earconSoundEnabled {
                speechPlayer.playSessionEndEarcon()
            }
            stopSession()
            return
        }

        // 2. 檢查是否為純打斷或禮貌應答詞（如「等一下」、「好，知道了」）
        if pureStopWords.contains(strippedPunctuation) && pendingImageData == nil {
            resumeOrFinishAfterEmptyInput()
            return
        }

        if earconSoundEnabled && voiceInputMode == .liveContinuous {
            speechPlayer.playSpeechSentEarcon()
        }
        errorMessage = nil
        processTranscript(cleanTranscript)
    }

    private func handleSpeechFinished(audioURL: URL) {
        let wasSingleTurn = (voiceInputMode == .singleTurn)
        state = .transcribing

        Task {
            defer {
                try? FileManager.default.removeItem(at: audioURL)
            }

            do {
                let transcript = try await apiClient.uploadAudioForTranscription(fileURL: audioURL)
                let cleanTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)

                guard cleanTranscript.count >= 1 else {
                    resumeOrFinishAfterEmptyInput()
                    return
                }

                self.errorMessage = nil
                if wasSingleTurn {
                    self.textInput = self.dictationBaseText.isEmpty ? cleanTranscript : "\(self.dictationBaseText) \(cleanTranscript)"
                    self.voiceInputMode = .none
                    self.state = .idle
                } else {
                    self.processTranscript(cleanTranscript)
                }
            } catch {
                self.errorMessage = "轉錄失敗: \(error.localizedDescription)"
                resumeOrFinishAfterEmptyInput()
            }
        }
    }

    private func resumeOrFinishAfterEmptyInput() {
        if voiceInputMode == .liveContinuous {
            try? vad.startListening()
            state = .listening(isSpeaking: false, currentDb: currentDb)
        } else {
            voiceInputMode = .none
            state = .idle
        }
    }

    private func processTranscript(_ text: String) {
        let attachedImg = pendingImageData
        pendingImageData = nil
        let imageBase64 = attachedImg?.base64EncodedString()

        let userMessage = ChatMessage(role: .user, content: text, attachedImageData: attachedImg)
        messages.append(userMessage)

        currentStreamingText = ""
        currentToolStatus = nil
        speechPlayer.resetForNewTurn()
        state = .thinkingAndStreaming(toolStatus: nil)
        if voiceInputMode == .liveContinuous && !vad.isBargeInMonitoring {
            vad.startBargeInMonitoring()
        }

        // 若為 🎙️ 單次語音輸入，送出後即可退回一般模式（回覆完不重開麥克風）
        if voiceInputMode == .singleTurn {
            voiceInputMode = .none
        }

        let assistantMessageId = UUID()
        let assistantMessage = ChatMessage(id: assistantMessageId, role: .assistant, content: "", status: .streaming)
        messages.append(assistantMessage)
        syncCurrentSessionToHistory()

        let fullQuery = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let speakThisTurn = shouldSpeakCurrentTurn
        let clientTag = speakThisTurn ? "ios-voice" : "telegram"

        activeStreamTask?.cancel()
        beginBackgroundExecutionTask()
        speechPlayer.startBackgroundKeepAlive()

        activeStreamTask = Task {
            defer {
                Task { @MainActor in
                    self.endBackgroundExecutionTask()
                    if self.voiceInputMode != .liveContinuous {
                        self.speechPlayer.stopBackgroundKeepAlive()
                    }
                }
            }
            let stream = apiClient.streamVoiceChat(
                query: fullQuery,
                client: clientTag,
                level: effectiveLevel,
                imageBase64: imageBase64
            )

            do {
                for try await event in stream {
                    guard !Task.isCancelled else { break }

                    switch event {
                    case .thoughtDelta(let delta):
                        if let idx = self.messages.firstIndex(where: { $0.id == assistantMessageId }) {
                            self.messages[idx].thoughtProcess += delta
                        }

                    case .status(let statusText, let tts):
                        self.currentToolStatus = statusText
                        self.state = .thinkingAndStreaming(toolStatus: statusText)

                        if let idx = self.messages.firstIndex(where: { $0.id == assistantMessageId }) {
                            self.messages[idx].toolHistory.append(statusText)
                        }

                        if speakThisTurn, self.speakToolStatusEnabled, let speakText = tts, !speakText.isEmpty {
                            self.speechPlayer.speakStatus(speakText)
                        }

                    case .textDelta(let delta):
                        self.currentStreamingText += delta
                        if let idx = self.messages.firstIndex(where: { $0.id == assistantMessageId }) {
                            self.messages[idx].content += delta
                        }

                    case .ttsSentence(let sentence):
                        self.currentToolStatus = nil
                        if speakThisTurn {
                            if case .thinkingAndStreaming = self.state {
                                self.state = .speaking(currentSentence: sentence)
                            }
                            self.speechPlayer.enqueueSentence(sentence)
                        }

                    case .audioChunk(let seq, let sentence, let audioData):
                        self.currentToolStatus = nil
                        if speakThisTurn {
                            if case .thinkingAndStreaming = self.state {
                                self.state = .speaking(currentSentence: sentence)
                            }
                            self.speechPlayer.enqueueAudioChunk(seq: seq, sentence: sentence, audioData: audioData)
                        }

                    case .done(let fullReply, _, _, let reportUrl, let quickReplies):
                        self.currentToolStatus = nil
                        if let idx = self.messages.firstIndex(where: { $0.id == assistantMessageId }) {
                            self.messages[idx].content = fullReply.isEmpty ? self.currentStreamingText : fullReply
                            self.messages[idx].status = .completed
                            self.messages[idx].reportUrl = reportUrl.isEmpty ? nil : reportUrl
                            self.messages[idx].quickReplies = quickReplies
                        }
                        self.syncCurrentSessionToHistory()
                        self.refreshQuotaStatus(force: false)
                        if speakThisTurn {
                            self.speechPlayer.markGenerationFinished()
                        } else {
                            self.handleQueueFinished()
                        }

                    case .error(let msg):
                        print("Voice stream error: \(msg)")
                        self.errorMessage = "助理回應異常: \(msg)"
                        if let idx = self.messages.firstIndex(where: { $0.id == assistantMessageId }) {
                            self.messages[idx].status = .error
                        }
                        self.syncCurrentSessionToHistory()
                        self.refreshQuotaStatus(force: false)
                        if speakThisTurn {
                            self.speechPlayer.handleStreamInterruption(currentText: self.currentStreamingText)
                        } else {
                            self.handleQueueFinished()
                        }
                    }
                }
            } catch {
                if Task.isCancelled || error is CancellationError {
                    return
                }
                self.errorMessage = "串流中斷: \(error.localizedDescription)"
                if let idx = self.messages.firstIndex(where: { $0.id == assistantMessageId }) {
                    self.messages[idx].status = .error
                }
                if speakThisTurn {
                    self.speechPlayer.handleStreamInterruption(currentText: self.currentStreamingText)
                } else {
                    self.handleQueueFinished()
                }
            }
        }
    }

    /// 手動朗讀或停止朗讀特定訊息氣泡
    public func toggleSpeakMessage(_ message: ChatMessage) {
        if case .speaking = state {
            speechPlayer.stopImmediately()
            state = (voiceInputMode == .liveContinuous) ? .listening(isSpeaking: false, currentDb: currentDb) : .idle
            return
        }
        let clean = message.content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        speechPlayer.resetForNewTurn()
        state = .speaking(currentSentence: clean)
        speechPlayer.enqueueSentence(clean)
        speechPlayer.markGenerationFinished()
    }

    private func handleQueueFinished() {
        currentStreamingText = ""
        currentToolStatus = nil

        if voiceInputMode == .liveContinuous {
            // Live 連續語音模式：助理講完話後，播放清脆「叮」提示音並無縫切換為正常收音等待下一句！
            if earconSoundEnabled {
                speechPlayer.playListeningReadyEarcon()
            }
            state = .listening(isSpeaking: false, currentDb: currentDb)
            if vad.isBargeInMonitoring {
                vad.transitionFromBargeInToNormalListening()
            } else {
                try? vad.startListening()
            }
        } else {
            vad.stopListening()
            state = .idle
        }
    }

    // 相容舊手勢呼叫
    public func handleScreenTap() {
        handleLiveActionTap()
    }

    public func handleLongPressStart() {
        if case .listening = state {
            vad.forceFinishSpeech()
        }
    }

    public func handleLongPressEnd() {
        if case .listening = state {
            vad.forceFinishSpeech()
        }
    }

    // MARK: - 音訊輸出路由控制 (車載藍牙 / 揚聲器 / 聽筒)
    public func updateCurrentAudioRoute() {
        let session = AVAudioSession.sharedInstance()
        let outputs = session.currentRoute.outputs
        if let output = outputs.first {
            switch output.portType {
            case .bluetoothHFP, .bluetoothA2DP, .bluetoothLE:
                currentAudioRouteName = output.portName.isEmpty ? "藍牙音訊" : output.portName
                currentAudioRouteIcon = "beats.headphones"
                isBluetoothOrCarConnected = true
                isForcedSpeaker = false
            case .carAudio:
                currentAudioRouteName = output.portName.isEmpty ? "車載音訊" : output.portName
                currentAudioRouteIcon = "car.fill"
                isBluetoothOrCarConnected = true
                isForcedSpeaker = false
            case .airPlay:
                currentAudioRouteName = output.portName.isEmpty ? "AirPlay" : output.portName
                currentAudioRouteIcon = "airplayaudio"
                isBluetoothOrCarConnected = true
                isForcedSpeaker = false
            case .builtInSpeaker:
                currentAudioRouteName = "手機揚聲器"
                currentAudioRouteIcon = "speaker.wave.3.fill"
                isBluetoothOrCarConnected = false
                isForcedSpeaker = true
            case .builtInReceiver:
                currentAudioRouteName = "手機聽筒"
                currentAudioRouteIcon = "ear"
                isBluetoothOrCarConnected = false
                isForcedSpeaker = false
            case .headphones:
                currentAudioRouteName = "有線耳機"
                currentAudioRouteIcon = "headphones"
                isBluetoothOrCarConnected = true
                isForcedSpeaker = false
            default:
                currentAudioRouteName = output.portName
                currentAudioRouteIcon = "speaker.wave.2"
                isBluetoothOrCarConnected = false
            }
        }
    }

    public func setAudioRoute(forceSpeaker: Bool) {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .voiceChat,
                options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP, .duckOthers]
            )
            if forceSpeaker {
                try session.overrideOutputAudioPort(.speaker)
                isForcedSpeaker = true
            } else {
                try session.overrideOutputAudioPort(.none)
                isForcedSpeaker = false
            }
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            updateCurrentAudioRoute()
        } catch {
            print("[VoiceSessionCoordinator] setAudioRoute error: \(error)")
        }
    }

    public func setAudioRouteToReceiver() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .voiceChat,
                options: [.allowBluetoothHFP, .allowBluetoothA2DP]
            )
            try session.overrideOutputAudioPort(.none)
            isForcedSpeaker = false
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            updateCurrentAudioRoute()
        } catch {
            print("[VoiceSessionCoordinator] setAudioRouteToReceiver error: \(error)")
        }
    }
}
