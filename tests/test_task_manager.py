import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import task_manager as tm


def test_extract_clean_log_tail_strips_ansi(tmp_path):
    log_file = tmp_path / "task.log"
    log_file.write_text("\x1b[32mPASS\x1b[0m line 1\n\nline 2\n", encoding="utf-8")

    tail = tm._extract_clean_log_tail(log_file, max_lines=10)
    assert "PASS line 1" in tail
    assert "line 2" in tail
    assert "\x1b" not in tail


def test_spawn_backward_compat(tmp_path):
    script_file = tmp_path / "demo.py"
    script_file.write_text("print('hi')\n", encoding="utf-8")

    mgr = tm.TaskManager()
    with patch.object(tm, "TASKS_JSON_PATH", tmp_path / "tasks.json"), \
         patch.object(tm, "TASK_LOGS_DIR", tmp_path / "logs"), \
         patch("task_manager.subprocess.Popen") as mock_popen:
        (tmp_path / "logs").mkdir(exist_ok=True)
        mock_popen.return_value = MagicMock(pid=12345)

        res = mgr.spawn(name="legacy", script=str(script_file), args=["--flag"])

    assert res["success"] is True
    assert res["pid"] == 12345
    launched_cmd = mock_popen.call_args[0][0]
    assert "_wrap" not in launched_cmd
    assert str(script_file) in launched_cmd
    assert "--flag" in launched_cmd


def test_spawn_cmd_uses_wrap(tmp_path):
    mgr = tm.TaskManager()
    with patch.object(tm, "TASKS_JSON_PATH", tmp_path / "tasks.json"), \
         patch.object(tm, "TASK_LOGS_DIR", tmp_path / "logs"), \
         patch("task_manager.subprocess.Popen") as mock_popen:
        (tmp_path / "logs").mkdir(exist_ok=True)
        mock_popen.return_value = MagicMock(pid=54321)

        res = mgr.spawn_cmd(
            name="CI Watch",
            cmd="echo ci_done",
            cwd=str(tmp_path),
            ttl_minutes=15,
            notify="telegram",
            sync_memory=True,
        )

    assert res["success"] is True
    assert res["pid"] == 54321
    launched_cmd = mock_popen.call_args[0][0]
    assert "_wrap" in launched_cmd
    assert "--cmd" in launched_cmd
    assert "echo ci_done" in launched_cmd
    assert "--notify" in launched_cmd
    assert "telegram" in launched_cmd
    assert "--sync-memory" in launched_cmd


def test_run_wrapped_task_success_notify_and_sync(tmp_path):
    log_file = tmp_path / "wrap_ok.log"
    hook_marker = tmp_path / "hook_ok.txt"
    cmd = f'"{sys.executable}" -c "print(\'wrapped success output\')"'
    on_ok = f'"{sys.executable}" -c "import os; open(r\'{hook_marker}\', \'w\').write(os.environ[\'TASK_EXIT_CODE\'])"'

    with patch("task_manager._send_telegram_notification", return_value=True) as mock_tg, \
         patch("task_manager.urllib.request.urlopen") as mock_urlopen, \
         patch("memory_manager.memory_manager.log_interaction") as mock_log_interaction:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = b'{"status":"synced"}'
        mock_urlopen.return_value = mock_resp

        rc = tm._run_wrapped_task(
            task_id="task_test_ok",
            name="Test OK",
            log_file=str(log_file),
            ttl_minutes=5,
            notify="telegram",
            sync_memory=True,
            cwd=str(tmp_path),
            cmd=cmd,
            on_success_cmd=on_ok,
        )

    assert rc == 0
    assert "wrapped success output" in log_file.read_text(encoding="utf-8")
    mock_tg.assert_called_once()
    assert "執行完成" in mock_tg.call_args[0][0]
    mock_log_interaction.assert_called_once()
    mock_urlopen.assert_called_once()
    assert hook_marker.read_text(encoding="utf-8") == "0"


def test_run_wrapped_task_failure_triggers_on_fail_hook(tmp_path):
    log_file = tmp_path / "wrap_fail.log"
    hook_marker = tmp_path / "hook_fail.txt"
    cmd = f'"{sys.executable}" -c "import sys; print(\'boom\'); sys.exit(2)"'
    on_fail = f'"{sys.executable}" -c "import os; open(r\'{hook_marker}\', \'w\').write(os.environ[\'TASK_EXIT_CODE\'])"'

    with patch("task_manager._send_telegram_notification", return_value=True) as mock_tg:
        rc = tm._run_wrapped_task(
            task_id="task_test_fail",
            name="Test Fail",
            log_file=str(log_file),
            ttl_minutes=5,
            notify="telegram",
            sync_memory=False,
            cwd=str(tmp_path),
            cmd=cmd,
            on_fail_cmd=on_fail,
        )

    assert rc == 2
    mock_tg.assert_called_once()
    assert "執行失敗" in mock_tg.call_args[0][0]
    assert hook_marker.read_text(encoding="utf-8") == "2"
