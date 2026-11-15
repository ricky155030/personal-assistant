import UIKit
import SwiftUI
import UniformTypeIdentifiers

@objc(ShareViewController)
final class ShareViewController: UIViewController {
    private var hostingController: UIHostingController<ShareExtensionView>?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .clear

        let rootView = ShareExtensionView(
            loadSharedPayload: { [weak self] in
                await self?.extractSharedPayload() ?? SharedPayload()
            },
            onClose: { [weak self] in
                self?.extensionContext?.completeRequest(returningItems: nil, completionHandler: nil)
            }
        )

        let host = UIHostingController(rootView: rootView)
        host.view.backgroundColor = .clear
        addChild(host)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(host.view)
        NSLayoutConstraint.activate([
            host.view.topAnchor.constraint(equalTo: view.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
        host.didMove(toParent: self)
        self.hostingController = host
    }

    private func extractSharedPayload() async -> SharedPayload {
        guard let items = extensionContext?.inputItems as? [NSExtensionItem] else {
            return SharedPayload()
        }

        var foundURL: String = ""
        var foundText: String = ""
        var foundTitle: String = ""

        for item in items {
            if let title = item.attributedTitle?.string, !title.isEmpty, foundTitle.isEmpty {
                foundTitle = title
            }
            if let contentText = item.attributedContentText?.string, !contentText.isEmpty, foundText.isEmpty {
                foundText = contentText
            }
            guard let providers = item.attachments else { continue }
            for provider in providers {
                if foundURL.isEmpty && provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                    if let url = await loadURL(from: provider) {
                        foundURL = url.absoluteString
                    }
                }
                if foundText.isEmpty && provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                    if let text = await loadText(from: provider) {
                        foundText = text
                    }
                }
            }
        }

        // 若 text 本身就是網址且尚未取得 URL，順手補齊
        if foundURL.isEmpty, let candidate = URL(string: foundText.trimmingCharacters(in: .whitespacesAndNewlines)),
           candidate.scheme == "http" || candidate.scheme == "https" {
            foundURL = candidate.absoluteString
        }

        return SharedPayload(title: foundTitle, url: foundURL, text: foundText)
    }

    private func loadURL(from provider: NSItemProvider) async -> URL? {
        await withCheckedContinuation { cont in
            provider.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { item, _ in
                if let url = item as? URL {
                    cont.resume(returning: url)
                } else if let str = item as? String, let url = URL(string: str) {
                    cont.resume(returning: url)
                } else {
                    cont.resume(returning: nil)
                }
            }
        }
    }

    private func loadText(from provider: NSItemProvider) async -> String? {
        await withCheckedContinuation { cont in
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { item, _ in
                cont.resume(returning: item as? String)
            }
        }
    }
}

private struct SharedPayload {
    var title: String = ""
    var url: String = ""
    var text: String = ""

    var isEmpty: Bool {
        title.isEmpty && url.isEmpty && text.isEmpty
    }

    var previewText: String {
        if !title.isEmpty && !url.isEmpty {
            return "\(title)\n\(url)"
        }
        return [title, url, text].first(where: { !$0.isEmpty }) ?? "正在讀取分享內容..."
    }
}

private struct ShareExtensionView: View {
    let loadSharedPayload: () async -> SharedPayload
    let onClose: () -> Void

    @State private var payload = SharedPayload()
    @State private var isLoadingPayload = true
    @State private var customPrompt: String = ""
    @State private var presets: [SharePromptPreset] = AppGroupStore.shared.sharePrompts
    @State private var isSending = false
    @State private var statusText: String? = nil
    @State private var didSucceed = false

    private var serverURL: String {
        AppGroupStore.shared.serverURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
    }

