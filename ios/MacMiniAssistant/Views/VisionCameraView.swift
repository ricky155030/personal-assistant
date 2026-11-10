import SwiftUI
import AVFoundation
import PhotosUI

// MARK: - 隨身眼睛：高科技拍照即問工作台 (VisionCameraView)
struct VisionCameraView: View {
    @ObservedObject var coordinator: VoiceSessionCoordinator
    @Environment(\.dismiss) private var dismiss

    @StateObject private var cameraModel = VisionCameraSessionModel()
    @State private var presets: [VisionActionPreset] = VisionPresetStore.loadPresets()
    @State private var selectedPresetId: String = "builtin_general" // 預設高亮選中「萬物百科」
    @State private var isVoiceFollowUpEnabled: Bool = VisionPresetStore.isVoiceFollowUpEnabled // 拍後是否自動開啟麥克風聽補充語音
    @State private var capturedImages: [Data] = []
    @State private var showPresetManager: Bool = false
    @State private var showAddPresetSheet: Bool = false
    @State private var showTrayDrawer: Bool = false

    // 相簿選取
    @State private var showPhotoPicker: Bool = false
    @State private var selectedPhotos: [PhotosPickerItem] = []

    // 提問語音/文字（拍完照後自動聽寫或輸入）
    @State private var questionText: String = ""
    @State private var isDictating: Bool = false

    private let haptic = UIImpactFeedbackGenerator(style: .medium)

