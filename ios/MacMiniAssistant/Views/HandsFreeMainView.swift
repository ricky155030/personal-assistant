import SwiftUI
import PhotosUI
import UIKit
import AVKit

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

    @State private var rotation: Double = 0
    @State private var scale: CGFloat = 1.0

    var body: some View {
        GeminiSparkleShape()
            .fill(
                AngularGradient(
                    gradient: Gradient(colors: [
                        Color(red: 0.26, green: 0.52, blue: 0.96), // Google Blue
                        Color(red: 0.92, green: 0.26, blue: 0.21), // Google Red
                        Color(red: 0.98, green: 0.74, blue: 0.02), // Google Yellow
                        Color(red: 0.20, green: 0.66, blue: 0.33), // Google Green
                        Color(red: 0.26, green: 0.52, blue: 0.96)
                    ]),
                    center: .center
                )
            )
            .frame(width: size, height: size)
            .scaleEffect(isAnimating ? scale : 1.0)
            .rotationEffect(.degrees(isAnimating ? rotation : 0))
            .onAppear {
                if isAnimating {
                    withAnimation(.easeInOut(duration: 1.4).repeatForever(autoreverses: true)) {
                        scale = 1.15
                    }
                }
            }
    }
}

// MARK: - 主畫面：Gemini 風格直接對話 + Live 即時語音對談
struct HandsFreeMainView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator

    @State private var showQuotaSheet: Bool = false
    @State private var showSettings: Bool = false
    @State private var showModelPopover: Bool = false
    @State private var isAtBottom: Bool = true
    @State private var hasUnseenUpdates: Bool = false
    @State private var isDraggingBottom: Bool = false
    @State private var startedDragAtBottom: Bool = false
    @State private var bottomPullOffset: CGFloat = 0
    @State private var bottomPullHapticTriggered: Bool = false
    @State private var isRefreshingHistory: Bool = false
    @State private var showVisionCamera: Bool = false
    @State private var showCameraPicker: Bool = false
    @State private var showPhotoLibraryPicker: Bool = false
    @State private var selectedPhotoItems: [PhotosPickerItem] = []
    @AppStorage("chatBaseFontSize") private var chatBaseFontSize: Double = 15.0
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
        .sheet(isPresented: $showSettings) {
            AssistantSettingsView(coordinator: coordinator)
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

    // MARK: - 錯誤卡片（附重試按鈕）
    private func errorCard(_ err: String) -> some View {
        let lastUserText = coordinator.messages.last(where: { $0.role == .user })?.content
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        return HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 14))
                .foregroundColor(.red.opacity(0.9))
                .padding(.top, 1)

            Text(err)
                .font(.system(size: 13))
                .foregroundColor(.white.opacity(0.88))
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)

            if !lastUserText.isEmpty && !coordinator.isProcessingOrSpeaking {
                Button(action: {
                    haptic.impactOccurred()
                    coordinator.errorMessage = nil
                    coordinator.sendQuickReply(lastUserText)
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .bold))
                        Text("重試")
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, 12)
                    .frame(height: 30)
                    .background(Capsule().fill(Color.red.opacity(0.55)))
                }
                .buttonStyle(.plain)
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

    // MARK: - 1. 背景漸層 (Live 模式時底部泛起動態藍光)
    private var geminiBackground: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()
                LinearGradient(
                    gradient: Gradient(colors: [
                        Color.clear,
                        Color(red: 0.03, green: 0.06, blue: 0.18).opacity(coordinator.isLiveMode ? 0.95 : 0.75),
                        Color(red: 0.06, green: 0.12, blue: 0.32).opacity(coordinator.isLiveMode ? 0.85 : 0.45),
                        Color(red: 0.02, green: 0.04, blue: 0.12).opacity(0.9)
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
        HStack(spacing: 12) {
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
                        HStack(spacing: 3) {
                            Text(coordinator.selectedModelFamily.displayName)
                                .font(.system(size: 15, weight: .bold))
                                .foregroundColor(.white)
                                .lineLimit(1)

                            Image(systemName: "chevron.down")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(Color(white: 0.6))
                        }

                        Text("\(thinkingLevelSubLabel)思考 · \(coordinator.responseLength.shortTag)")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundColor(Color(white: 0.58))
                            .lineLimit(1)
                    }
                }
                .padding(.leading, 12)
                .padding(.trailing, 14)
                .frame(height: 42)
                .background(
                    Capsule()
                        .fill(Color(white: 0.11))
                        .overlay(Capsule().stroke(Color.white.opacity(0.08), lineWidth: 1))
                )
                .fixedSize(horizontal: true, vertical: false)
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

            // 設定按鈕
            Button(action: {
                haptic.impactOccurred()
                showSettings = true
            }) {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.white.opacity(0.88))
                    .frame(width: 42, height: 42)
                    .background(
                        Circle()
                            .fill(Color(white: 0.11))
                            .overlay(Circle().stroke(Color.white.opacity(0.08), lineWidth: 1))
                    )
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

    // MARK: - 合併式用量膠囊（Context ◔ + 配額 ◑）
    private var usageCapsuleButton: some View {
        let ctx = latestContextUsage
        let quota = dynamicQuotaInfo(for: coordinator.selectedModelFamily)
        let quotaText = coordinator.quotaSnapshot != nil ? String(format: "%.0f%%", quota.pct) : "--%"

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
                }

                Rectangle()
                    .fill(Color.white.opacity(0.14))
                    .frame(width: 1, height: 16)

                // 當前模型剩餘配額（環形進度）
                HStack(spacing: 5) {
                    ZStack {
                        Circle()
                            .stroke(Color.white.opacity(0.12), lineWidth: 2.2)
                        Circle()
                            .trim(from: 0, to: CGFloat(max(0.03, min(1.0, quota.pct / 100.0))))
                            .stroke(quota.tint, style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                    }
                    .frame(width: 15, height: 15)

                    Text(quotaText)
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundColor(.white)
                        .monospacedDigit()
                }
            }
            .padding(.horizontal, 12)
            .frame(height: 42)
            .background(
                Capsule()
                    .fill(Color(white: 0.11))
                    .overlay(Capsule().stroke(Color.white.opacity(0.08), lineWidth: 1))
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("用量：Context \(ctx.pctText)，配額剩餘 \(quotaText)")
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
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundColor(Color(white: 0.55))
            .padding(.horizontal, 12)
            .padding(.top, 8)
            .padding(.bottom, 3)
    }

    private func popoverOptionRow(
        title: String,
        icon: String? = nil,
        isSelected: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: {
            haptic.impactOccurred()
            action()
        }) {
            HStack(spacing: 8) {
                if let icon = icon {
                    Image(systemName: icon)
                        .font(.system(size: 12))
                        .foregroundColor(isSelected ? .cyan : Color(white: 0.72))
                        .frame(width: 16)
                }
                Text(title)
                    .font(.system(size: 12.5, weight: isSelected ? .semibold : .regular))
                    .foregroundColor(.white)
                    .lineLimit(1)
                Spacer(minLength: 8)
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(.cyan)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var modelMenuPopoverContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 1) {
                // 連線狀態列
                HStack(spacing: 6) {
                    Circle()
                        .fill(connectionDotColor)
                        .frame(width: 6, height: 6)
                    Text(coordinator.serverStatusText)
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundColor(Color(white: 0.7))
                        .lineLimit(1)
                }
                .padding(.horizontal, 12)
                .padding(.top, 4)
                .padding(.bottom, 2)

                Divider().opacity(0.25).padding(.vertical, 3)

                popoverSectionHeader("AI 模型選擇")
                ForEach(ModelFamily.allCases) { fam in
                    popoverOptionRow(title: fam.fullName, icon: fam.icon, isSelected: coordinator.selectedModelFamily == fam) {
                        coordinator.selectedModelFamily = fam
                        showModelPopover = false
                    }
                }

                Divider().opacity(0.25).padding(.vertical, 3)
                popoverSectionHeader("思考深度")
                ForEach(ThinkingLevel.allCases) { level in
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

                Divider().opacity(0.25).padding(.vertical, 3)
                popoverSectionHeader("音訊輸出（目前：\(coordinator.currentAudioRouteName)）")
                popoverOptionRow(title: "手機底部揚聲器 (擴音)", icon: "speaker.wave.3.fill", isSelected: coordinator.isForcedSpeaker) {
                    coordinator.setAudioRoute(forceSpeaker: true)
                    showModelPopover = false
                }
                popoverOptionRow(title: "車機 / 藍牙設備優先", icon: "car.fill", isSelected: coordinator.isBluetoothOrCarConnected) {
                    coordinator.setAudioRoute(forceSpeaker: false)
                    showModelPopover = false
                }
                popoverOptionRow(title: "手機頂部聽筒 (私密)", icon: "ear", isSelected: false) {
                    coordinator.setAudioRouteToReceiver()
                    showModelPopover = false
                }
            }
            .padding(.vertical, 6)
        }
        .frame(width: 248)
        .frame(maxHeight: 460)
        .background(Color(white: 0.12))
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
        }
    }

    // MARK: - 3B. 主畫面直接顯示即時對話列表
    private var conversationScrollSection: some View {
        ScrollViewReader { proxy in
            ZStack(alignment: .bottomTrailing) {
                ScrollView {
                    LazyVStack(spacing: 20) {
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
                                        coordinator.toggleSpeakMessage(msg)
                                    } label: {
                                        Label(
                                            isSpeakingMessage(msg) ? "停止朗讀" : "朗讀此則訊息",
                                            systemImage: isSpeakingMessage(msg) ? "stop.circle" : "speaker.wave.2"
                                        )
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
                                hasUnseenUpdates = false
                            }
                            .onDisappear { isAtBottom = false }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 12)
                    .padding(.bottom, 8)
                }
                .scrollDismissesKeyboard(.interactively)
                .simultaneousGesture(
                    DragGesture(minimumDistance: 12)
                        .onChanged { value in
                            if !isDraggingBottom {
                                isDraggingBottom = true
                                startedDragAtBottom = isAtBottom
                                bottomPullHapticTriggered = false
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

                if !isAtBottom && coordinator.messages.count > 2 {
                    Button(action: {
                        haptic.impactOccurred()
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
                                        .fill(Color(white: 0.16).opacity(0.95))
                                        .overlay(
                                            Circle()
                                                .stroke(hasUnseenUpdates ? Color.cyan.opacity(0.6) : Color.white.opacity(0.18), lineWidth: 1)
                                        )
                                        .shadow(color: .black.opacity(0.35), radius: 5, x: 0, y: 2)
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
                    .padding(.bottom, 12)
                    .transition(.scale.combined(with: .opacity))
                }
            }
            .onAppear {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) {
                    scrollToBottom(proxy: proxy, animated: false)
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                    if isAtBottom {
                        scrollToBottom(proxy: proxy, animated: false)
                    }
                }
            }
            .onChange(of: coordinator.messages.count) { _, _ in
                // 使用者剛送出新訊息，或原本就在底部時才自動捲動；否則標記有新內容
                if isAtBottom || coordinator.messages.last?.role == .user {
                    hasUnseenUpdates = false
                    scrollToBottom(proxy: proxy, animated: true)
                } else {
                    hasUnseenUpdates = true
                }
            }
            .onChange(of: coordinator.currentStreamingText) { _, _ in
                if isAtBottom {
                    scrollToBottom(proxy: proxy, animated: false)
                } else if !coordinator.currentStreamingText.isEmpty {
                    hasUnseenUpdates = true
                }
            }
            .onChange(of: coordinator.liveTranscript) { _, _ in
                if isAtBottom {
                    scrollToBottom(proxy: proxy, animated: false)
                }
            }
        }
    }

    private func scrollToBottom(proxy: ScrollViewProxy, animated: Bool) {
        if animated {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo("bottom_anchor", anchor: .bottom)
            }
        } else {
            proxy.scrollTo("bottom_anchor", anchor: .bottom)
        }
    }

    private func isSpeakingMessage(_ msg: ChatMessage) -> Bool {
        guard msg.role == .assistant, msg.id == coordinator.messages.last?.id else { return false }
        if case .speaking = coordinator.state {
            return true
        }
        return false
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

    // MARK: - 4. 底部 Gemini 膠囊輸入列與 Live 狀態控制
    private var bottomInputSection: some View {
        VStack(spacing: 10) {
            // 若處於 ((|)) Live 連續語音對談模式，顯示浮動聲波控制膠囊
            if coordinator.isLiveMode {
                liveVoiceFloatingBar
                    .transition(.move(edge: .bottom).combined(with: .opacity))
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

            // Gemini 標誌性深色膠囊輸入列
            HStack(alignment: .bottom, spacing: 8) {
                // 左側 [ + ] 隨身眼睛拍照 / 相簿傳圖按鈕
                Menu {
                    Button(action: {
                        isInputFocused = false
                        showVisionCamera = true
                    }) {
                        Label("隨身眼睛拍照", systemImage: "camera.fill")
                    }

                    Button(action: {
                        isInputFocused = false
                        showPhotoLibraryPicker = true
                    }) {
                        Label("從相簿選取圖片（可多選）", systemImage: "photo.on.rectangle")
                    }
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 21, weight: .medium))
                        .foregroundColor(.white.opacity(0.88))
                        .frame(width: 36, height: 44)
                }
                .accessibilityLabel("附加圖片或拍照")

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
                .padding(.vertical, 8)

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
                            .frame(width: 38, height: 44)
                    }
                    .accessibilityLabel("隨身眼睛拍照")

                    if !coordinator.textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || !coordinator.pendingImagesData.isEmpty
                        || coordinator.pendingImageData != nil {
                        // 有文字或已附加照片時：顯示藍色 ↑ 送出按鈕
                        Button(action: {
                            haptic.impactOccurred()
                            isInputFocused = false
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
                    } else if coordinator.isProcessingOrSpeaking {
                        // 思考/工具執行/朗讀中：顯示紅色 ⏹️ 立即中止按鈕 (ACP Real-time Cancel)
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
            .background(
                RoundedRectangle(cornerRadius: 30, style: .continuous)
                    .fill(Color(red: 0.11, green: 0.12, blue: 0.15))
                    .overlay(
                        RoundedRectangle(cornerRadius: 30, style: .continuous)
                            .stroke(
                                coordinator.isSingleDictationMode
                                    ? Color.red.opacity(0.45)
                                    : (coordinator.isLiveMode ? Color.blue.opacity(0.45) : Color.white.opacity(0.07)),
                                lineWidth: 1
                            )
                    )
            )
        }
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

            // 思考或說話中：提供單鍵 ⏹️ 中止本輪回應按鈕（不退出 Live 模式）
            if coordinator.isProcessingOrSpeaking {
                Button(action: {
                    haptic.impactOccurred()
                    coordinator.cancelCurrentTurn()
                }) {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(.white)
                        .frame(width: 32, height: 32)
                        .background(Color.orange.opacity(0.85))
                        .clipShape(Circle())
                }
            }

            // 暫停/恢復收音按鈕
            Button(action: {
                haptic.impactOccurred()
                coordinator.togglePause()
            }) {
                Image(systemName: coordinator.state == .paused ? "mic.slash.fill" : "pause.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(coordinator.state == .paused ? .orange : .white.opacity(0.85))
                    .frame(width: 32, height: 32)
                    .background(Color.white.opacity(0.1))
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
                    .font(.system(size: 14, weight: .bold))
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
                .fill(Color(red: 0.08, green: 0.13, blue: 0.26).opacity(0.95))
                .overlay(
                    Capsule()
                        .stroke(liveStateColor.opacity(0.45), lineWidth: 1)
                )
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
            return tool ?? "Gemini 思考處理中..."
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
private struct PreviewPhotoItem: Identifiable {
    let id = UUID()
    let data: Data
}

private struct FullScreenPhotoViewer: View {
    let imageData: Data
    @Environment(\.dismiss) private var dismiss
    @State private var scale: CGFloat = 1.0
    @State private var dragOffset: CGFloat = 0

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black
                .opacity(max(0.15, 1.0 - Double(dragOffset / 380.0)))
                .ignoresSafeArea()

            if let uiImg = UIImage(data: imageData) {
                Image(uiImage: uiImg)
                    .resizable()
                    .scaledToFit()
                    .scaleEffect(scale * max(0.85, 1.0 - dragOffset / 1200.0))
                    .offset(y: dragOffset)
                    .gesture(
                        MagnificationGesture()
                            .onChanged { val in
                                scale = max(1.0, min(val, 4.0))
                            }
                            .onEnded { _ in
                                if scale < 1.1 {
                                    withAnimation(.spring()) { scale = 1.0 }
                                }
                            }
                    )
                    .simultaneousGesture(
                        DragGesture(minimumDistance: 18)
                            .onChanged { value in
                                guard scale <= 1.05 else { return }
                                if value.translation.height > 0 {
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
                    .onTapGesture(count: 2) {
                        withAnimation(.spring()) {
                            scale = scale > 1.2 ? 1.0 : 2.2
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            Button(action: { dismiss() }) {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 30))
                    .foregroundColor(.white.opacity(0.85))
                    .padding(20)
            }
            .opacity(dragOffset > 20 ? 0.4 : 1.0)
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
    @State private var previewPhoto: PreviewPhotoItem? = nil
    @AppStorage("chatBaseFontSize") private var chatBaseFontSize: Double = 15.0

    private var isUser: Bool {
        message.role == .user
    }

    private var hasThoughts: Bool {
        !message.thoughtProcess.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
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
                                                previewPhoto = PreviewPhotoItem(data: multi[idx])
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
                                previewPhoto = PreviewPhotoItem(data: imgData)
                            }
                    }

                    Text(message.content)
                        .font(.system(size: chatBaseFontSize))
                        .foregroundColor(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .background(
                            RoundedRectangle(cornerRadius: 20, style: .continuous)
                                .fill(Color(red: 0.16, green: 0.18, blue: 0.24))
                        )
                        .textSelection(.enabled)
                }
            }
            .fullScreenCover(item: $previewPhoto) { item in
                FullScreenPhotoViewer(imageData: item.data)
            }
        } else {
            // 助理回覆：左側 Gemini 四色星芒標頭 + 乾淨無框全寬文字與工具膠囊
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    GeminiSparkleIcon(
                        size: 22,
                        isAnimating: message.status == .streaming || isCurrentlySpeaking
                    )

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
                    let latestTool = message.toolHistory.last ?? ""
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
                                    .foregroundColor(Color(white: 0.80))
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
                            .background(Color.white.opacity(0.07))
                            .overlay(
                                Capsule().stroke(Color.white.opacity(0.14), lineWidth: 0.8)
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

                // 助理回覆正文（完整 Markdown 渲染 + 動態字體）
                if !message.content.isEmpty {
                    MarkdownMessageView(content: message.content)
                        .textSelection(.enabled)
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

                // HTML 報告連結（若有）
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

                        // 6. 子代理 / 背景常駐任務狀態提示
                        backgroundTasksCard

                        // 7. ACP 統一大腦 Context 記憶容量
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

    private var backgroundTasksCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("子代理 / 背景常駐任務", systemImage: "gearshape.2.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                Spacer()
                HStack(spacing: 5) {
                    Circle()
                        .fill(snap.activeTaskCount > 0 ? Color.green : Color.gray)
                        .frame(width: 8, height: 8)
                    Text("\(snap.activeTaskCount) 個執行中")
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundColor(snap.activeTaskCount > 0 ? .green : .secondary)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(snap.activeTaskCount > 0 ? Color.green.opacity(0.15) : Color.white.opacity(0.05))
                .cornerRadius(6)
            }

            if !snap.activeTaskNames.isEmpty {
                Divider()
                    .background(Color.white.opacity(0.08))

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
                Text("目前無背景監控或長任務運行中")
                    .font(.caption)
                    .foregroundColor(.secondary)
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
