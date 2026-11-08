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
        }
    }

    private func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .default,
                options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP, .mixWithOthers]
            )
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            print("Failed to configure AVAudioSession category: \(error)")
        }
    }
}
