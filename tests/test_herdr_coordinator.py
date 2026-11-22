import sys
import json
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.herdr_coordinator import (
    COORDINATION_RULE,
    inject_coordination_rule,
    clean_terminal_text,
    detect_question_or_confirmation,
    extract_completion_summary,
    list_herdr_agents,
    get_herdr_agent,
    read_herdr_agent_visible,
    send_herdr_keys,
    submit_herdr_prompt,
    dispatch_task,
    relay_answer,
    watch_agent,
    notify_question,
    notify_completion,
)


@pytest.fixture(autouse=True)
def _isolate_pending_actions():
    """避免單元測試呼叫本機後端 /api/actions/pending 寫入正式佇列。"""
    with patch("scripts.herdr_coordinator._push_pending_action"):
        yield


def test_inject_coordination_rule():
    prompt = "請重構 database.py 中的連線池模組"
    injected = inject_coordination_rule(prompt)
    assert prompt in injected
    assert COORDINATION_RULE in injected

    # 再次注入不重複
    double_injected = inject_coordination_rule(injected)
    assert double_injected.count(COORDINATION_RULE) == 1


def test_clean_terminal_text():
    raw_terminal = (
        "\x1b[32m正在編譯專案...\x1b[0m\n"
        "────────────────────────────────────────────\n"
        "編譯完成，耗時 1.5 秒。\n"
        "────────────────────────────────────────────\n"
        ">\n"
        "模型: Gemini 3.8 Flash │ 上下文: 14.7% │ 權杖數: 154k │ 每週額度: 74%\n"
    )
    cleaned = clean_terminal_text(raw_terminal)
    assert "正在編譯專案..." in cleaned
    assert "編譯完成，耗時 1.5 秒。" in cleaned
    assert "模型:" not in cleaned
    assert "─" not in cleaned
    assert ">" not in cleaned.splitlines()


def test_clean_terminal_text_strips_coordination_rule():
    # 確保規則本文被過濾，防止 Agent terminal 回顯 Prompt 造成假陽性提問判讀
    raw = (
        "User: 請執行架構分析。\n"
        f"{COORDINATION_RULE}\n"
        "Agent: 好的，我正在分析系統架構。\n"
    )
    cleaned = clean_terminal_text(raw)
    assert "【協調指令規範】" not in cleaned
    assert "好的，我正在分析系統架構。" in cleaned


def test_detect_question_patterns():
    # 測試 Q1 / 選項模式
    text_with_options = (
        "分析發現有兩種相容策略：\n"
        "Q1: 請問採用哪種備份方式？\n"
        "選項 1: 本地 SQLite 備份\n"
        "選項 2: 雲端同步備份\n"
    )
    detected = detect_question_or_confirmation(text_with_options, status="idle")
    assert detected is not None
    assert "Q1: 請問採用哪種備份方式？" in detected
    assert "選項 1: 本地 SQLite 備份" in detected

    # 測試 [Y/n] 或等待確認
    text_confirm = "即將刪除過期快取檔案，是否繼續？ [Y/n]"
    detected_confirm = detect_question_or_confirmation(text_confirm, status="idle")
    assert detected_confirm is not None
    assert "是否繼續？ [Y/n]" in detected_confirm

    # 測試純完工文字（不應誤判為問題）
    text_done = "測試通過！所有 12 個單元測試均為綠燈。\n代碼已完成重構。"
    assert detect_question_or_confirmation(text_done, status="idle") is None

    # 測試 Herdr blocked 狀態（即使無特定文字，亦需視為確認中）
    detected_blocked = detect_question_or_confirmation("", status="blocked")
    assert detected_blocked is not None


def test_extract_completion_summary():
    long_output = "\n".join([f"Step {i}: executed successfully" for i in range(100)])
    summary = extract_completion_summary(long_output)
    assert "Step 99: executed successfully" in summary


