import sys
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.zigbee_watchdog import check_and_heal_once

DUMMY_CFG = {
    "hass_url": "http://homeassistant.local:8123",
    "hass_token": "dummy-token",
    "ha_host": "homeassistant.local",
    "ssh_user": "alex",
    "compose_dir": "~/homeassistant",
}


@patch("scripts.zigbee_watchdog.notify_alert")
@patch("scripts.zigbee_watchdog.fetch_bridge_state", return_value="on")
def test_watchdog_healthy(mock_fetch, mock_notify):
    ctx = {"alert_sent": False}
    assert check_and_heal_once(ctx, DUMMY_CFG) == "healthy"
    mock_notify.assert_not_called()


@patch("scripts.zigbee_watchdog.notify_alert")
@patch("scripts.zigbee_watchdog.run_ssh_command", return_value=(0, "", ""))
@patch("scripts.zigbee_watchdog.fetch_bridge_state", return_value="off")
def test_watchdog_usb_missing_alerts_once(mock_fetch, mock_ssh, mock_notify):
    ctx = {"alert_sent": False}
    assert check_and_heal_once(ctx, DUMMY_CFG) == "usb_missing"
    assert ctx["alert_sent"] is True
    mock_notify.assert_called_once()
    assert mock_notify.call_args[1]["critical"] is True

    # 第二次巡檢不重複洗版
    mock_notify.reset_mock()
    assert check_and_heal_once(ctx, DUMMY_CFG) == "usb_missing"
    mock_notify.assert_not_called()


@patch("scripts.zigbee_watchdog.time.sleep")
@patch("scripts.zigbee_watchdog.notify_alert")
@patch("scripts.zigbee_watchdog.run_ssh_command")
@patch("scripts.zigbee_watchdog.fetch_bridge_state")
def test_watchdog_auto_heals_when_usb_present(mock_fetch, mock_ssh, mock_notify, _mock_sleep):
    mock_fetch.side_effect = ["off", "on"]
    mock_ssh.side_effect = [
        (0, "/dev/ttyUSB0", ""),
        (0, "Container zigbee2mqtt Started", ""),
    ]
    ctx = {"alert_sent": False}
    assert check_and_heal_once(ctx, DUMMY_CFG) == "auto_healed"
    assert ctx["alert_sent"] is False
    mock_notify.assert_called_once()
    assert mock_notify.call_args[1]["critical"] is False
