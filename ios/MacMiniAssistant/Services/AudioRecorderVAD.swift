import Foundation
import AVFoundation
import Speech

public final class AudioRecorderVAD: NSObject {
    private let audioEngine = AVAudioEngine()
    private var audioFile: AVAudioFile?
    private var currentRecordingURL: URL?
    private var recordingAudioFormat: AVAudioFormat?

    // iOS 原生即時語音辨識 (SFSpeechRecognizer)
    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-TW"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var recognitionStreamStartTime: Date = Date()

    // 專屬中英技術與生活熱詞注入
    private let contextualVocabulary: [String] = [
        "等一下", "Gemini", "Mac mini", "HomePod", "Apple Watch", "Tailscale",
        "FastAPI", "SwiftUI", "Python", "main.py", "agent.py", "agy",
        "SideStore", "Coupang", "酷澎", "Home Assistant", "Telegram",
        "Whisper", "GitHub", "Google Calendar", "Google Sheets",
        "Xcode", "API", "Token", "Subagent", "TaskManager", "Chrome",
        "冷氣", "新風機", "待辦事項", "待買清單"
    ]

    // 觸發語音打斷的專屬關鍵詞（包含「等一下」才打斷助理說話）
    private let bargeInKeywords: [String] = ["等一下", "等下"]

    // 400ms 預錄環形記憶體快取
    private var preRollBuffers: [AVAudioPCMBuffer] = []
    private let maxPreRollCount: Int = 18

    // 靈敏度與動態底噪門檻
    public var speechStartThreshold: Float = -42.0
    public var silenceThreshold: Float = -46.0
    public var silenceDuration: TimeInterval = 2.0
    public var ambientNoiseFloor: Float = -50.0
    private var smoothedDb: Float = -50.0

    private var consecutiveSpeechFrames: Int = 0
    private let maxRecordingDuration: TimeInterval = 60.0

    public private(set) var isRecording = false
    public private(set) var isSpeaking = false
    /// 是否正處於「助理回答中，監聽主人是否說出『等一下』打斷」狀態
    public private(set) var isBargeInMonitoring = false
    /// 是否正處於「等待後端思考中（保持 AVAudioEngine 不中斷以支援背景/鎖屏常駐）」
    public private(set) var isWaitingForAssistant = false
    public private(set) var currentTranscript: String = ""

    private var lastTranscriptChangeTime: Date?
    private var lastSpeechTime: Date?
    private var recordingStartTime: Date?
    private var endpointTimer: DispatchSourceTimer?
    private var hasFinishedCurrentTurn = false
    /// 是否保持引擎常駐（Live 連續模式下為 true，確保鎖屏/背景切換時 iOS 不會凍結音訊 Session）
    public var keepEngineAliveInBackground: Bool = false

    // Callbacks
    public var onDbUpdate: ((Float) -> Void)?
    public var onSpeechStarted: (() -> Void)?
    /// 當助理正在說話，偵測到主人說出「等一下」時立即觸發打斷
    public var onBargeInDetected: (() -> Void)?
    public var onPartialTranscript: ((String) -> Void)?
    public var onSilenceProgress: ((Float, TimeInterval) -> Void)?
    /// 做法 A 主通道：iOS 原生即時辨識完成，0 延遲直接送出文字
    public var onTranscriptFinished: ((String) -> Void)?
    /// 備援通道：若 SFSpeechRecognizer 離線或未授權，回傳音檔交由後端 Whisper 轉錄
    public var onSpeechFinished: ((URL) -> Void)?

    private let queue = DispatchQueue(label: "com.assistant.macminiassistant.vad", qos: .userInitiated)

    public override init() {
        super.init()
    }

    /// 請求麥克風與 iOS 語音辨識雙權限
    public static func requestPermissions(completion: @escaping (Bool, Bool) -> Void) {
        let requestMic: (@escaping (Bool) -> Void) -> Void = { cb in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { granted in
                    cb(granted)
                }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { granted in
                    cb(granted)
                }
            }
        }

