import Foundation
import AppIntents
import SwiftUI

// MARK: - Notification Names for App Intents
public extension Notification.Name {
    static let triggerLiveVoice = Notification.Name("com.personalassistant.triggerLiveVoice")
    static let triggerSingleTurnVoice = Notification.Name("com.personalassistant.triggerSingleTurnVoice")
    static let triggerNewChat = Notification.Name("com.personalassistant.triggerNewChat")
    static let triggerCompactContext = Notification.Name("com.personalassistant.triggerCompactContext")
}

// MARK: - 動作按鈕 / Siri / 捷徑 App Intents

/// 動作按鈕第一推薦：立即啟動 Mac mini 助理並展開免持連續語音對話
public struct StartLiveVoiceIntent: AppIntent {
    public static var title: LocalizedStringResource = "開始連續語音對話"
    public static var description = IntentDescription("立即喚醒 Mac mini 個人助理，進入全免持連續語音對談模式 (適合散步/開車/動作按鈕)")
    public static var openAppWhenRun: Bool = true

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult {
        NotificationCenter.default.post(name: .triggerLiveVoice, object: nil)
        return .result()
    }
}

/// 快速語音速記：開啟單次語音收音並將內容填入對話框
public struct StartSingleVoiceIntent: AppIntent {
    public static var title: LocalizedStringResource = "開始語音速記"
    public static var description = IntentDescription("開啟 Mac mini 助理麥克風進行單次語音轉文字速記")
    public static var openAppWhenRun: Bool = true

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult {
        NotificationCenter.default.post(name: .triggerSingleTurnVoice, object: nil)
        return .result()
    }
}

/// 開啟新對話 Session
public struct StartNewChatIntent: AppIntent {
    public static var title: LocalizedStringResource = "開啟新對話"
    public static var description = IntentDescription("清除當前對話上下文並開啟全新 Mac mini 助理對話")
    public static var openAppWhenRun: Bool = true

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult {
        NotificationCenter.default.post(name: .triggerNewChat, object: nil)
        return .result()
    }
}

// MARK: - iOS 系統捷徑自動提供者 (AppShortcutsProvider)
public struct MacMiniAssistantShortcutsProvider: AppShortcutsProvider {
    public static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: StartLiveVoiceIntent(),
            phrases: [
                "用 \(.applicationName) 開始對話",
                "跟 \(.applicationName) 語音對話",
                "開啟 \(.applicationName) 連續語音",
                "呼叫 \(.applicationName)"
            ],
            shortTitle: "連續語音對談",
            systemImageName: "waveform.circle.fill"
        )
        AppShortcut(
            intent: StartSingleVoiceIntent(),
            phrases: [
                "用 \(.applicationName) 語音速記",
                "記錄到 \(.applicationName)"
            ],
            shortTitle: "語音速記",
            systemImageName: "mic.circle.fill"
        )
        AppShortcut(
            intent: StartNewChatIntent(),
            phrases: [
                "開啟 \(.applicationName) 新對話"
            ],
            shortTitle: "開啟新對話",
            systemImageName: "plus.bubble.fill"
        )
    }
}
