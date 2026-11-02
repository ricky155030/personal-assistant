import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import settings

class MemoryManager:
    """
    秘書級模組化記憶庫管理器
    以 memory/ 目錄下的多個專責 Markdown 模組作為 Source of Truth：
      - profile.md: 個人檔案、身分定位與溝通模式
      - preferences.md: 飲食生活、系統與工具偏好
      - projects.md: 專案目標與日曆委派原則
      - notes.md: 重大里程碑與備忘筆記
    日常待辦事項則動態委派至 Google Calendar「待辦事項」。
    同時自動編譯聚合為 MEMORY.md 以相容全系統一次性閱讀需求。
    """

    def __init__(self, memory_dir: Path = settings.MEMORY_DIR, memory_md_path: Path = settings.MEMORY_MD_PATH):
        self.memory_dir = memory_dir
        self.memory_md_path = memory_md_path
        self.agent_md_path = memory_md_path  # 兼容別名
        self.profile_path = self.memory_dir / "profile.md"
        self.preferences_path = self.memory_dir / "preferences.md"
        self.projects_path = self.memory_dir / "projects.md"
        self.notes_path = self.memory_dir / "notes.md"

        self._ensure_files_exist()
        self.sync_to_memory_md()

    def _ensure_files_exist(self):
        """確保 memory 目錄與基礎模組檔案齊全"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.profile_path.exists():
            self.profile_path.write_text(
                "# 主人個人檔案與核心身分 (`memory/profile.md`)\n\n"
                "## 1. 核心身分 (Agent Identity)\n"
                "- **身分**：住在 Mac mini 上的專屬個人 AI 秘書與助理。\n"
                "- **主人**：<您的稱呼>\n"
                "- **溝通端點**：主要透過 Telegram Bot、Apple Watch（語音互動）、iPhone 與 Mac mini。\n\n"
                "## 2. 關於主人 (User Profile)\n"
                "- **稱呼**：<您的稱呼>\n"
                "- **所在地**：台灣 / 台北 (時區: Asia/Taipei)\n",
                encoding="utf-8"
            )

        if not self.preferences_path.exists():
            self.preferences_path.write_text(
                "# 個人偏好與生活習慣 (`memory/preferences.md`)\n\n"
                "## 1. 飲食與生活習慣\n"
                "- **咖啡**：喜歡喝冰美式咖啡。\n\n"
                "## 2. 工具與架構偏好\n"
                "- **系統架構**：傾向輕量、自動化、由 Mac mini 本機主導掌控的私有化架構。\n",
                encoding="utf-8"
            )

        if not self.projects_path.exists():
            self.projects_path.write_text(
                "# 專案目標與持續追蹤 (`memory/projects.md`)\n\n"
                "## 1. 核心專案進度 (Core Projects)\n\n"
                "## 2. 待辦與排程原則\n"
                "- 所有日程與任務一律委派寫入 Google Calendar「待辦事項」日曆。\n",
                encoding="utf-8"
            )

        if not self.notes_path.exists():
            self.notes_path.write_text(
                "# 備忘筆記與重要記憶 (`memory/notes.md`)\n\n"
                "## 重要事件與記憶列表\n",
                encoding="utf-8"
            )

    def _backup(self, target_path: Path):
        """修改前自動建立備份"""
        if target_path.exists():
            bak_path = target_path.with_suffix(".md.bak")
            shutil.copy2(target_path, bak_path)

    def compile_memory(self) -> str:
        """依序聚合各模組內容為完整的全域記憶文本"""
        standard_names = ["profile.md", "preferences.md", "homeassistant.md", "projects.md", "notes.md"]
        ordered_files = []
        for name in standard_names:
            p = self.memory_dir / name
            if p.exists():
                ordered_files.append((name, p))

        # 動態載入其餘自訂模組
        seen_names = set(standard_names)
        if self.memory_dir.exists():
            for p in sorted(self.memory_dir.glob("*.md")):
                if p.name not in seen_names and not p.name.startswith("."):
                    ordered_files.append((p.name, p))
        
        banner = (
            "# Personal Assistant Master Memory (`MEMORY.md`)\n\n"
            "> 此檔案為 Mac mini 個人助理的完整聚合記憶庫（由 `memory/*.md` 自動同步生成）。\n"
            "> 模組化子記憶位於 `memory/` 目錄，具體日程與待辦事項由 Google Calendar「待辦事項」日曆維護。\n\n"
            "---\n\n"
        )
        
        sections = []
        for filename, filepath in ordered_files:
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8").strip()
                if content:
                    sections.append(content)
        
        return banner + "\n\n---\n\n".join(sections) + "\n"

    def sync_to_memory_md(self):
        """將編譯後的記憶同步至 MEMORY.md，確保向下相容與全域載入"""
        try:
            self._backup(self.memory_md_path)
            full_content = self.compile_memory()
            self.memory_md_path.write_text(full_content, encoding="utf-8")
        except Exception as e:
            self._log_audit("error", {"action": "sync_to_memory_md", "error": str(e)})

    def sync_to_agent_md(self):
        """相容別名"""
        self.sync_to_memory_md()

    def read_memory(self) -> str:
        """讀取聚合後的完整記憶"""
        if not self.memory_md_path.exists():
            self.sync_to_memory_md()
        return self.memory_md_path.read_text(encoding="utf-8")

    def read_module(self, name: str) -> Optional[str]:
        """讀取單一模組內容 (例如 'preferences', 'profile', 'notes')"""
        target = self.memory_dir / f"{name}.md"
        if target.exists():
            return target.read_text(encoding="utf-8")
        return None

    def update_preference(self, category: str, detail: str) -> str:
        """更新主人偏好設定至 memory/preferences.md"""
        self._backup(self.preferences_path)
        content = self.preferences_path.read_text(encoding="utf-8")
        
        new_entry = f"- **{category}**：{detail}"
        
        # 若已有該分類項目則進行替換，否則追加至適當區段
        lines = content.splitlines()
        replaced = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f"- **{category}**："):
                new_lines.append(new_entry)
                replaced = True
            else:
                new_lines.append(line)
        
        if not replaced:
            new_lines.append(new_entry)
        
        updated_content = "\n".join(new_lines) + "\n"
        self.preferences_path.write_text(updated_content, encoding="utf-8")
        
        # 同步回 MEMORY.md
        self.sync_to_memory_md()
        self._log_audit("preference", {"category": category, "detail": detail})
        return f"已記錄偏好至 memory/preferences.md：[{category}] {detail}"

    def add_memory_note(self, note: str) -> str:
        """追加一條備忘筆記至 memory/notes.md"""
        self._backup(self.notes_path)
        content = self.notes_path.read_text(encoding="utf-8").rstrip()
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_entry = f"- **{now_str}**：{note}"
        
        updated_content = content + f"\n{new_entry}\n"
        self.notes_path.write_text(updated_content, encoding="utf-8")
        
        self.sync_to_memory_md()
        self._log_audit("note", {"note": note})
        return f"已添加備忘至 memory/notes.md：{note}"

    def record_demand(self, title: str, detail: str = "", status: str = "進行中") -> str:
        """記錄重要專案需求或追蹤項目至 memory/projects.md"""
        self._backup(self.projects_path)
        now_str = datetime.now().strftime("%Y-%m-%d")
        desc = f" - {detail}" if detail else ""
        new_entry = f"- [{status}] [{now_str}] {title}{desc}"
        
        content = self.projects_path.read_text(encoding="utf-8").rstrip()
        updated_content = content + f"\n{new_entry}\n"
        self.projects_path.write_text(updated_content, encoding="utf-8")
        
        # 同步寫入 jsonl
        demand_record = {
            "timestamp": datetime.now().isoformat(),
            "title": title,
            "detail": detail,
            "status": status,
        }
        with open(settings.DEMANDS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(demand_record, ensure_ascii=False) + "\n")
            
        self.sync_to_memory_md()
        self._log_audit("demand", demand_record)
        return f"已記錄專案項目至 memory/projects.md：[{status}] {title}"

    def log_interaction(self, query: str, reply: str, actions: List[str] = None):
        """記錄每筆語音對話紀錄與執行的動作"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "reply": reply,
            "actions": actions or [],
        }
        with open(settings.HISTORY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _log_audit(self, event_type: str, data: Dict[str, Any]):
        audit_file = settings.DATA_DIR / "audit.jsonl"
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "data": data,
        }
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

memory_manager = MemoryManager()
