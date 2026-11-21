import SwiftUI
import PhotosUI
import UIKit
import AVKit
import ImageIO
import UniformTypeIdentifiers

// MARK: - Gemini 標誌性四色四角星芒 Shape
struct GeminiSparkleShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let w = rect.width
        let h = rect.height
        let cx = rect.midX
        let cy = rect.midY

        let top = CGPoint(x: cx, y: 0)
        let right = CGPoint(x: w, y: cy)
        let bottom = CGPoint(x: cx, y: h)
        let left = CGPoint(x: 0, y: cy)

        // 控制點內縮程度（形成優雅的內凹弧線四角星）
        let curveInset: CGFloat = 0.14
        let cTopRight = CGPoint(x: cx + w * curveInset, y: cy - h * curveInset)
        let cBottomRight = CGPoint(x: cx + w * curveInset, y: cy + h * curveInset)
        let cBottomLeft = CGPoint(x: cx - w * curveInset, y: cy + h * curveInset)
        let cTopLeft = CGPoint(x: cx - w * curveInset, y: cy - h * curveInset)

        path.move(to: top)
        path.addQuadCurve(to: right, control: cTopRight)
        path.addQuadCurve(to: bottom, control: cBottomRight)
        path.addQuadCurve(to: left, control: cBottomLeft)
        path.addQuadCurve(to: top, control: cTopLeft)
        path.closeSubpath()
        return path
    }
}

struct GeminiSparkleIcon: View {
    var size: CGFloat = 44
    var isAnimating: Bool = false

    @State private var scale: CGFloat = 1.0

    var body: some View {
        Image("AssistantAvatar")
            .resizable()
            .scaledToFill()
            .frame(width: size, height: size)
            .clipShape(Circle())
            .overlay(
                Circle()
                    .stroke(
                        isAnimating ? Color.cyan.opacity(0.85) : Color.white.opacity(0.22),
                        lineWidth: isAnimating ? 1.5 : 0.8
                    )
            )
            .shadow(color: isAnimating ? Color.cyan.opacity(0.45) : Color.clear, radius: 5)
            .scaleEffect(isAnimating ? scale : 1.0)
            .onAppear {
                if isAnimating {
                    withAnimation(.easeInOut(duration: 1.4).repeatForever(autoreverses: true)) {
                        scale = 1.12
                    }
                }
            }
    }
}

enum ModelBrand {
    case gemini
    case claude
    case muse

    static func resolve(engineId: String, modelId: String) -> ModelBrand {
        let low = modelId.lowercased()
        if low.contains("claude") || low.contains("opus") || low.contains("sonnet") {
            return .claude
        }
        if low.contains("muse") || (engineId == "omp" && !low.contains("claude")) {
            return .muse
        }
        return .gemini
    }
}

struct ModelBrandIcon: View {
    let brand: ModelBrand
    var size: CGFloat = 15

    var body: some View {
        switch brand {
        case .gemini:
            GeminiSparkleShape()
                .fill(
                    LinearGradient(
                        colors: [
                            Color(red: 0.26, green: 0.52, blue: 0.96),
                            Color(red: 0.61, green: 0.45, blue: 0.80),
                            Color(red: 0.85, green: 0.40, blue: 0.44)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: size, height: size)

        case .claude:
            Canvas { context, canvasSize in
                let center = CGPoint(x: canvasSize.width / 2, y: canvasSize.height / 2)
                let outerR = min(canvasSize.width, canvasSize.height) * 0.45
                let innerR = outerR * 0.34
                let color = Color(red: 0.85, green: 0.47, blue: 0.34)
                for i in 0..<8 {
                    let angle = Double(i) * (.pi / 4.0)
                    let p1 = CGPoint(x: center.x + CGFloat(cos(angle)) * innerR, y: center.y + CGFloat(sin(angle)) * innerR)
                    let p2 = CGPoint(x: center.x + CGFloat(cos(angle)) * outerR, y: center.y + CGFloat(sin(angle)) * outerR)
                    var path = Path()
                    path.move(to: p1)
                    path.addLine(to: p2)
                    context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: max(1.4, size * 0.12), lineCap: .round))
                }
                let dotR = max(1.6, size * 0.11)
                context.fill(
                    Path(ellipseIn: CGRect(x: center.x - dotR, y: center.y - dotR, width: dotR * 2, height: dotR * 2)),
                    with: .color(color)
                )
            }
            .frame(width: size, height: size)

        case .muse:
            ZStack {
                GeminiSparkleShape()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.22, green: 0.74, blue: 0.97),
                                Color(red: 0.66, green: 0.33, blue: 0.97)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: size * 0.80, height: size * 0.80)
                    .offset(x: -size * 0.10, y: -size * 0.08)

                GeminiSparkleShape()
                    .fill(Color(red: 0.22, green: 0.74, blue: 0.97))
                    .frame(width: size * 0.40, height: size * 0.40)
                    .offset(x: size * 0.28, y: size * 0.26)
            }
            .frame(width: size, height: size)
        }
    }
}

