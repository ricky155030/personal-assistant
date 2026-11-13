import Foundation

public enum VoiceInputMode: String, Equatable {
    case none           // 待命 / 純文字輸入模式
    case singleTurn     // 🎙️ 單次語音輸入（講完自動送出，助理回覆後不自動重開麥克風）
    case liveContinuous // ((|)) Gemini Live 即時連續語音對談（講完自動送出 -> 語音回答 -> 自動重開麥克風）
    case cameraFollowUp // 📷 隨身眼睛補充語音（專供相機拍完後聽寫，不走常規發送管線）
}

public enum ModelFamily: String, CaseIterable, Identifiable {
    case hydrogen = "hydrogen"
    case argon = "argon"
    case flash = "flash"
    case claude = "claude"
    case opus = "opus"

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .hydrogen: return "Hydrogen"
        case .argon: return "Argon"
        case .flash: return "Flash"
        case .claude: return "Sonnet 5.5"
        case .opus: return "Opus 5.5"
        }
    }

    public var fullName: String {
        switch self {
        case .hydrogen: return "Gemini 4 Hydrogen"
        case .argon: return "Gemini 4 Argon"
        case .flash: return "Gemini 3.8 Flash"
        case .claude: return "Claude Sonnet 5.5"
        case .opus: return "Claude Opus 5.5"
        }
    }

    public var icon: String {
        switch self {
        case .hydrogen: return "flame.fill"
        case .argon: return "atom"
        case .flash: return "bolt.fill"
        case .claude: return "sparkles"
        case .opus: return "crown.fill"
        }
    }
}

public enum STTRecognitionMode: String, CaseIterable, Identifiable {
    case hybrid = "hybrid"
    case localOnly = "localOnly"
    case remoteOnly = "remoteOnly"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .hybrid:
            return "智慧雙軌 (推薦 · 預設)"
        case .localOnly:
            return "本機即時辨識 (Apple Speech 零延遲)"
        case .remoteOnly:
            return "後端伺服器辨識 (Whisper 高精確度)"
        }
    }

    public var subtitle: String {
        switch self {
        case .hybrid:
            return "邊講邊跳字預覽；若本機無字自動交由後端 Whisper 補正"
        case .localOnly:
            return "純本機離線極速處理，講完 0 延遲直接送出文字"
        case .remoteOnly:
            return "講完將完整音檔傳送至 Mac mini Whisper 深度轉錄"
        }
    }

    public var icon: String {
        switch self {
        case .hybrid:
            return "arrow.triangle.swap"
        case .localOnly:
            return "iphone.gen3"
        case .remoteOnly:
            return "server.rack"
        }
    }
}

public enum ThinkingLevel: String, CaseIterable, Identifiable {
    case low = "low"
    case med = "med"
    case high = "high"

    public var id: String { rawValue }

    public var headerTitle: String {
        switch self {
        case .low:
            return "快速 (Low)"
        case .med:
            return "中等 (Med)"
        case .high:
            return "深度 (High)"
        }
    }

    public var menuSubtitle: String {
        switch self {
        case .low:
            return "極速回應日常指令與家電控制"
        case .med:
            return "平衡速度與邏輯推理 (預設)"
        case .high:
            return "複雜架構設計與深度專案分析"
        }
    }
}

public enum ResponseLengthOption: String, CaseIterable, Identifiable, Codable {
    case concise = "concise"    // ⚡ 極簡俐落 (30~60字)
    case balanced = "balanced"  // ⚖️ 標準均衡 (80~150字，預設)
    case detailed = "detailed"  // 📚 完整詳解 (無字數限制)

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .concise: return "極簡俐落 (30~60字)"
        case .balanced: return "標準均衡 (80~150字)"
        case .detailed: return "完整詳解 (深度完整)"
        }
    }

    public var shortTag: String {
        switch self {
        case .concise: return "極簡"
        case .balanced: return "標準"
        case .detailed: return "詳解"
        }
    }

    public var icon: String {
        switch self {
        case .concise: return "bolt.fill"
        case .balanced: return "scalemass.fill"
        case .detailed: return "book.fill"
        }
    }

    public var promptInstruction: String {
        switch self {
        case .concise:
            return "【回覆長度約束：極簡模式】嚴格控制在 50 字以內，直奔核心結論，禁止多餘寒暄與鋪陳。"
        case .balanced:
            return "【回覆長度約束：標準模式】以結構化條理分明方式回覆，嚴格控制在 120 字以內，重點突出，適合語音聆聽。"
        case .detailed:
            return "【回覆長度約束：深度模式】解除字數限制，提供深入、完整、條理分明的詳細解答與具體步驟。"
        }
    }
}

