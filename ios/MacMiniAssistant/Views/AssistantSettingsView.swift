import SwiftUI
import AVFoundation

struct AssistantSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var coordinator: VoiceSessionCoordinator

    @State private var serverURL: String = MacMiniAPIClient.shared.baseURL
    @State private var apiKey: String = MacMiniAPIClient.shared.apiKey
    @State private var testConnectionResult: String? = nil
    @State private var isTestingConnection: Bool = false
    @State private var selectedEdgeVoice: String = UserDefaults.standard.string(forKey: "macmini_edge_tts_voice") ?? "zh-TW-HsiaoChenNeural"

    private var availableVoices: [AVSpeechSynthesisVoice] {
        NativeSpeechPlayer.getAvailableVoices()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac mini 連線設定") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("伺服器 URL (Tailscale)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        TextField("http://your-tailscale-device.ts.net:8899", text: $serverURL)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                            .keyboardType(.URL)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("API Key (X-Agent-Key)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        SecureField("your-api-key", text: $apiKey)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                    }

                    Button(action: {
                        testConnection()
                    }) {
                        HStack {
                            if isTestingConnection {
                                ProgressView()
                                    .scaleEffect(0.8)
                            }
                            Text(isTestingConnection ? "正在測試連線..." : "測試與 Mac mini 連線")
                        }
                    }
                    .disabled(isTestingConnection)

                    if let result = testConnectionResult {
                        Text(result)
                            .font(.caption)
                            .foregroundColor(result.contains("成功") ? .green : .red)
                    }
                }

                Section("真人神經語音引擎 (Edge-TTS)") {
                    ForEach(EdgeTTSVoice.allCases) { voice in
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(voice.title)
                                    .font(.body)
                                Text(voice == .systemLocal ? "離線直接由 iPhone 本地合成" : "Mac mini 後端即時串流真人 MP3 音訊 (超擬真)")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                            Spacer()
                            if selectedEdgeVoice == voice.rawValue {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundColor(.blue)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            selectedEdgeVoice = voice.rawValue
                            UserDefaults.standard.set(voice.rawValue, forKey: "macmini_edge_tts_voice")
                        }
                    }
                }

                Section("iPhone 本地備援語音") {
                    if availableVoices.isEmpty {
                        Text("系統未偵測到繁體中文語音，將使用預設發音。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(availableVoices, id: \.identifier) { voice in
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(voice.name)
                                        .font(.body)
                                    HStack(spacing: 6) {
                                        Text(qualityLabel(voice.quality))
                                            .font(.caption2)
                                            .bold()
                                            .padding(.horizontal, 6)
                                            .padding(.vertical, 2)
                                            .background(qualityColor(voice.quality).opacity(0.15))
                                            .foregroundColor(qualityColor(voice.quality))
                                            .cornerRadius(4)

                                        Text(voice.language)
                                            .font(.caption2)
                                            .foregroundColor(.secondary)
                                    }
                                }

                                Spacer()

                                if coordinator.speechPlayer.preferredVoice?.identifier == voice.identifier {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.blue)
                                        .font(.system(size: 18))
                                }

                                Button(action: {
                                    coordinator.speechPlayer.selectAndTestVoice(voice)
                                }) {
                                    Image(systemName: "speaker.wave.2.fill")
                                        .font(.system(size: 16))
                                        .foregroundColor(.blue)
                                        .padding(6)
                                        .background(Color.blue.opacity(0.1))
                                        .clipShape(Circle())
                                }
                                .buttonStyle(BorderlessButtonStyle())
                            }
                            .contentShape(Rectangle())
                            .onTapGesture {
                                coordinator.speechPlayer.selectAndTestVoice(voice)
                            }
                        }
                    }

                    Text("提示：若要使用更擬真的神經網路聲音，可在 iPhone「設定」➔「輔助使用」➔「朗讀內容」➔「聲音」中下載「美佳（高級 / 增強版）」或「Siri」語音。")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Section("語音斷句與免持設定") {
                    Picker("助理朗讀語速", selection: $coordinator.ttsSpeed) {
                        ForEach(TTSSpeedOption.allCases) { spd in
                            Text(spd.title).tag(spd)
                        }
                    }

                    Stepper(value: $coordinator.pauseSeconds, in: 1...10, step: 1) {
                        HStack {
                            Text("說話停頓斷句秒數")
                            Spacer()
                            Text("\(coordinator.pauseSeconds) 秒")
                                .bold()
                                .foregroundColor(.blue)
                        }
                    }
                    Text("當您停止說話達 \(coordinator.pauseSeconds) 秒時，系統將自動判定斷句（可於 1 ~ 10 秒間以整數秒自由調整）。")
                        .font(.footnote)
                        .foregroundColor(.secondary)

                    Toggle("口袋盲操微提示音 (收音/送出叮咚聲)", isOn: $coordinator.earconSoundEnabled)
                    Text("在 Live 模式下，講完送出時發出輕柔確認音、助理回答完重開麥克風時發出清脆提示音；對助理說「沒事了」或「掰掰」可自動掛斷關閉麥克風。")
                        .font(.footnote)
                        .foregroundColor(.secondary)

                    Toggle("語音播報工具執行進度", isOn: $coordinator.speakToolStatusEnabled)
                    Text("當助理在背景讀寫檔案、呼叫 Subagent 或執行指令時，以輕聲簡短口語播報進度，不用看螢幕也能掌握現況。")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Section("關於本 App") {
                    HStack {
                        Text("版本")
                        Spacer()
                        let ver = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
                        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "16"
                        Text("\(ver) (Build \(build))")
                            .foregroundColor(.secondary)
                    }
                    HStack {
                        Text("專屬主人")
                        Spacer()
                        Text("Owner")
                            .foregroundColor(.secondary)
                    }
                    HStack {
                        Text("語音引擎")
                        Spacer()
                        Text("SFSpeechRecognizer + Edge-TTS")
                            .foregroundColor(.secondary)
                    }
                }
            }
            .navigationTitle("助理設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("儲存") {
                        MacMiniAPIClient.shared.baseURL = serverURL
                        MacMiniAPIClient.shared.apiKey = apiKey
                        coordinator.checkServerHealth()
                        dismiss()
                    }
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("關閉") {
                        dismiss()
                    }
                }
            }
        }
    }

    private func testConnection() {
        isTestingConnection = true
        testConnectionResult = nil
        Task {
            // 先暫存目前輸入的設定
            let oldBase = MacMiniAPIClient.shared.baseURL
            let oldKey = MacMiniAPIClient.shared.apiKey
            MacMiniAPIClient.shared.baseURL = serverURL
            MacMiniAPIClient.shared.apiKey = apiKey

            let (ok, ms, detail) = await MacMiniAPIClient.shared.checkHealth()
            isTestingConnection = false
            if ok {
                testConnectionResult = "✅ 連線成功！延遲 \(ms) ms"
            } else {
                testConnectionResult = "❌ 連線失敗: \(detail)"
                MacMiniAPIClient.shared.baseURL = oldBase
                MacMiniAPIClient.shared.apiKey = oldKey
            }
        }
    }

    private func qualityLabel(_ quality: AVSpeechSynthesisVoiceQuality) -> String {
        switch quality {
        case .premium:
            return "高級 (Premium)"
        case .enhanced:
            return "增強 (Enhanced)"
        case .default:
            return "標準 (Default)"
        @unknown default:
            return "一般"
        }
    }

    private func qualityColor(_ quality: AVSpeechSynthesisVoiceQuality) -> Color {
        switch quality {
        case .premium:
            return .purple
        case .enhanced:
            return .blue
        case .default:
            return .secondary
        @unknown default:
            return .secondary
        }
    }
}