    private var currentPreset: VisionActionPreset {
        presets.first(where: { $0.id == selectedPresetId }) ?? presets.first ?? VisionActionPreset.defaultPresets[0]
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            // 1. 相機即時視訊取景層
            VisionCameraPreviewView(session: cameraModel.session)
                .ignoresSafeArea()
                .onTapGesture { location in
                    cameraModel.focus(at: location)
                }

            // 2. 科技感取景對焦線框 (HUD Overlay)
            cameraHUDOverlay

            // 3. 全局控制介面
            VStack(spacing: 0) {
                topControlBar
                    .padding(.horizontal, 16)
                    .padding(.top, 12)

                Spacer()

                // 4. 模式選擇列（萬物百科 / 蝦皮上架 / 嬰幼兒備品 / ➕）
                actionPresetsBar
                    .padding(.bottom, 12)

                // 5. 底部動態工作台：
                // 若尚未拍照 -> 顯示白色快門與相簿按鈕
                // 若已拍照 -> 顯示「照片縮圖 + 語音提問框 + 藍色送出按鈕」
                if capturedImages.isEmpty {
                    shutterControlBar
                        .padding(.horizontal, 24)
                        .padding(.bottom, 20)
                } else {
                    reviewAndAskWorkbench
                        .padding(.horizontal, 16)
                        .padding(.bottom, 18)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }

            // 6. 點擊托盤展開的多圖微抽屜
            if showTrayDrawer {
                trayDrawerOverlay
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .onAppear {
            haptic.prepare()
            presets = VisionPresetStore.loadPresets()
            isVoiceFollowUpEnabled = VisionPresetStore.isVoiceFollowUpEnabled
            if !presets.contains(where: { $0.id == selectedPresetId }) {
                selectedPresetId = presets.first?.id ?? "builtin_general"
            }
            cameraModel.checkPermissionAndStart()
        }
        .onDisappear {
            stopDictation()
            cameraModel.stopSession()
        }
        .sheet(isPresented: $showPresetManager) {
            VisionPresetManagerView(presets: $presets)
        }
        .sheet(isPresented: $showAddPresetSheet) {
            AddVisionPresetSheet { newPreset in
                presets.append(newPreset)
                selectedPresetId = newPreset.id
                VisionPresetStore.savePresets(presets)
            }
        }
        .photosPicker(isPresented: $showPhotoPicker, selection: $selectedPhotos, matching: .images)
        .onChange(of: selectedPhotos) { _, newItems in
            guard !newItems.isEmpty else { return }
            Task {
                var loaded: [Data] = []
                for item in newItems {
                    if let data = try? await item.loadTransferable(type: Data.self),
                       let uiImg = UIImage(data: data),
                       let jpeg = HandsFreeMainView.compressImage(uiImg) {
                        loaded.append(jpeg)
                    }
                }
                await MainActor.run {
                    capturedImages.append(contentsOf: loaded)
                    selectedPhotos.removeAll()
                    haptic.impactOccurred()
                    if isVoiceFollowUpEnabled {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            startDictationForQuestion()
                        }
                    }
                }
            }
        }
        .onChange(of: coordinator.liveTranscript) { _, newTranscript in
            if isDictating {
                let clean = newTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
                if !clean.isEmpty {
                    self.questionText = clean
                }
            }
        }
    }

    // MARK: - 頂部控制列
    private var topControlBar: some View {
        HStack(spacing: 12) {
            // 關閉相機
            Button(action: {
                haptic.impactOccurred()
                stopDictation()
                dismiss()
            }) {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 38, height: 38)
                    .background(Color.black.opacity(0.55))
                    .clipShape(Circle())
            }

            // 閃光燈切換
            Button(action: {
                haptic.impactOccurred()
                cameraModel.toggleFlash()
            }) {
                Image(systemName: cameraModel.isFlashOn ? "bolt.fill" : "bolt.slash.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(cameraModel.isFlashOn ? .yellow : .white)
                    .frame(width: 38, height: 38)
                    .background(Color.black.opacity(0.55))
                    .clipShape(Circle())
            }

            Spacer()

            // 🎙️ 拍攝後是否自動開啟麥克風聽補充語音開關 (即時開關)
            Button(action: {
                haptic.impactOccurred()
                isVoiceFollowUpEnabled.toggle()
                VisionPresetStore.isVoiceFollowUpEnabled = isVoiceFollowUpEnabled
            }) {
                HStack(spacing: 5) {
                    Image(systemName: isVoiceFollowUpEnabled ? "waveform.badge.mic" : "mic.slash")
                        .font(.system(size: 13, weight: .bold))
                    Text(isVoiceFollowUpEnabled ? "自動聽音: 開" : "自動聽音: 關")
                        .font(.system(size: 12, weight: .semibold))
                }
                .foregroundColor(isVoiceFollowUpEnabled ? .cyan : .white.opacity(0.75))
                .padding(.horizontal, 10)
                .frame(height: 38)
                .background(Color.black.opacity(0.55))
                .clipShape(Capsule())
                .overlay(
                    Capsule()
                        .stroke(isVoiceFollowUpEnabled ? Color.cyan.opacity(0.6) : Color.white.opacity(0.2), lineWidth: 1)
                )
            }

            // ⚙️ 自訂動作按鈕管理
            Button(action: {
                haptic.impactOccurred()
                showPresetManager = true
            }) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.white)
                    .frame(width: 38, height: 38)
                    .background(Color.black.opacity(0.55))
                    .clipShape(Circle())
            }
        }
    }

    // MARK: - HUD 對焦與取景線框
    private var cameraHUDOverlay: some View {
        GeometryReader { proxy in
            let w = proxy.size.width
            let h = proxy.size.height
            let boxSize = min(w * 0.76, h * 0.52)

            ZStack {
                // 中央科技感四角框
                VStack {
                    HStack {
                        CornerAngleView(edge: .topLeft)
                        Spacer()
                        CornerAngleView(edge: .topRight)
                    }
                    Spacer()
                    HStack {
                        CornerAngleView(edge: .bottomLeft)
                        Spacer()
                        CornerAngleView(edge: .bottomRight)
                    }
                }
                .frame(width: boxSize, height: boxSize)

                // 點擊手動對焦動畫方框
                if let pt = cameraModel.focusPoint {
                    Rectangle()
                        .stroke(Color.yellow, lineWidth: 1.5)
                        .frame(width: 64, height: 64)
                        .position(pt)
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .allowsHitTesting(false)
    }

    // MARK: - 模式選擇列（僅切換模式，不擅自發送）
    private var actionPresetsBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(presets) { preset in
                    let isSelected = (preset.id == selectedPresetId)
                    Button(action: {
                        haptic.impactOccurred()
                        withAnimation(.spring(response: 0.25, dampingFraction: 0.8)) {
                            selectedPresetId = preset.id
                        }
                    }) {
                        HStack(spacing: 6) {
                            Image(systemName: preset.icon)
                                .font(.system(size: 13, weight: .bold))
                            Text(preset.title)
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .foregroundColor(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .background(
                            Capsule()
                                .fill(
                                    isSelected
                                        ? LinearGradient(
                                            colors: [Color(red: 0.20, green: 0.45, blue: 0.95), Color(red: 0.12, green: 0.30, blue: 0.75)],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                        : LinearGradient(
                                            colors: [Color.white.opacity(0.16), Color.white.opacity(0.10)],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                )
                                .overlay(
                                    Capsule()
                                        .stroke(isSelected ? Color.cyan.opacity(0.9) : Color.white.opacity(0.18), lineWidth: isSelected ? 1.5 : 1)
                                )
                                .shadow(color: isSelected ? Color.blue.opacity(0.5) : .clear, radius: 8)
                        )
                    }
                }

                // ➕ 新增自訂按鈕
                Button(action: {
                    haptic.impactOccurred()
                    showAddPresetSheet = true
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "plus")
                            .font(.system(size: 13, weight: .bold))
                        Text("自訂")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .foregroundColor(.white.opacity(0.85))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(
                        Capsule()
                            .fill(Color.white.opacity(0.15))
                            .overlay(Capsule().stroke(Color.white.opacity(0.25), lineWidth: 1))
                    )
                }
            }
            .padding(.horizontal, 16)
        }
    }

    // MARK: - 拍照前：白色大快門與相簿控制列
    private var shutterControlBar: some View {
        HStack(alignment: .center) {
            // 左側：相簿選圖
            Button(action: {
                haptic.impactOccurred()
                showPhotoPicker = true
            }) {
                Image(systemName: "photo.on.rectangle")
                    .font(.system(size: 22))
                    .foregroundColor(.white.opacity(0.9))
                    .frame(width: 52, height: 52)
                    .background(Color.white.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
            .frame(width: 60)

            Spacer()

            // 中央：白色大快門圓鈕 (⚪)
            Button(action: {
                haptic.impactOccurred()
                cameraModel.capturePhoto { jpegData in
                    guard let data = jpegData else { return }
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.78)) {
                        capturedImages.append(data)
                    }
                    if isVoiceFollowUpEnabled {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            startDictationForQuestion()
                        }
                    }
                }
            }) {
                ZStack {
                    Circle()
                        .stroke(Color.white, lineWidth: 4.5)
                        .frame(width: 74, height: 74)

                    Circle()
                        .fill(Color.white)
                        .frame(width: 60, height: 60)
                }
            }

            Spacer()

            // 右側平衡佔位
            Color.clear
                .frame(width: 60, height: 52)
        }
    }

    // MARK: - 拍照後：即拍即問工作台（照片縮圖 + 語音提問框 + 藍色送出按鈕）
    private var reviewAndAskWorkbench: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                // 左側：拍立得照片縮圖（點擊可展開抽屜檢視或刪除，也可以按 + 再拍一張）
                ZStack(alignment: .topTrailing) {
                    if let lastData = capturedImages.last, let uiImg = UIImage(data: lastData) {
                        Button(action: {
                            haptic.impactOccurred()
                            withAnimation(.spring(response: 0.3, dampingFraction: 0.75)) {
                                showTrayDrawer.toggle()
                            }
                        }) {
                            Image(uiImage: uiImg)
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: 46, height: 46)
                                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.white, lineWidth: 2))
                        }

                        if capturedImages.count > 1 {
                            Text("\(capturedImages.count)")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundColor(.white)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 2)
                                .background(Capsule().fill(Color.red))
                                .offset(x: 5, y: -5)
                        }
                    }
                }

                // 中間：語音即時聽寫與問題輸入框
                HStack(spacing: 8) {
                    Button(action: {
                        haptic.impactOccurred()
                        if isDictating {
                            stopDictation()
                        } else {
                            startDictationForQuestion()
                        }
                    }) {
                        HStack(spacing: 5) {
                            Image(systemName: isDictating ? "waveform" : "mic.fill")
                                .font(.system(size: 15, weight: .bold))
                                .foregroundColor(isDictating ? .cyan : .white.opacity(0.8))

                            if isDictating {
                                MiniAudioWaveView(db: coordinator.currentDb, isListening: isDictating)
                            }
                        }
                    }

                    TextField(
                        isDictating ? "正在聆聽 (如: 有毒嗎?)..." : "補充問題 (或直接點送出)...",
                        text: $questionText
                    )
                    .font(.system(size: 15))
                    .foregroundColor(.white)

                    if !questionText.isEmpty {
                        Button(action: { questionText = "" }) {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 14))
                                .foregroundColor(Color.white.opacity(0.6))
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .fill(Color(red: 0.12, green: 0.14, blue: 0.18))
                        .overlay(
                            RoundedRectangle(cornerRadius: 22, style: .continuous)
                                .stroke(isDictating ? Color.cyan.opacity(0.8) : Color.white.opacity(0.2), lineWidth: 1)
                        )
                )

                // 核心送出按鈕：唯一送出點！
                Button(action: {
                    haptic.impactOccurred()
                    sendFinalVisionRequest()
                }) {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [Color(red: 0.22, green: 0.52, blue: 1.0), Color(red: 0.10, green: 0.35, blue: 0.88)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 46, height: 46)
                        .overlay(
                            Image(systemName: "arrow.up")
                                .font(.system(size: 18, weight: .bold))
                                .foregroundColor(.white)
                        )
                        .shadow(color: Color.blue.opacity(0.45), radius: 6)
                }
            }

            // 下方輔助列：加拍一張 / 重拍清空
            HStack(spacing: 16) {
                Button(action: {
                    haptic.impactOccurred()
                    cameraModel.capturePhoto { jpegData in
                        guard let data = jpegData else { return }
                        withAnimation {
                            capturedImages.append(data)
                        }
                    }
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "camera.badge.plus")
                            .font(.system(size: 12))
                        Text("加拍下一張")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .foregroundColor(.white.opacity(0.8))
                }

                Spacer()

                Button(action: {
                    haptic.impactOccurred()
                    stopDictation()
                    withAnimation {
                        capturedImages.removeAll()
                        questionText = ""
                        showTrayDrawer = false
                    }
                }) {
                    Text("重拍清空")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.red.opacity(0.9))
                }
            }
            .padding(.horizontal, 8)
        }
    }

    // MARK: - 啟動專屬問題聽寫
    private func startDictationForQuestion() {
        isDictating = true
        coordinator.startVisionVoiceFollowUp(
            onPartial: { partialText in
                let clean = partialText.trimmingCharacters(in: .whitespacesAndNewlines)
                if !clean.isEmpty {
                    self.questionText = clean
                }
            },
            onFinished: { transcript in
                Task { @MainActor in
                    let clean = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !clean.isEmpty {
                        self.questionText = clean
                    }
                    self.isDictating = false
                }
            }
        )
    }

    private func stopDictation() {
        isDictating = false
        _ = coordinator.stopVisionVoiceFollowUp()
    }

    // MARK: - 核心唯一送出函式：將「模式 Prompt + 主人提問 + 所有照片」一次性打包送出
    private func sendFinalVisionRequest() {
        guard !capturedImages.isEmpty else { return }
        let flushedLive = coordinator.stopVisionVoiceFollowUp()
        isDictating = false

        let preset = currentPreset
        var extra = questionText.trimmingCharacters(in: .whitespacesAndNewlines)
        if extra.isEmpty && !flushedLive.isEmpty {
            extra = flushedLive
        }
        let imagesToSend = capturedImages

        // 重置相機內部狀態並關閉
        capturedImages.removeAll()
        questionText = ""
        dismiss()

        // 唯一送出！絕不發送兩次！
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            coordinator.sendVisionRequest(
                prompt: preset.prompt,
                imagesData: imagesToSend,
                extraVoiceNote: extra.isEmpty ? nil : extra
            )
        }
    }

    // MARK: - 托盤多圖展開微抽屜
    private var trayDrawerOverlay: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("已拍照片 (\(capturedImages.count) 張)")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(.white)

                    Spacer()

                    Button("收合") {
                        haptic.impactOccurred()
                        withAnimation {
                            showTrayDrawer = false
                        }
                    }
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(.cyan)
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(capturedImages.indices, id: \.self) { idx in
                            if let uiImg = UIImage(data: capturedImages[idx]) {
                                ZStack(alignment: .topTrailing) {
                                    Image(uiImage: uiImg)
                                        .resizable()
                                        .aspectRatio(contentMode: .fill)
                                        .frame(width: 80, height: 80)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))

                                    // 刪除該張按鈕
                                    Button(action: {
                                        haptic.impactOccurred()
                                        withAnimation {
                                            capturedImages.remove(at: idx)
                                            if capturedImages.isEmpty {
                                                showTrayDrawer = false
                                            }
                                        }
                                    }) {
                                        Image(systemName: "xmark.circle.fill")
                                            .font(.system(size: 18))
                                            .foregroundColor(.red)
                                            .background(Circle().fill(Color.white))
                                    }
                                    .offset(x: 4, y: -4)
                                }
                            }
                        }

                        // 補選相片
                        Button(action: {
                            haptic.impactOccurred()
                            showPhotoPicker = true
                        }) {
                            VStack(spacing: 4) {
                                Image(systemName: "plus")
                                    .font(.system(size: 18, weight: .bold))
                                Text("加相片")
                                    .font(.system(size: 11))
                            }
                            .foregroundColor(.white.opacity(0.8))
                            .frame(width: 80, height: 80)
                            .background(Color.white.opacity(0.12))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
            }
            .padding(16)
            .background(
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(Color(red: 0.12, green: 0.13, blue: 0.17))
                    .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(Color.white.opacity(0.15), lineWidth: 1))
            )
            .padding(.horizontal, 16)
            .padding(.bottom, 96)
        }
        .background(Color.black.opacity(0.4).onTapGesture {
            withAnimation { showTrayDrawer = false }
        })
    }
}