public enum SessionState: Equatable {
    case idle
    case listening(isSpeaking: Bool, currentDb: Float)
    case transcribing
    case thinkingAndStreaming(toolStatus: String?)
    case speaking(currentSentence: String)
    case paused

    public static func == (lhs: SessionState, rhs: SessionState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.transcribing, .transcribing), (.paused, .paused):
            return true
        case (.listening(let lSpeaking, _), .listening(let rSpeaking, _)):
            return lSpeaking == rSpeaking
        case (.thinkingAndStreaming(let lTool), .thinkingAndStreaming(let rTool)):
            return lTool == rTool
        case (.speaking(let lSent), .speaking(let rSent)):
            return lSent == rSent
        default:
            return false
        }
    }

    public var statusDescription: String {
        switch self {
        case .idle:
            return "待命"
        case .listening(let isSpeaking, _):
            return isSpeaking ? "正在聆聽您說話..." : "請開口說話，講完自動送出..."
        case .transcribing:
            return "正在語音辨識..."
        case .thinkingAndStreaming(let toolStatus):
            if let tool = toolStatus, !tool.isEmpty {
                return tool
            }
            return "正在思考..."
        case .speaking(let sentence):
            return sentence.isEmpty ? "助理回答中..." : sentence
        case .paused:
            return "麥克風已暫停"
        }
    }
}

public enum MessageRole: String, Codable {
    case user
    case assistant
    case system
}

public enum MessageStatus: String, Codable {
    case sending
    case streaming
    case completed
    case error
}

public struct TurnUsage: Codable, Equatable {
    public var input_tokens: Int?
    public var output_tokens: Int?
    public var thought_tokens: Int?
    public var cached_read_tokens: Int?
    public var total_tokens: Int?
}

public struct TurnMetadata: Codable, Equatable {
    public var model: String?
    public var thinking_level: String?
    public var ttft_sec: Double?
    public var total_sec: Double?
    public var usage: TurnUsage?
    public var estimated_cost_usd: Double?

    public var summaryText: String {
        var parts: [String] = []
        if let m = model, !m.isEmpty {
            let shortModel = m
                .replacingOccurrences(of: "gemini-4-hydrogen-", with: "G4 Hydrogen ")
                .replacingOccurrences(of: "gemini-4-argon-", with: "G4 Argon ")
                .replacingOccurrences(of: "gemini-3.8-flash-", with: "G3.8 Flash ")
                .replacingOccurrences(of: "claude-sonnet-5-5-", with: "Sonnet 5.5 ")
                .replacingOccurrences(of: "claude-opus-5-5-", with: "Opus 5.5 ")
            parts.append(shortModel)
        }
        if let t = total_sec, t > 0 {
            parts.append(String(format: "%.1fs", t))
        }
        if let cost = estimated_cost_usd, cost > 0 {
            parts.append(String(format: "~$%.4f", cost))
        }
        if let c = usage?.cached_read_tokens, c > 0 {
            let cStr = c >= 1000 ? String(format: "%.1fk", Double(c) / 1000.0) : "\(c)"
            parts.append("Cache \(cStr)")
        }
        return parts.joined(separator: " · ")
    }
}

public struct ChatMessage: Identifiable, Codable, Equatable {
    public let id: UUID
    public let role: MessageRole
    public var content: String
    public let timestamp: Date
    public var status: MessageStatus
    public var toolHistory: [String]
    public var thoughtProcess: String
    public var quickReplies: [String]
    public var reportUrl: String?
    public var attachedImageData: Data?
    public var attachedImagesData: [Data]?
    public var metadata: TurnMetadata?