// MARK: - 主畫面：Gemini 風格直接對話 + Live 即時語音對談
struct HandsFreeMainView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    @Environment(\.scenePhase) private var scenePhase

    @State private var showQuotaSheet: Bool = false
    @State private var showQuickPromptDrawer: Bool = false
    @State private var showArtifactsSheet: Bool = false
    @State private var showBackgroundTasksSheet: Bool = false
    @State private var quickPrompts: [QuickPromptPreset] = QuickPromptStore.loadPresets()
    @State private var showSettings: Bool = false
    @State private var showSearchSheet: Bool = false
    @State private var scrollToMessageId: UUID? = nil
    @State private var showModelPopover: Bool = false
    @State private var isAtBottom: Bool = true
    @State private var userScrolledUp: Bool = false
    @State private var lastUserScrollUpGestureAt: Date = .distantPast
    @State private var hasUnseenUpdates: Bool = false
    @State private var lastFollowScrollAt: Date = .distantPast
    @State private var isDraggingBottom: Bool = false
    @State private var startedDragAtBottom: Bool = false
    @State private var bottomPullOffset: CGFloat = 0
    @State private var bottomPullHapticTriggered: Bool = false
    @State private var isRefreshingHistory: Bool = false
    @State private var showVisionCamera: Bool = false
    @State private var showCameraPicker: Bool = false
    @State private var showPhotoLibraryPicker: Bool = false
    @State private var showFileImporter: Bool = false
    @State private var showAttachmentSheet: Bool = false
    @State private var selectedPhotoItems: [PhotosPickerItem] = []
    @State private var quotedMessage: ChatMessage? = nil
    @State private var herdrAgents: [HerdrAgentInfo] = []
    @State private var showMentionPicker: Bool = false
    @State private var mentionQuery: String = ""
    @State private var isLoadingHerdrAgents: Bool = false
    @State private var pendingActions: [PendingActionItem] = []
    @State private var herdrHistoryActions: [PendingActionItem] = []
    @State private var showPendingActionsSheet: Bool = false
    @State private var availableAppUpdate: MacMiniAPIClient.AppUpdateInfo? = nil
    @State private var isUpdateBannerDismissed: Bool = false
    @Environment(\.openURL) private var openURL
    @AppStorage("chatBaseFontSize") private var chatBaseFontSize: Double = 15.0
    @AppStorage("showQuickSkillBar") private var showQuickSkillBar: Bool = true
    @FocusState private var isInputFocused: Bool

    /// 單則訊息最多可附加的照片張數
    private static let maxAttachedImages = 5

    private let haptic = UIImpactFeedbackGenerator(style: .medium)

    var body: some View {
        ZStack {
            // 1. Gemini 純黑至深邃午夜藍漸層背景
            geminiBackground
                .onTapGesture {
                    isInputFocused = false
                }

            VStack(spacing: 0) {
                // 2. 頂部導覽列（連線狀態燈 + 模型膠囊 / 合併用量膠囊 / 設定）
                topHeaderBar
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 8)

                // 離線提示橫幅（確認離線時自動滑出，恢復後自動收起）
                if isConfirmedOffline {
                    offlineBanner
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                // 新版 App 更新提醒橫幅（比對 GitHub SideStore 軟體源）
                if let update = availableAppUpdate, !isUpdateBannerDismissed {
                    appUpdateBanner(update)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                // 除錯診斷 HUD（於設定頁開啟）
                if coordinator.showDebugHUD {
                    debugHUDView
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                // 3. 中央主區域：即時對話流
                conversationScrollSection

                // 4. 底部 Gemini 輸入膠囊與 Live 語音控制區
                bottomInputSection
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 12)
            }
            .animation(.easeInOut(duration: 0.25), value: isConfirmedOffline)
        }
        .sheet(isPresented: $showQuotaSheet) {
            QuotaDetailSheetView(coordinator: coordinator)
                .presentationDetents([.height(435), .medium])
                .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showQuickPromptDrawer) {
            QuickPromptDrawerView(presets: $quickPrompts) { prompt in
                haptic.impactOccurred()
                isInputFocused = false
                coordinator.sendQuickReply(prompt)
            }
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showArtifactsSheet) {
            ArtifactsDrawerView { artifact in
                haptic.impactOccurred()
                quotedMessage = ChatMessage(
                    role: .assistant,
                    content: "產物「\(artifact.title)」：\(artifact.url)"
                )
                isInputFocused = true
            }
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showBackgroundTasksSheet) {
            BackgroundTasksSheetView(coordinator: coordinator)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showSettings) {
            AssistantSettingsView(coordinator: coordinator)
        }
        .sheet(isPresented: $showPendingActionsSheet) {
            PendingActionsSheetView(
                actions: $pendingActions,
                history: $herdrHistoryActions,
                onRefresh: {
                    let payload = await MacMiniAPIClient.shared.fetchPendingActionsPayload()
                    await MainActor.run {
                        self.pendingActions = payload.actions
                        self.herdrHistoryActions = payload.history
                    }
                },
                onResolve: { item, answer, dismiss, actionMode in
                    let (ok, updated, updatedHistory, dispatchedTo) = await MacMiniAPIClient.shared.resolvePendingAction(
                        id: item.id,
                        answer: answer,
                        dismiss: dismiss,
                        action: actionMode
                    )
                    await MainActor.run {
                        if ok {
                            self.pendingActions = updated
                            self.herdrHistoryActions = updatedHistory
                            if !dismiss {
                                self.coordinator.recordAndSyncActionDecision(
                                    title: item.title,
                                    answer: answer,
                                    dispatchedTo: dispatchedTo
                                )
                            }
                        }
                    }
                    return ok
                }
            )
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showSearchSheet) {
            HistorySearchSheetView(
                coordinator: coordinator,
                onJumpToMessage: { targetId in
                    showSearchSheet = false
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                        scrollToMessageId = targetId
                    }
                },
                onQuoteSnippet: { text in
                    showSearchSheet = false
                    coordinator.textInput = "針對先前這段紀錄追問：\n「\(text.prefix(180))」\n\n"
                }
            )
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
        }
        .fullScreenCover(isPresented: $showVisionCamera) {
            VisionCameraView(coordinator: coordinator)
        }
        .fullScreenCover(isPresented: $showCameraPicker) {
            CameraImagePicker { image in
                if let jpeg = Self.compressImage(image) {
                    appendPendingImages([jpeg])
                }
            }
            .ignoresSafeArea()
        }
        .photosPicker(
            isPresented: $showPhotoLibraryPicker,
            selection: $selectedPhotoItems,
            maxSelectionCount: max(1, Self.maxAttachedImages - coordinator.pendingImagesData.count),
            matching: .images
        )
        .onChange(of: selectedPhotoItems) { _, newItems in
            guard !newItems.isEmpty else { return }
            Task {
                var loaded: [Data] = []
                for item in newItems {
                    if let data = try? await item.loadTransferable(type: Data.self),
                       let uiImage = UIImage(data: data),
                       let jpeg = Self.compressImage(uiImage) {
                        loaded.append(jpeg)
                    }
                }
                await MainActor.run {
                    appendPendingImages(loaded)
                    selectedPhotoItems = []
                }
            }
        }
        .fileImporter(
            isPresented: $showFileImporter,
            allowedContentTypes: [.pdf, .plainText, .commaSeparatedText, .json, .xml, .data, .item],
            allowsMultipleSelection: true
        ) { result in
            switch result {
            case .success(let urls):
                Task.detached(priority: .userInitiated) {
                    var picked: [PendingUploadFile] = []
                    for url in urls.prefix(5) {
                        let scoped = url.startAccessingSecurityScopedResource()
                        defer {
                            if scoped { url.stopAccessingSecurityScopedResource() }
                        }
                        if let data = try? Data(contentsOf: url), data.count <= 15 * 1024 * 1024 {
                            picked.append(PendingUploadFile(filename: url.lastPathComponent, data: data))
                        }
                    }
                    let immutablePicked = picked
                    await MainActor.run {
                        let room = max(0, 5 - coordinator.pendingFiles.count)
                        coordinator.pendingFiles.append(contentsOf: immutablePicked.prefix(room))
                    }
                }
            case .failure(let err):
                coordinator.errorMessage = "檔案選取失敗：\(err.localizedDescription)"
            }
        }
        .onAppear {
            syncIdleTimer()
            Task {
                await MacMiniAPIClient.shared.reportAppState(active: scenePhase == .active)
                async let pendingTask = MacMiniAPIClient.shared.fetchPendingActionsPayload()
                async let updateTask = MacMiniAPIClient.shared.checkForAppUpdate()
                let (payload, update) = await (pendingTask, updateTask)
                await MainActor.run {
                    self.pendingActions = payload.actions
                    self.herdrHistoryActions = payload.history
                    self.availableAppUpdate = update
                }
            }
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                guard scenePhase == .active else { continue }
                let payload = await MacMiniAPIClient.shared.fetchPendingActionsPayload()
                await MainActor.run {
                    self.pendingActions = payload.actions
                    self.herdrHistoryActions = payload.history
                }
            }
        }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
        .onChange(of: shouldKeepScreenAwake) { syncIdleTimer() }
        .onChange(of: scenePhase) { _, newPhase in
            syncIdleTimer()
            switch newPhase {
            case .active:
                Task {
                    await MacMiniAPIClient.shared.reportAppState(active: true)
                    let payload = await MacMiniAPIClient.shared.fetchPendingActionsPayload()
                    await MainActor.run {
                        self.pendingActions = payload.actions
                        self.herdrHistoryActions = payload.history
                    }
                }
            case .background:
                // 切到背景時短暫申請背景任務確保狀態回報送達伺服器，方便後端決定是否補發唯一 HA 重要通知
                var bgTaskId: UIBackgroundTaskIdentifier = .invalid
                bgTaskId = UIApplication.shared.beginBackgroundTask(withName: "ReportAppBackground") {
                    if bgTaskId != .invalid {
                        UIApplication.shared.endBackgroundTask(bgTaskId)
                        bgTaskId = .invalid
                    }
                }
                Task {
                    await MacMiniAPIClient.shared.reportAppState(active: false)
                    if bgTaskId != .invalid {
                        UIApplication.shared.endBackgroundTask(bgTaskId)
                        bgTaskId = .invalid
                    }
                }
            default:
                break
            }
        }
    }

    /// 思考、串流、語音朗讀或開啟語音對話期間保持螢幕常亮，結束後自動恢復系統休眠
    private var shouldKeepScreenAwake: Bool {
        coordinator.isProcessingOrSpeaking || coordinator.isLiveMode || coordinator.voiceInputMode == .singleTurn
    }

    private func syncIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = (scenePhase == .active && shouldKeepScreenAwake)
    }

    /// 將新照片加入待送出佇列（相容舊的單張 pendingImageData，並限制最多張數）
    private func appendPendingImages(_ newImages: [Data]) {
        guard !newImages.isEmpty else { return }
        var merged = coordinator.pendingImagesData
        if let legacy = coordinator.pendingImageData {
            merged.insert(legacy, at: 0)
            coordinator.pendingImageData = nil
        }
        merged.append(contentsOf: newImages)
        coordinator.pendingImagesData = Array(merged.prefix(Self.maxAttachedImages))
    }

    // MARK: - 離線提示橫幅
    private var offlineBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(.orange)

            VStack(alignment: .leading, spacing: 1) {
                Text("與 Mac mini 連線中斷")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.white)
                Text("每 12 秒自動重試，恢復後會自動收起")
                    .font(.system(size: 11))
                    .foregroundColor(Color(white: 0.65))
            }

            Spacer()

            Button(action: {
                haptic.impactOccurred()
                coordinator.checkServerHealth()
            }) {
                Text("立即重試")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.orange)
                    .padding(.horizontal, 12)
                    .frame(height: 30)
                    .background(Capsule().fill(Color.orange.opacity(0.16)))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(red: 0.20, green: 0.13, blue: 0.05).opacity(0.9))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.orange.opacity(0.35), lineWidth: 1)
                )
        )
    }

    private func appUpdateBanner(_ update: MacMiniAPIClient.AppUpdateInfo) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "arrow.down.circle.fill")
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(.cyan)

            VStack(alignment: .leading, spacing: 1) {
                Text("發現新版 v\(update.version) (Build \(update.buildVersion))")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.white)
                Text("您尚未更新，請前往 SideStore 下載新版")
                    .font(.system(size: 11))
                    .foregroundColor(Color(white: 0.72))
            }

            Spacer()

            Button(action: {
                haptic.impactOccurred()
                if let url = URL(string: "sidestore://") {
                    openURL(url)
                }
            }) {
                Text("去更新")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.cyan)
                    .padding(.horizontal, 12)
                    .frame(height: 30)
                    .background(Capsule().fill(Color.cyan.opacity(0.18)))
            }
            .buttonStyle(.plain)

            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isUpdateBannerDismissed = true
                }
            }) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(Color(white: 0.55))
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(red: 0.06, green: 0.16, blue: 0.22).opacity(0.92))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.cyan.opacity(0.35), lineWidth: 1)
                )
        )
    }

    // MARK: - 錯誤卡片（同步進度 / 帶回輸入框）
    private func errorCard(_ err: String) -> some View {
        let lastUserText = coordinator.messages.last(where: { $0.role == .user })?.content
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 14))
                    .foregroundColor(.red.opacity(0.9))
                    .padding(.top, 1)

                Text(err)
                    .font(.system(size: 13))
                    .foregroundColor(.white.opacity(0.88))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }

            HStack(spacing: 8) {
                Button(action: {
                    haptic.impactOccurred()
                    coordinator.errorMessage = nil
                    Task {
                        await coordinator.rebuildTimelineFromServer()
                    }
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.system(size: 11, weight: .bold))
                        Text("同步進度")
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 12)
                    .frame(height: 30)
                    .background(Capsule().fill(Color.cyan.opacity(0.35)))
                }
                .buttonStyle(.plain)

                if !lastUserText.isEmpty && !coordinator.isProcessingOrSpeaking {
                    Button(action: {
                        haptic.impactOccurred()
                        coordinator.errorMessage = nil
                        if coordinator.textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            coordinator.textInput = lastUserText
                        }
                        isInputFocused = true
                    }) {
                        HStack(spacing: 4) {
                            Image(systemName: "square.and.pencil")
                                .font(.system(size: 11, weight: .bold))
                            Text("帶回輸入框")
                                .font(.system(size: 12, weight: .semibold))
                        }
                        .foregroundColor(.white)
                        .padding(.horizontal, 12)
                        .frame(height: 30)
                        .background(Capsule().fill(Color.red.opacity(0.50)))
                    }
                    .buttonStyle(.plain)
                }

                Spacer()
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color.red.opacity(0.12))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.red.opacity(0.35), lineWidth: 1)
                )
        )
    }

    // MARK: - 1. 背景漸層 (深邃環境光暈 + Live 模式底部動態藍光，襯托液態玻璃折射)
    private var geminiBackground: some View {
        ZStack {
            Color(red: 0.03, green: 0.04, blue: 0.08).ignoresSafeArea()

            RadialGradient(
                colors: [Color(red: 0.14, green: 0.45, blue: 0.88).opacity(0.24), Color.clear],
                center: .topLeading,
                startRadius: 20,
                endRadius: 360
            )
            .ignoresSafeArea()

            RadialGradient(
                colors: [Color(red: 0.45, green: 0.24, blue: 0.85).opacity(0.18), Color.clear],
                center: UnitPoint(x: 0.88, y: 0.55),
                startRadius: 20,
                endRadius: 340
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()
                LinearGradient(
                    gradient: Gradient(colors: [
                        Color.clear,
                        Color(red: 0.04, green: 0.09, blue: 0.24).opacity(coordinator.isLiveMode ? 0.95 : 0.72),
                        Color(red: 0.07, green: 0.16, blue: 0.40).opacity(coordinator.isLiveMode ? 0.88 : 0.42),
                        Color(red: 0.02, green: 0.04, blue: 0.12).opacity(0.92)
                    ]),
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: 360)
            }
            .ignoresSafeArea()
        }
    }

    // MARK: - 2. 頂部 Gemini 風格導覽列
    private var topHeaderBar: some View {
        HStack(spacing: 8) {
            // 模型選擇、思考層級、語音辨識與語音設定選單（改用自訂小字級 Popover 解決系統 Menu 字體過大換行問題）
            Button(action: {
                haptic.impactOccurred()
                isInputFocused = false
                showModelPopover = true
            }) {
                HStack(spacing: 8) {
                    // 連線狀態燈（綠：已連線 / 橘：檢查中 / 紅：離線）
                    Circle()
                        .fill(connectionDotColor)
                        .frame(width: 7, height: 7)
                        .shadow(color: connectionDotColor.opacity(0.7), radius: 3)

                    VStack(alignment: .leading, spacing: 1) {
                        HStack(spacing: 4) {
                            let activeModelId = coordinator.selectedEngineId == "agy"
                                ? coordinator.selectedModelFamily.rawValue
                                : coordinator.selectedEngineModelId
                            ModelBrandIcon(
                                brand: ModelBrand.resolve(engineId: coordinator.selectedEngineId, modelId: activeModelId),
                                size: 13
                            )

                            Text(coordinator.currentEngineDisplayName)
                                .font(.system(size: 15, weight: .bold))
                                .foregroundColor(.white)
                                .lineLimit(1)
                                .minimumScaleFactor(0.78)

                            Image(systemName: "chevron.down")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(Color(white: 0.6))
                        }

                        HStack(spacing: 4) {
                            Text("\(thinkingLevelSubLabel)思考 · \(coordinator.responseLength.shortTag)")
                                .font(.system(size: 10.5, weight: .medium))
                                .foregroundColor(Color(white: 0.58))
                                .lineLimit(1)
                                .minimumScaleFactor(0.85)

                            if coordinator.currentEngineSupportsQuota {
                                let quota = dynamicQuotaInfo(for: coordinator.selectedModelFamily)
                                let quotaText = coordinator.quotaSnapshot != nil ? String(format: "%.0f%%", quota.pct) : "--%"
                                Text("·")
                                    .font(.system(size: 10.5, weight: .medium))
                                    .foregroundColor(Color(white: 0.45))
                                HStack(spacing: 3) {
                                    ZStack {
                                        Circle()
                                            .stroke(Color.white.opacity(0.14), lineWidth: 1.5)
                                        Circle()
                                            .trim(from: 0, to: CGFloat(max(0.03, min(1.0, quota.pct / 100.0))))
                                            .stroke(quota.tint, style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                                            .rotationEffect(.degrees(-90))
                                    }
                                    .frame(width: 9, height: 9)

                                    Text(quotaText)
                                        .font(.system(size: 10.5, weight: .semibold, design: .rounded))
                                        .foregroundColor(quota.tint)
                                        .monospacedDigit()
                                        .lineLimit(1)
                                }
                            }
                        }
                    }
                }
                .padding(.leading, 12)
                .padding(.trailing, 12)
                .frame(width: 166, height: 42, alignment: .leading)
                .liquidGlass(in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("模型與語音設定，目前 \(coordinator.selectedModelFamily.displayName)")
            .popover(isPresented: $showModelPopover, attachmentAnchor: .rect(.bounds), arrowEdge: .top) {
                modelMenuPopoverContent
                    .presentationCompactAdaptation(.popover)
            }

            Spacer(minLength: 4)

            // 右側：合併式「用量」膠囊（Context 佔比 + 當前模型配額），點擊開啟完整系統狀態面板
            usageCapsuleButton

            // Herdr 派工督工抽屜 (Action Queue) 按鈕：
            // 🟠 待決策 -> 橘色數字徽章；🔵 完工待檢閱 -> 藍色數字徽章；🟢 執行中 -> 小綠點
            Button(action: {
                haptic.impactOccurred()
                isInputFocused = false
                showPendingActionsSheet = true
                Task {
                    let payload = await MacMiniAPIClient.shared.fetchPendingActionsPayload()
                    await MainActor.run {
                        self.pendingActions = payload.actions
                        self.herdrHistoryActions = payload.history
                    }
                }
            }) {
                let waitingDecisionCount = pendingActions.filter { $0.isWaitingDecision }.count
                let completedReviewCount = pendingActions.filter { $0.isCompleted }.count
                let workingCount = pendingActions.filter { $0.isWorking }.count
                let activeBadgeCount = waitingDecisionCount + completedReviewCount + workingCount
                let iconTint: Color = waitingDecisionCount > 0
                    ? .orange
                    : (completedReviewCount > 0 ? .cyan : (workingCount > 0 ? .green : .white.opacity(0.72)))

                ZStack(alignment: .topTrailing) {
                    Image(systemName: pendingActions.isEmpty ? "tray" : "tray.full.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(iconTint)
                        .frame(width: 40, height: 40)
                        .liquidGlass(in: Circle())

                    if activeBadgeCount > 0 {
                        let badgeColor: Color = waitingDecisionCount > 0
                            ? .orange
                            : (completedReviewCount > 0 ? .cyan : .green)
                        Text("\(min(activeBadgeCount, 99))")
                            .font(.system(size: 10, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                            .padding(.horizontal, 5)
                            .frame(minWidth: 18, minHeight: 18)
                            .background(
                                Capsule()
                                    .fill(badgeColor)
                                    .overlay(Capsule().stroke(Color.black.opacity(0.65), lineWidth: 1.5))
                            )
                            .offset(x: 4, y: -4)
                    }
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Herdr 派工督工抽屜，目前 \(pendingActions.count) 項活躍任務")

            // 設定按鈕
            Button(action: {
                haptic.impactOccurred()
                showSettings = true
            }) {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.white.opacity(0.88))
                    .frame(width: 42, height: 42)
                    .liquidGlass(in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("設定")
        }
    }

    private var connectionDotColor: Color {
        if coordinator.isServerReachable { return .green }
        return coordinator.serverStatusText.hasPrefix("離線") ? .red : .orange
    }

    /// 是否確定處於離線狀態（排除 App 剛啟動尚未完成首次健康檢查的情況）
    private var isConfirmedOffline: Bool {
        !coordinator.isServerReachable && coordinator.serverStatusText.hasPrefix("離線")
    }

    // MARK: - 用量膠囊（Context ◔ + 背景任務 ⚡）
    private var usageCapsuleButton: some View {
        let ctx = latestContextUsage

        return Button(action: {
            haptic.impactOccurred()
            isInputFocused = false
            coordinator.refreshQuotaStatus(force: false)
            showQuotaSheet = true
        }) {
            HStack(spacing: 9) {
                // Context 佔比（實心填充圓）
                HStack(spacing: 5) {
                    ZStack {
                        Circle().fill(Color.white.opacity(0.06))
                        ContextPieFillShape(progress: ctx.ratio)
                            .fill(ctx.tint.opacity(0.55))
                        Circle().stroke(ctx.tint.opacity(0.75), lineWidth: 1.2)
                    }
                    .frame(width: 16, height: 16)

                    Text(ctx.pctText)
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundColor(.white)
                        .monospacedDigit()
                        .lineLimit(1)
                }
            }
            .padding(.horizontal, 12)
            .frame(height: 44)
            .liquidGlass(in: Capsule())
            .fixedSize(horizontal: true, vertical: false)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("用量：Context \(ctx.pctText)")
    }

    // MARK: - Context 佔比圓形填充指示器 (與 ACP 統一大腦 Context 用量同步，點擊檢視明細 / 切換音訊)
    private var latestContextUsage: (ratio: Double, pctText: String, detailText: String, tint: Color) {
        guard let snap = coordinator.quotaSnapshot else {
            return (0.0, "--%", "尚無 ACP Context 統計", Color(white: 0.5))
        }
        let pct = snap.contextPercent
        let clampedRatio = min(1.0, max(0.0, pct / 100.0))
        let pctText = pct >= 10.0 ? String(format: "%.0f%%", pct) : String(format: "%.1f%%", pct)
        let tint: Color = pct >= 80.0 ? .red : (pct >= 50.0 ? .orange : .purple)
        let detail = "ACP Context：\(snap.contextTokens.formatted()) / \(snap.contextLimit.formatted()) tokens"
        return (clampedRatio, pctText, detail, tint)
    }

    // MARK: - 精緻小字級 Popover 選單內容
    private func popoverSectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold))
            .foregroundColor(Color(white: 0.55))
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 4)
    }

    private func popoverOptionRow(
        title: String,
        icon: String? = nil,
        brandIcon: ModelBrand? = nil,
        isSelected: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: {
            haptic.impactOccurred()
            action()
        }) {
            HStack(spacing: 9) {
                if let brandIcon = brandIcon {
                    ModelBrandIcon(brand: brandIcon, size: 15)
                        .frame(width: 16)
                } else if let icon = icon {
                    Image(systemName: icon)
                        .font(.system(size: 12.5))
                        .foregroundColor(isSelected ? .cyan : Color(white: 0.72))
                        .frame(width: 16)
                }
                Text(title)
                    .font(.system(size: 13, weight: isSelected ? .semibold : .regular))
                    .foregroundColor(.white)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.cyan)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(isSelected ? Color.cyan.opacity(0.12) : Color.clear)
            )
            .padding(.horizontal, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var modelMenuPopoverContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 2) {
                // 連線狀態列
                HStack(spacing: 7) {
                    Circle()
                        .fill(connectionDotColor)
                        .frame(width: 7, height: 7)
                    Text(coordinator.serverStatusText)
                        .font(.system(size: 11.5, weight: .medium, design: .rounded))
                        .foregroundColor(Color(white: 0.75))
                        .lineLimit(1)
                }
                .padding(.horizontal, 16)
                .padding(.top, 4)
                .padding(.bottom, 4)

                Divider().opacity(0.22).padding(.horizontal, 12).padding(.vertical, 3)

                popoverSectionHeader("主腦引擎切換")
                let engineList = coordinator.brainCatalog?.engines ?? [
                    BrainEngineOption(id: "agy", label: "Antigravity (agy)", supportsQuota: true, selectedModel: "hydrogen", models: []),
                    BrainEngineOption(id: "omp", label: "Oh-My-Pi (omp)", supportsQuota: false, selectedModel: "muse-spark-1.3-contributor", models: [
                        BrainModelOption(id: "muse-spark-1.3-contributor", label: "Muse Spark 1.3"),
                        BrainModelOption(id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5")
                    ])
                ]
                ForEach(engineList) { eng in
                    popoverOptionRow(
                        title: eng.label,
                        icon: eng.id == "agy" ? "sparkles" : "cpu",
                        isSelected: coordinator.selectedEngineId == eng.id
                    ) {
                        coordinator.selectBrainEngine(eng.id)
                    }
                }

                Divider().opacity(0.25).padding(.vertical, 3)

                popoverSectionHeader("AI 模型選擇")
                if coordinator.selectedEngineId == "agy" {
                    ForEach(ModelFamily.allCases) { fam in
                        popoverOptionRow(
                            title: fam.fullName,
                            brandIcon: ModelBrand.resolve(engineId: "agy", modelId: fam.rawValue),
                            isSelected: coordinator.selectedModelFamily == fam
                        ) {
                            coordinator.selectedModelFamily = fam
                            coordinator.selectBrainEngine("agy", modelId: fam.rawValue)
                            coordinator.clampThinkingLevelIfNeeded()
                            showModelPopover = false
                        }
                    }
                } else {
                    let curModels = engineList.first(where: { $0.id == coordinator.selectedEngineId })?.models ?? []
                    ForEach(curModels) { m in
                        popoverOptionRow(
                            title: m.label,
                            brandIcon: ModelBrand.resolve(engineId: coordinator.selectedEngineId, modelId: m.id),
                            isSelected: coordinator.selectedEngineModelId == m.id
                        ) {
                            coordinator.selectBrainEngine(coordinator.selectedEngineId, modelId: m.id)
                            showModelPopover = false
                        }
                    }
                }

                Divider().opacity(0.25).padding(.vertical, 3)
                popoverSectionHeader("思考深度")
                ForEach(coordinator.supportedThinkingLevels) { level in
                    popoverOptionRow(title: "\(level.headerTitle) — \(level.menuSubtitle)", isSelected: coordinator.thinkingLevel == level) {
                        coordinator.thinkingLevel = level
                        showModelPopover = false
                    }
                }

                Divider().opacity(0.25).padding(.vertical, 3)
                popoverSectionHeader("回覆長度")
                ForEach(ResponseLengthOption.allCases) { opt in
                    popoverOptionRow(title: opt.title, icon: opt.icon, isSelected: coordinator.responseLength == opt) {
                        coordinator.responseLength = opt
                        showModelPopover = false
                    }
                }

                Divider().opacity(0.25).padding(.vertical, 3)
                popoverSectionHeader("語音辨識模式")
                ForEach(STTRecognitionMode.allCases) { mode in
                    popoverOptionRow(title: mode.title, icon: mode.icon, isSelected: coordinator.sttMode == mode) {
                        coordinator.sttMode = mode
                        showModelPopover = false
                    }
                }

                Divider().opacity(0.25).padding(.vertical, 3)
                popoverSectionHeader("助理朗讀語速")
                ForEach(TTSSpeedOption.allCases) { spd in
                    popoverOptionRow(title: spd.title, isSelected: coordinator.ttsSpeed == spd) {
                        coordinator.ttsSpeed = spd
                        showModelPopover = false
                    }
                }
            }
            .padding(.vertical, 10)
        }
        .contentMargins(.vertical, 6, for: .scrollContent)
        .frame(width: 284)
        .frame(maxHeight: 470)
        .presentationBackground(Color(red: 0.12, green: 0.13, blue: 0.16))
        .preferredColorScheme(.dark)
    }

    private func dynamicQuotaInfo(for family: ModelFamily) -> (pct: Double, resetIn: String, tint: Color, icon: String) {
        guard let snap = coordinator.quotaSnapshot else {
            return (100.0, "--", .green, "bolt.fill")
        }
        switch family {
        case .hydrogen:
            let pct = snap.weeklyRemainingPct
            let tint: Color = pct >= 50 ? .cyan : (pct >= 20 ? .orange : .red)
            return (pct, snap.weeklyRefreshesIn, tint, "flame.fill")
        case .neon:
            let pct = snap.weeklyRemainingPct
            let tint: Color = pct >= 50 ? .mint : (pct >= 20 ? .orange : .red)
            return (pct, snap.weeklyRefreshesIn, tint, "sparkles.rectangle.stack.fill")
        case .flash:
            let pct = snap.weeklyRemainingPct
            let tint: Color = pct >= 50 ? .green : (pct >= 20 ? .orange : .red)
            return (pct, snap.weeklyRefreshesIn, tint, "bolt.fill")
        case .argon:
            let pct = snap.argonRemainingPct
            let tint: Color = pct >= 50 ? Color(red: 0.45, green: 0.65, blue: 1.0) : (pct >= 20 ? .orange : .red)
            return (pct, snap.argonRefreshesIn, tint, "atom")
        case .claude, .opus:
            let pct = snap.thirdPartyRemainingPct
            let tint: Color = pct >= 50 ? .purple : (pct >= 20 ? .orange : .red)
            return (pct, snap.thirdPartyRefreshesIn, tint, family.icon)
        }
    }

    private var thinkingLevelSubLabel: String {
        switch coordinator.thinkingLevel {
        case .low: return "快速"
        case .med: return "中等"
        case .high: return "深度"
        case .xhigh: return "極深"
        }
    }

    // MARK: - 3B. 主畫面直接顯示即時對話列表
    private var conversationScrollSection: some View {
        ScrollViewReader { proxy in
            ZStack(alignment: .bottomTrailing) {
                ScrollView {
                    LazyVStack(spacing: 20) {
                        if coordinator.messages.isEmpty {
                            zeroStateDashboard
                                .padding(.top, 12)
                        }
                        ForEach(Array(coordinator.messages.enumerated()), id: \.element.id) { idx, msg in
                            if shouldShowTimestampSeparator(at: idx) {
                                Text(formatMessageTimestamp(msg.timestamp))
                                    .font(.caption2)
                                    .fontWeight(.medium)
                                    .foregroundColor(Color(white: 0.48))
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 3)
                                    .background(Capsule().fill(Color.white.opacity(0.06)))
                                    .frame(maxWidth: .infinity)
                            }

                            GeminiMessageRow(
                                message: msg,
                                isCurrentlySpeaking: isSpeakingMessage(msg),
                                onTapSpeak: {
                                    haptic.impactOccurred()
                                    coordinator.toggleSpeakMessage(msg)
                                },
                                onTapQuickReply: { chosen in
                                    haptic.impactOccurred()
                                    coordinator.sendQuickReply(chosen)
                                }
                            )
                            .id(msg.id)
                            .contextMenu {
                                if !msg.content.isEmpty {
                                    Button {
                                        UIPasteboard.general.string = msg.content
                                    } label: {
                                        Label("複製文字", systemImage: "doc.on.doc")
                                    }

                                    Button {
                                        haptic.impactOccurred()
                                        quotedMessage = msg
                                        isInputFocused = true
                                    } label: {
                                        Label("引用追問", systemImage: "quote.bubble")
                                    }

                                    Button {
                                        coordinator.toggleSpeakMessage(msg)
                                    } label: {
                                        Label(
                                            isSpeakingMessage(msg) ? "停止朗讀" : "朗讀此則訊息",
                                            systemImage: isSpeakingMessage(msg) ? "stop.circle" : "speaker.wave.2"
                                        )
                                    }

                                    Button {
                                        haptic.impactOccurred()
                                        shareMessageAsScreenshot(msg)
                                    } label: {
                                        Label("截圖分享", systemImage: "square.and.arrow.up")
                                    }

                                    if msg.role == .user {
                                        Button {
                                            coordinator.sendQuickReply(msg.content)
                                        } label: {
                                            Label("重新發送", systemImage: "arrow.clockwise")
                                        }
                                    }
                                }
                            }
                        }

                        // 當使用者在 Live 模式開口講話時，即時在右側顯示邊講邊跳字的預覽氣泡！
                        if coordinator.isLiveMode && !coordinator.liveTranscript.isEmpty {
                            liveTranscriptPreviewBubble
                                .id("live_transcript_preview")
                        }

                        if let err = coordinator.errorMessage {
                            errorCard(err)
                                .id("error_card")
                        }

                        // 底部繼續上拉重整提示（當滑到最底下繼續往上拉時顯示）
                        if bottomPullOffset > 15 || isRefreshingHistory {
                            HStack(spacing: 8) {
                                if isRefreshingHistory {
                                    ProgressView()
                                        .tint(.cyan)
                                        .scaleEffect(0.8)
                                    Text("正在清除快取並重抓最新紀錄...")
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundColor(.cyan)
                                } else {
                                    let ready = bottomPullOffset >= 65
                                    Image(systemName: ready ? "arrow.triangle.2.circlepath" : "arrow.up")
                                        .font(.system(size: 12, weight: .bold))
                                        .foregroundColor(ready ? .cyan : .secondary)
                                    Text(ready ? "放開以清除快取並重整對話" : "繼續上拉以重整對話")
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundColor(ready ? .cyan : .secondary)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                            .transition(.opacity)
                        }

                        Color.clear
                            .frame(height: 8)
                            .id("bottom_anchor")
                            .onAppear {
                                isAtBottom = true
                                userScrolledUp = false
                                hasUnseenUpdates = false
                            }
                            .onDisappear {
                                // 只有當使用者近期確實有手勢上滑（含慣性滑動）時，才視為離開底部；
                                // 若只是新訊息或串流長高把 bottom_anchor 推到畫面外，不可誤判為手動上滑而中斷自動跟隨。
                                let recentlyDraggedUp = isDraggingBottom || Date().timeIntervalSince(lastUserScrollUpGestureAt) < 1.2
                                if recentlyDraggedUp || userScrolledUp {
                                    isAtBottom = false
                                    userScrolledUp = true
                                }
                            }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 12)
                    .padding(.bottom, 8)
                }
                .scrollDismissesKeyboard(.interactively)
                .simultaneousGesture(
                    DragGesture(minimumDistance: 10)
                        .onChanged { value in
                            if !isDraggingBottom {
                                isDraggingBottom = true
                                startedDragAtBottom = isAtBottom
                                bottomPullHapticTriggered = false
                            }
                            // 手指往下拉（translation.height > 0）= 正在往上回看歷史訊息：
                            // 立即標記 userScrolledUp 並暫停自動置底，確保助理邊講話時使用者能安心回看舊訊息。
                            if value.translation.height > 18 {
                                lastUserScrollUpGestureAt = Date()
                            }
                            if value.translation.height > 28 {
                                userScrolledUp = true
                                isAtBottom = false
                            }
                            guard startedDragAtBottom && !isRefreshingHistory else { return }
                            let pullUp = -value.translation.height
                            if pullUp > 0 {
                                bottomPullOffset = pullUp
                                if pullUp >= 65 && !bottomPullHapticTriggered {
                                    bottomPullHapticTriggered = true
                                    haptic.impactOccurred()
                                } else if pullUp < 65 {
                                    bottomPullHapticTriggered = false
                                }
                            } else {
                                bottomPullOffset = 0
                            }
                        }
                        .onEnded { value in
                            let pullUp = -value.translation.height
                            let shouldRefresh = startedDragAtBottom && pullUp >= 65 && !isRefreshingHistory
                            isDraggingBottom = false
                            startedDragAtBottom = false
                            bottomPullHapticTriggered = false
                            if shouldRefresh {
                                isRefreshingHistory = true
                                bottomPullOffset = 0
                                Task {
                                    await coordinator.rebuildTimelineFromServer()
                                    await MainActor.run {
                                        withAnimation(.easeOut(duration: 0.2)) {
                                            isRefreshingHistory = false
                                        }
                                        userScrolledUp = false
                                        scrollToBottom(proxy: proxy, animated: true)
                                    }
                                }
                            } else {
                                withAnimation(.easeOut(duration: 0.2)) {
                                    bottomPullOffset = 0
                                }
                            }
                        }
                )

                if (!isAtBottom || userScrolledUp) && coordinator.messages.count > 2 {
                    Button(action: {
                        haptic.impactOccurred()
                        userScrolledUp = false
                        hasUnseenUpdates = false
                        scrollToBottom(proxy: proxy, animated: true)
                    }) {
                        ZStack(alignment: .topTrailing) {
                            Image(systemName: "arrow.down")
                                .font(.system(size: 14, weight: .bold))
                                .foregroundColor(.white)
                                .frame(width: 38, height: 38)
                                .background(
                                    Circle()
                                        .fill(.ultraThinMaterial)
                                        .overlay(
                                            Circle()
                                                .stroke(hasUnseenUpdates ? Color.cyan.opacity(0.6) : Color.white.opacity(0.20), lineWidth: 1)
                                        )
                                        .shadow(color: .black.opacity(0.35), radius: 6, x: 0, y: 2)
                                )

                            if hasUnseenUpdates {
                                Circle()
                                    .fill(Color.cyan)
                                    .frame(width: 9, height: 9)
                                    .offset(x: 1, y: -1)
                            }
                        }
                    }
                    .accessibilityLabel("回到底部")
                    .padding(.trailing, 16)
                    .padding(.bottom, coordinator.voiceInputMode == .liveContinuous ? 72 : 16)
                    .transition(.scale.combined(with: .opacity))
                }
            }
            .onAppear {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) {
                    userScrolledUp = false
                    scrollToBottom(proxy: proxy, animated: false)
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                    if !userScrolledUp {
                        scrollToBottom(proxy: proxy, animated: false)
                    }
                }
            }
            .onChange(of: coordinator.messages.count) { _, _ in
                // 使用者送出新訊息、或是新回覆氣泡跳出（且使用者並非剛在 4 秒內手動上滑）時，強制自動置底並恢復跟隨
                let isNewUserTurn = coordinator.messages.last?.role == .user
                let isActivelyReadingHistory = userScrolledUp && (isDraggingBottom || Date().timeIntervalSince(lastUserScrollUpGestureAt) < 4.0)
                if isNewUserTurn || !isActivelyReadingHistory {
                    userScrolledUp = false
                    hasUnseenUpdates = false
                    scrollToBottom(proxy: proxy, animated: true)
                } else {
                    hasUnseenUpdates = true
                }
            }
            .onChange(of: scrollToMessageId) { _, targetId in
                guard let targetId = targetId else { return }
                isAtBottom = false
                userScrolledUp = true
                lastUserScrollUpGestureAt = Date()
                withAnimation(.easeInOut(duration: 0.3)) {
                    proxy.scrollTo(targetId, anchor: .center)
                }
                scrollToMessageId = nil
            }
            .onChange(of: coordinator.currentStreamingText) { oldText, newText in
                // 當新一輪串流剛開始跳出文字（空 -> 非空），且使用者並非正在手滑回看，自動鎖定置底
                if oldText.isEmpty && !newText.isEmpty {
                    let isActivelyReadingHistory = userScrolledUp && (isDraggingBottom || Date().timeIntervalSince(lastUserScrollUpGestureAt) < 4.0)
                    if !isActivelyReadingHistory {
                        userScrolledUp = false
                        hasUnseenUpdates = false
                        scrollToBottom(proxy: proxy, animated: true)
                        return
                    }
                }
                if !userScrolledUp && !isDraggingBottom {
                    followBottom(proxy: proxy)
                } else if !newText.isEmpty {
                    hasUnseenUpdates = true
                }
            }
            .onChange(of: coordinator.messages.last?.content) { _, _ in
                // 與 currentStreamingText 同一 token 的雙重寫入，只要使用者未手動上滑就持續節流跟隨到底部
                if !userScrolledUp && !isDraggingBottom {
                    followBottom(proxy: proxy)
                } else {
                    hasUnseenUpdates = true
                }
            }
            .onChange(of: coordinator.messages.last?.status) { _, newStatus in
                if newStatus == .completed {
                    if !userScrolledUp && !isDraggingBottom {
                        hasUnseenUpdates = false
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) {
                            guard !userScrolledUp && !isDraggingBottom else { return }
                            scrollToBottom(proxy: proxy, animated: true)
                        }
                    } else {
                        hasUnseenUpdates = true
                    }
                }
            }
            .onChange(of: coordinator.liveTranscript) { old, _ in
                // 使用者開始講話（空→非空）時強制歸位底部並恢復自動跟隨
                if old.isEmpty && !coordinator.liveTranscript.isEmpty {
                    userScrolledUp = false
                    hasUnseenUpdates = false
                    scrollToBottom(proxy: proxy, animated: false)
                } else if !coordinator.liveTranscript.isEmpty {
                    if !userScrolledUp && !isDraggingBottom {
                        followBottom(proxy: proxy)
                    } else {
                        hasUnseenUpdates = true
                    }
                }
            }
        }
    }

    /// 置底：若原本不在底部（LazyVStack 底部氣泡尚未實體化），先定位最後一則訊息再沉降至 bottom_anchor，
    /// 避免長距離跳轉因估算高度失準而衝過頭變成一片空白；已在底部時僅對 bottom_anchor 快照一次。
    private func scrollToBottom(proxy: ScrollViewProxy, animated: Bool) {
        lastFollowScrollAt = Date()
        if !isAtBottom, let lastId = coordinator.messages.last?.id {
            proxy.scrollTo(lastId, anchor: .bottom)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                guard !isDraggingBottom && !userScrolledUp else { return }
                isAtBottom = true
                if animated {
                    withAnimation(.easeOut(duration: 0.18)) {
                        proxy.scrollTo("bottom_anchor", anchor: .bottom)
                    }
                } else {
                    proxy.scrollTo("bottom_anchor", anchor: .bottom)
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.28) {
                guard !isDraggingBottom && !userScrolledUp else { return }
                isAtBottom = true
                proxy.scrollTo("bottom_anchor", anchor: .bottom)
            }
            return
        }

        isAtBottom = true
        if animated {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo("bottom_anchor", anchor: .bottom)
            }
            // LazyVStack 長訊息展開後高度會變動，離散事件才需要一次沉降對齊
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                if !userScrolledUp && !isDraggingBottom {
                    proxy.scrollTo("bottom_anchor", anchor: .bottom)
                }
            }
        } else {
            proxy.scrollTo("bottom_anchor", anchor: .bottom)
        }
    }

    /// 高頻跟隨（串流 token / 即時轉錄 partial）：不做動畫、不排延遲二次捲動，
    /// 0.15s 節流一次，避免與手勢互搶造成跳動。
    private func followBottom(proxy: ScrollViewProxy, minInterval: TimeInterval = 0.15) {
        let now = Date()
        guard now.timeIntervalSince(lastFollowScrollAt) >= minInterval else { return }
        lastFollowScrollAt = now
        proxy.scrollTo("bottom_anchor", anchor: .bottom)
    }

    private func isSpeakingMessage(_ msg: ChatMessage) -> Bool {
        guard msg.role == .assistant,
              msg.id == coordinator.speakingMessageId,
              case .speaking = coordinator.state else {
            return false
        }
        return true
    }

    private func shouldShowTimestampSeparator(at index: Int) -> Bool {
        guard index > 0 else { return true }
        let prev = coordinator.messages[index - 1].timestamp
        let curr = coordinator.messages[index].timestamp
        return curr.timeIntervalSince(prev) >= 600
    }

    private func formatMessageTimestamp(_ date: Date) -> String {
        let cal = Calendar.current
        let timeStr = date.formatted(date: .omitted, time: .shortened)
        if cal.isDateInToday(date) {
            return "今天 \(timeStr)"
        } else if cal.isDateInYesterday(date) {
            return "昨天 \(timeStr)"
        } else {
            let dateStr = date.formatted(.dateTime.month(.defaultDigits).day(.defaultDigits))
            return "\(dateStr) \(timeStr)"
        }
    }

    // MARK: - 邊講邊跳字的即時預覽氣泡 (徹底解決「不知道有沒有聽到」與「何時送出」)
    private var liveTranscriptPreviewBubble: some View {
        HStack {
            Spacer(minLength: 48)

            VStack(alignment: .trailing, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: "waveform")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(.cyan)
                    Text(coordinator.liveTranscript)
                        .font(.system(size: 16))
                        .foregroundColor(.white)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .fill(Color(red: 0.14, green: 0.22, blue: 0.38))
                        .overlay(
                            RoundedRectangle(cornerRadius: 20, style: .continuous)
                                .stroke(Color.cyan.opacity(0.45), lineWidth: 1)
                        )
                )

                // 自動斷句送出進度條
                if coordinator.silenceProgress > 0.05 {
                    HStack(spacing: 6) {
                        Text("即將自動送出...")
                            .font(.system(size: 11))
                            .foregroundColor(.cyan.opacity(0.85))
                        ProgressView(value: Double(coordinator.silenceProgress))
                            .progressViewStyle(LinearProgressViewStyle(tint: .cyan))
                            .frame(width: 64)
                    }
                    .padding(.trailing, 4)
                }
            }
        }
    }

    private var zeroStateDashboard: some View {
        let activeTasks = coordinator.quotaSnapshot?.activeTaskCount ?? 0
        let ctx = latestContextUsage
        let quota = dynamicQuotaInfo(for: coordinator.selectedModelFamily)
        let quotaText: String = {
            if !coordinator.currentEngineSupportsQuota {
                return "∞ 無額度"
            }
            return coordinator.quotaSnapshot != nil ? String(format: "%.0f%%", quota.pct) : "--%"
        }()

        return VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Mac mini 隨時待命")
                    .font(.title2.weight(.bold))
                    .foregroundColor(.primary)
                Text("點選下方卡片快速查看狀態，或直接開口交談")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 4)

            // 1. 今日簡報與待辦卡片
            Button(action: {
                haptic.impactOccurred()
                coordinator.sendQuickReply("請給我今日簡報與待辦事項")
            }) {
                HStack(spacing: 12) {
                    Image(systemName: "calendar.badge.clock")
                        .font(.title3)
                        .foregroundColor(.cyan)
                        .frame(width: 40, height: 40)
                        .background(Circle().fill(Color.cyan.opacity(0.14)))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("今日簡報與待辦")
                            .font(.headline)
                            .foregroundColor(.primary)
                        Text("一鍵彙整行事曆行程與近期專案進度")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.secondary)
                }
                .padding(14)
                .background(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(.ultraThinMaterial)
                        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Color.white.opacity(0.1), lineWidth: 1))
                )
            }
            .buttonStyle(.plain)

            // 2. 背景任務與系統狀態卡片
            Button(action: {
                haptic.impactOccurred()
                coordinator.refreshQuotaStatus(force: true)
                showQuotaSheet = true
            }) {
                HStack(spacing: 12) {
                    Image(systemName: "bolt.shield.fill")
                        .font(.title3)
                        .foregroundColor(activeTasks > 0 ? .cyan : .green)
                        .frame(width: 40, height: 40)
                        .background(Circle().fill((activeTasks > 0 ? Color.cyan : Color.green).opacity(0.14)))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("背景任務與系統狀態")
                            .font(.headline)
                            .foregroundColor(.primary)
                        Text("執行中 \(activeTasks) 項 · Context \(ctx.pctText) · 配額 \(quotaText)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.secondary)
                }
                .padding(14)
                .background(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(.ultraThinMaterial)
                        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Color.white.opacity(0.1), lineWidth: 1))
                )
            }
            .buttonStyle(.plain)

            // 3. 居家與採買快查
            HStack(spacing: 10) {
                Button(action: {
                    haptic.impactOccurred()
                    coordinator.sendQuickReply("查詢目前待買清單")
                }) {
                    Label("待買清單", systemImage: "cart.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(.primary)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.ultraThinMaterial))
                }
                .buttonStyle(.plain)

                Button(action: {
                    haptic.impactOccurred()
                    coordinator.sendQuickReply("查詢酷澎最新追蹤價格與狀態")
                }) {
                    Label("酷澎盯價", systemImage: "tag.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(.primary)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.ultraThinMaterial))
                }
                .buttonStyle(.plain)

                Button(action: {
                    haptic.impactOccurred()
                    coordinator.sendQuickReply("查詢目前家裡電器與溫濕度狀態")
                }) {
                    Label("家電狀態", systemImage: "house.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(.primary)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.ultraThinMaterial))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 16)
    }

    // MARK: - 4. 底部 Gemini 膠囊輸入列與 Live 狀態控制
    private var bottomInputSection: some View {
        VStack(spacing: 10) {
            // 若處於 ((|)) Live 連續語音對談模式，顯示浮動聲波控制膠囊
            if coordinator.isLiveMode {
                liveVoiceFloatingBar
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            } else if showQuickSkillBar && coordinator.pendingImagesData.isEmpty && coordinator.pendingImageData == nil {
                let activeTasks = coordinator.quotaSnapshot?.activeTaskCount ?? 0
                HStack(spacing: 8) {
                    Button(action: {
                        haptic.impactOccurred()
                        isInputFocused = false
                        showQuickPromptDrawer = true
                    }) {
                        Label("常用指令", systemImage: "square.grid.2x2")
                            .font(.caption.weight(.semibold))
                            .foregroundColor(.primary)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 34)
                            .liquidGlass(in: Capsule())
                    }
                    .buttonStyle(.plain)

                    Button(action: {
                        haptic.impactOccurred()
                        isInputFocused = false
                        showArtifactsSheet = true
                    }) {
                        Label("產物", systemImage: "shippingbox.fill")
                            .font(.caption.weight(.semibold))
                            .foregroundColor(.primary)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 34)
                            .liquidGlass(in: Capsule())
                    }
                    .buttonStyle(.plain)

                    Button(action: {
                        haptic.impactOccurred()
                        isInputFocused = false
                        showBackgroundTasksSheet = true
                    }) {
                        HStack(spacing: 5) {
                            Image(systemName: "bolt.fill")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(activeTasks > 0 ? .cyan : .secondary)
                            Text("背景任務")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.primary)
                            if activeTasks > 0 {
                                Text("\(activeTasks)")
                                    .font(.system(size: 10, weight: .bold, design: .rounded))
                                    .foregroundColor(.white)
                                    .padding(.horizontal, 5)
                                    .frame(minWidth: 16, minHeight: 16)
                                    .background(Capsule().fill(Color.cyan))
                                    .monospacedDigit()
                            }
                        }
                        .padding(.horizontal, 12)
                        .frame(minHeight: 34)
                        .liquidGlass(in: Capsule())
                    }
                    .buttonStyle(.plain)

                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 2)
            }

            // 若有附加圖片（支援單張與多張），於輸入列上方顯示橫向縮圖預覽列
            let allPendingImages: [Data] = {
                if !coordinator.pendingImagesData.isEmpty {
                    return coordinator.pendingImagesData
                } else if let single = coordinator.pendingImageData {
                    return [single]
                }
                return []
            }()

            if let quoted = quotedMessage {
                HStack(spacing: 8) {
                    Rectangle()
                        .fill(Color.cyan.opacity(0.85))
                        .frame(width: 3)
                        .cornerRadius(2)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(quoted.role == .user ? "引用你的提問" : "引用助理回覆")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.cyan)
                        Text(quoted.content.replacingOccurrences(of: "\n", with: " "))
                            .font(.system(size: 12))
                            .foregroundColor(Color(white: 0.78))
                            .lineLimit(1)
                    }

                    Spacer()

                    Button(action: {
                        haptic.impactOccurred()
                        quotedMessage = nil
                    }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 16))
                            .foregroundColor(Color(white: 0.55))
                    }
                    .accessibilityLabel("取消引用")
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Color.white.opacity(0.08))
                )
            }

            if coordinator.isFetchingLocation || coordinator.pendingLocationLabel != nil || !coordinator.pendingFiles.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        if coordinator.isFetchingLocation {
                            HStack(spacing: 6) {
                                ProgressView()
                                    .scaleEffect(0.75)
                                    .tint(.cyan)
                                Text("取得目前位置...")
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(.white.opacity(0.85))
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(Color.cyan.opacity(0.18)))
                        } else if let loc = coordinator.pendingLocationLabel {
                            HStack(spacing: 6) {
                                Image(systemName: "location.fill")
                                    .font(.system(size: 11))
                                    .foregroundColor(.cyan)
                                Text(loc)
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(.white.opacity(0.92))
                                    .lineLimit(1)
                                    .frame(maxWidth: 200, alignment: .leading)
                                Button(action: {
                                    haptic.impactOccurred()
                                    coordinator.pendingLocationLabel = nil
                                }) {
                                    Image(systemName: "xmark.circle.fill")
                                        .font(.system(size: 14))
                                        .foregroundColor(Color(white: 0.6))
                                }
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(Color.cyan.opacity(0.18)))
                        }

                        ForEach(coordinator.pendingFiles) { file in
                            HStack(spacing: 6) {
                                Image(systemName: "doc.fill")
                                    .font(.system(size: 11))
                                    .foregroundColor(.orange)
                                Text(file.filename)
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(.white.opacity(0.92))
                                    .lineLimit(1)
                                    .frame(maxWidth: 160, alignment: .leading)
                                Button(action: {
                                    haptic.impactOccurred()
                                    coordinator.pendingFiles.removeAll { $0.id == file.id }
                                }) {
                                    Image(systemName: "xmark.circle.fill")
                                        .font(.system(size: 14))
                                        .foregroundColor(Color(white: 0.6))
                                }
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(Color.orange.opacity(0.18)))
                        }
                    }
                }
            }

            if !allPendingImages.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("已附加 \(allPendingImages.count) 張照片（最多 \(Self.maxAttachedImages) 張）")
                            .font(.system(size: 11.5, weight: .semibold))
                            .foregroundColor(Color(white: 0.72))

                        Spacer()

                        Button(action: {
                            haptic.impactOccurred()
                            coordinator.pendingImagesData.removeAll()
                            coordinator.pendingImageData = nil
                        }) {
                            Text("全部清除")
                                .font(.system(size: 11.5, weight: .medium))
                                .foregroundColor(.secondary)
                        }
                    }

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Array(allPendingImages.enumerated()), id: \.offset) { idx, imgData in
                                if let uiImg = UIImage(data: imgData) {
                                    ZStack(alignment: .topTrailing) {
                                        Image(uiImage: uiImg)
                                            .resizable()
                                            .scaledToFill()
                                            .frame(width: 58, height: 58)
                                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                                            .overlay(
                                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                                    .stroke(Color.white.opacity(0.2), lineWidth: 1)
                                            )

                                        Button(action: {
                                            haptic.impactOccurred()
                                            if !coordinator.pendingImagesData.isEmpty && idx < coordinator.pendingImagesData.count {
                                                coordinator.pendingImagesData.remove(at: idx)
                                            } else {
                                                coordinator.pendingImageData = nil
                                            }
                                        }) {
                                            Image(systemName: "xmark.circle.fill")
                                                .font(.system(size: 17))
                                                .foregroundColor(.white)
                                                .background(Circle().fill(Color.black.opacity(0.65)))
                                        }
                                        .offset(x: 4, y: -4)
                                    }
                                    .padding(.top, 4)
                                    .padding(.trailing, 4)
                                }
                            }

                            if allPendingImages.count < Self.maxAttachedImages {
                                Button(action: {
                                    haptic.impactOccurred()
                                    isInputFocused = false
                                    showPhotoLibraryPicker = true
                                }) {
                                    VStack(spacing: 3) {
                                        Image(systemName: "plus")
                                            .font(.system(size: 16, weight: .semibold))
                                        Text("加選")
                                            .font(.system(size: 10))
                                    }
                                    .foregroundColor(.white.opacity(0.75))
                                    .frame(width: 58, height: 58)
                                    .background(
                                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                                            .strokeBorder(Color.white.opacity(0.25), style: StrokeStyle(lineWidth: 1, dash: [4]))
                                    )
                                }
                                .padding(.top, 4)
                            }
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(Color(red: 0.13, green: 0.15, blue: 0.20))
                )
            }

            if showMentionPicker {
                herdrMentionPickerOverlay
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }

            // Gemini 標誌性深色膠囊輸入列
            HStack(alignment: .bottom, spacing: 8) {
                // 左側 [ + ] 相簿選圖 / 附加檔案 / 帶入目前位置（改用 Button + confirmationDialog 避免在多行 TextField 取得焦點時 Menu 觸控失效）
                Button(action: {
                    haptic.impactOccurred()
                    isInputFocused = false
                    showAttachmentSheet = true
                }) {
                    Image(systemName: "plus")
                        .font(.system(size: 21, weight: .medium))
                        .foregroundColor(.white.opacity(0.88))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("附加圖片、檔案或目前位置")
                .confirmationDialog("附加內容", isPresented: $showAttachmentSheet, titleVisibility: .hidden) {
                    Button("從相簿選取圖片") {
                        showPhotoLibraryPicker = true
                    }
                    Button("附加檔案或文件") {
                        showFileImporter = true
                    }
                    Button("帶入目前位置") {
                        coordinator.attachCurrentLocation()
                    }
                    Button("搜尋對話紀錄") {
                        showSearchSheet = true
                    }
                    Button("取消", role: .cancel) {}
                }

                // 文字輸入框：「問問助理」
                TextField(
                    "",
                    text: $coordinator.textInput,
                    prompt: Text("問問助理")
                        .foregroundColor(Color(white: 0.55)),
                    axis: .vertical
                )
                .font(.system(size: max(14.0, chatBaseFontSize)))
                .lineLimit(1...5)
                .foregroundColor(.white)
                .focused($isInputFocused)
                .padding(.vertical, 6)
                .frame(minHeight: 44, alignment: .center)
                .onChange(of: coordinator.textInput) { _, newValue in
                    if let (_, query) = activeMentionToken(in: newValue) {
                        mentionQuery = query
                        if !showMentionPicker {
                            showMentionPicker = true
                            refreshHerdrAgentsForMention()
                        }
                    } else if showMentionPicker {
                        showMentionPicker = false
                        mentionQuery = ""
                    }
                }

                // 右側按鈕區：📷 隨身眼睛拍照 + (有字或有圖時顯示 ↑ 送出 / 無字時顯示 ((|)) Live 連續對談)
                HStack(spacing: 6) {
                    // 📷 隨身眼睛快拍按鈕
                    Button(action: {
                        haptic.impactOccurred()
                        isInputFocused = false
                        showVisionCamera = true
                    }) {
                        Image(systemName: "camera.fill")
                            .font(.system(size: 19, weight: .medium))
                            .foregroundColor(.white.opacity(0.88))
                            .frame(minWidth: 44, minHeight: 44)
                    }
                    .accessibilityLabel("隨身眼睛拍照")

                    if coordinator.isProcessingOrSpeaking {
                        // 思考/工具執行/朗讀中：優先顯示紅色 ⏹️ 立即中止按鈕，並鎖定每次只能送一筆
                        Button(action: {
                            haptic.impactOccurred()
                            coordinator.cancelCurrentTurn()
                        }) {
                            Circle()
                                .fill(
                                    LinearGradient(
                                        colors: [Color(red: 0.95, green: 0.25, blue: 0.25), Color(red: 0.78, green: 0.14, blue: 0.18)],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                                .frame(width: 44, height: 44)
                                .shadow(color: Color.red.opacity(0.45), radius: 8)
                                .overlay(
                                    Image(systemName: "stop.fill")
                                        .font(.system(size: 16, weight: .bold))
                                        .foregroundColor(.white)
                                )
                        }
                        .accessibilityLabel("停止進行中任務")
                    } else if !coordinator.textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || !coordinator.pendingImagesData.isEmpty
                        || coordinator.pendingImageData != nil
                        || !coordinator.pendingFiles.isEmpty
                        || coordinator.pendingLocationLabel != nil {
                        // 有文字或已附加照片時：顯示藍色 ↑ 送出按鈕
                        Button(action: {
                            haptic.impactOccurred()
                            isInputFocused = false
                            if let quoted = quotedMessage {
                                let snippet = quoted.content
                                    .replacingOccurrences(of: "\n", with: " ")
                                    .trimmingCharacters(in: .whitespacesAndNewlines)
                                let truncated = snippet.count > 160 ? String(snippet.prefix(160)) + "..." : snippet
                                let roleLabel = quoted.role == .user ? "我先前提問" : "助理先前回覆"
                                let userText = coordinator.textInput.trimmingCharacters(in: .whitespacesAndNewlines)
                                coordinator.textInput = "【引用\(roleLabel)：「\(truncated)」】\n\(userText)"
                                quotedMessage = nil
                            }
                            coordinator.sendTextMessage()
                        }) {
                            Circle()
                                .fill(Color(red: 0.20, green: 0.45, blue: 0.95))
                                .frame(width: 44, height: 44)
                                .overlay(
                                    Image(systemName: "arrow.up")
                                        .font(.system(size: 18, weight: .bold))
                                        .foregroundColor(.white)
                                )
                        }
                        .accessibilityLabel("送出訊息")
                    } else {
                        // 按鈕 2：((|)) Gemini Live 即時連續語音對談按鈕
                        Button(action: {
                            haptic.impactOccurred()
                            isInputFocused = false
                            withAnimation(.spring(response: 0.3, dampingFraction: 0.78)) {
                                coordinator.toggleLiveVoiceMode()
                            }
                        }) {
                            ZStack {
                                Circle()
                                    .fill(
                                        coordinator.isLiveMode
                                            ? LinearGradient(
                                                colors: [Color(red: 0.22, green: 0.52, blue: 1.0), Color(red: 0.10, green: 0.32, blue: 0.85)],
                                                startPoint: .topLeading,
                                                endPoint: .bottomTrailing
                                            )
                                            : LinearGradient(
                                                colors: [Color(red: 0.11, green: 0.21, blue: 0.44), Color(red: 0.09, green: 0.17, blue: 0.36)],
                                                startPoint: .topLeading,
                                                endPoint: .bottomTrailing
                                            )
                                    )
                                    .frame(width: 44, height: 44)
                                    .shadow(color: coordinator.isLiveMode ? Color.blue.opacity(0.55) : .clear, radius: 10)

                                GeminiLiveWaveformIcon(
                                    isActive: coordinator.isLiveMode,
                                    db: coordinator.currentDb
                                )
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .liquidGlass(
                in: RoundedRectangle(cornerRadius: 30, style: .continuous),
                tint: coordinator.isSingleDictationMode
                    ? Color.red.opacity(0.22)
                    : (coordinator.isLiveMode ? Color.blue.opacity(0.24) : nil)
            )
        }
        .animation(.easeOut(duration: 0.18), value: showMentionPicker)
    }

    // MARK: - @ Herdr Agent 浮動選單與補全邏輯
    private var herdrMentionPickerOverlay: some View {
        let items = filteredHerdrAgents
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "terminal.fill")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.cyan)
                Text("指定 Herdr Agent 派工")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.white.opacity(0.85))
                Spacer()
                if isLoadingHerdrAgents {
                    ProgressView()
                        .scaleEffect(0.7)
                        .tint(.cyan)
                }
                Button(action: {
                    showMentionPicker = false
                }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.white.opacity(0.6))
                        .frame(width: 24, height: 24)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 10)
            .padding(.top, 8)

            if items.isEmpty && !isLoadingHerdrAgents {
                Text(herdrAgents.isEmpty ? "目前沒有線上 Herdr Agent" : "找不到符合「@\(mentionQuery)」的 Agent")
                    .font(.system(size: 12.5))
                    .foregroundColor(.white.opacity(0.55))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
            } else {
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 4) {
                        ForEach(items) { agent in
                            Button(action: {
                                haptic.impactOccurred()
                                applyMentionSelection(agent)
                            }) {
                                HStack(spacing: 10) {
                                    Circle()
                                        .fill(agent.status == "idle" ? Color.green : Color.orange)
                                        .frame(width: 8, height: 8)

                                    VStack(alignment: .leading, spacing: 2) {
                                        HStack(spacing: 6) {
                                            Text("@\(agent.project)")
                                                .font(.system(size: 13.5, weight: .bold))
                                                .foregroundColor(.white)
                                            if !agent.name.isEmpty && agent.name != agent.project {
                                                Text("(\(agent.name))")
                                                    .font(.system(size: 11.5))
                                                    .foregroundColor(.white.opacity(0.6))
                                            }
                                        }
                                        let modelTag = (agent.model?.isEmpty == false) ? " · \(agent.model!)" : ""
                                        Text("\(agent.engine.uppercased())\(modelTag) · \(agent.status == "idle" ? "閒置待機" : agent.status)")
                                            .font(.system(size: 11))
                                            .foregroundColor(.white.opacity(0.62))
                                            .lineLimit(1)
                                    }

                                    Spacer()

                                    Image(systemName: "arrow.up.left")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundColor(.cyan.opacity(0.85))
                                }
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(
                                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                                        .fill(Color.white.opacity(0.06))
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 8)
                    .padding(.bottom, 8)
                }
                .frame(maxHeight: 190)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(red: 0.11, green: 0.14, blue: 0.21).opacity(0.96))
                .overlay(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(Color.cyan.opacity(0.32), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.45), radius: 12, y: -4)
        )
    }

    private var filteredHerdrAgents: [HerdrAgentInfo] {
        let q = mentionQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return herdrAgents }
        return herdrAgents.filter {
            $0.project.lowercased().contains(q)
                || $0.name.lowercased().contains(q)
                || $0.engine.lowercased().contains(q)
        }
    }

    private func activeMentionToken(in text: String) -> (Range<String.Index>, String)? {
        guard let atIdx = text.lastIndex(of: "@") else { return nil }
        if atIdx > text.startIndex {
            let prev = text[text.index(before: atIdx)]
            guard prev.isWhitespace || prev.isNewline else { return nil }
        }
        let afterAt = text.index(after: atIdx)
        let querySlice = text[afterAt...]
        if querySlice.contains(where: { $0.isWhitespace || $0.isNewline }) {
            return nil
        }
        return (atIdx..<text.endIndex, String(querySlice))
    }

    private func refreshHerdrAgentsForMention() {
        isLoadingHerdrAgents = true
        Task {
            let fetched = await MacMiniAPIClient.shared.fetchHerdrAgents()
            await MainActor.run {
                self.herdrAgents = fetched
                self.isLoadingHerdrAgents = false
            }
        }
    }

    private func applyMentionSelection(_ agent: HerdrAgentInfo) {
        if let (range, _) = activeMentionToken(in: coordinator.textInput) {
            coordinator.textInput.replaceSubrange(range, with: "@\(agent.project) ")
        } else {
            coordinator.textInput += "@\(agent.project) "
        }
        showMentionPicker = false
        mentionQuery = ""
    }

    // MARK: - Gemini Live 浮動聲波狀態列 (支援點擊打斷、立即送出、暫停麥克風)
    private var liveVoiceFloatingBar: some View {
        HStack(spacing: 12) {
            // 動態聲波指示器
            HStack(spacing: 3) {
                ForEach(0..<4, id: \.self) { i in
                    Capsule()
                        .fill(liveStateColor)
                        .frame(width: 3.5, height: liveBarHeight(index: i))
                        .animation(.easeOut(duration: 0.12), value: coordinator.currentDb)
                }
            }
            .frame(width: 24, height: 22)

            // 狀態描述 (點擊可立即送出或打斷助理)
            Button(action: {
                haptic.impactOccurred()
                coordinator.handleLiveActionTap()
            }) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(liveStatusTitle)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.white)
                        .lineLimit(1)

                    Text(liveStatusSubtitle)
                        .font(.system(size: 11))
                        .foregroundColor(.white.opacity(0.65))
                        .lineLimit(1)
                }
            }
            .buttonStyle(.plain)

            Spacer()

            // 停頓自動送出圓環進度
            if coordinator.silenceProgress > 0.05 {
                ZStack {
                    Circle()
                        .stroke(Color.white.opacity(0.15), lineWidth: 3)
                        .frame(width: 24, height: 24)
                    Circle()
                        .trim(from: 0, to: CGFloat(coordinator.silenceProgress))
                        .stroke(Color.cyan, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .frame(width: 24, height: 24)
                        .rotationEffect(.degrees(-90))
                }
            }

            // 暫停/恢復收音按鈕
            Button(action: {
                haptic.impactOccurred()
                coordinator.togglePause()
            }) {
                Image(systemName: coordinator.state == .paused ? "mic.slash.fill" : "pause.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(coordinator.state == .paused ? .orange : .white.opacity(0.85))
                    .frame(width: 32, height: 32)
                    .background(Color.white.opacity(0.12))
                    .clipShape(Circle())
            }

            // 結束 Live 語音對談按鈕
            Button(action: {
                haptic.impactOccurred()
                withAnimation {
                    coordinator.stopSession()
                }
            }) {
                Image(systemName: "xmark")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 32, height: 32)
                    .background(Color.red.opacity(0.75))
                    .clipShape(Circle())
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(
            Capsule()
                .fill(.ultraThinMaterial)
                .overlay(
                    Capsule()
                        .stroke(liveStateColor.opacity(0.45), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.35), radius: 8, x: 0, y: 3)
        )
    }

    private var liveStateColor: Color {
        switch coordinator.state {
        case .listening(let isSpeaking, _):
            return isSpeaking ? .green : .cyan
        case .transcribing, .thinkingAndStreaming:
            return .yellow
        case .speaking:
            return Color(red: 0.35, green: 0.65, blue: 1.0)
        case .paused, .idle:
            return .gray
        }
    }

    private var liveStatusTitle: String {
        switch coordinator.state {
        case .listening(let isSpeaking, _):
            return isSpeaking ? "正在聽您說話..." : "Live 連續聆聽中"
        case .transcribing:
            return "正在辨識語音..."
        case .thinkingAndStreaming(let tool):
            let cleanTool = tool?.replacingOccurrences(of: "⚙️ ", with: "").replacingOccurrences(of: "⚙️", with: "").trimmingCharacters(in: .whitespaces)
            return (cleanTool?.isEmpty == false) ? cleanTool! : "Gemini 思考處理中..."
        case .speaking:
            return "助理語音回答中"
        case .paused:
            return "麥克風已暫停"
        case .idle:
            return "點擊開始語音對談"
        }
    }

    private var liveStatusSubtitle: String {
        switch coordinator.state {
        case .listening(let isSpeaking, _):
            return isSpeaking ? "講完停頓自動送出｜點此立即送出" : "請直接開口，無需按住螢幕"
        case .transcribing, .thinkingAndStreaming:
            return "說「等一下」或點此即可立即中止思考"
        case .speaking:
            return "說「等一下」或點此即可立即打斷"
        case .paused:
            return "點擊恢復聆聽"
        default:
            return "免持連續對話模式"
        }
    }

    private func liveBarHeight(index: Int) -> CGFloat {
        let base: CGFloat = 6
        if case .listening(let isSpeaking, _) = coordinator.state, isSpeaking {
            let normalized = CGFloat(max(0, min(1, (coordinator.currentDb + 50) / 35)))
            let multipliers: [CGFloat] = [0.6, 1.0, 0.8, 0.5]
            return base + normalized * 14 * multipliers[index % 4]
        } else if case .speaking = coordinator.state {
            let heights: [CGFloat] = [10, 18, 14, 9]
            return heights[index % 4]
        }
        return base
    }

    // MARK: - 除錯診斷 HUD
    private var debugHUDView: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("收音與連線診斷 (\(coordinator.serverStatusText))", systemImage: "wrench.and.screwdriver.fill")
                    .font(.caption)
                    .bold()
                    .foregroundColor(.cyan)
                Spacer()
                Button("重新檢測") {
                    coordinator.checkServerHealth()
                }
                .font(.caption2)
                .foregroundColor(.yellow)
            }

            HStack {
                Text("即時音量: \(String(format: "%.1f", coordinator.currentDb)) dB")
                Spacer()
                Text("底噪: \(String(format: "%.1f", coordinator.vad.ambientNoiseFloor)) dB")
                Spacer()
                Text("停頓設定: \(coordinator.pauseSeconds) 秒")
            }
            .font(.caption2)
            .foregroundColor(.secondary)
        }
        .padding(12)
        .background(Color(white: 0.1).opacity(0.9))
        .cornerRadius(12)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.cyan.opacity(0.3), lineWidth: 1))
    }

    // 圖片壓縮輔助（限制最長邊 1280px，JPEG 0.78 品質，兼顧清晰度與秒傳速度）
    static func compressImage(_ image: UIImage) -> Data? {
        let maxDim: CGFloat = 1280
        let size = image.size
        let ratio = min(maxDim / max(size.width, 1), maxDim / max(size.height, 1), 1.0)
        let targetSize = CGSize(width: size.width * ratio, height: size.height * ratio)

        let renderer = UIGraphicsImageRenderer(size: targetSize)
        let resized = renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: targetSize))
        }
        return resized.jpegData(compressionQuality: 0.78)
    }

    // 高效能 ImageIO 直接縮圖壓縮（不解碼完整高畫素 Bitmap，大幅降低記憶體與延遲）
    nonisolated static func compressImageData(_ rawData: Data, maxDim: Int = 1280, quality: CGFloat = 0.78) -> Data? {
        let sourceOptions = [kCGImageSourceShouldCache: false] as CFDictionary
        if let source = CGImageSourceCreateWithData(rawData as CFData, sourceOptions) {
            let thumbOptions: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceShouldCacheImmediately: false,
                kCGImageSourceThumbnailMaxPixelSize: maxDim
            ]
            if let cgImage = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbOptions as CFDictionary) {
                return UIImage(cgImage: cgImage).jpegData(compressionQuality: quality)
            }
        }
        return nil
    }
}

