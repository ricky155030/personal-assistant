import Foundation

public enum VoiceInputMode: String, Equatable {
    case none           // 待命 / 純文字輸入模式
    case singleTurn     // 🎙️ 單次語音輸入（講完自動送出，助理回覆後不自動重開麥克風）
    case liveContinuous // ((|)) Gemini Live 即時連續語音對談（講完自動送出 -> 語音回答 -> 自動重開麥克風）
}

public enum ThinkingLevel: String, CaseIterable, Identifiable {
    case low = "low"
    case med = "med"
    case high = "high"

    public var id: String { rawValue }

    public var headerTitle: String {
        switch self {
        case .low:
            return "Flash 快速"
        case .med:
            return "Flash 中等"
        case .high:
            return "Pro 深度"
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
        attachedImageData: Data? = nil
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
        self.attachedImageData = attachedImageData
    }

    enum CodingKeys: String, CodingKey {
        case id, role, content, timestamp, status, toolHistory, thoughtProcess, quickReplies, reportUrl, attachedImageData
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
    case done(fullReply: String, latency: Double, thinkingLevel: String, reportUrl: String, quickReplies: [String])
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
        planTier: String = "Google AI Pro"
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
