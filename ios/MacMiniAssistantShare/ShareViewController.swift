import UIKit
import SwiftUI
import UniformTypeIdentifiers
import ImageIO

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
        var foundImages: [UIImage] = []
        var foundFiles: [SharedFileItem] = []

        for item in items {
            if let title = item.attributedTitle?.string, !title.isEmpty, foundTitle.isEmpty {
                foundTitle = title
            }
            if let contentText = item.attributedContentText?.string, !contentText.isEmpty, foundText.isEmpty {
                foundText = contentText
            }
            guard let providers = item.attachments else { continue }
            for provider in providers {
                if foundImages.count < 9 && provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                    if let img = await loadImage(from: provider) {
                        foundImages.append(downscaleImage(img, maxDimension: 1280))
                    }
                    continue
                }
                if foundFiles.count < 5 && (
                    provider.hasItemConformingToTypeIdentifier(UTType.pdf.identifier)
                    || provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier)
                    || (!provider.hasItemConformingToTypeIdentifier(UTType.url.identifier)
                        && !provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier)
                        && provider.hasItemConformingToTypeIdentifier(UTType.data.identifier))
                ) {
                    if let fileItem = await loadFile(from: provider) {
                        foundFiles.append(fileItem)
                        continue
                    }
                }
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
        if foundURL.isEmpty, let candidate = URL(string: foundText.trimmingCharacters(in: CharacterSet.whitespacesAndNewlines)),
           candidate.scheme == "http" || candidate.scheme == "https" {
            foundURL = candidate.absoluteString
        }

        return SharedPayload(title: foundTitle, url: foundURL, text: foundText, images: foundImages, files: foundFiles)
    }

    private func loadImage(from provider: NSItemProvider) async -> UIImage? {
        // 優先使用 loadFileRepresentation + ImageIO 直接產出縮圖，避免在 Share Extension (120MB 限制) 解碼完整 48MP 原圖導致閃退
        if let thumb = await loadThumbnailViaFileRepresentation(from: provider, maxPixelSize: 1280) {
            return thumb
        }
        return await withCheckedContinuation { cont in
            let options: [AnyHashable: Any] = [
                NSItemProviderPreferredImageSizeKey: NSValue(cgSize: CGSize(width: 1280, height: 1280))
            ]
            provider.loadItem(forTypeIdentifier: UTType.image.identifier, options: options) { item, _ in
                if let url = item as? URL, let thumb = Self.thumbnailImage(fromURL: url, maxPixelSize: 1280) {
                    cont.resume(returning: thumb)
                } else if let data = item as? Data, let thumb = Self.thumbnailImage(fromData: data, maxPixelSize: 1280) {
                    cont.resume(returning: thumb)
                } else if let image = item as? UIImage {
                    cont.resume(returning: image)
                } else {
                    cont.resume(returning: nil)
                }
            }
        }
    }

    private func loadThumbnailViaFileRepresentation(from provider: NSItemProvider, maxPixelSize: CGFloat) async -> UIImage? {
        await withCheckedContinuation { cont in
            provider.loadFileRepresentation(forTypeIdentifier: UTType.image.identifier) { url, _ in
                guard let url = url else {
                    cont.resume(returning: nil)
                    return
                }
                let thumb = Self.thumbnailImage(fromURL: url, maxPixelSize: maxPixelSize)
                cont.resume(returning: thumb)
            }
        }
    }

    private static func thumbnailImage(fromURL url: URL, maxPixelSize: CGFloat) -> UIImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        return thumbnailImage(fromSource: source, maxPixelSize: maxPixelSize)
    }

    private static func thumbnailImage(fromData data: Data, maxPixelSize: CGFloat) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return thumbnailImage(fromSource: source, maxPixelSize: maxPixelSize)
    }

    private static func thumbnailImage(fromSource source: CGImageSource, maxPixelSize: CGFloat) -> UIImage? {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceShouldCacheImmediately: false,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
        ]
        guard let cgImage = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            return nil
        }
        return UIImage(cgImage: cgImage)
    }

    private func downscaleImage(_ image: UIImage, maxDimension: CGFloat) -> UIImage {
        let size = image.size
        let maxSide = max(size.width, size.height)
        guard maxSide > maxDimension, maxSide > 0 else { return image }
        let scale = maxDimension / maxSide
        let newSize = CGSize(width: size.width * scale, height: size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: newSize)
        return renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: newSize))
        }
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

    private func loadFile(from provider: NSItemProvider) async -> SharedFileItem? {
        let preferredType = provider.registeredTypeIdentifiers.first(where: { $0 != UTType.fileURL.identifier }) ?? UTType.data.identifier
        return await withCheckedContinuation { cont in
            provider.loadFileRepresentation(forTypeIdentifier: preferredType) { url, _ in
                guard let url = url, let data = try? Data(contentsOf: url), !data.isEmpty, data.count <= 20 * 1024 * 1024 else {
                    cont.resume(returning: nil)
                    return
                }
                let filename = url.lastPathComponent.isEmpty ? "shared_file" : url.lastPathComponent
                let mimeType = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
                let sizeText = ByteCountFormatter.string(fromByteCount: Int64(data.count), countStyle: .file)
                cont.resume(returning: SharedFileItem(filename: filename, mimeType: mimeType, data: data, sizeText: sizeText))
            }
        }
    }
}