    private var apiKey: String {
        AppGroupStore.shared.apiKey
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.08, blue: 0.12).ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // 分享內容預覽卡片
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 6) {
                                Image(systemName: "link.circle.fill")
                                    .foregroundColor(.cyan)
                                Text("分享給 Mac mini 助理")
                                    .font(.caption.weight(.semibold))
                                    .foregroundColor(.cyan)
                            }

                            if isLoadingPayload {
                                HStack(spacing: 8) {
                                    ProgressView()
                                    Text("正在讀取分享內容...")
                                        .font(.subheadline)
                                        .foregroundColor(.secondary)
                                }
                            } else {
                                Text(payload.previewText)
                                    .font(.subheadline)
                                    .foregroundColor(.white)
                                    .lineLimit(4)
                            }
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .fill(Color.white.opacity(0.06))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                                        .stroke(Color.white.opacity(0.12), lineWidth: 1)
                                )
                        )

                        if serverURL.isEmpty {
                            Text("尚未偵測到 Mac mini 伺服器設定，請先開啟一次 MacMiniAssistant 主 App 完成同步。")
                                .font(.footnote)
                                .foregroundColor(.orange)
                                .padding(12)
                                .background(Color.orange.opacity(0.12))
                                .cornerRadius(10)
                        }

                        // 快捷 Prompt 按鈕區（點擊直接送出）
                        VStack(alignment: .leading, spacing: 10) {
                            Text("一鍵快捷指令")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.secondary)

                            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                                ForEach(presets) { preset in
                                    Button(action: {
                                        sendShare(prompt: preset.prompt)
                                    }) {
                                        HStack(spacing: 8) {
                                            Image(systemName: preset.icon)
                                                .font(.system(size: 14, weight: .semibold))
                                                .foregroundColor(.cyan)
                                            Text(preset.title)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundColor(.white)
                                                .lineLimit(1)
                                            Spacer()
                                        }
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 11)
                                        .background(
                                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                                .fill(Color(red: 0.11, green: 0.17, blue: 0.27))
                                                .overlay(
                                                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                                                        .stroke(Color.cyan.opacity(0.32), lineWidth: 1)
                                                )
                                        )
                                    }
                                    .buttonStyle(.plain)
                                    .disabled(isSending || isLoadingPayload || payload.isEmpty || serverURL.isEmpty)
                                }
                            }
                        }

                        // 自訂指令輸入框
                        VStack(alignment: .leading, spacing: 8) {
                            Text("或輸入自訂指令")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.secondary)

                            HStack(spacing: 8) {
                                TextField("例如：幫我翻譯並記到待辦", text: $customPrompt)
                                    .textFieldStyle(.plain)
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 10)
                                    .foregroundColor(.white)
                                    .background(
                                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                                            .fill(Color.white.opacity(0.08))
                                    )

                                Button(action: {
                                    let p = customPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
                                    sendShare(prompt: p.isEmpty ? "幫我快速摘要重點" : p)
                                }) {
                                    Image(systemName: "paperplane.fill")
                                        .font(.system(size: 16, weight: .semibold))
                                        .foregroundColor(.white)
                                        .frame(width: 42, height: 42)
                                        .background(Color.blue)
                                        .clipShape(Circle())
                                }
                                .disabled(isSending || isLoadingPayload || payload.isEmpty || serverURL.isEmpty)
                            }
                        }

                        if let statusText = statusText {
                            HStack(spacing: 8) {
                                if isSending {
                                    ProgressView()
                                        .tint(.cyan)
                                } else {
                                    Image(systemName: didSucceed ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                        .foregroundColor(didSucceed ? .green : .red)
                                }
                                Text(statusText)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundColor(didSucceed ? .green : (isSending ? .cyan : .red))
                            }
                            .padding(.top, 4)
                        }
                    }
                    .padding(18)
                }
            }
            .navigationTitle("MacMini 助理")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { onClose() }
                        .foregroundColor(.secondary)
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            presets = AppGroupStore.shared.sharePrompts
            payload = await loadSharedPayload()
            isLoadingPayload = false
        }
    }

    private func sendShare(prompt: String) {
        guard !serverURL.isEmpty, let url = URL(string: "\(serverURL)/api/share") else {
            statusText = "伺服器網址無效"
            return
        }

        isSending = true
        didSucceed = false
        statusText = "正在傳送給 Mac mini..."

        Task {
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.timeoutInterval = 10.0
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            if !apiKey.isEmpty {
                request.setValue(apiKey, forHTTPHeaderField: "X-Agent-Key")
            }

            let body: [String: String] = [
                "prompt": prompt,
                "url": payload.url,
                "text": payload.text,
                "title": payload.title,
            ]
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)

            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                if (200...299).contains(code) {
                    isSending = false
                    didSucceed = true
                    statusText = "已送出！Mac mini 處理完會自動通知您"
                    try? await Task.sleep(nanoseconds: 650_000_000)
                    onClose()
                } else {
                    isSending = false
                    statusText = "傳送失敗 (HTTP \(code))"
                }
            } catch {
                isSending = false
                statusText = "連線失敗：\(error.localizedDescription)"
            }
        }
    }
}
