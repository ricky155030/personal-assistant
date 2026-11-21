import SwiftUI

/// 背景任務追蹤專屬面板：以原生清單呈現進行中與近期失敗的背景任務，點擊可檢視即時日誌。
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
        let runningItems = trackedTasks?.running ?? []
        let failedItems = trackedTasks?.failed ?? []
        let activeCount = max(snap.activeTaskCount, runningItems.count)

        NavigationStack {
            Group {
                if isLoading && runningItems.isEmpty && failedItems.isEmpty && snap.activeTaskNames.isEmpty {
                    ProgressView("載入背景任務中...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if runningItems.isEmpty && failedItems.isEmpty && snap.activeTaskNames.isEmpty {
                    ContentUnavailableView(
                        "目前無背景任務",
                        systemImage: "gearshape.2",
                        description: Text("目前沒有執行中或近期失敗的背景任務。")
                    )
                } else {
                    List {
                        if !runningItems.isEmpty {
                            Section("進行中 (\(activeCount))") {
                                ForEach(runningItems) { item in
                                    trackedTaskRow(item: item, isError: false)
                                }
                            }
                        } else if !snap.activeTaskNames.isEmpty {
                            Section("進行中 (\(activeCount))") {
                                ForEach(snap.activeTaskNames, id: \.self) { name in
                                    HStack(spacing: 8) {
                                        Circle()
                                            .fill(Color.green)
                                            .frame(width: 7, height: 7)
                                        Text(name)
                                            .font(.system(size: 14, weight: .medium))
                                            .foregroundColor(.primary)
                                            .lineLimit(1)
                                    }
                                }
                            }
                        }

                        if !failedItems.isEmpty {
                            Section("近期失敗 / 異常（點擊查看日誌）") {
                                ForEach(failedItems.prefix(15)) { item in
                                    trackedTaskRow(item: item, isError: true)
                                }
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                    .refreshable {
                        await reload()
                    }
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

    private func trackedTaskRow(item: MacMiniAPIClient.TrackedTaskItem, isError: Bool) -> some View {
        Button {
            selectedTaskForLog = item
        } label: {
            HStack(spacing: 10) {
                Image(systemName: isError ? "exclamationmark.triangle.fill" : "circle.fill")
                    .font(.system(size: isError ? 12 : 7))
                    .foregroundColor(isError ? .red : .green)
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.name)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.primary)
                        .lineLimit(2)
                    Text("\(item.status) · \(item.started_at ?? item.task_id)")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