@patch("scripts.herdr_coordinator.run_herdr_cmd")
def test_list_herdr_agents(mock_cmd):
    mock_response = MagicMock()
    mock_response.returncode = 0
    mock_response.stdout = json.dumps({
        "result": {
            "agents": [
                {"agent": "agy", "pane_id": "w1:p1", "agent_status": "idle"},
                {"agent": "claude", "pane_id": "w1:p2", "agent_status": "working"},
            ]
        }
    })
    mock_cmd.return_value = mock_response

    agents = list_herdr_agents()
    assert len(agents) == 2
    assert agents[0]["agent"] == "agy"
    assert agents[1]["agent_status"] == "working"


@patch("scripts.herdr_coordinator.run_herdr_cmd")
def test_get_herdr_agent(mock_cmd):
    mock_response = MagicMock()
    mock_response.returncode = 0
    mock_response.stdout = json.dumps({
        "result": {
            "agent": {"agent": "agy", "pane_id": "wZ:p2", "agent_status": "idle"}
        }
    })
    mock_cmd.return_value = mock_response

    info = get_herdr_agent("agy")
    assert info is not None
    assert info["pane_id"] == "wZ:p2"
    assert info["agent_status"] == "idle"


@patch("scripts.herdr_coordinator.run_herdr_cmd")
def test_read_herdr_agent_visible(mock_cmd):
    mock_response = MagicMock()
    mock_response.returncode = 0
    mock_response.stdout = "Terminal visible lines here"
    mock_cmd.return_value = mock_response

    out = read_herdr_agent_visible("agy", lines=50)
    assert "Terminal visible lines here" in out
    mock_cmd.assert_called_with(["agent", "read", "agy", "--source", "visible", "--lines", "50"])


@patch("scripts.herdr_coordinator.run_herdr_cmd")
def test_send_herdr_keys(mock_cmd):
    mock_response = MagicMock()
    mock_response.returncode = 0
    mock_response.stdout = json.dumps({"result": {}})
    mock_cmd.return_value = mock_response

    res = send_herdr_keys("agy", "選項 1\n")
    assert res["success"] is True
    mock_cmd.assert_called_with(["agent", "send-keys", "agy", "選項 1\n"])


@patch("scripts.herdr_coordinator.run_herdr_cmd")
def test_submit_herdr_prompt(mock_cmd):
    mock_response = MagicMock()
    mock_response.returncode = 0
    mock_response.stdout = json.dumps({"result": {}})
    mock_cmd.return_value = mock_response

    res = submit_herdr_prompt("agy", "執行單元測試")
    assert res["success"] is True
    mock_cmd.assert_called_with(["agent", "prompt", "agy", "執行單元測試"])


@patch("scripts.herdr_coordinator.get_herdr_agent", return_value=None)
@patch("scripts.herdr_coordinator.submit_herdr_prompt")
def test_dispatch_task_no_watch(mock_submit, _mock_get):
    mock_submit.return_value = {"success": True}

    res = dispatch_task(
        agent="agy",
        prompt="請分析專案依賴",
        no_watch=True,
    )
    assert res["success"] is True
    assert "watch_task" not in res
    mock_submit.assert_called_once()
    prompt_arg = mock_submit.call_args[0][1]
    assert COORDINATION_RULE in prompt_arg


@patch("scripts.herdr_coordinator.get_herdr_agent", return_value=None)
@patch("task_manager.TaskManager.spawn")
@patch("scripts.herdr_coordinator.submit_herdr_prompt")
def test_dispatch_task_with_watch(mock_submit, mock_spawn, _mock_get):
    mock_submit.return_value = {"success": True}
    mock_spawn.return_value = {"task_id": "task_20261111_120000", "pid": 12345}

    res = dispatch_task(
        agent="coder",
        prompt="修復登入驗證錯誤",
        no_watch=False,
        interval=2.0,
    )
    assert res["success"] is True
    assert res["watch_task"]["task_id"] == "task_20261111_120000"
    mock_spawn.assert_called_once()


@patch("scripts.herdr_coordinator.submit_herdr_prompt")
@patch("scripts.herdr_coordinator.send_herdr_keys")
def test_relay_answer(mock_send_keys, mock_submit_prompt):
    mock_submit_prompt.return_value = {"success": True}
    mock_send_keys.return_value = {"success": True}

    # 文字答覆 -> 應調用 submit_herdr_prompt
    res = relay_answer("agy", "選項 2: 雲端備份")
    assert res["success"] is True
    mock_submit_prompt.assert_called_once_with("agy", "選項 2: 雲端備份")

    # 單一控制鍵 -> 應調用 send_herdr_keys
    res_key = relay_answer("agy", "Enter")
    assert res_key["success"] is True
    mock_send_keys.assert_called_once_with("agy", "Enter")


