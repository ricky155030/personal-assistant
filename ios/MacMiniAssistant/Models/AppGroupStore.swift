import Foundation

/// 系統分享擴充 (Share Extension) 自訂快捷指令項目
public struct SharePromptPreset: Identifiable, Codable, Equatable {
    public let id: UUID
    public var title: String
    public var prompt: String
    public var icon: String

    public init(id: UUID = UUID(), title: String, prompt: String, icon: String = "sparkles") {
        self.id = id
        self.title = title
        self.prompt = prompt
        self.icon = icon
    }

    public static let defaultPresets: [SharePromptPreset] = [
        SharePromptPreset(title: "摘要重點", prompt: "幫我快速摘要這篇內容的核心重點與結論", icon: "doc.text.magnifyingglass"),
        SharePromptPreset(title: "比價追蹤", prompt: "幫我評估這個商品，查詢合理價格或提供追蹤建議", icon: "tag.fill"),
        SharePromptPreset(title: "記到待辦", prompt: "幫我把這件事記到 Google Calendar 待辦事項", icon: "checklist"),
        SharePromptPreset(title: "翻譯重點", prompt: "幫我翻譯成流暢的繁體中文並整理重點", icon: "character.bubble"),
    ]
}

/// 跨 App 與 Widget / Share Extension 的共享資料庫
public final class AppGroupStore {
    public static let shared = AppGroupStore()

    public static let appGroupIdentifier = "group.com.personalassistant.MacMiniAssistant"

    private let defaults: UserDefaults

    public init() {
        self.defaults = UserDefaults(suiteName: Self.appGroupIdentifier) ?? UserDefaults.standard
    }

    private enum Keys {
        static let isServerReachable = "shared_server_reachable"
        static let latencyMs = "shared_latency_ms"
        static let statusMessage = "shared_status_message"
        static let quotaFlashPercent = "shared_quota_flash_percent"
        static let quotaProPercent = "shared_quota_pro_percent"
        static let lastUpdatedTime = "shared_last_updated_time"
        static let baseURL = "macmini_server_url"
        static let apiKey = "macmini_api_key"
        static let sharePrompts = "shared_share_prompts"
        static let activeEngineId = "shared_active_engine_id"
        static let gatewayQuotas = "shared_gateway_quotas"
    }

    public struct GatewayQuotaWidgetItem: Codable, Equatable, Identifiable {
        public let id: String
        public let label: String
        public let online: Bool
        public let hasUsage: Bool
        public let rolling5hRemainingPct: Double?
        public let weeklyRemainingPct: Double?

        public init(
            id: String,
            label: String,
            online: Bool,
            hasUsage: Bool,
            rolling5hRemainingPct: Double?,
            weeklyRemainingPct: Double?
        ) {
            self.id = id
            self.label = label
            self.online = online
            self.hasUsage = hasUsage
            self.rolling5hRemainingPct = rolling5hRemainingPct
            self.weeklyRemainingPct = weeklyRemainingPct
        }
    }

    public var serverURL: String {
        get { defaults.string(forKey: Keys.baseURL) ?? "" }
        set { defaults.set(newValue, forKey: Keys.baseURL) }
    }

    public var apiKey: String {
        get { defaults.string(forKey: Keys.apiKey) ?? "" }
        set { defaults.set(newValue, forKey: Keys.apiKey) }
    }

    public var sharePrompts: [SharePromptPreset] {
        get {
            guard let data = defaults.data(forKey: Keys.sharePrompts),
                  let decoded = try? JSONDecoder().decode([SharePromptPreset].self, from: data),
                  !decoded.isEmpty else {
                return SharePromptPreset.defaultPresets
            }
            return decoded
        }
        set {
            if let encoded = try? JSONEncoder().encode(newValue) {
                defaults.set(encoded, forKey: Keys.sharePrompts)
            }
        }
    }

    public var isServerReachable: Bool {
        get { defaults.bool(forKey: Keys.isServerReachable) }
        set { defaults.set(newValue, forKey: Keys.isServerReachable) }
    }

    public var latencyMs: Int {
        get { defaults.integer(forKey: Keys.latencyMs) }
        set { defaults.set(newValue, forKey: Keys.latencyMs) }
    }

    public var statusMessage: String {
        get { defaults.string(forKey: Keys.statusMessage) ?? "檢查中..." }
        set { defaults.set(newValue, forKey: Keys.statusMessage) }
    }

    public var quotaFlashPercent: Double {
        get { defaults.double(forKey: Keys.quotaFlashPercent) }
        set { defaults.set(newValue, forKey: Keys.quotaFlashPercent) }
    }

    public var quotaProPercent: Double {
        get { defaults.double(forKey: Keys.quotaProPercent) }
        set { defaults.set(newValue, forKey: Keys.quotaProPercent) }
    }

    public var lastUpdatedTime: Date? {
        get { defaults.object(forKey: Keys.lastUpdatedTime) as? Date }
        set { defaults.set(newValue, forKey: Keys.lastUpdatedTime) }
    }

    public var activeEngineId: String {
        get { defaults.string(forKey: Keys.activeEngineId) ?? "agy" }
        set { defaults.set(newValue, forKey: Keys.activeEngineId) }
    }

    public var gatewayQuotas: [GatewayQuotaWidgetItem] {
        get {
            guard let data = defaults.data(forKey: Keys.gatewayQuotas),
                  let decoded = try? JSONDecoder().decode([GatewayQuotaWidgetItem].self, from: data) else {
                return []
            }
            return decoded
        }
        set {
            if let encoded = try? JSONEncoder().encode(newValue) {
                defaults.set(encoded, forKey: Keys.gatewayQuotas)
            }
        }
    }

    public func updateSnapshot(
        isReachable: Bool,
        latency: Int,
        message: String,
        flashPercent: Double? = nil,
        proPercent: Double? = nil,
        activeEngineId: String? = nil,
        gatewayQuotas: [GatewayQuotaWidgetItem]? = nil
    ) {
        self.isServerReachable = isReachable
        self.latencyMs = latency
        self.statusMessage = message
        if let f = flashPercent { self.quotaFlashPercent = f }
        if let p = proPercent { self.quotaProPercent = p }
        if let e = activeEngineId { self.activeEngineId = e }
        if let g = gatewayQuotas { self.gatewayQuotas = g }
        self.lastUpdatedTime = Date()
    }
}