// MARK: - HUD 科技感拐角視圖
private struct CornerAngleView: View {
    enum CornerEdge {
        case topLeft, topRight, bottomLeft, bottomRight
    }
    let edge: CornerEdge

    var body: some View {
        Canvas { context, size in
            var path = Path()
            let len: CGFloat = 22
            let thick: CGFloat = 3.5

            switch edge {
            case .topLeft:
                path.move(to: CGPoint(x: 0, y: len))
                path.addLine(to: CGPoint(x: 0, y: 0))
                path.addLine(to: CGPoint(x: len, y: 0))
            case .topRight:
                path.move(to: CGPoint(x: size.width - len, y: 0))
                path.addLine(to: CGPoint(x: size.width, y: 0))
                path.addLine(to: CGPoint(x: size.width, y: len))
            case .bottomLeft:
                path.move(to: CGPoint(x: 0, y: size.height - len))
                path.addLine(to: CGPoint(x: 0, y: size.height))
                path.addLine(to: CGPoint(x: len, y: size.height))
            case .bottomRight:
                path.move(to: CGPoint(x: size.width - len, y: size.height))
                path.addLine(to: CGPoint(x: size.width, y: size.height))
                path.addLine(to: CGPoint(x: size.width, y: size.height - len))
            }
            context.stroke(path, with: .color(.white), lineWidth: thick)
        }
        .frame(width: 24, height: 24)
    }
}

