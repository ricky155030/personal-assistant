import SwiftUI
import UIKit

/// 供 `.sheet(item:)` 綁定彈出自由選字視窗的輕量資料模型
public struct SelectableTextItem: Identifiable, Equatable {
    public let id = UUID()
    public let title: String
    public let text: String

    public init(title: String = "選取文字", text: String) {
        self.title = title
        self.text = text
    }
}

/// 以 UIKit `UITextView` (`isEditable = false`, `isSelectable = true`) 封裝的原生自由選字元件，
/// 支援雙擊選詞、長按放大鏡與拖拉藍點跨行反白複製。
public struct SelectableUITextView: UIViewRepresentable {
    public let text: String
    public let fontSize: Double

    public init(text: String, fontSize: Double = 15.5) {
        self.text = text
        self.fontSize = fontSize
    }

    public func makeUIView(context: Context) -> UITextView {
        let tv = UITextView()
        tv.isEditable = false
        tv.isSelectable = true
        tv.isScrollEnabled = true
        tv.alwaysBounceVertical = true
        tv.showsVerticalScrollIndicator = true
        tv.backgroundColor = .clear
        tv.textColor = UIColor(white: 0.95, alpha: 1.0)
        tv.tintColor = .systemCyan
        tv.dataDetectorTypes = []
        tv.textContainerInset = UIEdgeInsets(top: 16, left: 14, bottom: 28, right: 14)
        tv.textContainer.lineFragmentPadding = 0
        tv.attributedText = Self.makeAttributedString(text: text, fontSize: fontSize)
        return tv
    }

    public func updateUIView(_ uiView: UITextView, context: Context) {
        if uiView.text != text {
            let selected = uiView.selectedRange
            uiView.attributedText = Self.makeAttributedString(text: text, fontSize: fontSize)
            if selected.location + selected.length <= (uiView.text as NSString).length {
                uiView.selectedRange = selected
            }
        }
    }

    private static func makeAttributedString(text: String, fontSize: Double) -> NSAttributedString {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 5.0
        paragraph.paragraphSpacing = 6.0
        let attrs: [NSAttributedString.Key: Any] = [
            .font: UIFont.systemFont(ofSize: CGFloat(fontSize), weight: .regular),
            .foregroundColor: UIColor(white: 0.95, alpha: 1.0),
            .paragraphStyle: paragraph
        ]
        return NSAttributedString(string: text, attributes: attrs)
    }
}

/// 訊息自由選字複製彈窗（方案 A：保留主畫面 MarkdownMessageView 排版，點選「選取文字」進入原生 UITextView 自由反白）
public struct SelectableTextSheetView: View {
    public let title: String
    public let text: String

    @Environment(\.dismiss) private var dismiss
    @AppStorage("chatBaseFontSize") private var chatBaseFontSize: Double = 15.0
    @State private var copiedAll: Bool = false

    public init(title: String = "選取文字", text: String) {
        self.title = title
        self.text = text
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.07, blue: 0.14).ignoresSafeArea()

                VStack(spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: "selection.pin.in.out")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.cyan)
                        Text("雙擊詞彙或長按拖拉藍點，即可自由反白複製段落")
                            .font(.system(size: 12.5, weight: .medium))
                            .foregroundColor(Color(white: 0.75))
                            .lineLimit(2)
                        Spacer(minLength: 4)
                        Button {
                            UIPasteboard.general.string = text
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            copiedAll = true
                            DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
                                copiedAll = false
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: copiedAll ? "checkmark" : "doc.on.doc")
                                    .font(.system(size: 11.5, weight: .bold))
                                Text(copiedAll ? "已複製" : "複製全文")
                                    .font(.system(size: 12, weight: .semibold))
                            }
                            .foregroundColor(copiedAll ? .green : .cyan)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(
                                Capsule()
                                    .fill((copiedAll ? Color.green : Color.cyan).opacity(0.15))
                                    .overlay(
                                        Capsule()
                                            .stroke((copiedAll ? Color.green : Color.cyan).opacity(0.35), lineWidth: 0.8)
                                    )
                            )
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 10)

                    SelectableUITextView(text: text, fontSize: max(15.0, chatBaseFontSize + 0.5))
                        .background(
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .fill(Color.white.opacity(0.05))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                                        .stroke(Color.white.opacity(0.10), lineWidth: 1)
                                )
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .padding(.horizontal, 14)
                        .padding(.bottom, 12)
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
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
}