// MARK: - 相機拍照 UIImagePickerController 封裝
struct CameraImagePicker: UIViewControllerRepresentable {
    var onImagePicked: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let parent: CameraImagePicker
        init(_ parent: CameraImagePicker) {
            self.parent = parent
        }

        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey : Any]) {
            if let img = info[.originalImage] as? UIImage {
                parent.onImagePicked(img)
            }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }
    }
}

// MARK: - 右下角 ((|)) 聲波圖示
struct GeminiLiveWaveformIcon: View {
    let isActive: Bool
    let db: Float

    var body: some View {
        HStack(spacing: 3.2) {
            Capsule()
                .fill(Color.white)
                .frame(width: 2.6, height: isActive ? barHeight(0.55) : 9)
            Capsule()
                .fill(Color.white)
                .frame(width: 2.6, height: isActive ? barHeight(1.0) : 17)
            Capsule()
                .fill(Color.white)
                .frame(width: 2.6, height: isActive ? barHeight(0.7) : 12)
        }
        .animation(.easeOut(duration: 0.12), value: db)
    }

    private func barHeight(_ factor: CGFloat) -> CGFloat {
        let level = CGFloat(max(0.15, min(1.0, (db + 52) / 36)))
        return 7 + level * 13 * factor
    }
}

// MARK: - Gemini 風格對話訊息列
private enum GalleryPhotoSource: Identifiable, Equatable {
    case data(Data, id: String)
    case url(URL)

