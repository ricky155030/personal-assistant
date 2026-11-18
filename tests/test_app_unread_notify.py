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