@patch("scripts.herdr_coordinator._push_pending_action")
@patch("scripts.herdr_coordinator.refine_question_for_action_queue", return_value={"formatted_question": "Q1: 採用何種方案？\n選項 1: A\n選項 2: B", "options": ["選項 1: A", "選項 2: B"]})
@patch("scripts.herdr_coordinator.get_herdr_agent", return_value=None)
@patch("scripts.herdr_coordinator._send_unified_app_alert")
def test_notify_question_and_completion(mock_app_alert, _mock_get, _mock_refine, _mock_push):
    mock_app_alert.return_value = True

    # 測試發送提問卡片至統一 App 通道
    ok_q = notify_question("agy", "Q1: 採用何種方案？\n選項 1: A\n選項 2: B")
    assert ok_q is True
    mock_app_alert.assert_called_once()
    kwargs_q = mock_app_alert.call_args.kwargs
    assert "Herdr Agent [agy] 請示主人" in kwargs_q["title"]
    assert "Q1:" in kwargs_q["message"]
    assert "選項 1" in (kwargs_q.get("memory_summary") or "")

    # 測試發送完工通知至統一 App 通道
    mock_app_alert.reset_mock()
    ok_c = notify_completion("agy", "所有程式碼重構完畢，測試全數通過！")
    assert ok_c is True
    mock_app_alert.assert_called_once()
    kwargs_c = mock_app_alert.call_args.kwargs
    assert "Herdr Agent [agy] 任務完成" in kwargs_c["title"]
    assert "所有程式碼重構完畢" in kwargs_c["message"]


@patch("scripts.herdr_coordinator.notify_question")
@patch("scripts.herdr_coordinator.read_herdr_agent_visible")
@patch("scripts.herdr_coordinator.get_herdr_agent")
def test_watch_agent_question_cycle(mock_get, mock_read, mock_notify):
    mock_notify.return_value = True
    mock_get.return_value = {"agent": "agy", "agent_status": "idle"}
    mock_read.return_value = (
        "Q1: 請確認是否覆蓋配置檔？\n"
        "選項 1: 覆蓋\n"
        "選項 2: 保留現狀\n"
    )

    # 執行 2 次迭代，驗證提問偵測有被觸發，且在相同提問時不重複發送 notification
    res = watch_agent("agy", interval=0.01, max_iterations=2, start_grace_seconds=0.0)
    assert res["status"] == "max_iterations_reached"
    # notify_question 應該只被調用 1 次（防重複通知）
    assert mock_notify.call_count == 1


@patch("scripts.herdr_coordinator.notify_completion")
@patch("scripts.herdr_coordinator.read_herdr_agent_visible")
@patch("scripts.herdr_coordinator.get_herdr_agent")
def test_watch_agent_completion_cycle(mock_get, mock_read, mock_notify):
    mock_notify.return_value = True

    # 模擬 2 個輪詢 tick：
    # Tick 1: Agent working
    # Tick 2: Agent idle (無提問，完工)
    mock_get.side_effect = [
        {"agent": "agy", "agent_status": "working"},
        {"agent": "agy", "agent_status": "idle"},
    ]
    mock_read.return_value = "測試通過！已將修正推播至主分支。\n所有功能驗證完畢。"

    res = watch_agent("agy", interval=0.01, start_grace_seconds=0.0)
    assert res["status"] == "completed"
    assert "測試通過" in res["summary"]
    mock_notify.assert_called_once()


from scripts.herdr_coordinator import main


@patch("scripts.herdr_coordinator.list_herdr_agents")
def test_cli_list(mock_list, capsys):
    mock_list.return_value = [{"agent": "agy", "agent_status": "idle", "pane_id": "w1:p1"}]
    with patch.object(sys, "argv", ["herdr_coordinator.py", "list"]):
        main()
    captured = capsys.readouterr()
    assert "agy" in captured.out
    assert "idle" in captured.out