    var id: String {
        switch self {
        case .data(_, let id): return "data_\(id)"
        case .url(let url): return "url_\(url.absoluteString)"
        }
    }
}

private struct PreviewPhotoItem: Identifiable {
    let id = UUID()
    let photos: [GalleryPhotoSource]
    let initialIndex: Int
}

private final class PhotoAlbumSaver: NSObject {
    static let shared = PhotoAlbumSaver()
    private var completions: [UnsafeMutableRawPointer: (Bool, String) -> Void] = [:]

    func save(_ source: GalleryPhotoSource, completion: @escaping @MainActor (Bool, String) -> Void) {
        Task {
            let imageData: Data?
            switch source {
            case .data(let data, _):
                imageData = data
            case .url(let url):
                imageData = try? await URLSession.shared.data(from: url).0
            }
            guard let data = imageData, let uiImage = UIImage(data: data) else {
                await completion(false, "無法取得圖片資料")
                return
            }
            await MainActor.run {
                let box = Unmanaged.passRetained(NSObject()).toOpaque()
                self.completions[box] = { ok, msg in
                    Task { @MainActor in completion(ok, msg) }
                }
                UIImageWriteToSavedPhotosAlbum(
                    uiImage,
                    self,
                    #selector(self.image(_:didFinishSavingWithError:contextInfo:)),
                    box
                )
            }
        }
    }

