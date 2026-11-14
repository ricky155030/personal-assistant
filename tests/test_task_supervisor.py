import json
import os
from unittest.mock import MagicMock, patch

import task_manager as tm
import task_supervisor as ts


def test_list_tasks_does_not_wipe_corrupt_json(tmp_path):
    tasks_file = tmp_path / "background_tasks.json"
    tasks_file.write_text("{corrupt json", encoding="utf-8")

    mgr = tm.TaskManager.__new__(tm.TaskManager)
    with patch.object(tm, "TASKS_JSON_PATH", tasks_file), \
         patch.object(tm, "TASK_LOGS_DIR", tmp_path / "logs"):
        result = mgr.list_tasks(include_finished=True)

    assert result == []
    # 原檔內容不可被覆寫洗成 []
    assert tasks_file.read_text(encoding="utf-8") == "{corrupt json"


def test_adopt_task_registers_live_pid(tmp_path):
    tasks_file = tmp_path / "background_tasks.json"
    tasks_file.write_text("[]", encoding="utf-8")

    mgr = tm.TaskManager.__new__(tm.TaskManager)
    with patch.object(tm, "TASKS_JSON_PATH", tasks_file), \
         patch.object(tm, "TASK_LOGS_DIR", tmp_path / "logs"):
        res1 = mgr.adopt_task(
            task_id="task_orphan_1",
            pid=os.getpid(),
            name="Orphan Monitor",
            script="scripts/demo.py",
            log_file="data/task_logs/task_orphan_1.log",
            started_at="2026-11-14 20:00:00",
            ttl_minutes=10080,
        )
        res2 = mgr.adopt_task(
            task_id="task_orphan_1",
            pid=os.getpid(),
            name="Orphan Monitor",
            script="scripts/demo.py",
            log_file="data/task_logs/task_orphan_1.log",
            started_at="2026-11-14 20:00:00",
            ttl_minutes=10080,
        )

    assert res1["success"] is True
    assert res2.get("already_registered") is True
    saved = json.loads(tasks_file.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["task_id"] == "task_orphan_1"
    assert saved[0]["status"] == "RUNNING"


def test_memory_sync_queue_retries_after_busy():
    with ts._MEMO_QUEUE_LOCK:
        ts._PENDING_MEMOS.clear()

    ts.enqueue_memory_sync("CI 綠燈", "task:ci_1")
    assert ts.get_pending_memo_count() == 1

    with patch.object(ts, "try_inject_memory_sync", return_value=(False, "brain_busy")):
        flushed = ts.flush_memory_sync_queue()
    assert flushed == 0
    assert ts.get_pending_memo_count() == 1

    with patch.object(ts, "try_inject_memory_sync", return_value=(True, "ok")):
        flushed = ts.flush_memory_sync_queue()
    assert flushed == 1
    assert ts.get_pending_memo_count() == 0


def test_patrol_tasks_restarts_only_completed_auto_restart():
    ts._LAST_RESTART_ATTEMPT.clear()
    fake_tasks = [
        {
            "task_id": "task_watch_1",
            "name": "Watch 1",
            "status": "COMPLETED",
            "auto_restart": True,
            "restart_spec": {
                "kind": "spawn",
                "name": "Watch 1",
                "script": "scripts/watch1.py",
                "args": ["--interval", "60"],
                "ttl_minutes": 10080,
            },
        },
        {
            "task_id": "task_killed_2",
            "name": "Killed 2",
            "status": "KILLED",
            "auto_restart": True,
            "restart_spec": {"kind": "spawn", "script": "scripts/watch2.py"},
        },
    ]
    mock_mgr = MagicMock()
    mock_mgr.list_tasks.return_value = fake_tasks
    mock_mgr.spawn.return_value = {"success": True, "task_id": "task_watch_1_new", "pid": 99999}

    with patch.object(ts, "task_manager", mock_mgr), \
         patch("telegram_service.send_telegram_sync"):
        report = ts.patrol_tasks_once()

    assert report["restarted"] == ["task_watch_1_new"]
    mock_mgr.spawn.assert_called_once()
    assert fake_tasks[0]["status"] == "RESTARTED"
    assert fake_tasks[0]["auto_restart"] is False
