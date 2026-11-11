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


@patch("scripts.herdr_coordinator.submit_herdr_prompt")
def test_dispatch_task_no_watch(mock_submit):
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


@patch("task_manager.TaskManager.spawn")
@patch("scripts.herdr_coordinator.submit_herdr_prompt")
def test_dispatch_task_with_watch(mock_submit, mock_spawn):
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


@patch("scripts.herdr_coordinator.send_herdr_keys")
def test_relay_answer(mock_send):
    mock_send.return_value = {"success": True}

    res = relay_answer("agy", "選項 2: 雲端備份")
    assert res["success"] is True
    mock_send.assert_called_once_with("agy", "選項 2: 雲端備份\n")


@patch("scripts.herdr_coordinator.send_telegram_alert")
def test_notify_question_and_completion(mock_tg):
    mock_tg.return_value = True

    # 測試發送提問卡片
    ok_q = notify_question("agy", "Q1: 採用何種方案？\n選項 1: A\n選項 2: B")
    assert ok_q is True
    mock_tg.assert_called()
    call_text = mock_tg.call_args[0][0]
    assert "Herdr Agent [agy] 請示主人" in call_text
    assert "relay --agent agy" in call_text

    # 測試發送完工卡片
    mock_tg.reset_mock()
    ok_c = notify_completion("agy", "所有程式碼重構完畢，測試全數通過！")
    assert ok_c is True
    mock_tg.assert_called()
    comp_text = mock_tg.call_args[0][0]
    assert "Herdr Agent [agy] 任務完成" in comp_text


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
        no_watch=True,
        interval=3.0,
        ttl_minutes=120,
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
        interval=1.5,
        timeout=None,
    )

