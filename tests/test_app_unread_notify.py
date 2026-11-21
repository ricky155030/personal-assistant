import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import main


def test_app_state_and_unread_critical_alert_lifecycle():
    from scripts.ios_notify import IOSNotifier

    with main._APP_STATE_LOCK:
        main._APP_STATE["active"] = True
        main._APP_STATE["has_unread_alert"] = False

    mock_notifier = MagicMock(spec=IOSNotifier)
    mock_notifier.send_critical.return_value = True
    mock_notifier.clear.return_value = True

    with patch("scripts.ios_notify.IOSNotifier", return_value=mock_notifier):
        # 1. 切到背景
        main._APP_STATE["active"] = False
        ok = main._notify_app_unread_reply("### 測試標題\n**重點內容** 已完成")
        assert ok is True
        mock_notifier.send_critical.assert_called_once()
        kwargs = mock_notifier.send_critical.call_args.kwargs
        assert kwargs["tag"] == main._UNREAD_ALERT_TAG
        assert kwargs["volume"] == 0.0
        assert "測試標題" in kwargs["message"]
        assert "#" not in kwargs["message"]
        assert "*" not in kwargs["message"]

        # 2. 回到前景讀取 -> 自動撤回唯一重要通知
        cleared = main._clear_app_unread_alert()
        assert cleared is True
        mock_notifier.clear.assert_called_once_with(tag=main._UNREAD_ALERT_TAG)

        # 3. 再次呼叫（已無未讀）不應重複撤回
        mock_notifier.clear.reset_mock()
        assert main._clear_app_unread_alert() is False
        mock_notifier.clear.assert_not_called()


def test_api_app_state_endpoint():
    client = TestClient(main.app)
    with patch("main.verify_token", return_value=True):
        main.app.dependency_overrides[main.verify_token] = lambda: True
        try:
            resp_bg = client.post("/api/app/state", json={"active": False})
            assert resp_bg.status_code == 200
            assert resp_bg.json()["active"] is False
            assert main._APP_STATE["active"] is False

            resp_fg = client.post("/api/app/state", json={"active": True})
            assert resp_fg.status_code == 200
            assert resp_fg.json()["active"] is True
            assert main._APP_STATE["active"] is True
        finally:
            main.app.dependency_overrides.pop(main.verify_token, None)


def test_internal_unread_notify_and_task_manager_unified_channel():
    import task_manager

    client = TestClient(main.app)
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        with main._APP_STATE_LOCK:
            main._APP_STATE["active"] = True
            main._APP_STATE["has_unread_alert"] = False

        # App 在前景時，背景任務通知應略過不打擾
        resp_fg = client.post("/api/internal/unread-notify", json={"title": "CI", "message": "ok"})
        assert resp_fg.status_code == 200
        assert resp_fg.json()["sent"] is False

        # App 在背景時，應發送唯一靜音重要通知
        main._APP_STATE["active"] = False
        with patch("main._notify_app_unread_reply", return_value=True) as mock_notify:
            resp_bg = client.post("/api/internal/unread-notify", json={"title": "✅ CI 監控", "message": "通過"})
            assert resp_bg.status_code == 200
            assert resp_bg.json()["sent"] is True
            mock_notify.assert_called_once_with("通過", title="✅ CI 監控")
    finally:
        main.app.dependency_overrides.pop(main.verify_token, None)

    # 預設 iOS 背景回報通道應完全不走 Telegram，統一走 iOS 通道
    with patch.object(task_manager, "_send_telegram_notification") as mock_tg, \
         patch.object(task_manager, "_send_ios_notification", return_value=True) as mock_ios:
        task_manager._dispatch_notifications("ios", "CI 測試", "task_1", 0, False, 1.5, "ok")
        mock_tg.assert_not_called()
        mock_ios.assert_called_once()


def test_api_herdr_agents_endpoint():
    import json
    import subprocess

    assert main._extract_model_from_terminal_text("模型: Gemini 3.8 Flash (High) │ 上下文: 13.3%") == "Gemini 3.8 Flash (High)"
    assert main._extract_model_from_terminal_text("> /model\n  ⎿  Model set to Gemini 3.8 Flash (Medium)") == "Gemini 3.8 Flash (Medium)"
    assert main._extract_model_from_terminal_text(" 󰵗 · 󰪣 Muse Spark 1.3 (C) · ~/Workspace/trading_system · 9.2%/1M") == "Muse Spark 1.3 (C)"

    fake_payload = {
        "result": {
            "agents": [
                {
                    "agent": "omp",
                    "agent_status": "idle",
                    "cwd": "/tmp/trading_system",
                    "pane_id": "w12:p1",
                    "focused": False,
                },
                {
                    "agent": "agy",
                    "agent_status": "busy",
                    "cwd": "/tmp/finlab_assistant",
                    "name": "finlab-gemini",
                    "pane_id": "w14:p1",
                    "focused": True,
                },
            ]
        }
    }
    fake_proc = MagicMock(returncode=0, stdout=json.dumps(fake_payload), stderr="")

    client = TestClient(main.app)
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        with patch.object(subprocess, "run", return_value=fake_proc), \
             patch("main._detect_herdr_agent_model", side_effect=["Muse Spark 1.3 (C)", "Gemini 3.8 Flash (High)"]):
            resp = client.get("/api/herdr/agents")
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        assert len(agents) == 2
        assert agents[0]["project"] == "trading_system"
        assert agents[0]["engine"] == "omp"
        assert agents[0]["model"] == "Muse Spark 1.3 (C)"
        assert agents[0]["status"] == "idle"
        assert agents[1]["name"] == "finlab-gemini"
        assert agents[1]["model"] == "Gemini 3.8 Flash (High)"
        assert agents[1]["focused"] is True
    finally:
        main.app.dependency_overrides.pop(main.verify_token, None)