    @objc private func image(_ image: UIImage, didFinishSavingWithError error: Error?, contextInfo: UnsafeMutableRawPointer?) {
        guard let ptr = contextInfo else { return }
        Unmanaged<NSObject>.fromOpaque(ptr).release()
        let cb = completions.removeValue(forKey: ptr)
        if let err = error {
            cb?(false, "儲存失敗：\(err.localizedDescription)")
        } else {
            cb?(true, "已儲存至相簿")
        }
    }
}

private struct ZoomableGalleryPhotoPage: View {
    let source: GalleryPhotoSource
    let dragOffset: CGFloat
    @Binding var currentScale: CGFloat

    var body: some View {
        Group {
            switch source {
            case .data(let data, _):
                if let uiImg = UIImage(data: data) {
                    styledImage(Image(uiImage: uiImg))
                }
            case .url(let url):
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        styledImage(image)
                    case .failure:
                        VStack(spacing: 10) {
                            Image(systemName: "photo.badge.exclamationmark")
                                .font(.system(size: 36))
                                .foregroundColor(.white.opacity(0.6))
                            Text("圖片載入失敗")
                                .font(.footnote)
                                .foregroundColor(.white.opacity(0.6))
                        }
                    case .empty:
                        ProgressView()
                            .tint(.white)
                            .scaleEffect(1.2)
                    @unknown default:
                        EmptyView()
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private func styledImage(_ image: Image) -> some View {
        image
            .resizable()
            .scaledToFit()
            .scaleEffect(currentScale * max(0.85, 1.0 - dragOffset / 1200.0))
            .offset(y: dragOffset)
            .gesture(
                MagnificationGesture()
                    .onChanged { val in
                        currentScale = max(1.0, min(val, 4.0))
                    }
                    .onEnded { _ in
                        if currentScale < 1.1 {
                            withAnimation(.spring()) { currentScale = 1.0 }
                        }
                    }
            )
            .onTapGesture(count: 2) {
                withAnimation(.spring()) {
                    currentScale = currentScale > 1.2 ? 1.0 : 2.2
                }
            }
    }
}

private struct FullScreenPhotoViewer: View {
    let photos: [GalleryPhotoSource]
    let initialIndex: Int
    @Environment(\.dismiss) private var dismiss
    @State private var currentIndex: Int = 0
    @State private var scale: CGFloat = 1.0
    @State private var dragOffset: CGFloat = 0
    @State private var saveFeedback: String? = nil
    @State private var isSaving: Bool = false

    init(photos: [GalleryPhotoSource], initialIndex: Int = 0) {
        self.photos = photos
        self.initialIndex = max(0, min(initialIndex, max(0, photos.count - 1)))
        self._currentIndex = State(initialValue: self.initialIndex)
    }

    private func saveCurrentPhoto() {
        guard !isSaving, photos.indices.contains(currentIndex) else { return }
        isSaving = true
        PhotoAlbumSaver.shared.save(photos[currentIndex]) { _, message in
            isSaving = false
            withAnimation(.easeInOut(duration: 0.2)) {
                saveFeedback = message
            }
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                withAnimation(.easeInOut(duration: 0.2)) {
                    if saveFeedback == message { saveFeedback = nil }
                }
            }
        }
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.black
                .opacity(max(0.15, 1.0 - Double(dragOffset / 380.0)))
                .ignoresSafeArea()

            TabView(selection: $currentIndex) {
                ForEach(Array(photos.enumerated()), id: \.offset) { idx, src in
                    ZoomableGalleryPhotoPage(
                        source: src,
                        dragOffset: dragOffset,
                        currentScale: $scale
                    )
                    .contextMenu {
                        Button {
                            saveCurrentPhoto()
                        } label: {
                            Label("儲存到相簿", systemImage: "square.and.arrow.down")
                        }
                    }
                    .tag(idx)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .onChange(of: currentIndex) {
                scale = 1.0
            }
            .simultaneousGesture(
                DragGesture(minimumDistance: 18)
                    .onChanged { value in
                        guard scale <= 1.05 else { return }
                        if value.translation.height > 0 && abs(value.translation.height) > abs(value.translation.width) {
                            dragOffset = value.translation.height
                        }
                    }
                    .onEnded { value in
                        guard scale <= 1.05 else {
                            dragOffset = 0
                            return
                        }
                        if value.translation.height > 110 || value.predictedEndTranslation.height > 220 {
                            dismiss()
                        } else {
                            withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                                dragOffset = 0
                            }
                        }
                    }
            )

            HStack(spacing: 12) {
                if photos.count > 1 {
                    Text("\(currentIndex + 1) / \(photos.count)")
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .foregroundColor(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(Capsule().fill(Color.black.opacity(0.55)))
                }
                Spacer()
                Button(action: { saveCurrentPhoto() }) {
                    Group {
                        if isSaving {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.system(size: 30))
                                .foregroundColor(.white.opacity(0.85))
                        }
                    }
                    .frame(width: 32, height: 32)
                }
                .disabled(isSaving)

                Button(action: { dismiss() }) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 30))
                        .foregroundColor(.white.opacity(0.85))
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .opacity(dragOffset > 20 ? 0.4 : 1.0)

            if let msg = saveFeedback {
                VStack {
                    Spacer()
                    Text(msg)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(Capsule().fill(Color.black.opacity(0.78)))
                        .padding(.bottom, 42)
                }
                .transition(.opacity)
            }
        }
        .presentationBackground(.clear)
    }
}

struct GeminiMessageRow: View {
    let message: ChatMessage
    let isCurrentlySpeaking: Bool
    let onTapSpeak: () -> Void
    var onTapQuickReply: ((String) -> Void)? = nil

    @State private var isThoughtExpanded: Bool = false
    @State private var isToolsExpanded: Bool = false
    @State private var isReportExpandedOverride: Bool? = nil
    @State private var isLongContentExpanded: Bool = false
    @State private var previewPhoto: PreviewPhotoItem? = nil
    @AppStorage("chatBaseFontSize") private var chatBaseFontSize: Double = 15.0

    private var isBackgroundReportError: Bool {
        guard message.isBackgroundReport else { return false }
        if let status = message.metadata?.task_status?.lowercased() {
            return status == "error" || status == "failed"
        }
        if let code = message.metadata?.exit_code {
            return code != 0
        }
        // 舊紀錄尚未帶入結構化狀態碼時，一律視為正常（藍色預設收合）
        return false
    }

    private var isBackgroundReportExpanded: Bool {
        isReportExpandedOverride ?? isBackgroundReportError
    }