private struct SharedFileItem: Identifiable {
    let id = UUID()
    let filename: String
    let mimeType: String
    let data: Data
    let sizeText: String
}

private struct SharedPayload {
    var title: String = ""
    var url: String = ""
    var text: String = ""
    var images: [UIImage] = []
    var files: [SharedFileItem] = []

    var isEmpty: Bool {
        title.isEmpty && url.isEmpty && text.isEmpty && images.isEmpty && files.isEmpty
    }

    var previewText: String {
        if !files.isEmpty && title.isEmpty && url.isEmpty && text.isEmpty {
            return "已選取 \(files.count) 份檔案"
        }
        if !images.isEmpty && title.isEmpty && url.isEmpty && text.isEmpty {
            return "已選取 \(images.count) 張照片"
        }
        if !title.isEmpty && !url.isEmpty {
            return "\(title)\n\(url)"
        }
        return [title, url, text].first(where: { !$0.isEmpty }) ?? "正在讀取分享內容..."
    }
}

private let imageSharePresets: [SharePromptPreset] = [
    SharePromptPreset(title: "蝦皮上架", prompt: "幫我用蝦皮賣家技能辨識這些商品照片並上架", icon: "bag.fill"),
    SharePromptPreset(title: "嬰兒備品", prompt: "幫我把這些實品照片新增到嬰幼兒備品清單", icon: "stroller.fill"),
    SharePromptPreset(title: "記帳憑證", prompt: "幫我辨識這張收據或帳單明細並記錄", icon: "doc.text.viewfinder"),
    SharePromptPreset(title: "看圖分析", prompt: "請幫我詳細辨識並分析這些圖片內容", icon: "eye.fill"),
]