def test_pending_action_queue_lifecycle(tmp_path):
    import subprocess
    from config import settings

    orig_data_dir = settings.DATA_DIR
    settings.DATA_DIR = tmp_path
    client = TestClient(main.app)
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        # 1. 新增待決策項目
        resp_create = client.post("/api/actions/pending", json={
            "agent": "w13:p1",
            "title": "market-ticker · agy",
            "question": "Q1: 請選擇方案？",
            "options": ["選項 1: 同意", "選項 2: 微調"],
            "source": "herdr:w13:p1",
        })
        assert resp_create.status_code == 200
        assert resp_create.json()["count"] == 1
        action_id = resp_create.json()["action"]["id"]

        # 2. 查詢列表
        resp_list = client.get("/api/actions/pending")
        assert resp_list.status_code == 200
        assert resp_list.json()["count"] == 1
        assert resp_list.json()["actions"][0]["options"] == ["選項 1: 同意", "選項 2: 微調"]

        # 3. 回覆並透過 relay 寫回、移出佇列
        fake_proc = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch.object(subprocess, "run", return_value=fake_proc) as mock_run:
            resp_resolve = client.post(f"/api/actions/pending/{action_id}/resolve", json={
                "answer": "選項 1: 同意",
                "dismiss": False,
            })
        assert resp_resolve.status_code == 200
        assert resp_resolve.json()["relayed"] is True
        assert resp_resolve.json()["count"] == 0
        mock_run.assert_called_once()
    finally:
        main.app.dependency_overrides.pop(main.verify_token, None)
        settings.DATA_DIR = orig_data_dir


def test_pending_action_brain_self_contained_context(tmp_path):
    import threading
    from config import settings

    orig_data_dir = settings.DATA_DIR
    settings.DATA_DIR = tmp_path
    client = TestClient(main.app)
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        resp_create = client.post("/api/actions/pending", json={
            "agent": "brain:restaurant_booking",
            "title": "主大腦確認 · 餐廳訂位",
            "question": "請問要訂哪一間分店？",
            "options": ["竹北店", "新竹巨城店"],
            "source": "brain",
            "context_prompt": "主人稍早要求訂明天晚上 7 點 2 位大人的鼎泰豐。",
        })
        assert resp_create.status_code == 200
        action = resp_create.json()["action"]
        assert action["context_prompt"] == "主人稍早要求訂明天晚上 7 點 2 位大人的鼎泰豐。"

        dispatched = {}

        def fake_chat(req_obj, *_args, **_kwargs):
            dispatched["query"] = req_obj.query
            dispatched["client"] = req_obj.client

        class ImmediateThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target = target
                self._args = args

            def start(self):
                if self._target:
                    self._target(*self._args)

        with patch.object(main, "chat", side_effect=fake_chat), \
             patch.object(threading, "Thread", ImmediateThread):
            resp_resolve = client.post(f"/api/actions/pending/{action['id']}/resolve", json={
                "answer": "竹北店",
                "dismiss": False,
            })

        assert resp_resolve.status_code == 200
        body = resp_resolve.json()
        assert body["relayed"] is True
        assert body["dispatched_to"] == "brain"
        assert body["count"] == 0
        assert "鼎泰豐" in dispatched["query"]
        assert "請問要訂哪一間分店？" in dispatched["query"]
        assert "竹北店" in dispatched["query"]
        assert main._clean_history_query(dispatched["query"]) == "已決策（主大腦確認 · 餐廳訂位）：竹北店"
    finally:
        main.app.dependency_overrides.pop(main.verify_token, None)
        settings.DATA_DIR = orig_data_dir


