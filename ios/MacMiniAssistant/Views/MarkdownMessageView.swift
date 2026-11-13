import SwiftUI

/// 原生輕量 Markdown 渲染器（零第三方依賴，支援動態字體與串流未閉合程式碼區塊）
struct MarkdownMessageView: View {
    let content: String
    @AppStorage("chatBaseFontSize") private var baseFontSize: Double = 15.0

    private enum Block: Identifiable {
        case heading(level: Int, text: String)
        case codeBlock(language: String, code: String)
        case blockquote(text: String)
        case bulletItem(marker: String, text: String)
        case table(headers: [String], rows: [[String]])
        case divider
        case paragraph(text: String)

        var id: String {
            switch self {
            case .heading(let lvl, let txt): return "h\(lvl)-\(txt.hashValue)"
            case .codeBlock(let lang, let code): return "code-\(lang)-\(code.hashValue)"
            case .blockquote(let txt): return "quote-\(txt.hashValue)"
            case .bulletItem(let m, let txt): return "li-\(m)-\(txt.hashValue)"
            case .table(let h, let r): return "tbl-\(h.joined().hashValue)-\(r.count)"
            case .divider: return "div-\(UUID().uuidString)"
            case .paragraph(let txt): return "p-\(txt.hashValue)"
            }
        }
    }

    var body: some View {
        let blocks = parseBlocks(content)
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                renderBlock(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func renderBlock(_ block: Block) -> some View {
        switch block {
        case .heading(let level, let text):
            inlineMarkdownText(text)
                .font(headingFont(for: level))
                .fontWeight(.bold)
                .foregroundColor(.white)
                .padding(.top, level == 1 ? 6 : 4)
                .padding(.bottom, 1)

        case .codeBlock(let language, let code):
            CodeBlockCard(language: language, code: code, fontSize: max(11.5, baseFontSize - 2))

        case .blockquote(let text):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(Color.cyan.opacity(0.65))
                    .frame(width: 3)

                inlineMarkdownText(text)
                    .font(.system(size: max(12.0, baseFontSize - 0.5)))
                    .foregroundColor(.white.opacity(0.82))
                    .lineSpacing(3)
            }
            .padding(.vertical, 2)

        case .bulletItem(let marker, let text):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(marker)
                    .font(.system(size: baseFontSize, weight: .semibold))
                    .foregroundColor(.cyan.opacity(0.9))
                    .frame(minWidth: marker == "•" ? 10 : 18, alignment: .leading)

                inlineMarkdownText(text)
                    .font(.system(size: baseFontSize))
                    .foregroundColor(.white.opacity(0.94))
                    .lineSpacing(3.5)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

        case .table(let headers, let rows):
            MarkdownTableView(headers: headers, rows: rows, fontSize: max(11.5, baseFontSize - 2))

        case .divider:
            Divider()
                .background(Color.white.opacity(0.18))
                .padding(.vertical, 4)

        case .paragraph(let text):
            inlineMarkdownText(text)
                .font(.system(size: baseFontSize))
                .foregroundColor(.white.opacity(0.94))
                .lineSpacing(3.5)
        }
    }

    private func headingFont(for level: Int) -> Font {
        switch level {
        case 1: return .system(size: baseFontSize + 3.0)
        case 2: return .system(size: baseFontSize + 1.5)
        default: return .system(size: baseFontSize + 0.5)
        }
    }

    private func inlineMarkdownText(_ raw: String) -> Text {
        if let attr = try? AttributedString(
            markdown: raw,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace
            )
        ) {
            return Text(attr)
        }
        return Text(raw)
    }

    // MARK: - Block Parser
    private func parseBlocks(_ source: String) -> [Block] {
        let normalized = source
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        let lines = normalized.components(separatedBy: "\n")
        var blocks: [Block] = []
        var paragraphBuffer: [String] = []
        var i = 0

        func flushParagraph() {
            guard !paragraphBuffer.isEmpty else { return }
            let joined = paragraphBuffer.joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty {
                blocks.append(.paragraph(text: joined))
            }
            paragraphBuffer.removeAll()
        }

        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // 1. 圍欄程式碼區塊 (```lang ... ```)
            if trimmed.hasPrefix("```") {
                flushParagraph()
                let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                var codeLines: [String] = []
                i += 1
                while i < lines.count {
                    let inner = lines[i]
                    if inner.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                        i += 1
                        break
                    }
                    codeLines.append(inner)
                    i += 1
                }
                blocks.append(.codeBlock(language: lang, code: codeLines.joined(separator: "\n")))
                continue
            }

            // 2. 空行
            if trimmed.isEmpty {
                flushParagraph()
                i += 1
                continue
            }

            // 3. 分隔線
            if isHorizontalRule(trimmed) {
                flushParagraph()
                blocks.append(.divider)
                i += 1
                continue
            }

            // 4. 標題 (# / ## / ###)
            if let (lvl, title) = parseHeading(trimmed) {
                flushParagraph()
                blocks.append(.heading(level: lvl, text: title))
                i += 1
                continue
            }

            // 5. 表格 (| ... | 且下一行是 | --- |)
            if isTableRow(trimmed), i + 1 < lines.count, isTableSeparator(lines[i + 1]) {
                flushParagraph()
                let headers = splitTableCells(trimmed)
                i += 2
                var rows: [[String]] = []
                while i < lines.count {
                    let rowTrimmed = lines[i].trimmingCharacters(in: .whitespaces)
                    if isTableRow(rowTrimmed) {
                        rows.append(splitTableCells(rowTrimmed))
                        i += 1
                    } else {
                        break
                    }
                }
                blocks.append(.table(headers: headers, rows: rows))
                continue
            }