    public init(
        id: UUID = UUID(),
        role: MessageRole,
        content: String,
        timestamp: Date = Date(),
        status: MessageStatus = .completed,
        toolHistory: [String] = [],
        thoughtProcess: String = "",
        quickReplies: [String] = [],
        reportUrl: String? = nil,
        attachedImageData: Data? = nil,
        attachedImagesData: [Data]? = nil,
        metadata: TurnMetadata? = nil
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.status = status
        self.toolHistory = toolHistory
        self.thoughtProcess = thoughtProcess
        self.quickReplies = quickReplies
        self.reportUrl = reportUrl
        self.attachedImageData = attachedImageData ?? attachedImagesData?.first
        self.attachedImagesData = attachedImagesData ?? (attachedImageData != nil ? [attachedImageData!] : nil)
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, role, content, timestamp, status, toolHistory, thoughtProcess, quickReplies, reportUrl, attachedImageData, attachedImagesData, metadata
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(UUID.self, forKey: .id)
        self.role = try container.decode(MessageRole.self, forKey: .role)
        self.content = try container.decode(String.self, forKey: .content)
        self.timestamp = try container.decode(Date.self, forKey: .timestamp)
        self.status = try container.decode(MessageStatus.self, forKey: .status)
        self.toolHistory = try container.decodeIfPresent([String].self, forKey: .toolHistory) ?? []
        self.thoughtProcess = try container.decodeIfPresent(String.self, forKey: .thoughtProcess) ?? ""
        self.quickReplies = try container.decodeIfPresent([String].self, forKey: .quickReplies) ?? []
        self.reportUrl = try container.decodeIfPresent(String.self, forKey: .reportUrl)
        self.attachedImageData = try container.decodeIfPresent(Data.self, forKey: .attachedImageData)
        self.attachedImagesData = try container.decodeIfPresent([Data].self, forKey: .attachedImagesData)
        self.metadata = try container.decodeIfPresent(TurnMetadata.self, forKey: .metadata)
    }
}

public struct ChatSession: Identifiable, Codable, Equatable {
    public let id: UUID
    public var title: String
    public var updatedAt: Date
    public var messages: [ChatMessage]

    public init(
        id: UUID = UUID(),
        title: String,
        updatedAt: Date = Date(),
        messages: [ChatMessage] = []
    ) {
        self.id = id
        self.title = title
        self.updatedAt = updatedAt
        self.messages = messages
    }
}

public enum SSEEvent {
    case thoughtDelta(delta: String)
    case status(text: String, tts: String?)
    case textDelta(delta: String)
    case ttsSentence(sentence: String)
    case audioChunk(seq: Int, sentence: String, audioData: Data)
    case done(fullReply: String, latency: Double, thinkingLevel: String, reportUrl: String, quickReplies: [String], metadata: TurnMetadata?)
    case error(message: String)
}

public struct QuotaSnapshot: Equatable {
    public var weeklyRemainingPct: Double
    public var weeklyRefreshesIn: String
    public var weeklyResetTimeTaipei: String
    public var rolling5hRemainingPct: Double
    public var rolling5hRefreshesIn: String
    public var rolling5hResetTimeTaipei: String
    public var contextPercent: Double
    public var contextTokens: Int
    public var contextLimit: Int
    public var currentModel: String
    public var sessionId: String
    public var planTier: String

    // Gemini 4 Argon
    public var argonRemainingPct: Double
    public var argonRefreshesIn: String
    public var argonResetTimeTaipei: String

    // Prompt Credits / Flow Credits
    public var promptCredits: Int
    public var flowCredits: Int
    public var monthlyPromptCredits: Int
    public var monthlyFlowCredits: Int

    // Third Party (Claude / GPT 3P)
    public var thirdPartyRemainingPct: Double
    public var thirdPartyRefreshesIn: String

    // 背景子代理 / 常駐任務
    public var activeTaskCount: Int
    public var activeTaskNames: [String]

    public init(
        weeklyRemainingPct: Double = 100.0,
        weeklyRefreshesIn: String = "--",
        weeklyResetTimeTaipei: String = "",
        rolling5hRemainingPct: Double = 100.0,
        rolling5hRefreshesIn: String = "--",
        rolling5hResetTimeTaipei: String = "",
        contextPercent: Double = 0.0,
        contextTokens: Int = 0,
        contextLimit: Int = 1_000_000,
        currentModel: String = "gemini-3.8-flash-medium",
        sessionId: String = "N/A",
        planTier: String = "Google AI Pro",
        argonRemainingPct: Double = 100.0,
        argonRefreshesIn: String = "--",
        argonResetTimeTaipei: String = "",
        promptCredits: Int = 0,
        flowCredits: Int = 0,
        monthlyPromptCredits: Int = 0,
        monthlyFlowCredits: Int = 0,
        thirdPartyRemainingPct: Double = 100.0,
        thirdPartyRefreshesIn: String = "--",
        activeTaskCount: Int = 0,
        activeTaskNames: [String] = []
    ) {
        self.weeklyRemainingPct = weeklyRemainingPct
        self.weeklyRefreshesIn = weeklyRefreshesIn
        self.weeklyResetTimeTaipei = weeklyResetTimeTaipei
        self.rolling5hRemainingPct = rolling5hRemainingPct
        self.rolling5hRefreshesIn = rolling5hRefreshesIn
        self.rolling5hResetTimeTaipei = rolling5hResetTimeTaipei
        self.contextPercent = contextPercent
        self.contextTokens = contextTokens
        self.contextLimit = contextLimit
        self.currentModel = currentModel
        self.sessionId = sessionId
        self.planTier = planTier
        self.argonRemainingPct = argonRemainingPct
        self.argonRefreshesIn = argonRefreshesIn
        self.argonResetTimeTaipei = argonResetTimeTaipei
        self.promptCredits = promptCredits
        self.flowCredits = flowCredits
        self.monthlyPromptCredits = monthlyPromptCredits
        self.monthlyFlowCredits = monthlyFlowCredits
        self.thirdPartyRemainingPct = thirdPartyRemainingPct
        self.thirdPartyRefreshesIn = thirdPartyRefreshesIn
        self.activeTaskCount = activeTaskCount
        self.activeTaskNames = activeTaskNames
    }
}

