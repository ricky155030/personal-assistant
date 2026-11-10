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

    @State private var showHistoryDrawer: Bool = false
    @State private var showQuotaSheet: Bool = false
    @State private var showSettings: Bool = false
    @State private var showVisionCamera: Bool = false
    @State private var showCameraPicker: Bool = false
    @State private var showPhotoLibraryPicker: Bool = false
    @State private var selectedPhotoItem: PhotosPickerItem? = nil
    @FocusState private var isInputFocused: Bool

    private let haptic = UIImpactFeedbackGenerator(style: .medium)

    var body: some View {
        ZStack {
            // 1. Gemini 純黑至深邃午夜藍漸層背景
            geminiBackground
                .onTapGesture {
                    isInputFocused = false
                }

            VStack(spacing: 0) {
                // 2. 頂部導覽列 (連線診斷 / Flash 中等 ⌄ / 開新對話 / 頭像設定)
                topHeaderBar
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 8)

                // 除錯診斷 HUD (點左上按鈕可展開)
                if coordinator.showDebugHUD {
                    debugHUDView
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                // 3. 中央主區域：無對話時顯示「宏瑋，該你囉！」，有對話時直接顯示即時對話流
                if coordinator.messages.isEmpty && !(coordinator.isLiveMode && !coordinator.liveTranscript.isEmpty) {
                    emptyGreetingSection
                } else {
                    conversationScrollSection
                }

                // 4. 底部 Gemini 輸入膠囊與 Live 語音控制區
                bottomInputSection
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 12)
            }
        }
        .sheet(isPresented: $showHistoryDrawer) {
            ConversationDrawerView(coordinator: coordinator)
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
                    coordinator.pendingImageData = jpeg
                }
            }
            .ignoresSafeArea()
        }
        .photosPicker(isPresented: $showPhotoLibraryPicker, selection: $selectedPhotoItem, matching: .images)
        .onChange(of: selectedPhotoItem) { _, newItem in
            guard let item = newItem else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self),
                   let uiImage = UIImage(data: data),
                   let jpeg = Self.compressImage(uiImage) {
                    await MainActor.run {
                        coordinator.pendingImageData = jpeg
                    }
                }
                await MainActor.run {
                    selectedPhotoItem = nil
                }
            }
        }
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
        HStack(spacing: 14) {
            // 左上抽屜按鈕（點擊打開近期語音/文字對話歷史紀錄）
            Button(action: {
                haptic.impactOccurred()
                isInputFocused = false
                showHistoryDrawer = true
            }) {
                ZStack(alignment: .topTrailing) {
                    Circle()
                        .fill(Color(white: 0.13))
                        .frame(width: 44, height: 44)
                        .overlay(
                            Circle()
                                .stroke(Color.white.opacity(0.08), lineWidth: 1)
                        )
                        .overlay(
                            VStack(spacing: 5) {
                                Capsule()
                                    .fill(Color.white.opacity(0.85))
                                    .frame(width: 16, height: 2)
                                Capsule()
                                    .fill(Color.white.opacity(0.85))
                                    .frame(width: 16, height: 2)
                            }
                        )

                    Circle()
                        .fill(coordinator.isServerReachable ? Color.green : Color.red)
                        .frame(width: 9, height: 9)
                        .overlay(Circle().stroke(Color.black, lineWidth: 1.5))
                        .offset(x: -2, y: 2)
                }
            }

            // 模型選擇、思考層級、語音辨識與語音設定選單
            Menu {
                Section("AI 模型選擇 (Model Family)") {
                    ForEach(ModelFamily.allCases) { fam in
                        Button(action: {
                            haptic.impactOccurred()
                            coordinator.selectedModelFamily = fam
                        }) {
                            HStack {
                                Label(fam.fullName, systemImage: fam.icon)
                                if coordinator.selectedModelFamily == fam {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }

                Section("思考深度 (Thinking Level)") {
                    ForEach(ThinkingLevel.allCases) { level in
                        Button(action: {
                            coordinator.thinkingLevel = level
                        }) {
                            HStack {
                                Text("\(level.headerTitle) — \(level.menuSubtitle)")
                                if coordinator.thinkingLevel == level {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }

                Section("回覆長度 (Response Length)") {
                    ForEach(ResponseLengthOption.allCases) { opt in
                        Button(action: {
                            haptic.impactOccurred()
                            coordinator.responseLength = opt
                        }) {
                            HStack {
                                Label(opt.title, systemImage: opt.icon)
                                if coordinator.responseLength == opt {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }

                Section("語音辨識模式 (STT Mode)") {
                    ForEach(STTRecognitionMode.allCases) { mode in
                        Button(action: {
                            haptic.impactOccurred()
                            coordinator.sttMode = mode
                        }) {
                            HStack {
                                Label(mode.title, systemImage: mode.icon)
                                if coordinator.sttMode == mode {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }

                Section("助理朗讀語速 (TTS Speed)") {
                    ForEach(TTSSpeedOption.allCases) { spd in
                        Button(action: {
                            coordinator.ttsSpeed = spd
                        }) {
                            HStack {
                                Text(spd.title)
                                if coordinator.ttsSpeed == spd {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text(coordinator.selectedModelFamily.displayName)
                            .font(.system(size: 17, weight: .bold))
                            .foregroundColor(.white)

                        Image(systemName: "chevron.down")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(Color(white: 0.65))
                    }

                    HStack(spacing: 4) {
                        Text(coordinator.thinkingLevel.headerTitle.replacingOccurrences(of: "Flash ", with: "").replacingOccurrences(of: "Pro ", with: ""))
                            .font(.system(size: 11.5, weight: .medium))
                            .foregroundColor(Color(white: 0.65))

                        Text("·")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(Color(white: 0.45))

                        Text(coordinator.ttsSpeed.title.components(separatedBy: " ").first ?? "1.0x")
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                            .foregroundColor(.cyan)

                        Text("·")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(Color(white: 0.45))

                        Text(coordinator.responseLength.shortTag)
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                            .foregroundColor(.yellow.opacity(0.9))
                    }
                }
                .fixedSize(horizontal: true, vertical: false)
            }

            Spacer()

            // 音訊輸出切換器 (支援車載藍牙、揚聲器擴音、聽筒切換)
            audioRoutePickerButton

            // 右上 1：AI 配額膠囊（動態連動選取的模型 Family：Flash / Argon / Claude）
            Button(action: {
                haptic.impactOccurred()
                coordinator.refreshQuotaStatus(force: false)
                showQuotaSheet = true
            }) {
                let info = dynamicQuotaInfo(for: coordinator.selectedModelFamily)

                HStack(spacing: 7) {
                    ZStack {
                        Circle()
                            .stroke(Color.white.opacity(0.12), lineWidth: 2.5)
                            .frame(width: 22, height: 22)
                        Circle()
                            .trim(from: 0, to: CGFloat(max(0.03, min(1.0, info.pct / 100.0))))
                            .stroke(info.tint, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                            .frame(width: 22, height: 22)
                            .rotationEffect(.degrees(-90))
                        Image(systemName: info.icon)
                            .font(.system(size: 9, weight: .bold))
                            .foregroundColor(info.tint)
                    }

                    VStack(alignment: .leading, spacing: 1) {
                        Text(coordinator.quotaSnapshot != nil ? String(format: "%.1f%%", info.pct) : "--%")
                            .font(.system(size: 12.5, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                        Text("⏳ \(info.resetIn)")
                            .font(.system(size: 10, weight: .medium, design: .rounded))
                            .foregroundColor(Color(white: 0.68))
                            .lineLimit(1)
                    }
                }
                .padding(.horizontal, 11)
                .frame(height: 42)
                .background(
                    Capsule()
                        .fill(Color(white: 0.13))
                        .overlay(
                            Capsule()
                                .stroke(info.tint.opacity(0.28), lineWidth: 1)
                        )
                )
            }

            // 右上 2：個人頭像 / 設定按鈕
            Button(action: {
                haptic.impactOccurred()
                showSettings = true
            }) {
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [Color(red: 0.22, green: 0.35, blue: 0.65), Color(red: 0.12, green: 0.18, blue: 0.35)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 42, height: 42)
                        .overlay(
                            Circle()
                                .stroke(Color(white: 0.22), lineWidth: 2)
                        )

                    Text("宏")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.white)
                }
            }
        }
    }

    // MARK: - 音訊輸出路由切換按鈕 (車機藍牙 / 揚聲器 / 聽筒一鍵切換)
    private var audioRoutePickerButton: some View {
        Menu {
            Section("當前音訊：\(coordinator.currentAudioRouteName)") {
                Button(action: {
                    haptic.impactOccurred()
                    coordinator.setAudioRoute(forceSpeaker: true)
                }) {
                    Label("手機底部揚聲器 (強制擴音)", systemImage: "speaker.wave.3.fill")
                }

                Button(action: {
                    haptic.impactOccurred()
                    coordinator.setAudioRoute(forceSpeaker: false)
                }) {
                    Label("車機 / 藍牙設備優先", systemImage: "car.fill")
                }

                Button(action: {
                    haptic.impactOccurred()
                    coordinator.setAudioRouteToReceiver()
                }) {
                    Label("手機頂部聽筒 (話筒私密)", systemImage: "ear")
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: coordinator.currentAudioRouteIcon)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(coordinator.isBluetoothOrCarConnected ? .cyan : (coordinator.isForcedSpeaker ? .green : .white))
            }
            .frame(width: 38, height: 38)
            .background(
                Circle()
                    .fill(Color(white: 0.14))
                    .overlay(
                        Circle()
                            .stroke(coordinator.isBluetoothOrCarConnected ? Color.cyan.opacity(0.4) : (coordinator.isForcedSpeaker ? Color.green.opacity(0.4) : Color.white.opacity(0.12)), lineWidth: 1)
                    )
            )
        }
    }

    private func dynamicQuotaInfo(for family: ModelFamily) -> (pct: Double, resetIn: String, tint: Color, icon: String) {
        guard let snap = coordinator.quotaSnapshot else {
            return (100.0, "--", .green, "bolt.fill")
        }
        switch family {
        case .flash:
            let pct = snap.weeklyRemainingPct
            let tint: Color = pct >= 50 ? .green : (pct >= 20 ? .orange : .red)
            return (pct, snap.weeklyRefreshesIn, tint, "bolt.fill")
        case .argon:
            let pct = snap.argonRemainingPct
            let tint: Color = pct >= 50 ? Color(red: 0.45, green: 0.65, blue: 1.0) : (pct >= 20 ? .orange : .red)
            return (pct, snap.argonRefreshesIn, tint, "atom")
        case .claude:
            let pct = snap.thirdPartyRemainingPct
            let tint: Color = pct >= 50 ? .purple : (pct >= 20 ? .orange : .red)
            return (pct, snap.thirdPartyRefreshesIn, tint, "sparkles")
        }
    }

    private var thinkingLevelSubLabel: String {
        switch coordinator.thinkingLevel {
        case .low: return "快速"
        case .med: return "中等"
        case .high: return "深度"
        }
    }

    // MARK: - 3A. 待命空畫面：「✨ 宏瑋，該你囉！」
    private var emptyGreetingSection: some View {
        VStack(spacing: 22) {
            Spacer()

            GeminiSparkleIcon(
                size: 46,
                isAnimating: coordinator.voiceInputMode != .none
            )

            Text("宏瑋，該你囉！")
                .font(.system(size: 31, weight: .medium))
                .foregroundColor(.white.opacity(0.93))
                .tracking(0.5)

            if coordinator.voiceInputMode != .none {
                HStack(spacing: 8) {
                    Circle()
                        .fill(Color.green)
                        .frame(width: 8, height: 8)
                    Text(coordinator.isLiveMode ? "Live 即時語音對談中，請直接開口..." : "正在語音轉文字，講完將留在下方對話框...")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundColor(.white.opacity(0.75))
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Color.white.opacity(0.08))
                .clipShape(Capsule())
                .transition(.opacity.combined(with: .scale))
            }

            if let err = coordinator.errorMessage {
                Text(err)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(.red.opacity(0.9))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            Spacer()
        }
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
        .onTapGesture {
            isInputFocused = false
        }
    }

    // MARK: - 3B. 主畫面直接顯示即時對話列表
    private var conversationScrollSection: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 22) {
                    ForEach(coordinator.messages) { msg in
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
                    }

                    // 當使用者在 Live 模式開口講話時，即時在右側顯示邊講邊跳字的預覽氣泡！
                    if coordinator.isLiveMode && !coordinator.liveTranscript.isEmpty {
                        liveTranscriptPreviewBubble
                            .id("live_transcript_preview")
                    }

                    if let err = coordinator.errorMessage {
                        Text("⚠️ \(err)")
                            .font(.footnote)
                            .foregroundColor(.red)
                            .padding(.horizontal, 16)
                    }

                    Color.clear
                        .frame(height: 8)
                        .id("bottom_anchor")
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 8)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: coordinator.messages.count) { _, _ in
                scrollToBottom(proxy: proxy, animated: true)
            }
            .onChange(of: coordinator.currentStreamingText) { _, _ in
                scrollToBottom(proxy: proxy, animated: false)
            }
            .onChange(of: coordinator.liveTranscript) { _, _ in
                scrollToBottom(proxy: proxy, animated: false)
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

            // 若有附加圖片，於輸入列上方顯示縮圖預覽卡
            if let imgData = coordinator.pendingImageData, let uiImg = UIImage(data: imgData) {
                HStack(spacing: 10) {
                    Image(uiImage: uiImg)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 48, height: 48)
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(Color.white.opacity(0.2), lineWidth: 1)
                        )

                    VStack(alignment: .leading, spacing: 2) {
                        Text("已附加照片")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.white)
                        Text("可直接開口說話或打字提問，一起傳給助理")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }

                    Spacer()

                    Button(action: {
                        haptic.impactOccurred()
                        coordinator.pendingImageData = nil
                    }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 20))
                            .foregroundColor(.secondary)
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
                        Label("從相簿選取圖片", systemImage: "photo.on.rectangle")
                    }
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 21, weight: .medium))
                        .foregroundColor(.white.opacity(0.88))
                        .frame(width: 36, height: 44)
                }

                // 文字輸入框：「問問 Gemini」（按左側 🎙️ 時，STT 文字直接即時填入此輸入框，不自動送出）
                HStack(spacing: 8) {
                    if coordinator.isSingleDictationMode {
                        Circle()
                            .fill(Color.red)
                            .frame(width: 8, height: 8)
                    }

                    TextField(
                        "",
                        text: $coordinator.textInput,
                        prompt: Text(coordinator.isSingleDictationMode ? "正在聆聽，講完將留在對話框..." : "問問 Gemini")
                            .foregroundColor(coordinator.isSingleDictationMode ? Color.red.opacity(0.85) : Color(white: 0.55)),
                        axis: .vertical
                    )
                    .font(.system(size: 17))
                    .lineLimit(1...5)
                    .foregroundColor(.white)
                    .focused($isInputFocused)
                }
                .padding(.vertical, 8)

                // 右側按鈕區：📷 隨身眼睛 + 🎙️ 語音轉文字填入框 + (有字或有圖時顯示 ↑ 送出 / 無字時顯示 ((|)) Live 連續對談)
                HStack(spacing: 6) {
                    // 📷 隨身眼睛快拍按鈕（位於麥克風圓形大按鈕旁）
                    Button(action: {
                        haptic.impactOccurred()
                        isInputFocused = false
                        showVisionCamera = true
                    }) {
                        Image(systemName: "camera.fill")
                            .font(.system(size: 19, weight: .medium))
                            .foregroundColor(.white.opacity(0.88))
                            .frame(width: 36, height: 44)
                    }

                    // 按鈕 1：🎙️ 語音轉文字 (STT 直接放進對話框，不自動送出)
                    Button(action: {
                        haptic.impactOccurred()
                        withAnimation(.spring(response: 0.25, dampingFraction: 0.8)) {
                            coordinator.toggleSingleTurnVoice()
                        }
                    }) {
                        ZStack {
                            if coordinator.isSingleDictationMode {
                                Circle()
                                    .fill(Color.red.opacity(0.22))
                                    .frame(width: 40, height: 40)
                            }
                            Image(systemName: coordinator.isSingleDictationMode ? "mic.fill" : "mic")
                                .font(.system(size: 20, weight: .medium))
                                .foregroundColor(coordinator.isSingleDictationMode ? .red : .white.opacity(0.88))
                                .frame(width: 38, height: 44)
                        }
                    }

                    if !coordinator.textInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || coordinator.pendingImageData != nil {
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
                Text("🛠️ 收音與連線診斷 (\(coordinator.serverStatusText))")
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
struct GeminiMessageRow: View {
    let message: ChatMessage
    let isCurrentlySpeaking: Bool
    let onTapSpeak: () -> Void
    var onTapQuickReply: ((String) -> Void)? = nil

    @State private var isThoughtExpanded: Bool = false

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
                    }

                    Text(message.content)
                        .font(.system(size: 16.5))
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
        } else {
            // 助理回覆：左側 Gemini 四色星芒標頭 + 乾淨無框全寬文字與工具膠囊
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    GeminiSparkleIcon(
                        size: 22,
                        isAnimating: message.status == .streaming || isCurrentlySpeaking
                    )

                    if message.status == .streaming && message.content.isEmpty {
                        Text(message.toolHistory.last ?? "正在思考...")
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

                // 工具呼叫軌跡膠囊
                if !message.toolHistory.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(message.toolHistory, id: \.self) { tool in
                                HStack(spacing: 4) {
                                    Image(systemName: "terminal.fill")
                                        .font(.system(size: 10))
                                    Text(tool)
                                        .font(.system(size: 11, weight: .medium))
                                        .lineLimit(1)
                                }
                                .padding(.horizontal, 9)
                                .padding(.vertical, 4)
                                .background(Color.white.opacity(0.07))
                                .foregroundColor(Color(white: 0.75))
                                .clipShape(Capsule())
                            }
                        }
                    }
                }

                // 大腦思考推導過程 (agent_thought_chunk 可展開/收折膠囊)
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
                                Image(systemName: (isThoughtExpanded || isActivelyThinking) ? "chevron.up" : "chevron.down")
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

                        if isThoughtExpanded || isActivelyThinking {
                            Text(message.thoughtProcess.trimmingCharacters(in: .whitespacesAndNewlines))
                                .font(.system(size: 13))
                                .foregroundColor(Color(white: 0.68))
                                .lineLimit(isActivelyThinking && !isThoughtExpanded ? 3 : nil)
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

                // 助理回覆正文
                if !message.content.isEmpty {
                    Text(LocalizedStringKey(message.content))
                        .font(.system(size: 16.5))
                        .lineSpacing(4)
                        .foregroundColor(.white.opacity(0.94))
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
