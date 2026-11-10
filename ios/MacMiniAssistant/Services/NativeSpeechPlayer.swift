import Foundation
import AVFoundation
import AudioToolbox

private struct AudioChunkItem {
    let seq: Int
    let sentence: String
    let audioData: Data
}

public final class NativeSpeechPlayer: NSObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate, @unchecked Sendable {
    private let synthesizer = AVSpeechSynthesizer()
    private var sentenceQueue: [String] = []
    private var audioQueue: [AudioChunkItem] = []
    private var currentAudioPlayer: AVAudioPlayer?
    private var keepAlivePlayer: AVAudioPlayer?
    private var earconPlayer: AVAudioPlayer?

    private var isGenerating = false
    private var isPlaying = false
    private var hasReceivedAudioChunks = false
    private var currentlyPlayingItem: AudioChunkItem?
    private var waitFallbackWorkItem: DispatchWorkItem?

    public var speechRate: Float = AVSpeechUtteranceDefaultSpeechRate * 1.05
    public var speechPitch: Float = 1.0
    public var preferredVoice: AVSpeechSynthesisVoice?

    public var onSentenceStarted: ((String) -> Void)?
    public var onQueueFinished: (() -> Void)?

    private let voiceIdKey = "macmini_preferred_voice_id"
    private let edgeVoiceKey = "macmini_edge_tts_voice"

    public var isLocalMode: Bool {
        let v = UserDefaults.standard.string(forKey: edgeVoiceKey) ?? "zh-TW-HsiaoChenNeural"
        return v == "local"
    }

    public override init() {
        super.init()
        synthesizer.delegate = self
        reloadPreferredVoice()
    }

    public static func getAvailableVoices() -> [AVSpeechSynthesisVoice] {
        let all = AVSpeechSynthesisVoice.speechVoices()
        let tw = all.filter { $0.language == "zh-TW" || $0.language == "zh-HK" }
        return tw.sorted {
            if $0.quality.rawValue != $1.quality.rawValue {
                return $0.quality.rawValue > $1.quality.rawValue
            }
            return $0.name < $1.name
        }
    }

    public func reloadPreferredVoice() {
        let savedId = UserDefaults.standard.string(forKey: voiceIdKey)
        let available = Self.getAvailableVoices()

        if let saved = savedId, let match = available.first(where: { $0.identifier == saved }) {
            preferredVoice = match
            return
        }

        // 自動優先挑選最高品質 (Premium 高級 > Enhanced 增強版 > Default)
        if let premium = available.first(where: { $0.quality == .premium }) {
            preferredVoice = premium
        } else if let enhanced = available.first(where: { $0.quality == .enhanced }) {
            preferredVoice = enhanced
        } else {
            preferredVoice = available.first ?? AVSpeechSynthesisVoice(language: "zh-TW")
        }
    }