// MARK: - 即時音波反饋元件
struct MiniAudioWaveView: View {
    let db: Float
    let isListening: Bool

    var body: some View {
        HStack(spacing: 2.5) {
            ForEach(0..<3) { idx in
                let normalized = max(0.2, min(1.0, (CGFloat(db) + 50.0) / 35.0))
                let scale: CGFloat = isListening ? (idx == 1 ? normalized : normalized * 0.7) : 0.2
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(Color.cyan)
                    .frame(width: 2.5, height: max(3.5, 14 * scale))
                    .animation(.easeOut(duration: 0.08), value: scale)
            }
        }
    }
}

// MARK: - AVFoundation 相機 Session 管理器
final class VisionCameraSessionModel: NSObject, ObservableObject, AVCapturePhotoCaptureDelegate {
    @Published var session = AVCaptureSession()
    @Published var isFlashOn: Bool = false
    @Published var focusPoint: CGPoint? = nil

    private let photoOutput = AVCapturePhotoOutput()
    private var photoCompletion: ((Data?) -> Void)?
    private var videoDevice: AVCaptureDevice?

    func checkPermissionAndStart() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            setupSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                if granted {
                    DispatchQueue.main.async {
                        self?.setupSession()
                    }
                }
            }
        default:
            break
        }
    }

    private func setupSession() {
        guard !session.isRunning else { return }
        session.automaticallyConfiguresApplicationAudioSession = false
        session.beginConfiguration()
        session.sessionPreset = .photo

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: device) else {
            session.commitConfiguration()
            return
        }
        self.videoDevice = device

        if session.canAddInput(input) {
            session.addInput(input)
        }
        if session.canAddOutput(photoOutput) {
            session.addOutput(photoOutput)
        }
        session.commitConfiguration()

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.session.startRunning()
        }
    }

    func stopSession() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.session.stopRunning()
        }
    }

    func toggleFlash() {
        isFlashOn.toggle()
    }

    func focus(at point: CGPoint) {
        guard let device = videoDevice else { return }
        do {
            try device.lockForConfiguration()
            if device.isFocusPointOfInterestSupported {
                device.focusPointOfInterest = CGPoint(x: point.y / UIScreen.main.bounds.height, y: 1.0 - (point.x / UIScreen.main.bounds.width))
                device.focusMode = .autoFocus
            }
            device.unlockForConfiguration()
            self.focusPoint = point
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                if self.focusPoint == point {
                    self.focusPoint = nil
                }
            }
        } catch {
            print("[VisionCamera] Focus error: \(error)")
        }
    }

    func capturePhoto(completion: @escaping (Data?) -> Void) {
        self.photoCompletion = completion
        let settings = AVCapturePhotoSettings()
        if photoOutput.supportedFlashModes.contains(.on) {
            settings.flashMode = isFlashOn ? .on : .off
        }
        photoOutput.capturePhoto(with: settings, delegate: self)
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        guard error == nil, let fileData = photo.fileDataRepresentation() else {
            photoCompletion?(nil)
            return
        }
        if let uiImg = UIImage(data: fileData), let compressed = HandsFreeMainView.compressImage(uiImg) {
            photoCompletion?(compressed)
        } else {
            photoCompletion?(fileData)
        }
    }
}