@patch("scripts.herdr_coordinator.dispatch_task")
def test_cli_dispatch(mock_dispatch):
    mock_dispatch.return_value = {"success": True}
    with patch.object(sys, "argv", ["herdr_coordinator.py", "dispatch", "--agent", "agy", "--prompt", "測試任務", "--no-watch"]):
        main()
    mock_dispatch.assert_called_once_with(
        agent="agy",
        prompt="測試任務",
        name=None,
        no_watch=True,
        interval=3.0,
        ttl_minutes=120,
        criteria=None,
        max_retries=3,
        batch_id=None,
        batch_total=None,
        auto_review=True,
        force=False,
    )


@patch("scripts.herdr_coordinator.relay_answer")
def test_cli_relay(mock_relay):
    mock_relay.return_value = {"success": True}
    with patch.object(sys, "argv", ["herdr_coordinator.py", "relay", "--agent", "agy", "--answer", "選項 1"]):
        main()
    mock_relay.assert_called_once_with(
        agent="agy",
        answer="選項 1",
    )


@patch("scripts.herdr_coordinator.watch_agent")
def test_cli_watch(mock_watch):
    mock_watch.return_value = {"status": "completed"}
    with patch.object(sys, "argv", ["herdr_coordinator.py", "watch", "--agent", "agy", "--interval", "1.5"]):
        main()
    mock_watch.assert_called_once_with(
        agent="agy",
        name=None,
        interval=1.5,
        timeout=None,
        task_prompt=None,
        criteria=None,
        max_retries=3,
        baseline_lines=0,
        batch_id=None,
        batch_total=None,
        auto_review=True,
    )


def test_read_session_turns_agy_baseline(tmp_path):
    from scripts.herdr_coordinator import read_session_turns

    log_file = tmp_path / "transcript_full.jsonl"
    lines = [
        json.dumps({"type": "USER_INPUT", "content": "old prompt"}),
        json.dumps({"type": "PLANNER_RESPONSE", "content": "old reply"}),
        json.dumps({"type": "USER_INPUT", "content": "new prompt"}),
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "content": "new reply done",
                "tool_calls": [{"name": "run_command"}],
            }
        ),
    ]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with patch("scripts.herdr_coordinator.get_herdr_agent", return_value=None), \
         patch("scripts.herdr_coordinator.resolve_session_log_path", return_value=("agy", log_file)):
        res = read_session_turns("agy", baseline_lines=2)

    assert res["agent_kind"] == "agy"
    assert res["total_lines"] == 4
    assert res["user_inputs"] == ["new prompt"]
    assert res["latest_reply"] == "new reply done"
    assert "run_command" in res["tool_calls"]


def test_read_session_turns_omp_baseline(tmp_path):
    from scripts.herdr_coordinator import read_session_turns

    log_file = tmp_path / "omp_session.jsonl"
    lines = [
        json.dumps({"type": "session", "id": "s1"}),
        json.dumps(
            {
                "type": "message",
                "message": {"role": "user", "content": [{"type": "text", "text": "fix bug"}]},
            }
        ),
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "bug fixed"}],
                },
            }
        ),
        json.dumps(
            {
                "type": "message",
                "message": {"role": "toolResult", "toolName": "edit_file", "isError": False},
            }
        ),
    ]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with patch("scripts.herdr_coordinator.get_herdr_agent", return_value=None), \
         patch("scripts.herdr_coordinator.resolve_session_log_path", return_value=("omp", log_file)):
        res = read_session_turns("omp-worker", baseline_lines=1)

    assert res["agent_kind"] == "omp"
    assert res["total_lines"] == 4
    assert res["user_inputs"] == ["fix bug"]
    assert res["latest_reply"] == "bug fixed"
    assert "edit_file" in res["tool_calls"]


@patch("scripts.herdr_coordinator.get_herdr_agent")
def test_dispatch_task_busy_guard(mock_get):
    mock_get.return_value = {"agent": "agy-worker", "agent_status": "working"}
    res = dispatch_task("agy-worker", "new task")
    assert res["success"] is False
    assert res["error"] == "agent_busy"


