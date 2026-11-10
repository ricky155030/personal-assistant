import SwiftUI
import WidgetKit

public struct AssistantWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    public let entry: AssistantWidgetEntry

    public var body: some View {
        widgetContent
            .containerBackground(for: .widget) {
                LinearGradient(
                    colors: [
                        Color(red: 0.08, green: 0.10, blue: 0.18),
                        Color(red: 0.04, green: 0.05, blue: 0.10)
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            }
    }

    @ViewBuilder
    private var widgetContent: some View {
        switch family {
        case .systemSmall:
            SmallWidgetView(entry: entry)
                .widgetURL(URL(string: "macminiassistant://live"))
        case .systemMedium:
            MediumWidgetView(entry: entry)
        case .accessoryCircular:
            CircularAccessoryView(entry: entry)
                .widgetURL(URL(string: "macminiassistant://live"))
        case .accessoryRectangular:
            RectangularAccessoryView(entry: entry)
                .widgetURL(URL(string: "macminiassistant://live"))
        default:
            SmallWidgetView(entry: entry)
                .widgetURL(URL(string: "macminiassistant://live"))
        }
    }
}

// MARK: - 桌面小尺寸 Widget (System Small)
private struct SmallWidgetView: View {
    let entry: AssistantWidgetEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // 頂部圖標與名稱
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundColor(.cyan)
                Text("Mac mini")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.white)
                Spacer()
                Circle()
                    .fill(entry.isReachable ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
            }

            // 連線狀態與延遲
            Text(entry.isReachable ? "連線正常 (\(entry.latencyMs)ms)" : "伺服器離線")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(entry.isReachable ? .green : .red.opacity(0.8))

            Spacer()

            // Gemini 額度進度
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text("Gemini 額度")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.white.opacity(0.6))
                    Spacer()
                    Text("\(Int(entry.quotaFlashPercent))%")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(.cyan)
                }

                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule()
                            .fill(Color.white.opacity(0.12))
                            .frame(height: 5)
                        Capsule()
                            .fill(
                                LinearGradient(
                                    colors: [.cyan, .blue],
                                    startPoint: .leading,
                                    endPoint: .trailing
                                )
                            )
                            .frame(width: max(0, min(geo.size.width * CGFloat(entry.quotaFlashPercent / 100.0), geo.size.width)), height: 5)
                    }
                }
                .frame(height: 5)
            }

            // 快捷語音提示標籤
            HStack(spacing: 5) {
                Image(systemName: "waveform")
                    .font(.system(size: 10, weight: .semibold))
                Text("點擊開始對話")
                    .font(.system(size: 10, weight: .semibold))
            }
            .foregroundColor(.white.opacity(0.9))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                Capsule()
                    .fill(Color.blue.opacity(0.35))
            )
        }
    }
}

// MARK: - 桌面中尺寸 Widget (System Medium)
private struct MediumWidgetView: View {
    let entry: AssistantWidgetEntry

    var body: some View {
        HStack(spacing: 14) {
            // 左半部：狀態與配額資訊
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(.cyan)
                    Text("Mac mini 助理")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                }

                HStack(spacing: 5) {
                    Circle()
                        .fill(entry.isReachable ? Color.green : Color.red)
                        .frame(width: 7, height: 7)
                    Text(entry.isReachable ? "Tailscale 直連 (\(entry.latencyMs)ms)" : "離線請開 VPN")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.secondary)
                }

                Spacer()

                // Flash 額度條
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text("Flash 額度")
                            .font(.system(size: 10))
                            .foregroundColor(.white.opacity(0.6))
                        Spacer()
                        Text("\(Int(entry.quotaFlashPercent))%")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.cyan)
                    }
                    ProgressView(value: min(max(entry.quotaFlashPercent / 100.0, 0), 1.0))
                        .tint(.cyan)
                        .scaleEffect(x: 1, y: 0.6, anchor: .center)
                }

                // Pro 額度條
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text("Pro 額度")
                            .font(.system(size: 10))
                            .foregroundColor(.white.opacity(0.6))
                        Spacer()
                        Text("\(Int(entry.quotaProPercent))%")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.purple)
                    }
                    ProgressView(value: min(max(entry.quotaProPercent / 100.0, 0), 1.0))
                        .tint(.purple)
                        .scaleEffect(x: 1, y: 0.6, anchor: .center)
                }
            }

            Divider()
                .background(Color.white.opacity(0.12))

            // 右半部：快捷觸控動作 (Interactive Links)
            VStack(spacing: 7) {
                Link(destination: URL(string: "macminiassistant://live")!) {
                    HStack(spacing: 6) {
                        Image(systemName: "waveform.circle.fill")
                            .font(.system(size: 14))
                        Text("連續免持")
                            .font(.system(size: 12, weight: .semibold))
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 10, weight: .bold))
                            .opacity(0.6)
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(
                                LinearGradient(
                                    colors: [Color.blue.opacity(0.8), Color.indigo.opacity(0.8)],
                                    startPoint: .leading,
                                    endPoint: .trailing
                                )
                            )
                    )
                }

                Link(destination: URL(string: "macminiassistant://single")!) {
                    HStack(spacing: 6) {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 12))
                        Text("語音速記")
                            .font(.system(size: 12, weight: .medium))
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 10, weight: .bold))
                            .opacity(0.4)
                    }
                    .foregroundColor(.white.opacity(0.9))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.white.opacity(0.08))
                    )
                }

                Link(destination: URL(string: "macminiassistant://newchat")!) {
                    HStack(spacing: 6) {
                        Image(systemName: "plus.bubble")
                            .font(.system(size: 12))
                        Text("開啟新對話")
                            .font(.system(size: 12, weight: .medium))
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 10, weight: .bold))
                            .opacity(0.4)
                    }
                    .foregroundColor(.white.opacity(0.9))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.white.opacity(0.08))
                    )
                }
            }
            .frame(width: 120)
        }
    }
}

// MARK: - 鎖定畫面圓形 Widget (Lock Screen Circular)
private struct CircularAccessoryView: View {
    let entry: AssistantWidgetEntry

    var body: some View {
        ZStack {
            AccessoryWidgetBackground()
            VStack(spacing: 1) {
                Image(systemName: entry.isReachable ? "waveform" : "exclamationmark.triangle")
                    .font(.system(size: 16, weight: .bold))
                Text(entry.isReachable ? "對話" : "離線")
                    .font(.system(size: 9, weight: .semibold))
            }
        }
    }
}

// MARK: - 鎖定畫面矩形 Widget (Lock Screen Rectangular)
private struct RectangularAccessoryView: View {
    let entry: AssistantWidgetEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 4) {
                Image(systemName: "sparkles")
                    .font(.system(size: 11, weight: .bold))
                Text("Mac mini 助理")
                    .font(.system(size: 12, weight: .bold))
            }
            Text(entry.isReachable ? "🟢 在線 (\(entry.latencyMs)ms) · 額度 \(Int(entry.quotaFlashPercent))%" : "🔴 伺服器離線 (請開 VPN)")
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Text("點擊立即喚醒連續語音對話")
                .font(.system(size: 10, weight: .medium))
        }
    }
}
