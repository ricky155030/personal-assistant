import Foundation

/// 跨 App 與 Widget 的共享資料庫
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

    public func updateSnapshot(
        isReachable: Bool,
        latency: Int,
        message: String,
        flashPercent: Double? = nil,
        proPercent: Double? = nil
    ) {
        self.isServerReachable = isReachable
        self.latencyMs = latency
        self.statusMessage = message
        if let f = flashPercent { self.quotaFlashPercent = f }
        if let p = proPercent { self.quotaProPercent = p }
        self.lastUpdatedTime = Date()
    }
}