private let fileSharePresets: [SharePromptPreset] = [
    SharePromptPreset(title: "重點摘要", prompt: "請幫我閱讀這份文件並整理核心重點條列", icon: "doc.text.magnifyingglass"),
    SharePromptPreset(title: "帳單對帳", prompt: "請幫我解析這份帳單或明細並檢查異常項目", icon: "creditcard.fill"),
    SharePromptPreset(title: "翻譯重點", prompt: "請幫我將這份文件翻譯成繁體中文重點摘要", icon: "character.book.closed.fill"),
    SharePromptPreset(title: "整理待辦", prompt: "請從這份文件中萃取需要我處理的待辦事項", icon: "checklist"),
]

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
    @State private var localServerURL: String = UserDefaults.standard.string(forKey: "share_ext_server_url") ?? ""
    @State private var localAPIKey: String = UserDefaults.standard.string(forKey: "share_ext_api_key") ?? ""
    @State private var inputServerURL: String = ""
    @State private var inputAPIKey: String = ""

    private var serverURL: String {
        let shared = AppGroupStore.shared.serverURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        if !shared.isEmpty { return shared }
        return localServerURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
    }

    private var apiKey: String {
        let shared = AppGroupStore.shared.apiKey
        if !shared.isEmpty { return shared }
        return localAPIKey
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
                                if !payload.images.isEmpty {
                                    ScrollView(.horizontal, showsIndicators: false) {
                                        HStack(spacing: 8) {
                                            ForEach(Array(payload.images.enumerated()), id: \.offset) { _, img in
                                                Image(uiImage: img)
                                                    .resizable()
                                                    .scaledToFill()
                                                    .frame(width: 68, height: 68)
                                                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                                                    .overlay(
                                                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                                                            .stroke(Color.white.opacity(0.18), lineWidth: 1)
                                                    )
                                            }
                                        }
                                    }
                                    .padding(.bottom, 2)
                                }
                                if !payload.files.isEmpty {
                                    VStack(alignment: .leading, spacing: 6) {
                                        ForEach(payload.files) { file in
                                            HStack(spacing: 8) {
                                                Image(systemName: "doc.fill")
                                                    .foregroundColor(.cyan)
                                                Text(file.filename)
                                                    .font(.subheadline.weight(.medium))
                                                    .foregroundColor(.white)
                                                    .lineLimit(1)
                                                Spacer()
                                                Text(file.sizeText)
                                                    .font(.caption)
                                                    .foregroundColor(.secondary)
                                            }
                                            .padding(.horizontal, 10)
                                            .padding(.vertical, 6)
                                            .background(RoundedRectangle(cornerRadius: 8).fill(Color.white.opacity(0.08)))
                                        }
                                    }
                                    .padding(.bottom, 2)
                                }
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
                            VStack(alignment: .leading, spacing: 10) {
                                Text("尚未偵測到共享伺服器設定（SideStore 免費簽名可能隔離了 App Group），請在此輸入一次即可永久記住：")
                                    .font(.footnote)
                                    .foregroundColor(.orange)

                                TextField("伺服器網址（例如 http://...ts.net:8899）", text: $inputServerURL)
                                    .textFieldStyle(.plain)
                                    .keyboardType(.URL)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 8)
                                    .foregroundColor(.white)
                                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.white.opacity(0.08)))

                                SecureField("API Key（選填）", text: $inputAPIKey)
                                    .textFieldStyle(.plain)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 8)
                                    .foregroundColor(.white)
                                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.white.opacity(0.08)))

                                Button(action: {
                                    let trimmedURL = inputServerURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ \n"))
                                    let trimmedKey = inputAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
                                    guard !trimmedURL.isEmpty else { return }
                                    UserDefaults.standard.set(trimmedURL, forKey: "share_ext_server_url")
                                    UserDefaults.standard.set(trimmedKey, forKey: "share_ext_api_key")
                                    localServerURL = trimmedURL
                                    localAPIKey = trimmedKey
                                }) {
                                    Text("儲存伺服器設定")
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundColor(.white)
                                        .frame(maxWidth: .infinity)
                                        .padding(.vertical, 9)
                                        .background(Color.orange)
                                        .cornerRadius(8)
                                }
                                .buttonStyle(.plain)
                            }
                            .padding(12)
                            .background(Color.orange.opacity(0.12))
                            .cornerRadius(10)
                        }

                        // 快捷 Prompt 按鈕區（點擊直接送出）
                        VStack(alignment: .leading, spacing: 10) {
                            Text("一鍵快捷指令")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.secondary)

                            let activePresets: [SharePromptPreset] = {
                                if !payload.files.isEmpty { return fileSharePresets }
                                if !payload.images.isEmpty { return imageSharePresets }
                                return presets
                            }()
                            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                                ForEach(activePresets) { preset in
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
            request.timeoutInterval = (payload.images.isEmpty && payload.files.isEmpty) ? 10.0 : 35.0
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            if !apiKey.isEmpty {
                request.setValue(apiKey, forHTTPHeaderField: "X-Agent-Key")
            }

            let imagesBase64 = payload.images.compactMap { img -> String? in
                img.jpegData(compressionQuality: 0.72)?.base64EncodedString()
            }
            let filesPayload = payload.files.map { file -> [String: String] in
                [
                    "filename": file.filename,
                    "mime_type": file.mimeType,
                    "data_base64": file.data.base64EncodedString(),
                ]
            }

            var body: [String: Any] = [
                "prompt": prompt,
                "url": payload.url,
                "text": payload.text,
                "title": payload.title,
            ]
            if !imagesBase64.isEmpty {
                body["images_base64"] = imagesBase64
            }
            if !filesPayload.isEmpty {
                body["files"] = filesPayload
            }
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
