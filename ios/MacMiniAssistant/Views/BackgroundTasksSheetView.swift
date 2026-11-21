import SwiftUI

/// 背景任務追蹤專屬面板：僅顯示進行中與近期失敗的背景任務，點擊可檢視即時日誌。
struct BackgroundTasksSheetView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    @Environment(\.dismiss) private var dismiss
    @State private var trackedTasks: MacMiniAPIClient.TrackedTasksResponse? = nil
    @State private var selectedTaskForLog: MacMiniAPIClient.TrackedTaskItem? = nil
    @State private var isLoading: Bool = true

    private var snap: QuotaSnapshot {
        coordinator.quotaSnapshot ?? QuotaSnapshot()
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.07, blue: 0.10).ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 14) {
                        backgroundTasksCard
                    }
                    .padding(16)
                }
            }
            .navigationTitle("背景任務")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        Task { await reload() }
                    } label: {
                        if isLoading {
                            ProgressView().scaleEffect(0.75)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(isLoading)
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                        .bold()
                }
            }
            .task {
                await reload()
            }
            .sheet(item: $selectedTaskForLog) { task in
                TaskLogViewerSheet(task: task, coordinator: coordinator) {
                    selectedTaskForLog = nil
                    dismiss()
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func reload() async {
        isLoading = true
        async let tasksReq = coordinator.apiClient.fetchTrackedTasks()
        coordinator.refreshQuotaStatus(force: true)
        trackedTasks = await tasksReq
        isLoading = false
    }

    private var backgroundTasksCard: some View {
        let runningItems = trackedTasks?.running ?? []
        let failedItems = trackedTasks?.failed ?? []
        let activeCount = max(snap.activeTaskCount, runningItems.count)

        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("背景任務追蹤 (進行中 / 失敗)", systemImage: "gearshape.2.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                Spacer()
                HStack(spacing: 5) {
                    Circle()
                        .fill(activeCount > 0 ? Color.green : Color.gray)
                        .frame(width: 8, height: 8)
                    Text("\(activeCount) 執行中")
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundColor(activeCount > 0 ? .green : .secondary)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(activeCount > 0 ? Color.green.opacity(0.15) : Color.white.opacity(0.05))
                .cornerRadius(6)
            }

            if !runningItems.isEmpty {
                Divider().background(Color.white.opacity(0.08))
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(runningItems) { item in
                        trackedTaskRow(item: item, isError: false)
                    }
                }
            } else if !snap.activeTaskNames.isEmpty {
                Divider().background(Color.white.opacity(0.08))
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(snap.activeTaskNames, id: \.self) { name in
                        HStack(spacing: 7) {
                            Image(systemName: "circle.fill")
                                .font(.system(size: 6))
                                .foregroundColor(.green)
                            Text(name)
                                .font(.system(size: 12.5, weight: .medium))
                                .foregroundColor(Color(white: 0.88))
                                .lineLimit(1)
                        }
                    }
                }
            } else {
                Text("目前無執行中的背景任務")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if !failedItems.isEmpty {
                Divider().background(Color.white.opacity(0.08))
                Text("近期失敗 / 異常紀錄（點擊查看日誌）")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundColor(.orange)
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(failedItems.prefix(15)) { item in
                        trackedTaskRow(item: item, isError: true)
                    }
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.white.opacity(0.05))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(Color.white.opacity(0.08), lineWidth: 1)
                )
        )
    }

    private func trackedTaskRow(item: MacMiniAPIClient.TrackedTaskItem, isError: Bool) -> some View {
        Button {
            selectedTaskForLog = item
        } label: {
            HStack(spacing: 8) {
                Image(systemName: isError ? "exclamationmark.triangle.fill" : "circle.fill")
                    .font(.system(size: isError ? 11 : 7))
                    .foregroundColor(isError ? .red : .green)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundColor(.white)
                        .lineLimit(1)
                    Text("\(item.status) · \(item.started_at ?? item.task_id)")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.secondary)
            }
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