// MARK: - AVCaptureVideoPreviewLayer UIView 封裝
struct VisionCameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> CameraPreviewUIView {
        let view = CameraPreviewUIView()
        view.videoPreviewLayer.session = session
        view.videoPreviewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: CameraPreviewUIView, context: Context) {}

    final class CameraPreviewUIView: UIView {
        override class var layerClass: AnyClass {
            AVCaptureVideoPreviewLayer.self
        }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer {
            layer as! AVCaptureVideoPreviewLayer
        }
    }
}

// MARK: - 動作按鈕管理面板 (VisionPresetManagerView)
struct VisionPresetManagerView: View {
    @Binding var presets: [VisionActionPreset]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("動作按鈕清單（可拖曳排序或刪除）") {
                    ForEach(presets) { preset in
                        HStack(spacing: 12) {
                            Image(systemName: preset.icon)
                                .font(.system(size: 16, weight: .bold))
                                .foregroundColor(.cyan)
                                .frame(width: 28)

                            VStack(alignment: .leading, spacing: 3) {
                                Text(preset.title)
                                    .font(.system(size: 15, weight: .semibold))
                                Text(preset.prompt)
                                    .font(.system(size: 12))
                                    .foregroundColor(.secondary)
                                    .lineLimit(2)
                            }
                        }
                    }
                    .onDelete { indexSet in
                        presets.remove(atOffsets: indexSet)
                        VisionPresetStore.savePresets(presets)
                    }
                    .onMove { indices, newOffset in
                        presets.move(fromOffsets: indices, toOffset: newOffset)
                        VisionPresetStore.savePresets(presets)
                    }
                }