@patch("scripts.herdr_coordinator.notify_completion")
@patch("scripts.herdr_coordinator.record_task_to_history_and_memory")
@patch("scripts.herdr_coordinator.submit_herdr_prompt")
@patch("scripts.herdr_coordinator.review_worker_output")
@patch("scripts.herdr_coordinator.collect_workspace_evidence")
@patch("scripts.herdr_coordinator.read_session_turns")
@patch("scripts.herdr_coordinator.read_herdr_agent_visible", return_value="")
@patch("scripts.herdr_coordinator.get_herdr_agent")
def test_watch_agent_review_reject_then_pass(
    mock_get,
    _mock_read_visible,
    mock_turns,
    mock_evidence,
    mock_review,
    mock_submit,
    mock_record,
    mock_notify,
):
    mock_get.side_effect = [
        {"agent": "agy", "agent_status": "working", "cwd": "/tmp/repo"},
        {"agent": "agy", "agent_status": "idle", "cwd": "/tmp/repo"},
        {"agent": "agy", "agent_status": "working", "cwd": "/tmp/repo"},
        {"agent": "agy", "agent_status": "idle", "cwd": "/tmp/repo"},
    ]
    mock_turns.return_value = {
        "latest_reply": "fixed attempt",
        "total_lines": 8,
        "log_path": "/tmp/log",
        "tool_calls": ["pytest"],
    }
    mock_evidence.return_value = "M app.py"
    mock_review.side_effect = [
        {"verdict": "REJECT", "summary": "missing tests", "feedback": "Add unit tests"},
        {"verdict": "PASS", "summary": "tests added and passing", "feedback": ""},
    ]
    mock_submit.return_value = {"success": True}
    mock_notify.return_value = True

    res = watch_agent(
        "agy",
        interval=0.01,
        start_grace_seconds=0.0,
        task_prompt="implement feature",
        max_retries=3,
        auto_review=True,
    )

    assert res["status"] == "completed"
    assert res["retries_used"] == 1
    mock_submit.assert_called_once()
    assert "Add unit tests" in mock_submit.call_args[0][1]
    mock_record.assert_called_once()
    mock_notify.assert_called_once()


def test_update_batch_progress(tmp_path):
    from scripts.herdr_coordinator import update_batch_progress

    with patch("scripts.herdr_coordinator.BASE_DIR", tmp_path):
        b1 = update_batch_progress("batch-1", "agy-1", "Task 1", "completed", "done 1", batch_total=2)
        assert b1 is not None
        assert b1["total"] == 2
        assert len(b1["tasks"]) == 1

        b2 = update_batch_progress("batch-1", "agy-2", "Task 2", "completed", "done 2", batch_total=2)
        assert b2 is not None
        assert len(b2["tasks"]) == 2
        assert (tmp_path / "data" / "herdr_batches.json").exists()


def test_memory_manager_append_jsonl_locked(tmp_path):
    from memory_manager import MemoryManager

    mm = MemoryManager()
    jsonl_file = tmp_path / "history.jsonl"
    mm._append_jsonl_locked(jsonl_file, {"role": "user", "content": "hello"})
    mm._append_jsonl_locked(jsonl_file, {"role": "assistant", "content": "world"})

    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["content"] == "hello"
    assert json.loads(lines[1])["content"] == "world"


