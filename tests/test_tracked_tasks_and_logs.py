import json
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import task_manager as tm


def test_list_tasks_records_only_failed_tasks_and_truncates_oversized_logs(tmp_path):
    tasks_file = tmp_path / "background_tasks.json"
    logs_dir = tmp_path / "task_logs"
    logs_dir.mkdir(parents=True)

    # 1. 建立正常與失敗任務記錄
    tasks_file.write_text(
        json.dumps([
            {
                "task_id": "task_ok",
                "pid": 999991,
                "name": "OK Task",
                "status": "COMPLETED",
                "started_at": "2026-11-15 20:00:00",
                "log_file": "data/task_logs/task_ok.log",
            },
            {
                "task_id": "task_fail",
                "pid": 999992,
                "name": "Fail Task",
                "status": "HEARTBEAT_TIMEOUT",
                "started_at": "2026-11-15 20:05:00",
                "log_file": "data/task_logs/task_fail.log",
            },
        ]),
        encoding="utf-8",
    )

    # 2. 建立超大 log 檔案與過期 (>30天) log 檔案
    big_log = logs_dir / "task_fail.log"
    big_log.write_text(("log line payload 1234567890\n" * 50000), encoding="utf-8")
    old_log = logs_dir / "task_ancient.log"
    old_log.write_text("very old log\n", encoding="utf-8")
    old_mtime = time.time() - (35 * 86400)
    os.utime(old_log, (old_mtime, old_mtime))

    mgr = tm.TaskManager()
    with patch.object(tm, "TASKS_JSON_PATH", tasks_file), \
         patch.object(tm, "TASK_LOGS_DIR", logs_dir):
        mgr.list_tasks(include_finished=True)
        mgr._clean_old_logs(days=30, max_file_bytes=200 * 1024)
        failed = mgr.list_failed_tasks()
        log_res = mgr.get_task_log("task_fail", tail_lines=20)

    assert len(failed) == 1
    assert failed[0]["task_id"] == "task_fail"
    assert not old_log.exists()
    assert big_log.stat().st_size <= 600 * 1024
    assert log_res["exists"] is True
    assert log_res["tail_lines"] == 20


def test_tracked_tasks_and_log_endpoints(tmp_path):
    tasks_file = tmp_path / "background_tasks.json"
    logs_dir = tmp_path / "task_logs"
    logs_dir.mkdir(parents=True)
    tasks_file.write_text("[]", encoding="utf-8")
    (tmp_path / "failed_tasks.json").write_text(
        json.dumps([{"task_id": "task_err_1", "name": "Err 1", "status": "FAILED", "started_at": "2026-11-15 21:00:00"}]),
        encoding="utf-8",
    )
    (logs_dir / "task_err_1.log").write_text("line1\nERROR boom\n", encoding="utf-8")

    client = TestClient(main.app)
    with patch.object(tm, "TASKS_JSON_PATH", tasks_file), \
         patch.object(tm, "TASK_LOGS_DIR", logs_dir), \
         patch("main.settings.API_KEY", ""):
        resp = client.get("/api/tasks/tracked")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] == []
        assert len(body["failed"]) == 1
        assert body["failed"][0]["task_id"] == "task_err_1"

        log_resp = client.get("/api/tasks/task_err_1/log?tail=50")
        assert log_resp.status_code == 200
        assert "ERROR boom" in log_resp.json()["content"]

        bad_resp = client.get("/api/tasks/missing_task/log")
        assert bad_resp.status_code == 404


def test_infer_history_source_recognizes_both_task_prefixes():
    assert main._infer_history_source("", ["task_report (task:123)"]) == "background_report"
    assert main._infer_history_source("", ["task_manager (task_123)", "status=成功完成"]) == "background_report"
    assert main._infer_history_source("", ["herdr_coordinator (w16:p1)"]) == "background_report"
