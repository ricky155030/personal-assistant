import SwiftUI

struct ConversationDrawerView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.07, blue: 0.10)
                    .ignoresSafeArea()

                VStack(spacing: 0) {
                    // 頂部：開啟新對話按鈕 + 伺服器連線狀態
                    VStack(spacing: 12) {
                        Button(action: {
                            coordinator.startNewChat()
                            dismiss()
                        }) {
                            HStack(spacing: 10) {
                                Image(systemName: "plus.bubble.fill")
                                    .font(.system(size: 16, weight: .semibold))
                                Text("開啟新對話")
                                    .font(.system(size: 16, weight: .semibold))
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundColor(.white.opacity(0.6))
                            }
                            .foregroundColor(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 13)
                            .background(
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .fill(Color(red: 0.14, green: 0.24, blue: 0.48))
                            )
                        }

                        // 連線與收音診斷資訊列
                        HStack {
                            Circle()
                                .fill(coordinator.isServerReachable ? Color.green : Color.red)
                                .frame(width: 8, height: 8)
                            Text(coordinator.serverStatusText)
                                .font(.caption)
                                .foregroundColor(.secondary)

                            Spacer()

                            Button(action: {
                                coordinator.showDebugHUD.toggle()
                                dismiss()
                            }) {
                                HStack(spacing: 4) {
                                    Image(systemName: "waveform.badge.magnifyingglass")
                                    Text(coordinator.showDebugHUD ? "隱藏收音診斷" : "顯示收音診斷")
                                }
                                .font(.caption)
                                .foregroundColor(.cyan)
                            }
                        }
                        .padding(.horizontal, 4)
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 12)
                    .padding(.bottom, 12)

                    Divider()
                        .background(Color.white.opacity(0.1))

                    // 近期對話歷史列表
                    if coordinator.sessionHistory.isEmpty {
                        VStack(spacing: 14) {
                            Spacer()
                            Image(systemName: "clock.arrow.circlepath")
                                .font(.system(size: 44))
                                .foregroundColor(.secondary.opacity(0.6))
                            Text("尚無近期對話紀錄")
                                .font(.headline)
                                .foregroundColor(.secondary)
                            Text("您與 Mac mini 助理的語音與文字對話會自動保存在此，方便隨時回顧或接續討論。")
                                .font(.subheadline)
                                .foregroundColor(.secondary.opacity(0.8))
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, 36)
                            Spacer()
                        }
                    } else {
                        List {
                            Section("近期對話 (\(coordinator.sessionHistory.count))") {
                                ForEach(coordinator.sessionHistory) { session in
                                    SessionHistoryRow(
                                        session: session,
                                        isCurrent: session.id == coordinator.currentSessionId,
                                        onSelect: {
                                            coordinator.selectSession(session)
                                            dismiss()
                                        }
                                    )
                                    .listRowBackground(
                                        session.id == coordinator.currentSessionId
                                            ? Color(red: 0.12, green: 0.18, blue: 0.32)
                                            : Color(red: 0.09, green: 0.10, blue: 0.14)
                                    )
                                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                        Button(role: .destructive) {
                                            coordinator.deleteSession(session)
                                        } label: {
                                            Label("刪除", systemImage: "trash")
                                        }
                                    }
                                }
                            }
                        }
                        .scrollContentBackground(.hidden)
                        .listStyle(.insetGrouped)
                    }
                }
            }
            .navigationTitle("近期對話歷史")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("關閉") {
                        dismiss()
                    }
                }
                if !coordinator.sessionHistory.isEmpty {
                    ToolbarItem(placement: .primaryAction) {
                        Button(role: .destructive, action: {
                            coordinator.clearAllHistory()
                        }) {
                            Image(systemName: "trash")
                                .foregroundColor(.red)
                        }
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct SessionHistoryRow: View {
    let session: ChatSession
    let isCurrent: Bool
    let onSelect: () -> Void

    private var lastAssistantReply: String {
        if let last = session.messages.last(where: { $0.role == .assistant && !$0.content.isEmpty }) {
            return last.content.replacingOccurrences(of: "\n", with: " ")
        }
        return "共 \(session.messages.count) 則訊息"
    }

    var body: some View {
        Button(action: onSelect) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: isCurrent ? "sparkles" : "bubble.left.and.bubble.right")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(isCurrent ? .cyan : .secondary)

                    Text(session.title)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.white)
                        .lineLimit(1)

                    Spacer()

                    Text(session.updatedAt, style: .relative)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }

                Text(lastAssistantReply)
                    .font(.system(size: 13))
                    .foregroundColor(.white.opacity(0.65))
                    .lineLimit(2)

                HStack(spacing: 8) {
                    Text("\(session.messages.count) 則對話")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)

                    if isCurrent {
                        Text("目前對話")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.cyan)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.cyan.opacity(0.15))
                            .clipShape(Capsule())
                    }
                }
            }
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
    }
}