def test_resolve_workspace_label_and_refine_question_success():
    from scripts.herdr_coordinator import (
        notify_question,
        refine_question_for_action_queue,
        resolve_workspace_label,
    )

    info = {"agent": "agy", "pane_id": "w13:p1", "cwd": "/Users/demo/Workspace/market-ticker"}
    assert resolve_workspace_label("w13:p1", agent_info=info) == "market-ticker · agy · w13:p1"

    mock_proc = MagicMock(
        returncode=0,
        stdout=json.dumps({
            "summary": "平行抓取重構已完成測試，需確認是否重啟服務",
            "question": "是否立即重啟線上報價服務？",
            "options": ["選項 1：立即重啟服務套用", "選項 2：稍後手動重啟"],
        }),
    )
    with patch("scripts.herdr_coordinator.shutil.which", return_value="/usr/local/bin/agy"), \
         patch("scripts.herdr_coordinator.subprocess.run", return_value=mock_proc):
        refined = refine_question_for_action_queue("market-ticker · agy · w13:p1", "Q1: 請選擇\n選項 1: A\n選項 2: B")

    assert "【背景】平行抓取重構已完成測試" in refined["formatted_question"]
    assert "是否立即重啟線上報價服務？" in refined["formatted_question"]
    assert refined["options"] == ["選項 1：立即重啟服務套用", "選項 2：稍後手動重啟"]

    with patch("scripts.herdr_coordinator.get_herdr_agent", return_value=info), \
         patch("scripts.herdr_coordinator.refine_question_for_action_queue", return_value=refined), \
         patch("scripts.herdr_coordinator._push_pending_action") as mock_push, \
         patch("scripts.herdr_coordinator._send_unified_app_alert", return_value=True):
        ok = notify_question("w13:p1", "raw question", name="自訂任務名稱")

    assert ok is True
    mock_push.assert_called_once_with(
        agent="w13:p1",
        title="market-ticker · agy · w13:p1",
        question_text=refined["formatted_question"],
        options=refined["options"],
    )


def test_refine_question_for_action_queue_fallback_on_timeout():
    import subprocess
    from scripts.herdr_coordinator import refine_question_for_action_queue

    raw = "目前已完成單元測試\nQ1: 請選擇部署方式\n選項 1: 直接推上 main\n選項 2: 僅留本機測試"
    with patch("scripts.herdr_coordinator.shutil.which", return_value="/usr/local/bin/agy"), \
         patch("scripts.herdr_coordinator.subprocess.run", side_effect=subprocess.TimeoutExpired("agy", 12)):
        res = refine_question_for_action_queue("market-ticker · agy · w13:p1", raw)

    assert "目前已完成單元測試" in res["formatted_question"]
    assert res["options"] == ["選項 1: 直接推上 main", "選項 2: 僅留本機測試"]


def test_notify_question_supervisor_auto_answer():
    """當工人提問屬於常規工程驗證（auto_answer 非空）時，監工應自動 relay 代答而不打擾主人。"""
    from scripts.herdr_coordinator import notify_question

    info = {"agent": "agy", "pane_id": "w13:p1", "cwd": "/Users/demo/Workspace/market-ticker"}
    refined_auto = {
        "formatted_question": "是否執行 pytest 驗證？",
        "options": [],
        "auto_answer": "請直接執行 pytest 並確認全綠",
    }
    with patch("scripts.herdr_coordinator.get_herdr_agent", return_value=info), \
         patch("scripts.herdr_coordinator.refine_question_for_action_queue", return_value=refined_auto), \
         patch("scripts.herdr_coordinator.relay_answer", return_value={"success": True}) as mock_relay, \
         patch("scripts.herdr_coordinator._push_pending_action") as mock_push, \
         patch("scripts.herdr_coordinator._send_unified_app_alert") as mock_alert:
        ok = notify_question("w13:p1", "請問是否要跑 pytest？", task_prompt="重構報價服務")

    assert ok is True
    mock_relay.assert_called_once_with("w13:p1", "請直接執行 pytest 並確認全綠")
    mock_alert.assert_not_called()
    assert mock_push.call_args.kwargs["status"] == "working"


def test_detect_question_ignores_working_and_stale_scrollback():
    stale_top = "\n".join([f"Q1: 舊問題 {i}？" for i in range(5)] + [f"普通輸出行 {i}" for i in range(35)])
    assert detect_question_or_confirmation(stale_top, status="idle") is None
    assert detect_question_or_confirmation("Q1: 新問題？", status="working") is None

    from scripts.herdr_coordinator import _summarize_fallback_question
    long_raw = "\n".join([f"步驟 {i} 說明..." for i in range(20)] + ["請問要採用選項 1 還是選項 2？"])
    short = _summarize_fallback_question(long_raw)
    assert len(short) <= 140
    assert "選項 1" in short



