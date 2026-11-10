import Foundation

public final class MacMiniAPIClient {
    public static let shared = MacMiniAPIClient()

    private let defaults = UserDefaults.standard
    private let serverURLKey = "macmini_server_url"
    private let apiKeyKey = "macmini_api_key"

    public var baseURL: String {
        get {
            defaults.string(forKey: serverURLKey) ?? "http://127.0.0.1:8899"
        }
        set {
            defaults.set(newValue, forKey: serverURLKey)
        }
    }

    public var apiKey: String {
        get {
            defaults.string(forKey: apiKeyKey) ?? ""
        }
        set {
            defaults.set(newValue, forKey: apiKeyKey)
        }
    }

    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60.0
        config.timeoutIntervalForResource = 180.0
        config.waitsForConnectivity = true
        config.httpShouldUsePipelining = false
        self.session = URLSession(configuration: config)
    }

    public func checkHealth() async -> (Bool, Int, String) {
        let cleanBase = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/health") else {
            return (false, 0, "無效網址")
        }

        let start = Date()
        var request = URLRequest(url: url)
        request.timeoutInterval = 3.5

        do {
            let (_, response) = try await session.data(for: request)
            let ms = Int(Date().timeIntervalSince(start) * 1000)
            guard let httpResp = response as? HTTPURLResponse, httpResp.statusCode == 200 else {
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                return (false, ms, "HTTP \(code)")
            }

            // 進一步驗證 API Key 是否正確（避免健康檢查成功但發送時 401 Unauthorized）
            if let authUrl = URL(string: "\(cleanBase)/api/history?limit=1") {
                var authReq = URLRequest(url: authUrl)
                authReq.timeoutInterval = 3.0
                authReq.setValue(apiKey, forHTTPHeaderField: "X-Agent-Key")
                if let (_, authResp) = try? await session.data(for: authReq),
                   let authHttp = authResp as? HTTPURLResponse, authHttp.statusCode == 401 {
                    return (false, ms, "端點通，但 API Key 未填或錯誤 (401)")
                }
            }

            return (true, ms, "連線與金鑰驗證正常 (\(ms)ms)")
        } catch {
            return (false, 0, "無法連線 (請確認 Tailscale VPN)")
        }
    }

    public func fetchQuotaStatus(forceRefresh: Bool = false) async -> QuotaSnapshot? {
        let cleanBase = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let urlStr = "\(cleanBase)/api/quota?refresh=\(forceRefresh ? "true" : "false")"
        guard let url = URL(string: urlStr) else { return nil }

        var request = URLRequest(url: url)
        request.timeoutInterval = forceRefresh ? 6.0 : 3.5
        request.setValue(apiKey, forHTTPHeaderField: "X-Agent-Key")

        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResp = response as? HTTPURLResponse, httpResp.statusCode == 200,
                  let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            let quota = root["quota"] as? [String: Any] ?? [:]
            let gemini = quota["gemini"] as? [String: Any] ?? [:]
            let weekly = gemini["weekly_7d"] as? [String: Any] ?? [:]
            let rolling5h = gemini["rolling_5h"] as? [String: Any] ?? [:]
            let argon = (quota["argon"] as? [String: Any]) ?? (quota["gemini_argon"] as? [String: Any]) ?? [:]
            let thirdParty = quota["third_party"] as? [String: Any] ?? [:]
            let p3Weekly = thirdParty["weekly_7d"] as? [String: Any] ?? [:]
            let credits = quota["credits"] as? [String: Any] ?? [:]
            let bgTasks = (root["background_tasks"] as? [String: Any]) ?? (quota["background_tasks"] as? [String: Any]) ?? [:]
            let context = root["context"] as? [String: Any] ?? [:]

            let activeNames = (bgTasks["active_names"] as? [String]) ?? []

            return QuotaSnapshot(
                weeklyRemainingPct: (weekly["remaining_pct"] as? NSNumber)?.doubleValue ?? 100.0,
                weeklyRefreshesIn: (weekly["refreshes_in"] as? String) ?? "--",
                weeklyResetTimeTaipei: (weekly["reset_time_taipei"] as? String) ?? "",
                rolling5hRemainingPct: (rolling5h["remaining_pct"] as? NSNumber)?.doubleValue ?? 100.0,
                rolling5hRefreshesIn: (rolling5h["refreshes_in"] as? String) ?? "--",
                rolling5hResetTimeTaipei: (rolling5h["reset_time_taipei"] as? String) ?? "",
                contextPercent: (context["percent"] as? NSNumber)?.doubleValue ?? 0.0,
                contextTokens: (context["current_tokens"] as? NSNumber)?.intValue ?? 0,
                contextLimit: (context["limit"] as? NSNumber)?.intValue ?? 1_000_000,
                currentModel: (context["current_model"] as? String) ?? "gemini-3.8-flash-medium",
                sessionId: (context["conversation_id"] as? String) ?? "N/A",
                planTier: (quota["plan_tier"] as? String) ?? "Google AI Pro",
                argonRemainingPct: (argon["remaining_pct"] as? NSNumber)?.doubleValue ?? 100.0,
                argonRefreshesIn: (argon["refreshes_in"] as? String) ?? "--",
                argonResetTimeTaipei: (argon["reset_time_taipei"] as? String) ?? "",
                promptCredits: (credits["available_prompt_credits"] as? NSNumber)?.intValue ?? 0,
                flowCredits: (credits["available_flow_credits"] as? NSNumber)?.intValue ?? 0,
                monthlyPromptCredits: (credits["monthly_prompt_credits"] as? NSNumber)?.intValue ?? 0,
                monthlyFlowCredits: (credits["monthly_flow_credits"] as? NSNumber)?.intValue ?? 0,
                thirdPartyRemainingPct: (p3Weekly["remaining_pct"] as? NSNumber)?.doubleValue ?? 100.0,
                thirdPartyRefreshesIn: (p3Weekly["refreshes_in"] as? String) ?? "--",
                activeTaskCount: (bgTasks["running_count"] as? NSNumber)?.intValue ?? activeNames.count,
                activeTaskNames: activeNames
            )
        } catch {
            return nil
        }
    }

    public func uploadAudioForTranscription(fileURL: URL) async throws -> String {
        let cleanBase = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/v1/audio/transcriptions") else {
            throw URLError(.badURL)
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "X-Agent-Key")
        request.setValue("close", forHTTPHeaderField: "Connection")

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let audioData = try Data(contentsOf: fileURL)

        var body = Data()
        // language 欄位
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"language\"\r\n\r\n".data(using: .utf8)!)
        body.append("zh\r\n".data(using: .utf8)!)

        // file 欄位
        let filename = fileURL.lastPathComponent
        let ext = fileURL.pathExtension.lowercased()
        let mimeType = ext == "caf" ? "audio/x-caf" : (ext == "m4a" ? "audio/m4a" : "audio/wav")
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n".data(using: .utf8)!)

        // 結束 boundary
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }

        // 靜默忽略純噪音
        if httpResponse.statusCode == 400 {
            return ""
        }

        if httpResponse.statusCode != 200 {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String ?? "HTTP \(httpResponse.statusCode)"
            throw NSError(domain: "Server", code: httpResponse.statusCode, userInfo: [NSLocalizedDescriptionKey: "伺服器回應異常 (\(httpResponse.statusCode)): \(detail)"])
        }

        struct TranscriptionResponse: Codable {
            let text: String
        }

        if let decoded = try? JSONDecoder().decode(TranscriptionResponse.self, from: data) {
            return decoded.text.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        return ""
    }

    /// 中途取消正在思考或執行工具的 ACP Turn (POST /api/voice-chat/cancel)
    public func cancelVoiceChat(level: String? = nil) async {
        let cleanBase = self.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/api/voice-chat/cancel") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 5.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(self.apiKey, forHTTPHeaderField: "X-Agent-Key")
        request.setValue("close", forHTTPHeaderField: "Connection")
        var payload: [String: Any] = ["reason": "ios_user_cancelled"]
        if let lvl = level {
            payload["level"] = lvl
        }
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        _ = try? await self.session.data(for: request)
    }

    /// 觸發 ACP 原生 `/compact` 無損壓縮對話記憶 (POST /api/quota/compact)
    public func compactContext() async -> (Bool, String) {
        let cleanBase = self.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/api/quota/compact") else {
            return (false, "無效網址")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 45.0
        request.setValue(self.apiKey, forHTTPHeaderField: "X-Agent-Key")
        do {
            let (data, resp) = try await self.session.data(for: request)
            guard let httpResp = resp as? HTTPURLResponse, httpResp.statusCode == 200,
                  let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let compact = root["compact"] as? [String: Any] else {
                return (false, "伺服器壓縮失敗")
            }
            let ok = (compact["success"] as? Bool) ?? false
            if ok {
                let beforeTok = (compact["before_tokens"] as? NSNumber)?.intValue ?? 0
                let afterTok = (compact["after_tokens"] as? NSNumber)?.intValue ?? 0
                return (true, "壓縮完成：\(beforeTok.formatted()) ➔ \(afterTok.formatted()) tokens")
            } else {
                let err = (compact["error"] as? String) ?? "未知原因"
                return (false, err)
            }
        } catch {
            return (false, error.localizedDescription)
        }
    }

    /// 取得單句 Edge-TTS 語音分片音訊（自動重試 2 次）
    public func fetchTTSAudioChunk(text: String, voice: String? = nil, rate: String? = nil) async throws -> Data {
        let cleanBase = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        var components = URLComponents(string: "\(cleanBase)/api/voice-chat/tts")
        let v = voice ?? UserDefaults.standard.string(forKey: "macmini_edge_tts_voice") ?? "zh-TW-HsiaoChenNeural"
        components?.queryItems = [
            URLQueryItem(name: "text", value: text),
            URLQueryItem(name: "voice", value: v)
        ]
        guard let url = components?.url else {
            throw URLError(.badURL)
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 8.0
        request.setValue(apiKey, forHTTPHeaderField: "X-Agent-Key")
        request.setValue("close", forHTTPHeaderField: "Connection")

        var lastError: Error?
        for attempt in 0...2 {
            if attempt > 0 {
                try? await Task.sleep(nanoseconds: UInt64(200_000_000 * attempt))
            }
            do {
                let (data, resp) = try await session.data(for: request)
                if let httpResp = resp as? HTTPURLResponse, httpResp.statusCode == 200, !data.isEmpty {
                    return data
                } else {
                    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                    throw NSError(domain: "Server", code: code, userInfo: [NSLocalizedDescriptionKey: "HTTP \(code)"])
                }
            } catch {
                lastError = error
            }
        }
        throw lastError ?? URLError(.cannotConnectToHost)
    }

    public func streamVoiceChat(
        query: String,
        client: String = "ios-voice",
        level: String? = nil,
        imageBase64: String? = nil,
        imagesBase64: [String]? = nil
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        return AsyncThrowingStream { continuation in
            Task {
                let cleanBase = self.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                guard let url = URL(string: "\(cleanBase)/api/voice-chat/stream") else {
                    continuation.finish(throwing: URLError(.badURL))
                    return
                }

                var request = URLRequest(url: url)
                request.httpMethod = "POST"
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.setValue(self.apiKey, forHTTPHeaderField: "X-Agent-Key")
                request.setValue("close", forHTTPHeaderField: "Connection")

                let savedVoice = UserDefaults.standard.string(forKey: "macmini_edge_tts_voice") ?? "zh-TW-HsiaoChenNeural"
                let savedRate = UserDefaults.standard.string(forKey: "macmini_tts_speed_rate") ?? "+0%"
                var payload: [String: Any] = [
                    "query": query,
                    "client": client,
                    "tts_voice": savedVoice,
                    "tts_rate": savedRate
                ]
                if let lvl = level {
                    payload["level"] = lvl
                }
                if let multi = imagesBase64, !multi.isEmpty {
                    let mediaArray = multi.map { ["data_base64": $0, "mime_type": "image/jpeg"] }
                    payload["media"] = mediaArray
                    if let first = multi.first {
                        payload["image_base64"] = first
                    }
                } else if let imgB64 = imageBase64, !imgB64.isEmpty {
                    payload["image_base64"] = imgB64
                    payload["media"] = [["data_base64": imgB64, "mime_type": "image/jpeg"]]
                }

                guard let body = try? JSONSerialization.data(withJSONObject: payload) else {
                    continuation.finish(throwing: URLError(.cannotDecodeContentData))
                    return
                }
                request.httpBody = body

                do {
                    var asyncBytes: URLSession.AsyncBytes?
                    var streamConnectError: Error?

                    // 自動重試最多 2 次（共 3 次嘗試），帶微小延遲重試
                    for attempt in 0...2 {
                        if attempt > 0 {
                            try? await Task.sleep(nanoseconds: UInt64(250_000_000 * attempt))
                        }
                        do {
                            let (bytes, resp) = try await self.session.bytes(for: request)
                            if let httpResp = resp as? HTTPURLResponse, httpResp.statusCode == 200 {
                                asyncBytes = bytes
                                streamConnectError = nil
                                break
                            } else {
                                let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                                throw NSError(domain: "Server", code: code, userInfo: [NSLocalizedDescriptionKey: "HTTP \(code)"])
                            }
                        } catch {
                            streamConnectError = error
                        }
                    }

                    guard let asyncBytes = asyncBytes else {
                        continuation.finish(throwing: streamConnectError ?? URLError(.badServerResponse))
                        return
                    }

                    for try await rawLine in asyncBytes.lines {
                        let line = rawLine.trimmingCharacters(in: .whitespaces)
                        guard line.hasPrefix("data:") else { continue }

                        let jsonString = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        guard let jsonData = jsonString.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
                              let type = json["type"] as? String else {
                            continue
                        }

                        switch type {
                        case "thought_delta":
                            let delta = json["delta"] as? String ?? ""
                            continuation.yield(.thoughtDelta(delta: delta))

                        case "status":
                            let text = json["text"] as? String ?? ""
                            let tts = json["tts"] as? String
                            continuation.yield(.status(text: text, tts: tts))

                        case "text_delta":
                            let delta = json["delta"] as? String ?? ""
                            continuation.yield(.textDelta(delta: delta))

                        case "tts_sentence":
                            let sentence = json["sentence"] as? String ?? ""
                            continuation.yield(.ttsSentence(sentence: sentence))

                        case "audio_chunk":
                            let seq = json["seq"] as? Int ?? 0
                            let sentence = json["sentence"] as? String ?? ""
                            if let b64 = json["audio_base64"] as? String,
                               let audioData = Data(base64Encoded: b64) {
                                continuation.yield(.audioChunk(seq: seq, sentence: sentence, audioData: audioData))
                            }

                        case "done":
                            let fullReply = json["full_reply"] as? String ?? ""
                            let latency = (json["latency"] as? NSNumber)?.doubleValue ?? 0.0
                            let thinkingLevel = json["thinking_level"] as? String ?? "med"
                            let reportUrl = json["report_url"] as? String ?? ""
                            let quickReplies = json["quick_replies"] as? [String] ?? []
                            continuation.yield(.done(
                                fullReply: fullReply,
                                latency: latency,
                                thinkingLevel: thinkingLevel,
                                reportUrl: reportUrl,
                                quickReplies: quickReplies
                            ))

                        case "error":
                            let errMsg = json["error"] as? String ?? "未知錯誤"
                            continuation.yield(.error(message: errMsg))

                        default:
                            break
                        }
                    }

                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
}