    private var backgroundReportTitle: String {
        if let metaName = message.metadata?.task_name?.trimmingCharacters(in: .whitespacesAndNewlines),
           !metaName.isEmpty {
            return metaName
        }
        let text = message.content
        if let start = text.range(of: "「"), let end = text[start.upperBound...].range(of: "」") {
            let name = String(text[start.upperBound..<end.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty { return name }
        }
        if text.contains("CI 監控") {
            return "CI 監控"
        }
        let firstLine = text.components(separatedBy: .newlines).first?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !firstLine.isEmpty {
            return firstLine.count > 18 ? String(firstLine.prefix(18)) + "…" : firstLine
        }
        return "背景任務"
    }

    private var isUser: Bool {
        message.role == .user
    }

    private var hasThoughts: Bool {
        !message.thoughtProcess.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var bubbleTimestampText: String {
        let cal = Calendar.current
        let timeStr = message.timestamp.formatted(date: .omitted, time: .shortened)
        if cal.isDateInToday(message.timestamp) {
            return "今天 \(timeStr)"
        } else if cal.isDateInYesterday(message.timestamp) {
            return "昨天 \(timeStr)"
        } else {
            let dateStr = message.timestamp.formatted(.dateTime.month(.defaultDigits).day(.defaultDigits))
            return "\(dateStr) \(timeStr)"
        }
    }

    var body: some View {
        if isUser {
            // 使用者訊息：右側深灰藍圓角氣泡（含附加照片縮圖）
            HStack {
                Spacer(minLength: 52)
                VStack(alignment: .trailing, spacing: 8) {
                    if let multi = message.attachedImagesData, multi.count > 1 {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                ForEach(multi.indices, id: \.self) { idx in
                                    if let uiImg = UIImage(data: multi[idx]) {
                                        Image(uiImage: uiImg)
                                            .resizable()
                                            .scaledToFill()
                                            .frame(width: 110, height: 110)
                                            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                            .onTapGesture {
                                                let sources = multi.enumerated().map { GalleryPhotoSource.data($0.element, id: "\(message.id.uuidString)_\($0.offset)") }
                                                previewPhoto = PreviewPhotoItem(photos: sources, initialIndex: idx)
                                            }
                                    }
                                }
                            }
                        }
                        .frame(maxWidth: 240)
                    } else if let imgData = message.attachedImageData, let uiImg = UIImage(data: imgData) {
                        Image(uiImage: uiImg)
                            .resizable()
                            .scaledToFill()
                            .frame(maxWidth: 200, maxHeight: 200)
                            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                            .onTapGesture {
                                previewPhoto = PreviewPhotoItem(
                                    photos: [.data(imgData, id: message.id.uuidString)],
                                    initialIndex: 0
                                )
                            }
                    }

                    if let fileNames = message.attachedFileNames, !fileNames.isEmpty {
                        VStack(alignment: .trailing, spacing: 6) {
                            ForEach(fileNames, id: \.self) { name in
                                HStack(spacing: 8) {
                                    Image(systemName: name.lowercased().hasSuffix(".pdf") ? "doc.richtext.fill" : "doc.fill")
                                        .font(.system(size: 14, weight: .semibold))
                                        .foregroundColor(Color(red: 0.45, green: 0.72, blue: 1.0))
                                    Text(name)
                                        .font(.system(size: 13, weight: .medium))
                                        .foregroundColor(.white.opacity(0.92))
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .liquidGlass(
                                    in: RoundedRectangle(cornerRadius: 14, style: .continuous),
                                    tint: Color.blue.opacity(0.18),
                                    interactive: false
                                )
                            }
                        }
                    }

                    Text(message.content)
                        .font(.system(size: chatBaseFontSize))
                        .foregroundColor(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .liquidGlass(
                            in: RoundedRectangle(cornerRadius: 20, style: .continuous),
                            tint: Color.blue.opacity(0.24),
                            interactive: false
                        )
                        .textSelection(.enabled)

                    Text(bubbleTimestampText)
                        .font(.caption2)
                        .foregroundColor(Color(white: 0.48))
                        .padding(.trailing, 4)
                }
            }
            .fullScreenCover(item: $previewPhoto) { item in
                FullScreenPhotoViewer(photos: item.photos, initialIndex: item.initialIndex)
            }
        } else {
            // 助理回覆：左側 Gemini 四色星芒標頭 + 乾淨無框全寬文字與工具膠囊
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    GeminiSparkleIcon(
                        size: 22,
                        isAnimating: message.status == .streaming || isCurrentlySpeaking
                    )

                    if message.isBackgroundReport {
                        Button(action: {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                isReportExpandedOverride = !isBackgroundReportExpanded
                            }
                        }) {
                            HStack(spacing: 6) {
                                HStack(spacing: 4) {
                                    Image(systemName: isBackgroundReportError ? "exclamationmark.triangle.fill" : "bell.badge.fill")
                                        .font(.system(size: 10.5, weight: .semibold))
                                    Text("背景回報")
                                        .font(.system(size: 11.5, weight: .semibold))
                                }
                                .foregroundColor(isBackgroundReportError ? Color(red: 1.0, green: 0.56, blue: 0.58) : Color(red: 0.48, green: 0.80, blue: 1.0))
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(isBackgroundReportError ? Color.red.opacity(0.22) : Color.blue.opacity(0.18))
                                .overlay(
                                    Capsule().stroke(isBackgroundReportError ? Color.red.opacity(0.45) : Color.blue.opacity(0.36), lineWidth: 0.8)
                                )
                                .clipShape(Capsule())

                                Text(backgroundReportTitle)
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundColor(.white.opacity(0.92))
                                    .lineLimit(1)
                                    .truncationMode(.tail)

                                Image(systemName: "chevron.down")
                                    .font(.system(size: 10.5, weight: .bold))
                                    .foregroundColor(Color(white: 0.65))
                                    .rotationEffect(.degrees(isBackgroundReportExpanded ? 180 : 0))
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }

                    if message.status == .streaming && message.content.isEmpty && message.toolHistory.isEmpty && !hasThoughts {
                        Text("正在思考...")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundColor(.secondary)
                    } else if isCurrentlySpeaking {
                        HStack(spacing: 4) {
                            Image(systemName: "speaker.wave.2.fill")
                                .font(.system(size: 12))
                            Text("朗讀中")
                                .font(.system(size: 12, weight: .medium))
                        }
                        .foregroundColor(.cyan)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.cyan.opacity(0.15))
                        .clipShape(Capsule())
                    }

                    Spacer()

                    Text(bubbleTimestampText)
                        .font(.caption2)
                        .foregroundColor(Color(white: 0.48))

                    // 語音朗讀 / 停止按鈕
                    if !message.content.isEmpty {
                        Button(action: onTapSpeak) {
                            Image(systemName: isCurrentlySpeaking ? "stop.circle.fill" : "speaker.wave.2")
                                .font(.system(size: 15))
                                .foregroundColor(isCurrentlySpeaking ? .cyan : .secondary)
                                .padding(6)
                        }
                    }
                }

                // 1. 工具呼叫軌跡（預設摺疊僅顯示最新一筆，點擊可展開完整步驟）
                if !message.toolHistory.isEmpty {
                    let rawTool = message.toolHistory.last ?? ""
                    let latestTool = rawTool.replacingOccurrences(of: "⚙️ ", with: "").replacingOccurrences(of: "⚙️", with: "").trimmingCharacters(in: .whitespaces)
                    let extraCount = max(0, message.toolHistory.count - 1)
                    VStack(alignment: .leading, spacing: 6) {
                        Button(action: {
                            withAnimation(.spring(response: 0.25, dampingFraction: 0.8)) {
                                isToolsExpanded.toggle()
                            }
                        }) {
                            HStack(spacing: 6) {
                                Image(systemName: "terminal.fill")
                                    .font(.system(size: 10.5, weight: .semibold))
                                    .foregroundColor(.cyan.opacity(0.9))
                                Text(latestTool)
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(Color(white: 0.85))
                                    .lineLimit(1)
                                if extraCount > 0 {
                                    Text("(\(message.toolHistory.count))")
                                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                                        .foregroundColor(.secondary)
                                }
                                Image(systemName: isToolsExpanded ? "chevron.up" : "chevron.down")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundColor(.secondary)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(.ultraThinMaterial)
                            .overlay(
                                Capsule().stroke(Color.white.opacity(0.16), lineWidth: 0.8)
                            )
                            .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)

                        if isToolsExpanded {
                            VStack(alignment: .leading, spacing: 4) {
                                ForEach(Array(message.toolHistory.enumerated()), id: \.offset) { idx, tool in
                                    HStack(spacing: 6) {
                                        Text("\(idx + 1).")
                                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                                            .foregroundColor(.secondary)
                                        Text(tool)
                                            .font(.system(size: 12))
                                            .foregroundColor(Color(white: 0.72))
                                    }
                                }
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(
                                RoundedRectangle(cornerRadius: 12, style: .continuous)
                                    .fill(Color.white.opacity(0.04))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                                            .stroke(Color.white.opacity(0.08), lineWidth: 0.8)
                                    )
                            )
                        }
                    }
                }

                // 2. 大腦思考推導過程（預設摺疊，點擊可展開）
                if hasThoughts {
                    let isActivelyThinking = (message.status == .streaming && message.content.isEmpty)
                    VStack(alignment: .leading, spacing: 6) {
                        Button(action: {
                            withAnimation(.spring(response: 0.25, dampingFraction: 0.8)) {
                                isThoughtExpanded.toggle()
                            }
                        }) {
                            HStack(spacing: 6) {
                                Image(systemName: "brain.head.profile")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundColor(.purple.opacity(0.9))
                                Text(isActivelyThinking ? "大腦正在推導思考..." : "檢視思考推導過程")
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundColor(Color(white: 0.78))
                                Image(systemName: isThoughtExpanded ? "chevron.up" : "chevron.down")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundColor(.secondary)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color.purple.opacity(0.12))
                            .overlay(
                                Capsule().stroke(Color.purple.opacity(0.28), lineWidth: 0.8)
                            )
                            .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)

                        if isThoughtExpanded {
                            Text(message.thoughtProcess.trimmingCharacters(in: .whitespacesAndNewlines))
                                .font(.system(size: 13))
                                .foregroundColor(Color(white: 0.68))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(
                                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                                        .fill(Color.white.opacity(0.04))
                                        .overlay(
                                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                                .stroke(Color.white.opacity(0.08), lineWidth: 0.8)
                                        )
                                )
                        }
                    }
                }

                // 助理回覆正文（背景回報預設摺疊，異常時預設展開；超過 20 行預設摺疊並提供一鍵展開）
                if !message.isBackgroundReport || isBackgroundReportExpanded {
                    if !message.content.isEmpty {
                        let contentLines = message.content.components(separatedBy: .newlines)
                        let isCollapsibleLongText = message.status != .streaming && contentLines.count > 20
                        let displayContent = (isCollapsibleLongText && !isLongContentExpanded)
                            ? contentLines.prefix(20).joined(separator: "\n")
                            : message.content

                        MarkdownMessageView(content: displayContent)
                            .textSelection(.enabled)

                        if isCollapsibleLongText {
                            Button(action: {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    isLongContentExpanded.toggle()
                                }
                            }) {
                                HStack(spacing: 6) {
                                    Text(isLongContentExpanded ? "收合內容" : "展開全文（共 \(contentLines.count) 行）")
                                        .font(.system(size: 12.5, weight: .semibold))
                                    Image(systemName: isLongContentExpanded ? "chevron.up" : "chevron.down")
                                        .font(.system(size: 10.5, weight: .bold))
                                }
                                .foregroundColor(.cyan)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(Color.cyan.opacity(0.12))
                                .overlay(
                                    Capsule().stroke(Color.cyan.opacity(0.32), lineWidth: 0.9)
                                )
                                .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                            .padding(.top, 2)
                        }
                    }
                }

                // 反問確認時自動生成的一鍵快速回覆膠囊 (Quick-Reply Chips)
                if !message.quickReplies.isEmpty, let onSelect = onTapQuickReply {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(message.quickReplies, id: \.self) { opt in
                                Button(action: {
                                    onSelect(opt)
                                }) {
                                    HStack(spacing: 5) {
                                        Image(systemName: "sparkles")
                                            .font(.system(size: 11, weight: .bold))
                                            .foregroundColor(.cyan)
                                        Text(opt)
                                            .font(.system(size: 13.5, weight: .semibold))
                                            .foregroundColor(.white)
                                    }
                                    .padding(.horizontal, 13)
                                    .padding(.vertical, 8)
                                    .background(
                                        Capsule()
                                            .fill(Color(red: 0.12, green: 0.22, blue: 0.38))
                                            .overlay(
                                                Capsule()
                                                    .stroke(Color.cyan.opacity(0.45), lineWidth: 1)
                                            )
                                    )
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.top, 2)
                    }
                }

                // 助理附帶圖片（支援單張與多張橫向滑動，點擊全螢幕左右滑動瀏覽）
                let assistantUrls = message.assistantImageUrls
                if assistantUrls.count == 1, let singleUrl = assistantUrls.first {
                    AsyncImage(url: singleUrl) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(maxWidth: 260, maxHeight: 220)
                                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                                .onTapGesture {
                                    previewPhoto = PreviewPhotoItem(photos: [.url(singleUrl)], initialIndex: 0)
                                }
                                .contextMenu {
                                    Button {
                                        PhotoAlbumSaver.shared.save(.url(singleUrl)) { _, _ in }
                                    } label: {
                                        Label("儲存圖片到相簿", systemImage: "square.and.arrow.down")
                                    }
                                }
                        case .failure:
                            HStack(spacing: 6) {
                                Image(systemName: "photo.badge.exclamationmark")
                                Text("圖片載入失敗")
                            }
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.06)))
                        case .empty:
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .fill(Color.white.opacity(0.06))
                                .frame(width: 220, height: 160)
                                .overlay(ProgressView().tint(.white))
                        @unknown default:
                            EmptyView()
                        }
                    }
                    .padding(.top, 4)
                } else if assistantUrls.count > 1 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(Array(assistantUrls.enumerated()), id: \.offset) { idx, imgUrl in
                                AsyncImage(url: imgUrl) { phase in
                                    switch phase {
                                    case .success(let image):
                                        image
                                            .resizable()
                                            .scaledToFill()
                                            .frame(width: 136, height: 136)
                                            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                            .onTapGesture {
                                                let sources = assistantUrls.map { GalleryPhotoSource.url($0) }
                                                previewPhoto = PreviewPhotoItem(photos: sources, initialIndex: idx)
                                            }
                                            .contextMenu {
                                                Button {
                                                    PhotoAlbumSaver.shared.save(.url(imgUrl)) { _, _ in }
                                                } label: {
                                                    Label("儲存圖片到相簿", systemImage: "square.and.arrow.down")
                                                }
                                            }
                                    case .failure:
                                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                                            .fill(Color.white.opacity(0.06))
                                            .frame(width: 136, height: 136)
                                            .overlay(
                                                Image(systemName: "photo.badge.exclamationmark")
                                                    .foregroundColor(.secondary)
                                            )
                                    case .empty:
                                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                                            .fill(Color.white.opacity(0.06))
                                            .frame(width: 136, height: 136)
                                            .overlay(ProgressView().tint(.white))
                                    @unknown default:
                                        EmptyView()
                                    }
                                }
                            }
                        }
                    }
                    .padding(.top, 4)
                }

                // HTML 報告連結（若有）
                if let widgetHtml = message.widgetHtml {
                    HTMLWidgetView(html: widgetHtml)
                        .padding(.top, 4)
                }

                if let reportUrl = message.reportUrl, let url = URL(string: reportUrl) {
                    Link(destination: url) {
                        HStack(spacing: 6) {
                            Image(systemName: "safari.fill")
                            Text("開啟專屬報告")
                                .font(.footnote)
                                .bold()
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 7)
                        .background(Color.blue.opacity(0.2))
                        .foregroundColor(.cyan)
                        .cornerRadius(10)
                    }
                }

                // 單輪模型與花費 Metadata（已完成且有紀錄時顯示）
                if message.status == .completed,
                   let metaText = message.metadata?.summaryText,
                   !metaText.isEmpty {
                    Text(metaText)
                        .font(.caption2)
                        .foregroundColor(Color(white: 0.48))
                        .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, message.isBackgroundReport ? 14 : 0)
            .padding(.vertical, message.isBackgroundReport ? 12 : 0)
            .background(
                Group {
                    if message.isBackgroundReport {
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(
                                isBackgroundReportError
                                    ? Color(red: 0.30, green: 0.10, blue: 0.13).opacity(0.88)
                                    : Color(red: 0.09, green: 0.14, blue: 0.22).opacity(0.88)
                            )
                    }
                }
            )
            .overlay(
                Group {
                    if message.isBackgroundReport {
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .stroke(
                                isBackgroundReportError
                                    ? Color(red: 0.95, green: 0.42, blue: 0.46).opacity(0.45)
                                    : Color.blue.opacity(0.28),
                                lineWidth: 1
                            )
                    }
                }
            )
            .fullScreenCover(item: $previewPhoto) { item in
                FullScreenPhotoViewer(photos: item.photos, initialIndex: item.initialIndex)
            }
        }
    }
}