                Section {
                    Button("重置為預設動作按鈕") {
                        presets = VisionActionPreset.defaultPresets
                        VisionPresetStore.savePresets(presets)
                    }
                    .foregroundColor(.red)
                }
            }
            .navigationTitle("管理相機動作按鈕")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    EditButton()
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
        }
    }
}

// MARK: - 新增自訂按鈕面板 (AddVisionPresetSheet)
struct AddVisionPresetSheet: View {
    var onAdd: (VisionActionPreset) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var title: String = ""
    @State private var icon: String = "sparkles"
    @State private var prompt: String = ""

    private let availableIcons = [
        "sparkles", "bag.fill", "heart.fill", "doc.text",
        "pills.fill", "book.closed.fill", "fork.knife", "cart.fill",
        "tag.fill", "leaf.fill", "cross.case.fill", "camera.metering.matrix"
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("按鈕基本資料") {
                    TextField("按鈕名稱 (例: 菜單翻譯、植物照護)", text: $title)

                    Picker("圖示", selection: $icon) {
                        ForEach(availableIcons, id: \.self) { ic in
                            Label(ic, systemImage: ic).tag(ic)
                        }
                    }
                }

                Section(
                    header: Text("專屬 Prompt 指令"),
                    footer: Text("提示：若需調用特定技能，可直接寫「請調用 skill <技能名稱>...」。拍完照片後大腦將依此指令處理。")
                ) {
                    TextEditor(text: $prompt)
                        .frame(minHeight: 120)
                }
            }
            .navigationTitle("新增相機動作按鈕")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("新增") {
                        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
                        let trimmedPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmedTitle.isEmpty, !trimmedPrompt.isEmpty else { return }
                        let newPreset = VisionActionPreset(
                            title: trimmedTitle,
                            icon: icon,
                            prompt: trimmedPrompt,
                            isBuiltIn: false
                        )
                        onAdd(newPreset)
                        dismiss()
                    }
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}