            // 6. 引言 (> ...)
            if trimmed.hasPrefix(">") {
                flushParagraph()
                var quoteLines: [String] = []
                while i < lines.count {
                    let qt = lines[i].trimmingCharacters(in: .whitespaces)
                    if qt.hasPrefix(">") {
                        let stripped = String(qt.dropFirst()).trimmingCharacters(in: .whitespaces)
                        quoteLines.append(stripped)
                        i += 1
                    } else {
                        break
                    }
                }
                blocks.append(.blockquote(text: quoteLines.joined(separator: "\n")))
                continue
            }

            // 7. 無序 / 有序清單
            if let (marker, itemText) = parseListItem(trimmed) {
                flushParagraph()
                blocks.append(.bulletItem(marker: marker, text: itemText))
                i += 1
                continue
            }

            // 8. 一般段落行
            paragraphBuffer.append(line)
            i += 1
        }

        flushParagraph()
        return blocks
    }

    private func isHorizontalRule(_ trimmed: String) -> Bool {
        let stripped = trimmed.replacingOccurrences(of: " ", with: "")
        guard stripped.count >= 3 else { return false }
        return stripped.allSatisfy { $0 == "-" }
            || stripped.allSatisfy { $0 == "*" }
            || stripped.allSatisfy { $0 == "_" }
    }

    private func parseHeading(_ trimmed: String) -> (Int, String)? {
        var count = 0
        for ch in trimmed {
            if ch == "#" { count += 1 } else { break }
        }
        guard (1...4).contains(count) else { return nil }
        let rest = trimmed.dropFirst(count)
        guard rest.first == " " else { return nil }
        let title = String(rest).trimmingCharacters(in: .whitespaces)
        return title.isEmpty ? nil : (count, title)
    }

    private func parseListItem(_ trimmed: String) -> (String, String)? {
        for prefix in ["- ", "* ", "+ "] {
            if trimmed.hasPrefix(prefix) {
                return ("•", String(trimmed.dropFirst(prefix.count)))
            }
        }
        if let dotRange = trimmed.range(of: ". "), dotRange.lowerBound > trimmed.startIndex {
            let numPart = trimmed[..<dotRange.lowerBound]
            if numPart.allSatisfy(\.isNumber) {
                return ("\(numPart).", String(trimmed[dotRange.upperBound...]))
            }
        }
        return nil
    }

    private func isTableRow(_ trimmed: String) -> Bool {
        trimmed.hasPrefix("|") && trimmed.contains("|") && trimmed.count >= 3
    }

    private func isTableSeparator(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.contains("|") && trimmed.contains("-") else { return false }
        let allowed = CharacterSet(charactersIn: "|-: ")
        return trimmed.unicodeScalars.allSatisfy { allowed.contains($0) }
    }

    private func splitTableCells(_ trimmed: String) -> [String] {
        var s = trimmed
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") { s.removeLast() }
        return s.components(separatedBy: "|").map {
            $0.trimmingCharacters(in: .whitespaces)
        }
    }
}

// MARK: - 程式碼區塊卡片（支援語言標籤與一鍵複製）
private struct CodeBlockCard: View {
    let language: String
    let code: String
    let fontSize: Double
    @State private var copied: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "code" : language)
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundColor(Color(white: 0.65))

                Spacer()

                Button(action: {
                    UIPasteboard.general.string = code
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    copied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                        copied = false
                    }
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: copied ? "checkmark" : "doc.on.doc")
                            .font(.caption2)
                        Text(copied ? "已複製" : "複製")
                            .font(.caption2)
                            .fontWeight(.medium)
                    }
                    .foregroundColor(copied ? .green : Color(white: 0.78))
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(Color.white.opacity(0.06))

            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(size: fontSize, design: .monospaced))
                    .foregroundColor(Color(white: 0.92))
                    .textSelection(.enabled)
                    .padding(12)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(red: 0.08, green: 0.09, blue: 0.12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(Color.white.opacity(0.12), lineWidth: 1)
                )
        )
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

// MARK: - 表格元件
private struct MarkdownTableView: View {
    let headers: [String]
    let rows: [[String]]
    let fontSize: Double

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 0) {
                    ForEach(Array(headers.enumerated()), id: \.offset) { _, header in
                        Text(header)
                            .font(.system(size: fontSize, weight: .bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 7)
                            .frame(minWidth: 84, alignment: .leading)
                    }
                }
                .background(Color.white.opacity(0.08))

                ForEach(Array(rows.enumerated()), id: \.offset) { rIdx, row in
                    Divider().background(Color.white.opacity(0.1))
                    HStack(spacing: 0) {
                        ForEach(0..<headers.count, id: \.self) { cIdx in
                            let cell = cIdx < row.count ? row[cIdx] : ""
                            Text(cell)
                                .font(.system(size: fontSize))
                                .foregroundColor(.white.opacity(0.88))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .frame(minWidth: 84, alignment: .leading)
                        }
                    }
                    .background(rIdx % 2 == 1 ? Color.white.opacity(0.025) : Color.clear)
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color.white.opacity(0.03))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .stroke(Color.white.opacity(0.12), lineWidth: 1)
                    )
            )
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
    }
}