    public func selectAndTestVoice(_ voice: AVSpeechSynthesisVoice) {
        stopImmediately()
        preferredVoice = voice
        UserDefaults.standard.set(voice.identifier, forKey: voiceIdKey)

        try? AVAudioSession.sharedInstance().setCategory(
            .playAndRecord,
            mode: .default,
            options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP, .mixWithOthers]
        )
        let utterance = AVSpeechUtterance(string: "你好，我是你的 Mac mini 隨身助理。")
        utterance.rate = speechRate
        utterance.pitchMultiplier = speechPitch
        utterance.voice = voice
        synthesizer.speak(utterance)
    }

    public func resetForNewTurn() {
        stopImmediately()
        sentenceQueue.removeAll()
        audioQueue.removeAll()
        currentlyPlayingItem = nil
        hasReceivedAudioChunks = false
        isGenerating = true
        isPlaying = false
    }

    /// 接收來自後端的類神經 MP3 串流音訊片段
    public func enqueueAudioChunk(seq: Int, sentence: String, audioData: Data) {
        guard !audioData.isEmpty else { return }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.cancelAudioWaitFallback()
            self.hasReceivedAudioChunks = true
            self.audioQueue.append(AudioChunkItem(seq: seq, sentence: sentence, audioData: audioData))
            self.audioQueue.sort { $0.seq < $1.seq }
            if !self.isPlaying {
                self.playNext()
            }
        }
    }

    /// 接收文字句子（若為純本地模式或降級備援使用）
    public func enqueueSentence(_ sentence: String) {
        let trimmed = sentence.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.sentenceQueue.append(trimmed)
            // 若為強制本地離線模式，立即播放本地 TTS
            if self.isLocalMode && !self.isPlaying {
                self.playNext()
            }
        }
    }

    public func markGenerationFinished() {
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.isGenerating = false
            self.cancelAudioWaitFallback()

            // 若後端串流已結束，但還有剩餘文字句子未朗讀（例如分片未生成或遺漏），立即本地備援播放
            if !self.isPlaying && (!self.audioQueue.isEmpty || !self.sentenceQueue.isEmpty) {
                self.playNext()
                return
            }

            if !self.isPlaying && self.audioQueue.isEmpty && self.sentenceQueue.isEmpty {
                self.onQueueFinished?()
            }
        }
    }

    /// 當 Edge-TTS 串流或網路請求斷線/中斷時，觸發本地備援機制無縫朗讀剩餘文字
    public func handleStreamInterruption(currentText: String = "") {
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            print("[NativeSpeechPlayer] ⚠️ 收到串流中斷訊號，啟動本地 AVSpeechSynthesizer 備援")
            self.isGenerating = false
            self.cancelAudioWaitFallback()

            // 確保 currentText 中尚未切出 ttsSentence 的剩餘未朗讀文字也被納入佇列
            let trimmed = currentText.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                let queuedText = self.sentenceQueue.joined()
                if !queuedText.contains(trimmed) && trimmed.count > queuedText.count {
                    let trailing = String(trimmed.suffix(trimmed.count - queuedText.count)).trimmingCharacters(in: .whitespacesAndNewlines)
                    if !trailing.isEmpty && !self.sentenceQueue.contains(trailing) {
                        self.sentenceQueue.append(trailing)
                    }
                }
            }

            if !self.isPlaying {
                self.playNext()
            }
        }
    }

    private func removeSentenceFromQueue(_ sentence: String) {
        if let idx = sentenceQueue.firstIndex(of: sentence) {
            sentenceQueue.remove(at: idx)
            return
        }
        let clean = sentence.trimmingCharacters(in: .whitespacesAndNewlines)
        if let idx = sentenceQueue.firstIndex(where: { $0.trimmingCharacters(in: .whitespacesAndNewlines) == clean }) {
            sentenceQueue.remove(at: idx)
        }
    }

    private func scheduleAudioWaitFallback() {
        waitFallbackWorkItem?.cancel()
        let item = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            guard !self.isPlaying && self.audioQueue.isEmpty && !self.sentenceQueue.isEmpty else { return }
            print("[NativeSpeechPlayer] ⏱️ 等待音訊分片逾時，自動切換本地 TTS 備援")
            let sentence = self.sentenceQueue.removeFirst()
            self.fallbackSpeakSentence(sentence)
        }
        waitFallbackWorkItem = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2, execute: item)
    }

    private func cancelAudioWaitFallback() {
        waitFallbackWorkItem?.cancel()
        waitFallbackWorkItem = nil
    }

    public static func ensureOptimalAudioRoute() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .voiceChat,
                options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP, .duckOthers]
            )
            let hasExternalRoute = session.currentRoute.outputs.contains { desc in
                let port = desc.portType
                return port == .bluetoothHFP || port == .bluetoothA2DP || port == .bluetoothLE
                    || port == .headphones || port == .airPlay || port == .carAudio
            }
            if hasExternalRoute {
                try? session.overrideOutputAudioPort(.none)
            } else {
                try? session.overrideOutputAudioPort(.speaker)
            }
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            print("[NativeSpeechPlayer] ensureOptimalAudioRoute error: \(error)")
        }
    }

    private func playNext() {
        Self.ensureOptimalAudioRoute()

        // 1. 優先播放來自後端的高品質 Edge-TTS MP3 音訊片段
        if !audioQueue.isEmpty {
            let item = audioQueue.removeFirst()
            removeSentenceFromQueue(item.sentence)
            currentlyPlayingItem = item
            isPlaying = true

            do {
                currentAudioPlayer = try AVAudioPlayer(data: item.audioData)
                currentAudioPlayer?.delegate = self
                currentAudioPlayer?.prepareToPlay()
                currentAudioPlayer?.play()
                onSentenceStarted?(item.sentence)
            } catch {
                print("[NativeSpeechPlayer] AudioPlayer error: \(error), fallback to local synthesizer")
                fallbackSpeakSentence(item.sentence)
            }
            return
        }

        // 2. 次之：若為強制本地離線模式，依序朗讀本地句子佇列
        if isLocalMode && !sentenceQueue.isEmpty {
            let sentence = sentenceQueue.removeFirst()
            fallbackSpeakSentence(sentence)
            return
        }

        // 3. 備援機制：後端串流已結束/中斷，或無音訊分片，但還有剩餘文字句子尚未朗讀
        if (!isGenerating || !hasReceivedAudioChunks) && !sentenceQueue.isEmpty {
            let sentence = sentenceQueue.removeFirst()
            print("[NativeSpeechPlayer] 🛡️ 串流中斷或分片缺失，無縫切換本地 TTS 備援朗讀: \(sentence.prefix(20))...")
            fallbackSpeakSentence(sentence)
            return
        }

        // 4. 若後端仍在生成中 (isGenerating == true)，但音訊佇列暫時空了且還有未播文字句子：
        // 守候 1.2 秒，若逾時新分片未送達則自動本地備援朗讀，確保語音絕對不腰斬
        if isGenerating && !sentenceQueue.isEmpty {
            scheduleAudioWaitFallback()
            return
        }

        // 5. 佇列全空
        isPlaying = false
        if !isGenerating {
            onQueueFinished?()
        }
    }

    private func fallbackSpeakSentence(_ sentence: String) {
        Self.ensureOptimalAudioRoute()
        isPlaying = true
        let utterance = AVSpeechUtterance(string: sentence)
        utterance.rate = speechRate
        utterance.pitchMultiplier = speechPitch
        if let voice = preferredVoice {
            utterance.voice = voice
        }

        onSentenceStarted?(sentence)
        synthesizer.speak(utterance)
    }

    /// 啟動無聲背景保活音訊（確保 App 退到背景或鎖屏等待後端 LLM 思考期間，iOS 絕不切斷網路連線）
    public func startBackgroundKeepAlive() {
        guard keepAlivePlayer == nil || keepAlivePlayer?.isPlaying == false else { return }
        let sampleRate: Int32 = 8000
        let numSamples: Int32 = 8000 // 1 秒無聲 PCM WAV
        let dataSize = numSamples * 2
        let fileSize = 36 + dataSize

        var wav = Data()
        wav.append("RIFF".data(using: .ascii)!)
        withUnsafeBytes(of: fileSize.littleEndian) { wav.append(contentsOf: $0) }
        wav.append("WAVEfmt ".data(using: .ascii)!)
        let fmtSize: Int32 = 16
        let audioFormat: Int16 = 1
        let numChannels: Int16 = 1
        let byteRate: Int32 = sampleRate * 2
        let blockAlign: Int16 = 2
        let bitsPerSample: Int16 = 16
        withUnsafeBytes(of: fmtSize.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: audioFormat.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: numChannels.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: sampleRate.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: byteRate.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: blockAlign.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: bitsPerSample.littleEndian) { wav.append(contentsOf: $0) }
        wav.append("data".data(using: .ascii)!)
        withUnsafeBytes(of: dataSize.littleEndian) { wav.append(contentsOf: $0) }
        wav.append(Data(count: Int(dataSize)))

        do {
            let player = try AVAudioPlayer(data: wav)
            player.numberOfLoops = -1 // 無限循環保活
            player.volume = 0.01
            player.prepareToPlay()
            player.play()
            keepAlivePlayer = player
        } catch {
            print("[NativeSpeechPlayer] KeepAlive error: \(error)")
        }
    }

    public func stopBackgroundKeepAlive() {
        keepAlivePlayer?.stop()
        keepAlivePlayer = nil
    }

    public func stopImmediately() {
        cancelAudioWaitFallback()
        currentlyPlayingItem = nil
        audioQueue.removeAll()
        sentenceQueue.removeAll()

        if let player = currentAudioPlayer, player.isPlaying {
            player.stop()
        }
        currentAudioPlayer = nil

        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        isPlaying = false
        isGenerating = false
    }

    public func speakStatus(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let utterance = AVSpeechUtterance(string: trimmed)
        utterance.rate = speechRate * 1.15
        utterance.volume = 0.75
        if let voice = preferredVoice {
            utterance.voice = voice
        }
        synthesizer.speak(utterance)
    }

    public func playChimeSound() {
        playListeningReadyEarcon()
    }

    // MARK: - 口袋盲操清脆雙音調微提示音 (Audio Earcons)
    /// 叮（上揚雙音）：代表助理回答完畢、麥克風已重新開啟，請開始說話
    public func playListeningReadyEarcon() {
        playSynthesizedTwoTone(freq1: 587.33, freq2: 783.99, duration1: 0.065, duration2: 0.085, volume: 0.20)
    }

    /// 咚（溫潤確認音）：代表偵測到說話停頓達標，已自動送出給大腦思考
    public func playSpeechSentEarcon() {
        playSynthesizedTwoTone(freq1: 523.25, freq2: 659.25, duration1: 0.055, duration2: 0.070, volume: 0.18)
    }

    /// 掛斷音（柔和下降音）：代表已辨識到「沒事了 / 掰掰」，自動結束 Live 連續對談
    public func playSessionEndEarcon() {
        playSynthesizedTwoTone(freq1: 659.25, freq2: 440.00, duration1: 0.075, duration2: 0.110, volume: 0.22)
    }

    private func playSynthesizedTwoTone(freq1: Double, freq2: Double, duration1: Double, duration2: Double, volume: Float) {
        let sampleRate: Double = 24000
        let count1 = Int(sampleRate * duration1)
        let count2 = Int(sampleRate * duration2)
        let totalSamples = count1 + count2
        let dataSize = Int32(totalSamples * 2)
        let fileSize = 36 + dataSize

        var wav = Data()
        wav.append("RIFF".data(using: .ascii)!)
        withUnsafeBytes(of: fileSize.littleEndian) { wav.append(contentsOf: $0) }
        wav.append("WAVEfmt ".data(using: .ascii)!)
        let fmtSize: Int32 = 16
        let audioFormat: Int16 = 1
        let numChannels: Int16 = 1
        let srInt = Int32(sampleRate)
        let byteRate: Int32 = srInt * 2
        let blockAlign: Int16 = 2
        let bitsPerSample: Int16 = 16
        withUnsafeBytes(of: fmtSize.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: audioFormat.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: numChannels.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: srInt.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: byteRate.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: blockAlign.littleEndian) { wav.append(contentsOf: $0) }
        withUnsafeBytes(of: bitsPerSample.littleEndian) { wav.append(contentsOf: $0) }
        wav.append("data".data(using: .ascii)!)
        withUnsafeBytes(of: dataSize.littleEndian) { wav.append(contentsOf: $0) }

        var pcm = [Int16](repeating: 0, count: totalSamples)
        for i in 0..<totalSamples {
            let isFirst = i < count1
            let localIdx = isFirst ? i : (i - count1)
            let localTotal = isFirst ? count1 : count2
            let freq = isFirst ? freq1 : freq2
            let t = Double(localIdx) / sampleRate
            // 平滑淡入淡出包絡線，消除爆音
            let progress = Double(localIdx) / Double(max(1, localTotal))
            let env = sin(progress * .pi)
            let sample = sin(2.0 * .pi * freq * t) * env * 0.45
            pcm[i] = Int16(max(-32767, min(32767, sample * 32767.0)))
        }
        pcm.withUnsafeBufferPointer { buf in
            wav.append(UnsafeBufferPointer(start: UnsafeRawPointer(buf.baseAddress!)?.assumingMemoryBound(to: UInt8.self), count: totalSamples * 2))
        }

        do {
            let player = try AVAudioPlayer(data: wav)
            player.volume = volume
            player.prepareToPlay()
            player.play()
            earconPlayer = player
        } catch {
            AudioServicesPlaySystemSound(1057)
        }
    }

    // MARK: - AVAudioPlayerDelegate
    public func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        DispatchQueue.main.async { [weak self] in
            self?.playNext()
        }
    }

    public func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            if let item = self.currentlyPlayingItem {
                print("[NativeSpeechPlayer] Decode error on item, fallback to local: \(item.sentence)")
                self.fallbackSpeakSentence(item.sentence)
            } else {
                self.playNext()
            }
        }
    }

    // MARK: - AVSpeechSynthesizerDelegate
    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [weak self] in
            self?.playNext()
        }
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [weak self] in
            self?.isPlaying = false
        }
    }
}
