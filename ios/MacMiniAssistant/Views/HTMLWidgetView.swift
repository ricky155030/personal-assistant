import SwiftUI
import WebKit

/// 輕量級對話氣泡內嵌 HTML Widget 渲染器
/// 僅在訊息帶有 `metadata.widget_html` 時按需掛載，不影響純文字氣泡效能。
public struct HTMLWidgetView: View {
    let html: String
    @State private var contentHeight: CGFloat = 120

    public init(html: String) {
        self.html = html
    }

    public var body: some View {
        HTMLWidgetWebView(html: html, contentHeight: $contentHeight)
            .frame(height: contentHeight)
            .frame(maxWidth: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            )
    }

    /// 離屏渲染 HTML Widget 為 UIImage，供單則訊息截圖分享卡片內嵌使用
    @MainActor
    public static func renderSnapshot(html: String, width: CGFloat = 350) async -> UIImage? {
        let trimmed = html.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: width, height: 120), configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false

        let coordinator = OffscreenSnapshotDelegate()
        webView.navigationDelegate = coordinator

        let loaded = await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
            coordinator.onFinish = { ok in cont.resume(returning: ok) }
            webView.loadHTMLString(HTMLWidgetWebView.wrappedHTMLDocument(trimmed), baseURL: nil)
        }
        guard loaded else { return nil }

        try? await Task.sleep(nanoseconds: 150_000_000)
        let measuredHeight = (try? await webView.evaluateJavaScript(
            "Math.max(document.body ? document.body.scrollHeight : 0, document.documentElement ? document.documentElement.scrollHeight : 0)"
        ) as? Double) ?? 180
        let finalHeight = min(max(CGFloat(ceil(measuredHeight)), 44), 2400)
        webView.frame = CGRect(x: 0, y: 0, width: width, height: finalHeight)
        webView.setNeedsLayout()
        webView.layoutIfNeeded()
        try? await Task.sleep(nanoseconds: 80_000_000)

        let snapConfig = WKSnapshotConfiguration()
        snapConfig.rect = CGRect(x: 0, y: 0, width: width, height: finalHeight)
        return try? await webView.takeSnapshot(configuration: snapConfig)
    }
}

private final class OffscreenSnapshotDelegate: NSObject, WKNavigationDelegate {
    var onFinish: ((Bool) -> Void)?
    private var completed = false

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard !completed else { return }
        completed = true
        onFinish?(true)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard !completed else { return }
        completed = true
        onFinish?(false)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard !completed else { return }
        completed = true
        onFinish?(false)
    }
}

private struct HTMLWidgetWebView: UIViewRepresentable {
    let html: String
    @Binding var contentHeight: CGFloat

    func makeCoordinator() -> Coordinator {
        Coordinator(contentHeight: $contentHeight)
    }

    func makeUIView(context: Context) -> WKWebView {
        let contentController = WKUserContentController()
        contentController.add(context.coordinator, name: "widgetHeight")

        let config = WKWebViewConfiguration()
        config.userContentController = contentController

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.bounces = false
        webView.scrollView.showsVerticalScrollIndicator = false
        webView.scrollView.showsHorizontalScrollIndicator = false
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.lastLoadedHTML != html else { return }
        context.coordinator.lastLoadedHTML = html
        webView.loadHTMLString(Self.wrappedHTMLDocument(html), baseURL: nil)
    }

    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        uiView.configuration.userContentController.removeScriptMessageHandler(forName: "widgetHeight")
    }

    static func wrappedHTMLDocument(_ body: String) -> String {
        let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
        let isFullDocument = trimmed.lowercased().hasPrefix("<!doctype") || trimmed.lowercased().hasPrefix("<html")
        let heightObserverScript = """
        <script>
        (function() {
          function notifyHeight() {
            var h = Math.max(
              document.body ? document.body.scrollHeight : 0,
              document.documentElement ? document.documentElement.scrollHeight : 0
            );
            if (h > 0 && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.widgetHeight) {
              window.webkit.messageHandlers.widgetHeight.postMessage(h);
            }
          }
          window.addEventListener('load', notifyHeight);
          if (typeof ResizeObserver !== 'undefined') {
            var ro = new ResizeObserver(notifyHeight);
            ro.observe(document.documentElement);
          }
          setTimeout(notifyHeight, 60);
          setTimeout(notifyHeight, 250);
        })();
        </script>
        """

        if isFullDocument {
            if let range = trimmed.range(of: "</body>", options: [.caseInsensitive, .backwards]) {
                var updated = trimmed
                updated.insert(contentsOf: heightObserverScript, at: range.lowerBound)
                return updated
            }
            return trimmed + heightObserverScript
        }

        return """
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <style>
          :root { color-scheme: dark light; }
          * { box-sizing: border-box; }
          html, body {
            margin: 0;
            padding: 0;
            background: rgba(15, 23, 42, 0.72);
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
            font-size: 14px;
            line-height: 1.45;
            overflow-x: hidden;
          }
          .widget-root {
            padding: 12px 14px;
            width: 100%;
          }
        </style>
        </head>
        <body>
          <div class="widget-root">\(trimmed)</div>
          \(heightObserverScript)
        </body>
        </html>
        """
    }

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        @Binding var contentHeight: CGFloat
        var lastLoadedHTML: String?

        init(contentHeight: Binding<CGFloat>) {
            _contentHeight = contentHeight
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "widgetHeight" else { return }
            let rawHeight: Double?
            if let num = message.body as? NSNumber {
                rawHeight = num.doubleValue
            } else if let dbl = message.body as? Double {
                rawHeight = dbl
            } else {
                rawHeight = nil
            }
            guard let height = rawHeight, height > 0 else { return }
            let clamped = min(max(CGFloat(ceil(height)), 44), 2400)
            DispatchQueue.main.async {
                if abs(self.contentHeight - clamped) > 1.0 {
                    self.contentHeight = clamped
                }
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url {
                UIApplication.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }
    }
}
