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
        NativeSpeechPlayer.ensureOptimalAudioRoute()
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