public enum EdgeTTSVoice: String, CaseIterable, Identifiable {
    case hsiaoChen = "zh-TW-HsiaoChenNeural"
    case yunJhe = "zh-TW-YunJheNeural"
    case hsiaoYu = "zh-TW-HsiaoYuNeural"
    case systemLocal = "local"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .hsiaoChen:
            return "曉臻 (女聲，親切自然 - 推薦)"
        case .yunJhe:
            return "雲哲 (男聲，自然沉穩)"
        case .hsiaoYu:
            return "曉雨 (女聲，溫柔輕聲)"
        case .systemLocal:
            return "iOS 原生離線語音 (本地)"
        }
    }
}

public enum TTSSpeedOption: String, CaseIterable, Identifiable {
    case normal = "+0%"
    case brisk = "+15%"
    case fast = "+25%"
    case turbo = "+45%"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .normal: return "1.0x 標準"
        case .brisk:  return "1.15x 輕快"
        case .fast:   return "1.25x 快速"
        case .turbo:  return "1.45x 極速"
        }
    }

    public var localRateMultiplier: Float {
        switch self {
        case .normal: return 1.05
        case .brisk:  return 1.15
        case .fast:   return 1.25
        case .turbo:  return 1.40
        }
    }
}

// MARK: - 隨身眼睛視覺動作預設集 (Vision Action Presets)
public struct VisionActionPreset: Identifiable, Codable, Equatable {
    public var id: String
    public var title: String
    public var icon: String
    public var prompt: String
    public var isBuiltIn: Bool

    public init(
        id: String = UUID().uuidString,
        title: String,
        icon: String,
        prompt: String,
        isBuiltIn: Bool = false
    ) {
        self.id = id
        self.title = title
        self.icon = icon
        self.prompt = prompt
        self.isBuiltIn = isBuiltIn
    }

    public static let defaultPresets: [VisionActionPreset] = [
        VisionActionPreset(
            id: "builtin_general",
            title: "萬物百科",
            icon: "sparkles",
            prompt: "請詳細說明眼前這張照片的物品名稱、規格、主要特徵、成分或重要資訊，並條理分明地說明用途與注意事項。",
            isBuiltIn: true
        ),
        VisionActionPreset(
            id: "builtin_shopee",
            title: "蝦皮上架",
            icon: "bag.fill",
            prompt: "請使用 skill shopee-seller 解析這張商品照片，自動匹配台灣蝦皮官方分類、撰寫 SEO 標題與規格並準備上架。",
            isBuiltIn: true
        ),
        VisionActionPreset(
            id: "builtin_baby",
            title: "嬰幼兒備品",
            icon: "heart.fill",
            prompt: "請詳細解析眼前這項嬰幼兒用品/備品的品名、適用年齡與月齡、材質安全性、使用方式與清潔消毒注意事項，並評估是否適合作為待產與新生兒備品。",
            isBuiltIn: true
        )
    ]
}

public final class VisionPresetStore {
    private static let userDefaultsKey = "macmini_vision_action_presets"
    public static let voiceFollowUpKey = "macmini_vision_voice_followup_enabled"

    public static func loadPresets() -> [VisionActionPreset] {
        if let data = UserDefaults.standard.data(forKey: userDefaultsKey),
           let list = try? JSONDecoder().decode([VisionActionPreset].self, from: data),
           !list.isEmpty {
            return list
        }
        return VisionActionPreset.defaultPresets
    }

    public static func savePresets(_ presets: [VisionActionPreset]) {
        if let data = try? JSONEncoder().encode(presets) {
            UserDefaults.standard.set(data, forKey: userDefaultsKey)
        }
    }

    public static var isVoiceFollowUpEnabled: Bool {
        get {
            // 預設為 false (極速模式，拍完立即發送)
            UserDefaults.standard.bool(forKey: voiceFollowUpKey)
        }
        set {
            UserDefaults.standard.set(newValue, forKey: voiceFollowUpKey)
        }
    }
}
