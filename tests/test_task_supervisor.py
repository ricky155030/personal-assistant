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


def test_patrol_kills_stale_heartbeat_task(tmp_path):
    import os
    import time as time_mod

    stale_log = tmp_path / "stale.log"
    stale_log.write_text("old heartbeat\n", encoding="utf-8")
    old_ts = time_mod.time() - 3600  # 60 分鐘前
    os.utime(stale_log, (old_ts, old_ts))

    fake_tasks = [
        {
            "task_id": "task_stale_1",
            "name": "Stale Watchdog",
            "pid": 43210,
            "status": "RUNNING",
            "log_file": str(stale_log),
            "heartbeat_timeout_minutes": 15,
        }
    ]
    mock_mgr = MagicMock()
    mock_mgr.list_tasks.return_value = fake_tasks

    with patch.object(ts, "task_manager", mock_mgr), \
         patch("telegram_service.send_telegram_sync") as mock_tg:
        report = ts.patrol_tasks_once()

    assert report["heartbeat_killed"] == ["task_stale_1"]
    assert fake_tasks[0]["status"] == "HEARTBEAT_TIMEOUT"
    mock_mgr._terminate_process.assert_called_once_with(43210)
    mock_mgr._save_tasks.assert_called_once()
    mock_tg.assert_called_once()


def test_try_inject_memory_sync_uses_primary_worker():
    import agy_singleton as agy_mod

    fake_worker = MagicMock()
    fake_worker.lock.acquire.return_value = True
    fake_worker._is_ready = True
    fake_worker.proc = MagicMock()
    fake_worker.proc.poll.return_value = None
    fake_worker.send_query_stream.return_value = iter([("text", "CI 已綠燈通過")])

    fake_pool = MagicMock(spec=["primary_worker"])
    fake_pool.primary_worker = fake_worker

    with patch.object(agy_mod, "agy_singleton", fake_pool), \
         patch("memory_manager.memory_manager.log_interaction") as mock_log:
        ok, reason = ts.try_inject_memory_sync("CI 輸出", "task:ci_99", report_to_user=True)

    assert ok is True
    assert reason == "ok"
    fake_worker.send_query_stream.assert_called_once()
    mock_log.assert_called_once()


def test_try_inject_memory_sync_auto_heals_on_error_with_budget_cap():
    import agy_singleton as agy_mod

    with ts._AUTO_HEAL_LOCK:
        ts._AUTO_HEAL_HISTORY.clear()

    fake_worker = MagicMock()
    fake_worker.lock.acquire.return_value = True
    fake_worker._is_ready = True
    fake_worker.proc = MagicMock()
    fake_worker.proc.poll.return_value = None
    fake_worker.send_query_stream.side_effect = [
        iter([("text", "已修復語法錯誤並通過驗證")]),
        iter([("text", "第二次修復嘗試完成")]),
        iter([("text", "已達上限，請人工介入")]),
    ]

    fake_pool = MagicMock(spec=["primary_worker"])
    fake_pool.primary_worker = fake_worker

    err_meta = {"task_name": "CI 監控", "task_status": "error", "exit_code": 1}

    with patch.object(agy_mod, "agy_singleton", fake_pool), \
         patch("memory_manager.memory_manager.log_interaction"):
        for _ in range(3):
            ok, reason = ts.try_inject_memory_sync(
                "CI 失敗",
                "task:ci_fail",
                report_to_user=True,
                metadata=err_meta,
            )
            assert ok is True
            assert reason == "ok"

    prompts = [call.args[0] for call in fake_worker.send_query_stream.call_args_list]
    assert "失敗自癒喚醒" in prompts[0] and "第 1/2 次嘗試" in prompts[0]
    assert "請勿呼叫任何工具" not in prompts[0]
    assert "失敗自癒喚醒" in prompts[1] and "第 2/2 次嘗試" in prompts[1]
    assert "已達自動修復上限" in prompts[2] and "請勿呼叫任何工具" in prompts[2]

    # 成功後應重設該任務的自動修復額度
    with patch.object(agy_mod, "agy_singleton", fake_pool), \
         patch("memory_manager.memory_manager.log_interaction"):
        fake_worker.send_query_stream.side_effect = None
        fake_worker.send_query_stream.return_value = iter([("text", "CI 綠燈通過")])
        ts.try_inject_memory_sync(
            "CI 成功",
            "task:ci_ok",
            report_to_user=True,
            metadata={"task_name": "CI 監控", "task_status": "ok", "exit_code": 0},
        )
    assert "CI 監控" not in ts._AUTO_HEAL_HISTORY