        requestMic { micGranted in
            guard micGranted else {
                DispatchQueue.main.async { completion(false, false) }
                return
            }
            SFSpeechRecognizer.requestAuthorization { status in
                let speechGranted = (status == .authorized)
                DispatchQueue.main.async {
                    completion(true, speechGranted)
                }
            }
        }
    }

    public func startListening(bargeInMode: Bool = false) throws {
        // 若引擎已在背景常駐運行中，直接無縫重置狀態與語音流，不中斷 AVAudioEngine！
        if audioEngine.isRunning {
            queue.async { [weak self] in
                guard let self = self else { return }
                self.ambientNoiseFloor = -50.0
                self.smoothedDb = -50.0
                self.preRollBuffers.removeAll()
                self.audioFile = nil
                self.currentRecordingURL = nil
                self.currentTranscript = ""
                self.lastTranscriptChangeTime = nil
                self.lastSpeechTime = nil
                self.recordingStartTime = Date()
                self.isSpeaking = false
                self.isWaitingForAssistant = false
                self.isBargeInMonitoring = bargeInMode
                self.consecutiveSpeechFrames = 0
                self.hasFinishedCurrentTurn = false
                self.isRecording = true
                self.startSpeechRecognitionStream()
            }
            startEndpointCheckTimer()
            return
        }

        stopListening()

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(
            .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP, .duckOthers]
        )
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let inputNode = audioEngine.inputNode
        try? inputNode.setVoiceProcessingEnabled(true)

        var recordingFormat = inputNode.inputFormat(forBus: 0)
        if recordingFormat.sampleRate <= 0 || recordingFormat.channelCount <= 0 {
            recordingFormat = inputNode.outputFormat(forBus: 0)
        }
        if recordingFormat.sampleRate <= 0 || recordingFormat.channelCount <= 0 {
            recordingFormat = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 1)!
        }
        self.recordingAudioFormat = recordingFormat

        ambientNoiseFloor = -50.0
        smoothedDb = -50.0
        preRollBuffers.removeAll()
        audioFile = nil
        currentRecordingURL = nil
        currentTranscript = ""
        lastTranscriptChangeTime = nil
        lastSpeechTime = nil
        recordingStartTime = Date()
        isSpeaking = false
        isWaitingForAssistant = false
        isBargeInMonitoring = bargeInMode
        consecutiveSpeechFrames = 0
        hasFinishedCurrentTurn = false

        startSpeechRecognitionStream()

        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            guard let self = self else { return }
            if !self.isWaitingForAssistant {
                self.recognitionRequest?.append(buffer)
            }
            self.processAudioBuffer(buffer: buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()
        isRecording = true

        startEndpointCheckTimer()
    }

    /// 當助理開始說話時，切換為「關鍵詞語音打斷監聽模式（偵測『等一下』）」
    public func startBargeInMonitoring() {
        if !audioEngine.isRunning {
            try? startListening(bargeInMode: true)
            return
        }
        queue.async { [weak self] in
            guard let self = self else { return }
            self.isWaitingForAssistant = false
            self.isBargeInMonitoring = true
            self.isSpeaking = false
            self.hasFinishedCurrentTurn = false
            self.currentTranscript = ""
            self.lastTranscriptChangeTime = nil
            self.lastSpeechTime = nil
            self.startSpeechRecognitionStream()
        }
    }

    /// 當助理自然講完話未被中途打斷時，無縫切換為正常收音模式
    public func transitionFromBargeInToNormalListening() {
        if !audioEngine.isRunning {
            try? startListening(bargeInMode: false)
            return
        }
        queue.async { [weak self] in
            guard let self = self else { return }
            self.isWaitingForAssistant = false
            self.isBargeInMonitoring = false
            self.isSpeaking = false
            self.ambientNoiseFloor = -50.0
            self.preRollBuffers.removeAll()
            self.currentTranscript = ""
            self.lastTranscriptChangeTime = nil
            self.lastSpeechTime = nil
            self.recordingStartTime = Date()
            self.hasFinishedCurrentTurn = false
            self.startSpeechRecognitionStream()
        }
    }

    private func startSpeechRecognitionStream() {
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil

        guard let recognizer = speechRecognizer, recognizer.isAvailable,
              SFSpeechRecognizer.authorizationStatus() == .authorized else {
            return
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if #available(iOS 16.0, *) {
            request.addsPunctuation = true
        }
        request.contextualStrings = contextualVocabulary
        self.recognitionRequest = request
        self.recognitionStreamStartTime = Date()

        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self = self else { return }

            if let result = result {
                let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty {
                    self.queue.async {
                        guard self.isRecording, !self.hasFinishedCurrentTurn, !self.isWaitingForAssistant else { return }
                        let now = Date()

                        // 若目前助理正在說話 (isBargeInMonitoring == true)：
                        // 必須先檢查辨識出的文字是否包含「等一下」，包含才觸發打斷！
                        if self.isBargeInMonitoring {
                            let matchedKeyword = self.bargeInKeywords.first(where: { text.contains($0) })
                            guard let keyword = matchedKeyword else {
                                // 未包含「等一下」，忽略不打斷助理
                                return
                            }

                            // 偵測到「等一下」！立即觸發打斷並轉入正式收音
                            self.isBargeInMonitoring = false
                            self.isSpeaking = true
                            self.recordingStartTime = now
                            self.lastTranscriptChangeTime = now
                            self.lastSpeechTime = now

                            // 擷取「等一下」起算的句子
                            let relevantText: String
                            if let range = text.range(of: keyword) {
                                relevantText = String(text[range.lowerBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
                            } else {
                                relevantText = text
                            }
                            self.currentTranscript = relevantText

                            DispatchQueue.main.async {
                                self.onBargeInDetected?()
                                self.onSpeechStarted?()
                                self.onPartialTranscript?(relevantText)
                            }
                            return
                        }

                        // 一般聆聽階段
                        if !self.isSpeaking {
                            self.isSpeaking = true
                            self.recordingStartTime = now
                            DispatchQueue.main.async {
                                self.onSpeechStarted?()
                            }
                        }

                        if text != self.currentTranscript {
                            self.currentTranscript = text
                            self.lastTranscriptChangeTime = now
                            self.lastSpeechTime = now
                            DispatchQueue.main.async {
                                self.onPartialTranscript?(text)
                            }
                        }

                        if result.isFinal {
                            self.finishCurrentRecording()
                        }
                    }
                }
            }

            if error != nil && self.isRecording {
                self.queue.async {
                    guard !self.isWaitingForAssistant else { return }
                    if !self.currentTranscript.isEmpty && !self.hasFinishedCurrentTurn && !self.isBargeInMonitoring {
                        self.finishCurrentRecording()
                    } else if !self.isSpeaking {
                        // 自動續約 SFSpeechRecognizer (突破 Apple 單次 60 秒限制，確保背景長時待機不掉線)
                        self.startSpeechRecognitionStream()
                    }
                }
            }
        }
    }

    private func startEndpointCheckTimer() {
        endpointTimer?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 0.1, repeating: 0.1)
        timer.setEventHandler { [weak self] in
            self?.checkEndpointingStatus()
        }
        timer.resume()
        endpointTimer = timer
    }

    private func checkEndpointingStatus() {
        guard isRecording, !hasFinishedCurrentTurn, !isWaitingForAssistant else { return }
        let now = Date()

        // 背景長時待機自動續約：Apple 限制單一 SFSpeechRecognitionTask 最長 60 秒
        // 當未在說話且已持續超過 45 秒時，無縫重建 RecognitionRequest
        if !isSpeaking && now.timeIntervalSince(recognitionStreamStartTime) >= 45.0 {
            startSpeechRecognitionStream()
            return
        }

        guard !isBargeInMonitoring, isSpeaking else { return }
        let totalDuration = recordingStartTime.map { now.timeIntervalSince($0) } ?? 0.0

        // 1. 優先以 SFSpeechRecognizer「文字停止跳動時間」判定斷句
        if !currentTranscript.isEmpty, let lastTextTime = lastTranscriptChangeTime {
            let elapsedSinceText = now.timeIntervalSince(lastTextTime)
            let ratio = Float(min(1.0, elapsedSinceText / silenceDuration))

            DispatchQueue.main.async { [weak self] in
                self?.onSilenceProgress?(ratio, elapsedSinceText)
            }

            if elapsedSinceText >= silenceDuration || totalDuration >= maxRecordingDuration {
                finishCurrentRecording()
            }
            return
        }

        // 2. 備援：若 SFSpeechRecognizer 離線，使用平滑音量倒數
        if let lastTime = lastSpeechTime {
            let elapsedSilence = now.timeIntervalSince(lastTime)
            let ratio = Float(min(1.0, elapsedSilence / silenceDuration))

            DispatchQueue.main.async { [weak self] in
                self?.onSilenceProgress?(ratio, elapsedSilence)
            }

            if elapsedSilence >= silenceDuration || totalDuration >= maxRecordingDuration {
                finishCurrentRecording()
            }
        }
    }

    public func stopListening() {
        endpointTimer?.cancel()
        endpointTimer = nil

        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil

        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        isRecording = false
        isSpeaking = false
        isBargeInMonitoring = false
        isWaitingForAssistant = false
        consecutiveSpeechFrames = 0
        preRollBuffers.removeAll()
        audioFile = nil
    }

    private func prepareNewAudioFile(format: AVAudioFormat) throws {
        let tempDir = FileManager.default.temporaryDirectory
        let fileName = "voice_\(UUID().uuidString).caf"
        let fileURL = tempDir.appendingPathComponent(fileName)

        self.audioFile = try AVAudioFile(forWriting: fileURL, settings: format.settings)
        self.currentRecordingURL = fileURL
        self.recordingStartTime = Date()
    }

    private func copyPCMBuffer(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else { return nil }
        copy.frameLength = buffer.frameLength
        guard let src = buffer.floatChannelData, let dst = copy.floatChannelData else { return nil }
        for ch in 0..<Int(buffer.format.channelCount) {
            memcpy(dst[ch], src[ch], Int(buffer.frameLength) * MemoryLayout<Float>.size)
        }
        return copy
    }

    private func processAudioBuffer(buffer: AVAudioPCMBuffer) {
        guard let channelData = buffer.floatChannelData?[0] else { return }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else { return }

        var sum: Float = 0.0
        let ptr = channelData
        for i in 0..<frameLength {
            let sample = ptr[i]
            sum += sample * sample
        }
        let rms = sqrt(sum / Float(frameLength))
        let db = 20.0 * log10(max(rms, 0.00001))

        DispatchQueue.main.async { [weak self] in
            self?.onDbUpdate?(db)
        }

        queue.async { [weak self] in
            guard let self = self, self.isRecording, !self.hasFinishedCurrentTurn, !self.isWaitingForAssistant else { return }
            let now = Date()

            self.smoothedDb = self.smoothedDb * 0.65 + db * 0.35

            // 若處於助理回答中 (isBargeInMonitoring)，完全交由 SFSpeechRecognizer 檢查是否出現「等一下」，不以音量盲目打斷！
            if self.isBargeInMonitoring {
                return
            }

            // 一般收音備援 (供 SFSpeechRecognizer 尚未出字或離線時記錄音檔)
            if !self.isSpeaking {
                if let copied = self.copyPCMBuffer(buffer) {
                    self.preRollBuffers.append(copied)
                    if self.preRollBuffers.count > self.maxPreRollCount {
                        self.preRollBuffers.removeFirst()
                    }
                }

                if db < -40.0 {
                    self.ambientNoiseFloor = min(-42.0, self.ambientNoiseFloor * 0.99 + db * 0.01)
                }

                let effectiveStart = max(self.speechStartThreshold, self.ambientNoiseFloor + 5.0)

                if self.smoothedDb >= effectiveStart {
                    self.consecutiveSpeechFrames += 1
                    if self.consecutiveSpeechFrames >= 3 {
                        self.isSpeaking = true
                        self.lastSpeechTime = now
                        self.recordingStartTime = now

                        if let format = self.recordingAudioFormat {
                            try? self.prepareNewAudioFile(format: format)
                        }
                        if let file = self.audioFile {
                            for preBuf in self.preRollBuffers {
                                try? file.write(from: preBuf)
                            }
                            self.preRollBuffers.removeAll()
                            try? file.write(from: buffer)
                        }

                        DispatchQueue.main.async {
                            self.onSpeechStarted?()
                        }
                    }
                } else {
                    self.consecutiveSpeechFrames = 0
                }
            } else {
                if let file = self.audioFile {
                    try? file.write(from: buffer)
                }

                if self.currentTranscript.isEmpty {
                    let effectiveActive = max(self.speechStartThreshold - 1.5, self.ambientNoiseFloor + 5.0)
                    if self.smoothedDb >= effectiveActive {
                        self.lastSpeechTime = now
                    }
                }
            }
        }
    }

    public func forceFinishSpeech() {
        queue.async { [weak self] in
            guard let self = self, self.isRecording, !self.hasFinishedCurrentTurn else { return }
            self.finishCurrentRecording()
        }
    }

    private func finishCurrentRecording() {
        guard !hasFinishedCurrentTurn else { return }
        hasFinishedCurrentTurn = true

        let finalTranscript = currentTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        let url = currentRecordingURL
        currentRecordingURL = nil
        audioFile = nil

        if keepEngineAliveInBackground {
            // Live 連續對談模式：保持 AVAudioEngine 持續運轉（防止鎖屏或切換到背景 App 時被 iOS 系統中斷音訊權限）
            isWaitingForAssistant = true
            isSpeaking = false
            recognitionRequest?.endAudio()
            recognitionRequest = nil
            recognitionTask?.cancel()
            recognitionTask = nil
        } else {
            stopListening()
        }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.onSilenceProgress?(0.0, 0.0)

            if !finalTranscript.isEmpty {
                if let fileURL = url {
                    try? FileManager.default.removeItem(at: fileURL)
                }
                self.onTranscriptFinished?(finalTranscript)
                return
            }

            if let fileURL = url {
                self.onSpeechFinished?(fileURL)
            }
        }
    }
}
