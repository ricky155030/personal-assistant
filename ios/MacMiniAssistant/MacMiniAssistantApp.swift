import SwiftUI
import AVFoundation

@main
struct MacMiniAssistantApp: App {
    @StateObject private var coordinator = VoiceSessionCoordinator()

    init() {
        configureAudioSession()
    }

    var body: some Scene {
        WindowGroup {
            HandsFreeMainView(coordinator: coordinator)
                .preferredColorScheme(.dark)
                .onOpenURL { url in
                    handleIncomingURL(url)
                }
                .onReceive(NotificationCenter.default.publisher(for: .triggerLiveVoice)) { _ in
                    triggerLiveVoiceAction()
                }
                .onReceive(NotificationCenter.default.publisher(for: .triggerSingleTurnVoice)) { _ in
                    triggerSingleVoiceAction()
                }
                .onReceive(NotificationCenter.default.publisher(for: .triggerNewChat)) { _ in
                    coordinator.startNewChat()
                }
        }
    }

    private func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .voiceChat,
                options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP, .duckOthers]
            )
            let hasExternalRoute = session.currentRoute.outputs.contains { desc in
                let port = desc.portType
                return port == .bluetoothHFP || port == .bluetoothA2DP || port == .bluetoothLE
                    || port == .headphones || port == .airPlay || port == .carAudio
            }
            if hasExternalRoute {
                try? session.overrideOutputAudioPort(.none)
            } else {
                try? session.overrideOutputAudioPort(.speaker)
            }
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            print("Failed to configure AVAudioSession category: \(error)")
        }
    }

    // MARK: - Deep Link / URL Scheme 處理
    private func handleIncomingURL(_ url: URL) {
        let scheme = url.scheme?.lowercased() ?? ""
        guard scheme == "macminiassistant" else { return }

        let host = url.host?.lowercased() ?? ""
        let path = url.path.lowercased()

        if host == "live" || path.contains("live") {
            triggerLiveVoiceAction()
        } else if host == "single" || path.contains("single") {
            triggerSingleVoiceAction()
        } else if host == "newchat" || path.contains("newchat") {
            coordinator.startNewChat()
        } else if host == "compact" || path.contains("compact") {
            coordinator.compactBrainContext()
        }
    }

    private func triggerLiveVoiceAction() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            if self.coordinator.voiceInputMode != .liveContinuous {
                self.coordinator.toggleLiveVoiceMode()
            }
        }
    }

    private func triggerSingleVoiceAction() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            if self.coordinator.voiceInputMode != .singleTurn {
                self.coordinator.toggleSingleTurnVoice()
            }
        }
    }
}
