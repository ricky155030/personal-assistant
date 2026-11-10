import WidgetKit
import SwiftUI

// MARK: - Timeline Entry
public struct AssistantWidgetEntry: TimelineEntry {
    public let date: Date
    public let isReachable: Bool
    public let latencyMs: Int
    public let statusMessage: String
    public let quotaFlashPercent: Double
    public let quotaProPercent: Double

    public static var preview: AssistantWidgetEntry {
        AssistantWidgetEntry(
            date: Date(),
            isReachable: true,
            latencyMs: 14,
            statusMessage: "Mac mini (14ms)",
            quotaFlashPercent: 88.0,
            quotaProPercent: 95.0
        )
    }
}

// MARK: - Timeline Provider
public struct AssistantTimelineProvider: TimelineProvider {
    public func placeholder(in context: Context) -> AssistantWidgetEntry {
        .preview
    }

    public func getSnapshot(in context: Context, completion: @escaping (AssistantWidgetEntry) -> Void) {
        completion(currentEntry())
    }

    public func getTimeline(in context: Context, completion: @escaping (Timeline<AssistantWidgetEntry>) -> Void) {
        let entry = currentEntry()
        // 建議每 15 分鐘重新整理一次，或由主 App 主動呼叫 WidgetCenter.shared.reloadAllTimelines()
        let nextUpdate = Calendar.current.date(byAdding: .minute, value: 15, to: Date()) ?? Date().addingTimeInterval(900)
        let timeline = Timeline(entries: [entry], policy: .after(nextUpdate))
        completion(timeline)
    }

    private func currentEntry() -> AssistantWidgetEntry {
        let store = AppGroupStore.shared
        return AssistantWidgetEntry(
            date: Date(),
            isReachable: store.isServerReachable,
            latencyMs: store.latencyMs,
            statusMessage: store.statusMessage,
            quotaFlashPercent: store.quotaFlashPercent > 0 ? store.quotaFlashPercent : 100.0,
            quotaProPercent: store.quotaProPercent > 0 ? store.quotaProPercent : 100.0
        )
    }
}

// MARK: - Widget Configuration
public struct MacMiniAssistantStatusWidget: Widget {
    public let kind: String = "MacMiniAssistantStatusWidget"

    public init() {}

    public var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: AssistantTimelineProvider()) { entry in
            AssistantWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("Mac mini 隨身助理")
        .description("即時顯示 Mac mini 連線狀態、延遲與 Gemini 額度，支援一鍵點擊喚醒語音對話。")
        .supportedFamilies([
            .systemSmall,
            .systemMedium,
            .accessoryCircular,
            .accessoryRectangular
        ])
    }
}
