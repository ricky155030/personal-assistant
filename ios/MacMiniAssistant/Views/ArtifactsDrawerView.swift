import SwiftUI

/// 助理交付產物櫃：僅展示助理曾正式貼給主人的報告網址與圖片產物，支援開啟、引用追問與安全軟移除。
struct ArtifactsDrawerView: View {
    var onQuoteArtifact: (ArtifactItem) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var artifacts: [ArtifactItem] = []
    @State private var isLoading: Bool = true

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && artifacts.isEmpty {
                    ProgressView("載入產物中...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if artifacts.isEmpty {
                    ContentUnavailableView(
                        "尚無交付產物",
                        systemImage: "shippingbox",
                        description: Text("當助理貼給您 HTML 報告或生成圖片時，會自動收錄在這裡。")
                    )
                } else {
                    List {
                        ForEach(artifacts) { item in
                            HStack(spacing: 12) {
                                Button {
                                    if let url = URL(string: item.url) {
                                        openURL(url)
                                    }
                                } label: {
                                    HStack(spacing: 12) {
                                        Image(systemName: item.type == "image" ? "photo.fill" : "doc.richtext.fill")
                                            .font(.system(size: 18, weight: .semibold))
                                            .foregroundColor(item.type == "image" ? .purple : .cyan)
                                            .frame(width: 34, height: 34)
                                            .background((item.type == "image" ? Color.purple : Color.cyan).opacity(0.14))
                                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(item.title)
                                                .font(.system(size: 15, weight: .semibold))
                                                .foregroundColor(.primary)
                                                .lineLimit(1)
                                            Text(formattedTime(item.updatedAt.isEmpty ? item.createdAt : item.updatedAt))
                                                .font(.system(size: 12))
                                                .foregroundColor(.secondary)
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)

                                Button {
                                    onQuoteArtifact(item)
                                    dismiss()
                                } label: {
                                    Text("引用")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundColor(.cyan)
                                        .padding(.horizontal, 10)
                                        .padding(.vertical, 6)
                                        .background(Color.cyan.opacity(0.14))
                                        .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button(role: .destructive) {
                                    Task {
                                        if let updated = await MacMiniAPIClient.shared.removeArtifact(id: item.id) {
                                            artifacts = updated
                                        }
                                    }
                                } label: {
                                    Label("移除", systemImage: "eye.slash")
                                }
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                    .refreshable {
                        artifacts = await MacMiniAPIClient.shared.fetchArtifacts()
                    }
                }
            }
            .navigationTitle("產物")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .task {
                isLoading = true
                artifacts = await MacMiniAPIClient.shared.fetchArtifacts()
                isLoading = false
            }
        }
    }

    private func formattedTime(_ iso: String) -> String {
        guard iso.count >= 16 else { return iso }
        return String(iso.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }
}
