import SwiftUI

/// 主畫面快捷指令抽屜：支援直接點擊發送、新增、編輯、刪除、拖曳排序與重置預設。
/// 所有變更即時持久化至 UserDefaults，免重新編譯 App。
struct QuickPromptDrawerView: View {
    @Binding var presets: [QuickPromptPreset]
    var onSelectPrompt: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var showEditorSheet: Bool = false
    @State private var editingPreset: QuickPromptPreset? = nil

    var body: some View {
        NavigationStack {
            List {
                Section("快捷指令（點擊直接發送 · 點左上角編輯可拖曳排序）") {
                    ForEach(presets) { preset in
                        Button {
                            onSelectPrompt(preset.prompt)
                            dismiss()
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: preset.icon)
                                    .font(.system(size: 16, weight: .bold))
                                    .foregroundColor(.cyan)
                                    .frame(width: 28)

                                VStack(alignment: .leading, spacing: 3) {
                                    Text(preset.title)
                                        .font(.system(size: 15, weight: .semibold))
                                        .foregroundColor(.primary)
                                    Text(preset.prompt)
                                        .font(.system(size: 12))
                                        .foregroundColor(.secondary)
                                        .lineLimit(2)
                                }

                                Spacer()

                                Button {
                                    editingPreset = preset
                                    showEditorSheet = true
                                } label: {
                                    Image(systemName: "pencil.circle.fill")
                                        .font(.system(size: 18))
                                        .foregroundColor(.secondary)
                                }
                                .buttonStyle(.plain)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    .onDelete { indexSet in
                        presets.remove(atOffsets: indexSet)
                        QuickPromptStore.savePresets(presets)
                    }
                    .onMove { indices, newOffset in
                        presets.move(fromOffsets: indices, toOffset: newOffset)
                        QuickPromptStore.savePresets(presets)
                    }
                }

                Section {
                    Button {
                        editingPreset = nil
                        showEditorSheet = true
                    } label: {
                        Label("新增自訂快捷指令", systemImage: "plus.circle.fill")
                            .foregroundColor(.cyan)
                    }

                    Button("重置為預設快捷指令") {
                        presets = QuickPromptPreset.defaultPresets
                        QuickPromptStore.savePresets(presets)
                    }
                    .foregroundColor(.red)
                }
            }
            .navigationTitle("常用指令")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    EditButton()
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .sheet(isPresented: $showEditorSheet) {
                QuickPromptEditorSheet(existing: editingPreset) { saved in
                    if let idx = presets.firstIndex(where: { $0.id == saved.id }) {
                        presets[idx] = saved
                    } else {
                        presets.append(saved)
                    }
                    QuickPromptStore.savePresets(presets)
                }
            }
        }
    }
}

private struct QuickPromptEditorSheet: View {
    let existing: QuickPromptPreset?
    var onSave: (QuickPromptPreset) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var title: String = ""
    @State private var prompt: String = ""
    @State private var selectedIcon: String = "sparkles"

    private let iconOptions = [
        "sparkles", "tag.fill", "text.magnifyingglass", "cart.fill",
        "calendar.badge.clock", "house.fill", "chart.line.uptrend.xyaxis",
        "bell.fill", "doc.text.fill", "star.fill", "wand.and.stars", "bolt.fill"
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("按鈕名稱") {
                    TextField("例如：台積電股價", text: $title)
                }

                Section("發送的 Prompt 內容") {
                    TextEditor(text: $prompt)
                        .frame(minHeight: 96)
                }

                Section("圖示") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 6), spacing: 12) {
                        ForEach(iconOptions, id: \.self) { icon in
                            Button {
                                selectedIcon = icon
                            } label: {
                                Image(systemName: icon)
                                    .font(.system(size: 18, weight: .semibold))
                                    .frame(width: 40, height: 40)
                                    .background(
                                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                                            .fill(selectedIcon == icon ? Color.cyan.opacity(0.25) : Color.secondary.opacity(0.12))
                                    )
                                    .foregroundColor(selectedIcon == icon ? .cyan : .primary)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle(existing == nil ? "新增快捷指令" : "編輯快捷指令")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("儲存") {
                        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
                        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !cleanTitle.isEmpty, !cleanPrompt.isEmpty else { return }
                        let item = QuickPromptPreset(
                            id: existing?.id ?? UUID().uuidString,
                            title: cleanTitle,
                            icon: selectedIcon,
                            prompt: cleanPrompt,
                            isBuiltIn: existing?.isBuiltIn ?? false
                        )
                        onSave(item)
                        dismiss()
                    }
                    .disabled(
                        title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                        prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
            }
            .onAppear {
                if let existing {
                    title = existing.title
                    prompt = existing.prompt
                    selectedIcon = existing.icon
                }
            }
        }
    }
}