def test_herdr_dispatch_hub_lifecycle_followup_and_archive(tmp_path):
    """驗證 Herdr 督工抽屜全週期：執行中進度 -> 待決策解鎖 -> 完工待檢閱 -> 同卡多輪續派 -> 結案歸檔與歷史續派。"""
    import subprocess
    from config import settings

    orig_data_dir = settings.DATA_DIR
    settings.DATA_DIR = tmp_path
    client = TestClient(main.app)
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        # 1. 派工啟動 (working)
        r1 = client.post("/api/actions/pending", json={
            "agent": "w13:p1",
            "title": "market-ticker · agy · w13:p1",
            "question": "🔨 工人正在執行任務...",
            "status": "working",
            "task_prompt": "實作平行抓取報價與單元測試",
            "elapsed_seconds": 35,
            "tool_count": 4,
            "tool_calls": ["read_file", "edit_file", "run_command"],
            "live_preview": "正在執行 pytest...",
            "terminal_tail": "4 passed in 0.82s",
        })
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["working_count"] == 1
        assert body1["attention_count"] == 0
        action_id = body1["action"]["id"]

        # 2. 中途卡住請示 (waiting_decision)
        r2 = client.post("/api/actions/pending", json={
            "agent": "w13:p1",
            "title": "market-ticker · agy · w13:p1",
            "question": "【背景】報價模組已完成\n是否啟用快取？",
            "options": ["選項 1：啟用", "選項 2：不啟用"],
            "status": "waiting_decision",
        })
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["action"]["id"] == action_id
        assert body2["action"]["task_prompt"] == "實作平行抓取報價與單元測試"
        assert body2["attention_count"] == 1
        assert body2["working_count"] == 0

        # 3. 主人點擊選項解鎖 -> 卡片不消失，轉回 working 狀態
        fake_proc = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch.object(subprocess, "run", return_value=fake_proc):
            r_unlock = client.post(f"/api/actions/pending/{action_id}/resolve", json={
                "answer": "選項 1：啟用",
                "dismiss": False,
            })
        assert r_unlock.status_code == 200
        b_unlock = r_unlock.json()
        assert b_unlock["count"] == 1
        assert b_unlock["actions"][0]["status"] == "working"
        assert b_unlock["working_count"] == 1
        assert b_unlock["attention_count"] == 0

        # 4. 驗收通過完工 (completed)
        summary_text = "✅ 完成項目\n- 完成平行抓取與快取機制\n🧪 驗證結果\n- pytest 6 passed"
        r3 = client.post("/api/actions/pending", json={
            "agent": "w13:p1",
            "title": "market-ticker · agy · w13:p1",
            "question": summary_text,
            "summary": summary_text,
            "status": "completed",
            "elapsed_seconds": 80,
            "tool_count": 7,
        })
        assert r3.status_code == 200
        assert r3.json()["attention_count"] == 1

        # 5. 主人直接在完工卡片輸入 Follow-up 續派第 2 輪
        with patch.object(subprocess, "run", return_value=fake_proc) as mock_dispatch:
            r_followup = client.post(f"/api/actions/pending/{action_id}/resolve", json={
                "answer": "再幫我加上超時重試機制",
                "dismiss": False,
                "action": "followup",
            })
        assert r_followup.status_code == 200
        b_fu = r_followup.json()
        assert b_fu["dispatched_to"] == "herdr_followup"
        card_r2 = b_fu["actions"][0]
        assert card_r2["status"] == "working"
        assert card_r2["round_no"] == 2
        assert card_r2["task_prompt"] == "再幫我加上超時重試機制"
        assert len(card_r2["rounds"]) == 1
        assert card_r2["rounds"][0]["round_no"] == 1
        assert "完成平行抓取" in card_r2["rounds"][0]["summary"]
        assert "dispatch" in mock_dispatch.call_args[0][0]

        # 6. 第 2 輪完工後點「結案歸檔」(dismiss=True) -> 移入 history 永久保存
        client.post("/api/actions/pending", json={
            "agent": "w13:p1",
            "title": "market-ticker · agy · w13:p1",
            "question": "✅ 完成項目\n- 新增超時重試\n🧪 驗證結果\n- pytest 8 passed",
            "status": "completed",
        })
        r_arch = client.post(f"/api/actions/pending/{action_id}/resolve", json={
            "answer": "",
            "dismiss": True,
        })
        assert r_arch.status_code == 200
        b_arch = r_arch.json()
        assert b_arch["count"] == 0
        assert len(b_arch["history"]) == 1
        assert b_arch["history"][0]["id"] == action_id
        assert b_arch["history"][0]["status"] == "archived"

        # 7. 從歷史紀錄卡片直接點擊續派第 3 輪 -> 自動喚醒回目前派工清單
        with patch.object(subprocess, "run", return_value=fake_proc):
            r_revive = client.post(f"/api/actions/pending/{action_id}/resolve", json={
                "answer": "幫我補上 Prometheus metrics",
                "dismiss": False,
                "action": "followup",
            })
        assert r_revive.status_code == 200
        b_rev = r_revive.json()
        assert b_rev["count"] == 1
        assert len(b_rev["history"]) == 0
        assert b_rev["actions"][0]["round_no"] == 3
        assert len(b_rev["actions"][0]["rounds"]) == 2
    finally:
        main.app.dependency_overrides.pop(main.verify_token, None)
        settings.DATA_DIR = orig_data_dir
