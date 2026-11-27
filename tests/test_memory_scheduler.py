import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import memory_scheduler
from memory_manager import MemoryManager


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)


def test_sync_skips_when_memory_dir_is_not_private_repo(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_md = tmp_path / "MEMORY.md"
    mm = MemoryManager(memory_dir=mem_dir, memory_md_path=mem_md)

    with patch.object(memory_scheduler.settings, "MEMORY_DIR", mem_dir), \
         patch.object(memory_scheduler, "memory_manager", mm), \
         patch.object(memory_scheduler, "send_telegram_sync") as mock_tg:
        res = memory_scheduler.sync_and_commit_memory(silent_if_no_changes=False)

    assert res["status"] == "skipped"
    assert mem_md.exists()
    mock_tg.assert_not_called()


def test_sync_commits_inside_private_memory_repo(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_md = tmp_path / "MEMORY.md"
    mm = MemoryManager(memory_dir=mem_dir, memory_md_path=mem_md)
    _init_git_repo(mem_dir)
    subprocess.run(["git", "add", "-A"], cwd=mem_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=mem_dir, check=True, capture_output=True)

    mm.add_memory_note("測試私有倉庫歸檔")

    with patch.object(memory_scheduler.settings, "MEMORY_DIR", mem_dir), \
         patch.object(memory_scheduler, "memory_manager", mm), \
         patch.object(
             memory_scheduler,
             "audit_diff_with_llm",
             return_value={"summary": "• 新增備忘", "commit_message": "docs(memory): add test note"},
         ), \
         patch.object(memory_scheduler, "send_telegram_sync") as mock_tg, \
         patch.object(memory_scheduler, "_send_app_notification", return_value=True) as mock_app:
        res = memory_scheduler.sync_and_commit_memory(silent_if_no_changes=False)

    assert res["status"] == "committed"
    assert res["pushed"] is False
    assert res["commit_hash"]
    mock_tg.assert_called_once()
    mock_app.assert_called_once()

    log_out = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=mem_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log_out == "docs(memory): add test note"


def test_distill_daily_conversations_extracts_and_deduplicates(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_md = tmp_path / "MEMORY.md"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    history_path = data_dir / "history.jsonl"
    demands_path = data_dir / "demands.jsonl"

    now = datetime.now()
    old = now - timedelta(hours=30)
    records = [
        {"timestamp": old.isoformat(), "query": "舊對話", "reply": "不該被讀取", "actions": []},
        {"timestamp": now.isoformat(), "query": "/sync_memory", "reply": "指令", "actions": []},
        {"timestamp": now.isoformat(), "query": "中斷", "reply": "err", "actions": ["interrupted_error"]},
        {"timestamp": now.isoformat(), "query": "以後報表請都用深色主題", "reply": "好的，之後報表都改用深色主題。", "actions": []},
    ]
    history_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    mm = MemoryManager(memory_dir=mem_dir, memory_md_path=mem_md)
    fake_extracted = {
        "preferences": [{"category": "報表風格", "detail": "偏好深色主題"}],
        "projects": [{"title": "私有記憶倉庫", "detail": "獨立版控", "status": "進行中"}],
        "notes": ["每晚 23:30 自動沉澱對話"],
    }

    with patch.object(memory_scheduler.settings, "HISTORY_LOG_PATH", history_path), \
         patch.object(memory_scheduler.settings, "DATA_DIR", data_dir), \
         patch.object(memory_scheduler.settings, "DEMANDS_LOG_PATH", demands_path), \
         patch.object(memory_scheduler, "memory_manager", mm), \
         patch.object(memory_scheduler, "extract_memories_with_llm", return_value=fake_extracted) as mock_llm, \
         patch.object(memory_scheduler, "send_telegram_sync") as mock_tg, \
         patch.object(memory_scheduler, "_send_app_notification", return_value=True) as mock_app:
        res1 = memory_scheduler.distill_daily_conversations(hours=24, notify=True)
        res2 = memory_scheduler.distill_daily_conversations(hours=24, notify=True)

    assert res1["status"] == "distilled"
    assert res1["count"] == 3
    assert len(mock_llm.call_args_list[0].args[0]) == 1
    assert mock_tg.call_count == 1
    assert mock_app.call_count == 1
    assert res2["status"] == "no_new_memories"
    assert "報表風格" in (mem_dir / "preferences.md").read_text(encoding="utf-8")
    assert "私有記憶倉庫" in (mem_dir / "projects.md").read_text(encoding="utf-8")
    assert "每晚 23:30 自動沉澱對話" in (mem_dir / "notes.md").read_text(encoding="utf-8")
    assert "報表風格" in mem_md.read_text(encoding="utf-8")


def test_distill_and_sync_memory_commits_before_unified_notify(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_md = tmp_path / "MEMORY.md"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    history_path = data_dir / "history.jsonl"
    demands_path = data_dir / "demands.jsonl"

    mm = MemoryManager(memory_dir=mem_dir, memory_md_path=mem_md)
    _init_git_repo(mem_dir)
    subprocess.run(["git", "add", "-A"], cwd=mem_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=mem_dir, check=True, capture_output=True)

    records = [
        {"timestamp": "2026-11-27T10:00:00", "query": "記住我喜歡用深色介面", "reply": "已記錄", "actions": []},
    ]
    history_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    fake_extracted = {
        "preferences": [],
        "projects": [],
        "notes": ["2026-11-27 測試沉澱備忘"],
    }

    with patch.object(memory_scheduler.settings, "MEMORY_DIR", mem_dir), \
         patch.object(memory_scheduler.settings, "HISTORY_LOG_PATH", history_path), \
         patch.object(memory_scheduler.settings, "DATA_DIR", data_dir), \
         patch.object(memory_scheduler.settings, "DEMANDS_LOG_PATH", demands_path), \
         patch.object(memory_scheduler, "memory_manager", mm), \
         patch.object(memory_scheduler, "extract_memories_with_llm", return_value=fake_extracted), \
         patch.object(
             memory_scheduler,
             "audit_diff_with_llm",
             return_value={"summary": "• 沉澱備忘", "commit_message": "docs(memory): distill 2026-11-27"},
         ), \
         patch.object(memory_scheduler, "send_telegram_sync") as mock_tg, \
         patch.object(memory_scheduler, "_send_app_notification", return_value=True) as mock_app:
        res = memory_scheduler.distill_and_sync_memory(date_filter="2026-11-27", notify=True)

    assert res["distill"]["status"] == "distilled"
    assert res["sync"]["status"] == "committed"
    mock_tg.assert_called_once()
    mock_app.assert_called_once()
    assert res["sync"]["commit_hash"] in mock_app.call_args.kwargs["message"]