// MARK: - AGY 配額與大腦 Context 詳情面板 (點擊右上配額膠囊彈出)
struct QuotaDetailSheetView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    @Environment(\.dismiss) private var dismiss

    private var snap: QuotaSnapshot {
        coordinator.quotaSnapshot ?? QuotaSnapshot()
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.07, blue: 0.10).ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 14) {
                        // 1. 7 天週配額 (Weekly 7d)
                        quotaRowCard(
                            icon: "calendar.badge.clock",
                            title: "AGY 7 天週配額 (Weekly)",
                            percent: snap.weeklyRemainingPct,
                            subtitle: "距離重置：\(snap.weeklyRefreshesIn)" + (snap.weeklyResetTimeTaipei.isEmpty ? "" : "（台北 \(snap.weeklyResetTimeTaipei)）"),
                            color: snap.weeklyRemainingPct >= 50 ? .green : (snap.weeklyRemainingPct >= 20 ? .orange : .red)
                        )

                        // 2. 5 小時區間配額 (Rolling 5h)
                        quotaRowCard(
                            icon: "bolt.circle.fill",
                            title: "5 小時滾動配額 (Rolling 5h)",
                            percent: snap.rolling5hRemainingPct,
                            subtitle: "距離重置：\(snap.rolling5hRefreshesIn)" + (snap.rolling5hResetTimeTaipei.isEmpty ? "" : "（台北 \(snap.rolling5hResetTimeTaipei)）"),
                            color: .cyan
                        )

                        // 3. Gemini 4 Argon (Pro 深度推理)
                        quotaRowCard(
                            icon: "atom",
                            title: "Gemini 4 Argon (Pro 深度推理)",
                            percent: snap.argonRemainingPct,
                            subtitle: "距離重置：\(snap.argonRefreshesIn)" + (snap.argonResetTimeTaipei.isEmpty ? "" : "（台北 \(snap.argonResetTimeTaipei)）"),
                            color: snap.argonRemainingPct >= 50 ? Color(red: 0.45, green: 0.65, blue: 1.0) : (snap.argonRemainingPct >= 20 ? .orange : .red)
                        )

                        // 4. 第三方模型 (Claude / GPT 3P)
                        quotaRowCard(
                            icon: "sparkles",
                            title: "Claude / GPT (第三方 3P)",
                            percent: snap.thirdPartyRemainingPct,
                            subtitle: "距離重置：\(snap.thirdPartyRefreshesIn)",
                            color: .purple
                        )

                        // 5. Prompt / Flow Credits 配額卡片
                        creditsCard

                        // 6. ACP 統一大腦 Context 記憶容量
                        contextMemoryCard
                    }
                    .padding(16)
                }
            }
            .navigationTitle("AI 配額與系統狀態")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: {
                        coordinator.refreshQuotaStatus(force: true)
                    }) {
                        HStack(spacing: 4) {
                            if coordinator.isRefreshingQuota {
                                ProgressView()
                                    .scaleEffect(0.75)
                            } else {
                                Image(systemName: "arrow.clockwise")
                            }
                            Text("刷新")
                                .font(.subheadline)
                        }
                    }
                    .disabled(coordinator.isRefreshingQuota)
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") {
                        dismiss()
                    }
                    .bold()
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var creditsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Prompt / Flow Credits", systemImage: "creditcard.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                Spacer()
                Text(snap.planTier)
                    .font(.caption2)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.blue.opacity(0.2))
                    .foregroundColor(.cyan)
                    .cornerRadius(6)
            }

            Divider()
                .background(Color.white.opacity(0.08))

            HStack(spacing: 20) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Available Prompt")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text("\(snap.promptCredits.formatted())")
                            .font(.system(size: 17, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                        if snap.monthlyPromptCredits > 0 {
                            Text("/ \(snap.monthlyPromptCredits.formatted())")
                                .font(.caption2)
                                .foregroundColor(Color(white: 0.6))
                        }
                    }
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 3) {
                    Text("Available Flow")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text("\(snap.flowCredits.formatted())")
                            .font(.system(size: 17, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                        if snap.monthlyFlowCredits > 0 {
                            Text("/ \(snap.monthlyFlowCredits.formatted())")
                                .font(.caption2)
                                .foregroundColor(Color(white: 0.6))
                        }
                    }
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
    }

    private var contextMemoryCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("ACP 統一大腦 Context 用量", systemImage: "brain.head.profile")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                Spacer()
                Text(String(format: "%.1f%%", snap.contextPercent))
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .foregroundColor(.purple)
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.white.opacity(0.1))
                        .frame(height: 7)
                    Capsule()
                        .fill(Color.purple)
                        .frame(width: max(6, geo.size.width * CGFloat(min(1.0, snap.contextPercent / 100.0))), height: 7)
                }
            }
            .frame(height: 7)

            HStack {
                Text("\(snap.contextTokens.formatted()) / \(snap.contextLimit.formatted()) tokens")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Spacer()
                Text("Session: \(String(snap.sessionId.prefix(8)))")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(.secondary)
            }

            Divider()
                .background(Color.white.opacity(0.1))
                .padding(.vertical, 2)

            HStack(spacing: 10) {
                Button(action: {
                    coordinator.compactBrainContext()
                }) {
                    HStack(spacing: 6) {
                        if coordinator.isCompactingContext {
                            ProgressView()
                                .scaleEffect(0.75)
                        } else {
                            Image(systemName: "wand.and.stars")
                                .font(.system(size: 12, weight: .bold))
                        }
                        Text(coordinator.isCompactingContext ? "正在無損壓縮記憶..." : "壓縮記憶 (/compact)")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(
                        Capsule()
                            .fill(Color.purple.opacity(0.28))
                            .overlay(
                                Capsule()
                                    .stroke(Color.purple.opacity(0.55), lineWidth: 1)
                            )
                    )
                }
                .disabled(coordinator.isCompactingContext || coordinator.isProcessingOrSpeaking)

                Spacer()

                if let statusMsg = coordinator.compactStatusMessage {
                    Text(statusMsg)
                        .font(.caption2)
                        .foregroundColor(.cyan)
                        .lineLimit(2)
                        .multilineTextAlignment(.trailing)
                } else {
                    Text("不中斷對話釋放空間")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
    }

    private func quotaRowCard(
        icon: String,
        title: String,
        percent: Double,
        subtitle: String,
        color: Color
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(title, systemImage: icon)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                Spacer()
                Text(String(format: "%.1f%%", percent))
                    .font(.system(size: 16, weight: .bold, design: .rounded))
                    .foregroundColor(color)
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.white.opacity(0.1))
                        .frame(height: 7)
                    Capsule()
                        .fill(color)
                        .frame(width: max(6, geo.size.width * CGFloat(min(1.0, percent / 100.0))), height: 7)
                }
            }
            .frame(height: 7)

            Text(subtitle)
                .font(.caption)
                .foregroundColor(Color(white: 0.72))
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
    }
}

// MARK: - 原生 AirPlay / 藍牙音訊路由選擇視圖封裝
struct AudioRoutePickerViewWrapper: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.activeTintColor = .systemCyan
        picker.tintColor = .white
        picker.prioritizesVideoDevices = false
        return picker
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}

// MARK: - 圓形扇區填充形狀 (Context 視窗佔比視覺化)
struct ContextPieFillShape: Shape {
    var progress: Double

    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let clamped = min(1.0, max(0.0, progress))
        var path = Path()
        guard clamped > 0 else { return path }
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let radius = min(rect.width, rect.height) / 2.0
        path.move(to: center)
        path.addArc(
            center: center,
            radius: radius,
            startAngle: .degrees(-90),
            endAngle: .degrees(-90 + 360.0 * clamped),
            clockwise: false
        )
        path.closeSubpath()
        return path
    }
}

struct TaskLogViewerSheet: View {
    let task: MacMiniAPIClient.TrackedTaskItem
    @ObservedObject var coordinator: VoiceSessionCoordinator
    let onAttachAndClose: () -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var logResp: MacMiniAPIClient.TaskLogResponse? = nil
    @State private var isLoading = true
    @State private var errorsOnly = false
    @State private var wrapLines = true
    @State private var tailCount = 200

    private var displayedLines: [String] {
        let all = (logResp?.content ?? "").components(separatedBy: "\n")
        if !errorsOnly { return all }
        return all.filter { line in
            let low = line.lowercased()
            return low.contains("error") || low.contains("fail") || low.contains("exception")
                || low.contains("traceback") || line.contains("❌") || line.contains("⚠️")
                || line.contains("🚨")
        }
    }

    private func lineColor(_ line: String) -> Color {
        let low = line.lowercased()
        if low.contains("error") || low.contains("fail") || low.contains("traceback") || line.contains("❌") || line.contains("🚨") {
            return Color(red: 1.0, green: 0.45, blue: 0.45)
        }
        if low.contains("warn") || line.contains("⚠️") {
            return .orange
        }
        if line.contains("✅") || low.contains("success") || low.contains("healthy") {
            return Color(red: 0.45, green: 0.90, blue: 0.55)
        }
        return Color(white: 0.86)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.06, blue: 0.09).ignoresSafeArea()

                VStack(spacing: 12) {
                    // 上層摘要卡
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(task.name)
                                .font(.system(size: 15, weight: .bold))
                                .foregroundColor(.white)
                                .lineLimit(2)
                            Spacer()
                            Text(task.status)
                                .font(.system(size: 11, weight: .bold, design: .rounded))
                                .foregroundColor(task.status == "RUNNING" ? .green : .red)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background((task.status == "RUNNING" ? Color.green : Color.red).opacity(0.16))
                                .cornerRadius(6)
                        }
                        Text("ID: \(task.task_id)  ·  開始: \(task.started_at ?? "-")")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(.secondary)
                        if let resp = logResp {
                            Text("共 \(resp.total_lines) 行 · 顯示最後 \(resp.tail_lines) 行 · \(ByteCountFormatter.string(fromByteCount: Int64(resp.file_size_bytes), countStyle: .file))")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.06)))

                    // 控制列
                    HStack(spacing: 10) {
                        Toggle("只看異常", isOn: $errorsOnly)
                            .toggleStyle(.button)
                            .font(.caption)
                            .tint(.orange)
                        Toggle("自動換行", isOn: $wrapLines)
                            .toggleStyle(.button)
                            .font(.caption)
                            .tint(.cyan)
                        Spacer()
                        if logResp?.truncated == true && tailCount < 600 {
                            Button("載入更多") {
                                tailCount = 600
                                Task {
                                    isLoading = true
                                    logResp = await coordinator.apiClient.fetchTaskLog(taskId: task.task_id, tail: tailCount)
                                    isLoading = false
                                }
                            }
                            .font(.caption.bold())
                            .foregroundColor(.cyan)
                        }
                    }

                    // 下層日誌檢視區
                    if isLoading {
                        Spacer()
                        ProgressView("載入日誌中...")
                        Spacer()
                    } else if displayedLines.isEmpty {
                        Spacer()
                        Text(errorsOnly ? "目前尾段日誌中無異常關鍵字" : "尚無日誌內容")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        Spacer()
                    } else {
                        ScrollView(wrapLines ? .vertical : [.vertical, .horizontal]) {
                            LazyVStack(alignment: .leading, spacing: 4) {
                                ForEach(Array(displayedLines.enumerated()), id: \.offset) { _, line in
                                    Text(line)
                                        .font(.system(size: 11.5, design: .monospaced))
                                        .foregroundColor(lineColor(line))
                                        .fixedSize(horizontal: !wrapLines, vertical: true)
                                        .textSelection(.enabled)
                                }
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .background(RoundedRectangle(cornerRadius: 12).fill(Color.black.opacity(0.45)))
                    }

                    // 底部 CTA：一鍵帶入對話給 AI 分析
                    Button(action: {
                        let tailSnippet = displayedLines.suffix(80).joined(separator: "\n")
                        coordinator.textInput = "請幫我分析背景任務「\(task.name)」（\(task.task_id)，狀態：\(task.status)）的日誌並找出問題：\n\n\(tailSnippet)"
                        onAttachAndClose()
                    }) {
                        Label("帶入對話請 AI 分析", systemImage: "sparkles")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.white)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color.cyan.opacity(0.85))
                            .cornerRadius(12)
                    }
                    .disabled((logResp?.content ?? "").isEmpty)
                }
                .padding(14)
            }
            .navigationTitle("任務日誌")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("關閉") { dismiss() }
                }
            }
            .task {
                isLoading = true
                logResp = await coordinator.apiClient.fetchTaskLog(taskId: task.task_id, tail: tailCount)
                isLoading = false
            }
        }
        .preferredColorScheme(.dark)
    }
}

// MARK: - 對話全文搜尋面板（同時搜尋提問與回覆，支援本地即時與後端全量紀錄）
private struct HistorySearchSheetView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    let onJumpToMessage: (UUID) -> Void
    let onQuoteSnippet: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var searchText: String = ""
    @State private var roleFilter: String = "all"
    @State private var remoteHits: [HistorySearchHit] = []
    @State private var isSearchingRemote: Bool = false
    @State private var searchTask: Task<Void, Never>? = nil
    @State private var expandedHitIds: Set<String> = []

    private var localMatches: [ChatMessage] {
        let terms = searchText
            .split(whereSeparator: { $0.isWhitespace })
            .map { $0.lowercased() }
        guard !terms.isEmpty else { return [] }
        return coordinator.messages.reversed().filter { msg in
            if roleFilter == "user" && msg.role != .user { return false }
            if roleFilter == "assistant" && msg.role != .assistant { return false }
            let lower = msg.content.lowercased()
            return !lower.isEmpty && terms.allSatisfy { lower.contains($0) }
        }
    }

    private func triggerSearch() {
        searchTask?.cancel()
        let q = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else {
            remoteHits = []
            isSearchingRemote = false
            return
        }
        isSearchingRemote = true
        let role = roleFilter
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else { return }
            let hits = await coordinator.apiClient.searchHistory(query: q, role: role, limit: 50)
            guard !Task.isCancelled else { return }
            await MainActor.run {
                remoteHits = hits
                isSearchingRemote = false
            }
        }
    }

    private func findLocalMessageId(for hit: HistorySearchHit) -> UUID? {
        let targetText = (hit.matchedIn == "user" ? hit.query : hit.reply)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !targetText.isEmpty else { return nil }
        return coordinator.messages.last(where: {
            $0.content.trimmingCharacters(in: .whitespacesAndNewlines) == targetText
        })?.id
    }

    private func highlightedText(_ text: String) -> AttributedString {
        var attr = AttributedString(text)
        let terms = searchText
            .split(whereSeparator: { $0.isWhitespace })
            .map { String($0) }
        for term in terms where !term.isEmpty {
            var searchStart = attr.startIndex
            while searchStart < attr.endIndex,
                  let range = attr[searchStart...].range(of: term, options: [.caseInsensitive, .diacriticInsensitive]) {
                attr[range].foregroundColor = .cyan
                attr[range].font = .system(size: 13.5, weight: .bold)
                searchStart = range.upperBound
            }
        }
        return attr
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(.secondary)
                    TextField("搜尋你的提問或助理回覆...", text: $searchText)
                        .textInputAutocapitalization(.never)
                        .disableAutocorrection(true)
                        .submitLabel(.search)
                        .onChange(of: searchText) { triggerSearch() }

                    if !searchText.isEmpty {
                        Button(action: {
                            searchText = ""
                            remoteHits = []
                        }) {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundColor(.secondary)
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.white.opacity(0.08)))
                .padding(.horizontal, 16)

                Picker("篩選", selection: $roleFilter) {
                    Text("全部").tag("all")
                    Text("我問的").tag("user")
                    Text("助理答的").tag("assistant")
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .onChange(of: roleFilter) { triggerSearch() }

                if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    VStack(spacing: 10) {
                        Spacer()
                        Image(systemName: "text.magnifyingglass")
                            .font(.system(size: 36))
                            .foregroundColor(.cyan.opacity(0.75))
                        Text("輸入關鍵字搜尋所有歷史對話")
                            .font(.subheadline.weight(.medium))
                            .foregroundColor(.secondary)
                        Text("支援多個關鍵字空白分隔，同時搜尋提問與回覆")
                            .font(.caption)
                            .foregroundColor(Color(white: 0.45))
                        Spacer()
                    }
                } else {
                    List {
                        if !remoteHits.isEmpty {
                            Section("雲端完整紀錄（\(remoteHits.count) 筆）") {
                                ForEach(remoteHits) { hit in
                                    let isExpanded = expandedHitIds.contains(hit.id)
                                    let localId = findLocalMessageId(for: hit)
                                    VStack(alignment: .leading, spacing: 6) {
                                        HStack {
                                            Text(hit.matchedIn == "user" ? "你的提問" : (hit.matchedIn == "both" ? "問答皆命中" : "助理回覆"))
                                                .font(.caption2.weight(.semibold))
                                                .foregroundColor(hit.matchedIn == "user" ? .blue : .cyan)
                                                .padding(.horizontal, 7)
                                                .padding(.vertical, 2)
                                                .background(Capsule().fill(Color.white.opacity(0.08)))
                                            Spacer()
                                            Text(String(hit.timestamp.prefix(16)).replacingOccurrences(of: "T", with: " "))
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                        }

                                        if !hit.query.isEmpty {
                                            Text(highlightedText("問：\(hit.query)"))
                                                .font(.system(size: 13.5))
                                                .lineLimit(isExpanded ? nil : 2)
                                        }
                                        if !hit.reply.isEmpty {
                                            Text(highlightedText("答：\(hit.reply)"))
                                                .font(.system(size: 13))
                                                .foregroundColor(Color(white: 0.85))
                                                .lineLimit(isExpanded ? nil : 3)
                                        }

                                        HStack(spacing: 12) {
                                            if let lid = localId {
                                                Button("跳轉到對話") {
                                                    onJumpToMessage(lid)
                                                }
                                                .font(.caption.weight(.semibold))
                                                .foregroundColor(.cyan)
                                            } else {
                                                Button(isExpanded ? "收合" : "展開全文") {
                                                    if isExpanded { expandedHitIds.remove(hit.id) }
                                                    else { expandedHitIds.insert(hit.id) }
                                                }
                                                .font(.caption.weight(.semibold))
                                                .foregroundColor(.cyan)
                                            }

                                            Button("引用追問") {
                                                let snippet = hit.reply.isEmpty ? hit.query : hit.reply
                                                onQuoteSnippet(snippet)
                                            }
                                            .font(.caption.weight(.semibold))
                                            .foregroundColor(.blue)
                                        }
                                        .buttonStyle(.borderless)
                                        .padding(.top, 2)
                                    }
                                    .padding(.vertical, 4)
                                }
                            }
                        } else if !localMatches.isEmpty {
                            Section("近期對話（\(localMatches.count) 筆）") {
                                ForEach(localMatches) { msg in
                                    Button(action: { onJumpToMessage(msg.id) }) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            HStack {
                                                Text(msg.role == .user ? "你的提問" : "助理回覆")
                                                    .font(.caption2.weight(.semibold))
                                                    .foregroundColor(msg.role == .user ? .blue : .cyan)
                                                Spacer()
                                                Text(msg.timestamp.formatted(date: .abbreviated, time: .shortened))
                                                    .font(.caption2)
                                                    .foregroundColor(.secondary)
                                            }
                                            Text(highlightedText(msg.content))
                                                .font(.system(size: 13.5))
                                                .foregroundColor(.primary)
                                                .lineLimit(3)
                                        }
                                    }
                                }
                            }
                        } else if isSearchingRemote {
                            HStack(spacing: 8) {
                                ProgressView()
                                Text("正在搜尋 Mac mini 完整歷史紀錄...")
                                    .font(.footnote)
                                    .foregroundColor(.secondary)
                            }
                        } else {
                            Text("找不到符合「\(searchText)」的對話紀錄")
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .padding(.top, 8)
            .navigationTitle("搜尋對話")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("關閉") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

// MARK: - Apple Liquid Glass 通用表面修飾器（iOS 26+ 原生 glassEffect，舊版自動降級為高光液態材質）
private extension View {
    @ViewBuilder
    func liquidGlass<S: Shape>(in shape: S, tint: Color? = nil, interactive: Bool = true) -> some View {
        if #available(iOS 26.0, *) {
            if let tint = tint {
                self.glassEffect(
                    interactive ? .regular.tint(tint).interactive() : .regular.tint(tint),
                    in: shape
                )
            } else {
                self.glassEffect(
                    interactive ? .regular.interactive() : .regular,
                    in: shape
                )
            }
        } else {
            self
                .background(
                    shape
                        .fill(.ultraThinMaterial)
                        .overlay(
                            shape.fill(
                                LinearGradient(
                                    colors: [
                                        (tint ?? Color.white).opacity(tint != nil ? 0.28 : 0.14),
                                        Color.white.opacity(0.03)
                                    ],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                        )
                        .overlay(
                            shape.stroke(
                                LinearGradient(
                                    colors: [
                                        Color.white.opacity(0.45),
                                        Color.white.opacity(0.08),
                                        Color.white.opacity(0.22)
                                    ],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                ),
                                lineWidth: 1
                            )
                        )
                )
                .shadow(color: Color.black.opacity(0.28), radius: 12, x: 0, y: 6)
        }
    }
}

// MARK: - 單則對話截圖分享卡片與渲染輔助
extension HandsFreeMainView {
    @MainActor
    func shareMessageAsScreenshot(_ message: ChatMessage) {
        Task { @MainActor in
            var widgetSnapshot: UIImage? = nil
            if let widgetHtml = message.metadata?.widget_html?.trimmingCharacters(in: .whitespacesAndNewlines),
               !widgetHtml.isEmpty {
                widgetSnapshot = await HTMLWidgetView.renderSnapshot(html: widgetHtml, width: 350)
            }

            let card = MessageScreenshotCard(
                message: message,
                baseFontSize: chatBaseFontSize,
                widgetSnapshot: widgetSnapshot
            )
            let renderer = ImageRenderer(content: card)
            renderer.scale = UIScreen.main.scale
            guard let image = renderer.uiImage else { return }

            let activityVC = UIActivityViewController(activityItems: [image], applicationActivities: nil)
            guard let scene = UIApplication.shared.connectedScenes.first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene,
                  let root = scene.windows.first(where: { $0.isKeyWindow })?.rootViewController else {
                return
            }
            var topVC = root
            while let presented = topVC.presentedViewController {
                topVC = presented
            }
            if let popover = activityVC.popoverPresentationController {
                popover.sourceView = topVC.view
                popover.sourceRect = CGRect(x: topVC.view.bounds.midX, y: topVC.view.bounds.midY, width: 0, height: 0)
                popover.permittedArrowDirections = []
            }
            topVC.present(activityVC, animated: true)
        }
    }
}

private struct MessageScreenshotCard: View {
    let message: ChatMessage
    let baseFontSize: Double
    let widgetSnapshot: UIImage?

    private var formattedTime: String {
        let df = DateFormatter()
        df.locale = Locale(identifier: "zh_TW")
        df.dateFormat = "yyyy/MM/dd HH:mm"
        return df.string(from: message.timestamp)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                if message.role == .assistant {
                    GeminiSparkleIcon(size: 20, isAnimating: false)
                    Text("Mac mini 隨身助理")
                        .font(.system(size: 13.5, weight: .semibold))
                        .foregroundColor(.white.opacity(0.92))
                } else {
                    Image(systemName: "person.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(Color(red: 0.48, green: 0.78, blue: 1.0))
                    Text("我的提問")
                        .font(.system(size: 13.5, weight: .semibold))
                        .foregroundColor(.white.opacity(0.92))
                }
                Spacer()
                Text(formattedTime)
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundColor(Color(white: 0.55))
            }

            if let fileNames = message.attachedFileNames, !fileNames.isEmpty {
                HStack(spacing: 6) {
                    ForEach(fileNames, id: \.self) { name in
                        HStack(spacing: 5) {
                            Image(systemName: name.lowercased().hasSuffix(".pdf") ? "doc.richtext.fill" : "doc.fill")
                                .font(.system(size: 12))
                                .foregroundColor(Color(red: 0.45, green: 0.72, blue: 1.0))
                            Text(name)
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.white.opacity(0.9))
                                .lineLimit(1)
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color.blue.opacity(0.18))
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                }
            }

            if let imgData = message.attachedImageData, let uiImg = UIImage(data: imgData) {
                Image(uiImage: uiImg)
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }

            if !message.content.isEmpty {
                MarkdownMessageView(content: message.content)
            }

            if let widgetSnapshot {
                Image(uiImage: widgetSnapshot)
                    .resizable()
                    .scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .stroke(Color.white.opacity(0.12), lineWidth: 1)
                    )
            }
        }
        .padding(20)
        .frame(width: 390, alignment: .leading)
        .background(
            LinearGradient(
                colors: [
                    Color(red: 0.04, green: 0.06, blue: 0.13),
                    Color(red: 0.07, green: 0.11, blue: 0.24)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .stroke(Color.white.opacity(0.14), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
    }
}

// MARK: - Herdr 派工督工中心 (Action Queue & Dispatch Hub) 抽屜面板
private struct PendingActionsSheetView: View {
    @Binding var actions: [PendingActionItem]
    @Binding var history: [PendingActionItem]
    let onRefresh: () async -> Void
    let onResolve: (PendingActionItem, String, Bool, String?) async -> Bool

    private enum HubTab: String, CaseIterable {
        case active = "active"
        case history = "history"
    }

    @Environment(\.dismiss) private var dismiss
    @State private var selectedTab: HubTab = .active
    @State private var customReplies: [String: String] = [:]
    @State private var busyIds: Set<String> = []
    @State private var expandedTerminals: Set<String> = []
    @State private var expandedRounds: Set<String> = []
    @State private var isRefreshing: Bool = false

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.07, blue: 0.14).ignoresSafeArea()

                VStack(spacing: 0) {
                    Picker("檢視分頁", selection: $selectedTab) {
                        Text("📌 目前派工 (\(actions.count))").tag(HubTab.active)
                        Text("🕘 歷史紀錄 (\(history.count))").tag(HubTab.history)
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 16)
                    .padding(.top, 10)
                    .padding(.bottom, 6)

                    let currentList = selectedTab == .active ? actions : history

                    if currentList.isEmpty {
                        Spacer()
                        VStack(spacing: 12) {
                            Image(systemName: selectedTab == .active ? "checkmark.seal.fill" : "clock.arrow.circlepath")
                                .font(.system(size: 40))
                                .foregroundColor(.cyan.opacity(0.8))
                            Text(selectedTab == .active ? "目前沒有進行中或待處理的派工" : "尚無已結案的歷史派工紀錄")
                                .font(.headline)
                                .foregroundColor(.white)
                            Text(
                                selectedTab == .active
                                    ? "當主大腦派工給 Herdr 工人時，這裡會即時顯示執行進度、待拍板問題與完工檢閱卡片。"
                                    : "在目前派工卡片點擊「結案歸檔」後，所有派工與驗收紀錄都會永久保存在這裡，隨時可喚醒續派。"
                            )
                            .font(.subheadline)
                            .foregroundColor(.white.opacity(0.6))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 28)
                        }
                        Spacer()
                    } else {
                        ScrollView {
                            LazyVStack(spacing: 12) {
                                ForEach(currentList) { item in
                                    actionCard(for: item, isHistoryTab: selectedTab == .history)
                                }
                            }
                            .padding(16)
                        }
                    }
                }
            }
            .navigationTitle("Herdr 派工督工中心")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: {
                        isRefreshing = true
                        Task {
                            await onRefresh()
                            await MainActor.run { isRefreshing = false }
                        }
                    }) {
                        if isRefreshing {
                            ProgressView().tint(.cyan)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .task {
                // 抽屜開啟期間每 3 秒自動同步即時進度
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                    await onRefresh()
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func formatElapsed(_ seconds: Int) -> String {
        guard seconds > 0 else { return "" }
        if seconds < 60 {
            return "\(seconds)s"
        }
        let m = seconds / 60
        let s = seconds % 60
        return "\(m)m \(s)s"
    }

    private func statusInfo(for item: PendingActionItem, isHistoryTab: Bool) -> (label: String, color: Color) {
        if isHistoryTab || item.isArchived {
            return ("已結案歸檔", .gray)
        }
        if item.isWaitingDecision {
            return ("待您決策", .orange)
        }
        if item.isCompleted {
            return ("完工待檢閱", .cyan)
        }
        if item.isRetrying {
            let elapsedStr = formatElapsed(item.elapsedSeconds)
            let suffix = elapsedStr.isEmpty ? "" : " · \(elapsedStr)"
            return ("重修 \(item.retryCount)/\(item.maxRetries)\(suffix)", .yellow)
        }
        let elapsedStr = formatElapsed(item.elapsedSeconds)
        let suffix = elapsedStr.isEmpty ? "" : " · \(elapsedStr)"
        return ("執行中\(suffix)", .green)
    }

    private func actionCard(for item: PendingActionItem, isHistoryTab: Bool) -> some View {
        let isBusy = busyIds.contains(item.id)
        let st = statusInfo(for: item, isHistoryTab: isHistoryTab)
        let showTerminal = expandedTerminals.contains(item.id)
        let showRounds = expandedRounds.contains(item.id)
        let draftBinding = Binding<String>(
            get: { customReplies[item.id] ?? "" },
            set: { customReplies[item.id] = $0 }
        )

        return VStack(alignment: .leading, spacing: 10) {
            // 1. 標題列：狀態圓點 + 專案視窗名稱 + 輪次標籤 + 狀態膠囊 + 結案/忽略按鈕
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Circle().fill(st.color).frame(width: 8, height: 8)
                    Text(item.title)
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                        .lineLimit(1)
                }

                if item.roundNo > 1 {
                    Text("第 \(item.roundNo) 輪")
                        .font(.system(size: 10.5, weight: .bold))
                        .foregroundColor(.cyan)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(Color.cyan.opacity(0.16)))
                }

                Text(st.label)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(st.color)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2.5)
                    .background(Capsule().fill(st.color.opacity(0.15)))

                Spacer(minLength: 4)

                Button(action: {
                    submit(item: item, answer: "", dismiss: true, actionMode: nil, isFromHistory: isHistoryTab)
                }) {
                    Text(isHistoryTab ? "刪除" : (item.isCompleted ? "✅ 結案歸檔" : "歸檔"))
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundColor(item.isCompleted && !isHistoryTab ? .cyan : .white.opacity(0.55))
                        .padding(.horizontal, item.isCompleted && !isHistoryTab ? 8 : 4)
                        .padding(.vertical, item.isCompleted && !isHistoryTab ? 4 : 2)
                        .background(
                            Capsule().fill(item.isCompleted && !isHistoryTab ? Color.cyan.opacity(0.16) : Color.clear)
                        )
                }
                .buttonStyle(.plain)
                .disabled(isBusy)
            }

            // 2. 前幾輪歷史紀錄摺疊區（多輪蓋樓）
            if !item.rounds.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Button(action: {
                        if showRounds {
                            expandedRounds.remove(item.id)
                        } else {
                            expandedRounds.insert(item.id)
                        }
                    }) {
                        HStack(spacing: 5) {
                            Image(systemName: showRounds ? "chevron.down" : "chevron.right")
                                .font(.system(size: 10, weight: .bold))
                            Text("📂 查看前 \(item.rounds.count) 輪派工與驗收紀錄")
                                .font(.system(size: 12, weight: .semibold))
                            Spacer()
                        }
                        .foregroundColor(.cyan.opacity(0.88))
                    }
                    .buttonStyle(.plain)

                    if showRounds {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(item.rounds, id: \.roundNo) { r in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("第 \(r.roundNo) 輪 · \(r.taskPrompt)")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundColor(.white.opacity(0.88))
                                    if !r.summary.isEmpty {
                                        Text(r.summary)
                                            .font(.system(size: 11.5))
                                            .foregroundColor(.white.opacity(0.68))
                                    }
                                }
                                .padding(8)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(
                                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                                        .fill(Color.white.opacity(0.05))
                                )
                            }
                        }
                    }
                }
            }

            // 3. 本輪交辦任務需求
            if !item.taskPrompt.isEmpty {
                HStack(alignment: .top, spacing: 6) {
                    Text("📋 交辦：")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.white.opacity(0.6))
                    Text(item.taskPrompt)
                        .font(.system(size: 12))
                        .foregroundColor(.white.opacity(0.82))
                        .lineLimit(3)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Color.white.opacity(0.05))
                )
            }

            // 4. 即時工具軌跡與最新動態預覽（執行中或有工具紀錄時）
            if item.toolCount > 0 || !item.toolCalls.isEmpty || (!item.livePreview.isEmpty && item.isWorking) {
                VStack(alignment: .leading, spacing: 5) {
                    if item.toolCount > 0 || !item.toolCalls.isEmpty {
                        let chain = item.toolCalls.suffix(5).joined(separator: " ➔ ")
                        Text("🔧 已呼叫 \(max(item.toolCount, item.toolCalls.count)) 次工具\(chain.isEmpty ? "" : "：\(chain)")")
                            .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                            .foregroundColor(.green.opacity(0.9))
                            .lineLimit(2)
                    }
                    if !item.livePreview.isEmpty && item.isWorking {
                        Text("💬 \(item.livePreview)")
                            .font(.system(size: 12))
                            .foregroundColor(.white.opacity(0.75))
                            .lineLimit(2)
                    }
                }
            }

            // 5. 主要內容（待決策問題 / 完工結構化摘要 / 執行中狀態）
            let displayBody = (item.isCompleted || isHistoryTab) && !item.summary.isEmpty ? item.summary : item.question
            if !displayBody.isEmpty {
                Text(displayBody)
                    .font(.system(size: 13.5))
                    .foregroundColor(.white.opacity(0.92))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            // 6. 可展開的即時終端機畫面預覽
            if !item.terminalTail.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Button(action: {
                        if showTerminal {
                            expandedTerminals.remove(item.id)
                        } else {
                            expandedTerminals.insert(item.id)
                        }
                    }) {
                        HStack(spacing: 4) {
                            Image(systemName: "terminal.fill")
                                .font(.system(size: 11))
                            Text(showTerminal ? "收合終端機實況" : "展開終端機實況")
                                .font(.system(size: 11.5, weight: .semibold))
                        }
                        .foregroundColor(.white.opacity(0.65))
                    }
                    .buttonStyle(.plain)

                    if showTerminal {
                        Text(item.terminalTail)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(Color(red: 0.65, green: 0.95, blue: 0.72))
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(
                                RoundedRectangle(cornerRadius: 8, style: .continuous)
                                    .fill(Color.black.opacity(0.6))
                            )
                    }
                }
            }

            // 7. 待決策選項按鈕（僅在 waiting_decision 時出現）
            if !item.options.isEmpty && item.isWaitingDecision && !isHistoryTab {
                VStack(spacing: 6) {
                    ForEach(item.options, id: \.self) { opt in
                        Button(action: {
                            submit(item: item, answer: opt, dismiss: false, actionMode: nil, isFromHistory: false)
                        }) {
                            HStack {
                                Text(opt)
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundColor(.white)
                                    .multilineTextAlignment(.leading)
                                Spacer()
                                Image(systemName: "paperplane.fill")
                                    .font(.system(size: 11))
                                    .foregroundColor(.cyan)
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 9)
                            .background(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .fill(Color.cyan.opacity(0.14))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                                            .stroke(Color.cyan.opacity(0.35), lineWidth: 1)
                                    )
                            )
                        }
                        .buttonStyle(.plain)
                        .disabled(isBusy)
                    }
                }
            }

            // 8. 底部互動輸入框（待決策補充 / 完工原視窗續派修改 / 歷史紀錄喚醒續派）
            if !item.isWorking || isHistoryTab {
                let placeholderText = isHistoryTab
                    ? "在此視窗繼續交辦新任務..."
                    : (item.isCompleted ? "不滿意或要加功能？輸入指令直接原視窗續派..." : "或輸入補充決策指令...")
                let buttonTitle = isBusy
                    ? "送出中"
                    : (isHistoryTab ? "喚醒續派" : (item.isCompleted ? "續派" : "送出"))
                let actionMode: String? = (item.isCompleted || isHistoryTab) ? "followup" : nil

                HStack(spacing: 8) {
                    TextField(placeholderText, text: draftBinding)
                        .font(.system(size: 13))
                        .foregroundColor(.white)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color.white.opacity(0.07))
                        )

                    Button(action: {
                        let ans = (customReplies[item.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !ans.isEmpty else { return }
                        submit(item: item, answer: ans, dismiss: false, actionMode: actionMode, isFromHistory: isHistoryTab)
                    }) {
                        Text(buttonTitle)
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .background(Capsule().fill(item.isCompleted || isHistoryTab ? Color.cyan.opacity(0.85) : Color.blue))
                    }
                    .buttonStyle(.plain)
                    .disabled(isBusy || (customReplies[item.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color(red: 0.11, green: 0.14, blue: 0.22))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(st.color.opacity(0.35), lineWidth: 1)
                )
        )
    }

    private func submit(
        item: PendingActionItem,
        answer: String,
        dismiss: Bool,
        actionMode: String?,
        isFromHistory: Bool
    ) {
        busyIds.insert(item.id)
        Task {
            let ok = await onResolve(item, answer, dismiss, actionMode)
            await MainActor.run {
                busyIds.remove(item.id)
                customReplies.removeValue(forKey: item.id)
                if ok && isFromHistory && !dismiss {
                    selectedTab = .active
                }
            }
        }
    }
}
