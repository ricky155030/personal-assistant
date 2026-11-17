#!/usr/bin/env python3
"""
Zigbee2MQTT 橋接器自動巡檢與自愈守護腳本 (scripts/zigbee_watchdog.py)

定期透過 Home Assistant API 檢查 `binary_sensor.zigbee2mqtt_bridge_connection_state`：
1. 若橋接器正常 (`on`)：清除告警狀態。
2. 若橋接器離線 (`off` / `unavailable`)：透過 SSH 檢查 HA 主機 USB 裝置是否存在。
   - USB 存在：自動重建並啟動 `zigbee2mqtt` 容器，嘗試自愈。
   - USB 消失（硬體卡死）：立即透過 iOS Critical Alert 與 Telegram 通知主人拔插。
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

BRIDGE_ENTITY = "binary_sensor.zigbee2mqtt_bridge_connection_state"
LOG_ERROR_PATTERNS = ("NWK_NO_ROUTE", "Data request failed", "SRSP -", "Adapter disconnected")
LOG_RESTART_COOLDOWN_SEC = 900


def get_config() -> Dict[str, str]:
    """讀取環境設定，避免硬編碼任何機敏主機資訊"""
    hass_url = os.getenv("HASS_URL", "").rstrip("/")
    hass_token = os.getenv("HASS_TOKEN", "")
    ha_host = os.getenv("ZIGBEE_SSH_HOST") or (urllib.parse.urlparse(hass_url).hostname or "")
    ssh_user = os.getenv("ZIGBEE_SSH_USER") or os.getenv("USER", "user")
    compose_dir = os.getenv("ZIGBEE_COMPOSE_DIR", "~/homeassistant")
    return {
        "hass_url": hass_url,
        "hass_token": hass_token,
        "ha_host": ha_host,
        "ssh_user": ssh_user,
        "compose_dir": compose_dir,
    }


def fetch_bridge_state(hass_url: str, hass_token: str, timeout: float = 8.0) -> Optional[str]:
    """查詢 Home Assistant Zigbee2MQTT 橋接器連線狀態"""
    if not hass_url or not hass_token:
        return None
    req = urllib.request.Request(
        f"{hass_url}/api/states/{BRIDGE_ENTITY}",
        headers={"Authorization": f"Bearer {hass_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("state", "")).lower()


def run_ssh_command(ssh_user: str, ha_host: str, remote_cmd: str, timeout: float = 20.0) -> Tuple[int, str, str]:
    """透過 SSH 執行遠端指令（非互動模式，避免卡住）"""
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{ssh_user}@{ha_host}", remote_cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def notify_alert(title: str, message: str, critical: bool = False) -> None:
    """發送 Telegram 與（可選）iOS Critical Alert 通知"""
    try:
        from scripts.telegram_notify import send_telegram_alert
        send_telegram_alert(f"{title}\n{message}")
    except Exception as e:
        print(f"[ZigbeeWatchdog] Telegram 通知失敗: {e}", file=sys.stderr)

    if critical:
        try:
            from scripts.ios_notify import IOSNotifier
            IOSNotifier().send_critical(title=title, message=message, volume=0.0, tag="zigbee_watchdog")
        except Exception as e:
            print(f"[ZigbeeWatchdog] iOS 通知失敗: {e}", file=sys.stderr)


def check_container_and_logs(ssh_user: str, ha_host: str) -> Tuple[str, str]:
    """透過 SSH 檢查 zigbee2mqtt 容器運行狀態與近 5 分鐘日誌是否有重複路由/通訊錯誤。

    回傳 (status, detail):
    - ("ok", "")：容器運行中且近期日誌無異常
    - ("container_down", detail)：容器未運行
    - ("route_errors", detail)：近期日誌出現 >= 2 次 Zigbee 路由/通訊失敗
    - ("ssh_failed", detail)：SSH 連線失敗（不誤判重啟）
    """
    cmd = (
        "docker inspect -f '{{.State.Running}}' zigbee2mqtt 2>/dev/null || echo 'false'; "
        "echo '---LOGS---'; "
        "docker logs --since 5m zigbee2mqtt 2>&1 | tail -n 100 || true"
    )
    rc, out, err = run_ssh_command(ssh_user, ha_host, cmd, timeout=15.0)
    if rc != 0:
        return "ssh_failed", err

    running_part, _, logs_part = out.partition("---LOGS---")
    if running_part.strip().lower() != "true":
        return "container_down", running_part.strip() or "not running"

    err_lines = [
        line.strip()
        for line in logs_part.splitlines()
        if any(pat in line for pat in LOG_ERROR_PATTERNS)
    ]
    if len(err_lines) >= 2:
        return "route_errors", f"近 5 分鐘出現 {len(err_lines)} 次通訊/路由錯誤（如 {err_lines[-1][:80]}）"
    return "ok", ""


def check_and_heal_once(state_ctx: Dict[str, Any], cfg: Optional[Dict[str, str]] = None) -> str:
    """執行單次巡檢與自愈邏輯，回傳狀態代碼"""
    cfg = cfg or get_config()
    try:
        bridge_state = fetch_bridge_state(cfg["hass_url"], cfg["hass_token"])
    except Exception as e:
        print(f"[ZigbeeWatchdog] ⚠️ 查詢 HA 狀態失敗: {e}", file=sys.stderr)
        return "ha_unreachable"

    ha_host = cfg["ha_host"]
    ssh_user = cfg["ssh_user"]
    compose_dir = cfg["compose_dir"]

    need_heal_reason = ""
    if bridge_state == "on":
        if ha_host:
            c_status, c_detail = check_container_and_logs(ssh_user, ha_host)
            if c_status == "container_down":
                need_heal_reason = f"容器未運行（{c_detail}）"
            elif c_status == "route_errors":
                last_ts = float(state_ctx.get("last_log_restart_ts", 0.0) or 0.0)
                if time.time() - last_ts >= LOG_RESTART_COOLDOWN_SEC:
                    need_heal_reason = c_detail
                else:
                    print(f"[ZigbeeWatchdog] ⏳ 偵測到路由錯誤但仍在冷卻期內，略過重啟: {c_detail}")

        if not need_heal_reason:
            if state_ctx.get("alert_sent"):
                notify_alert("✅ Zigbee2MQTT 已恢復連線", "橋接器狀態已回到正常連線。", critical=False)
            state_ctx["alert_sent"] = False
            return "healthy"
    else:
        need_heal_reason = f"橋接器離線（state={bridge_state}）"

    if not ha_host:
        return "missing_host"

    # 檢查 USB 裝置是否仍在主機上
    rc, out, err = run_ssh_command(
        ssh_user,
        ha_host,
        "ls /dev/ttyUSB* /dev/serial/by-id/* 2>/dev/null | head -n 1 || true",
    )
    if rc != 0:
        print(f"[ZigbeeWatchdog] ⚠️ SSH 檢查失敗: {err}", file=sys.stderr)
        return "ssh_failed"

    if not out:
        if not state_ctx.get("alert_sent"):
            notify_alert(
                "🚨 Zigbee USB 接收器離線",
                "HA 主機偵測不到 Zigbee USB 序列埠，請回家拔掉重插接收器。",
                critical=True,
            )
            state_ctx["alert_sent"] = True
        return "usb_missing"

    # USB 還在 -> 嘗試重啟/重建容器自愈
    print(f"[ZigbeeWatchdog] 🔧 {need_heal_reason}，但 USB 存在 ({out})，嘗試重啟 zigbee2mqtt 容器...")
    heal_cmd = (
        "docker restart zigbee2mqtt"
        if bridge_state == "on"
        else f"cd {compose_dir} && docker compose up -d --force-recreate zigbee2mqtt"
    )
    rc_up, _, err_up = run_ssh_command(ssh_user, ha_host, heal_cmd, timeout=45.0)
    if rc_up == 0:
        if bridge_state == "on":
            state_ctx["last_log_restart_ts"] = time.time()
        time.sleep(8)
        try:
            new_state = fetch_bridge_state(cfg["hass_url"], cfg["hass_token"])
        except Exception:
            new_state = None
        if new_state == "on":
            state_ctx["alert_sent"] = False
            notify_alert(
                "🔧 Zigbee2MQTT 已自動修復",
                f"觸發原因：{need_heal_reason}，已自動重啟容器恢復。",
                critical=False,
            )
            return "auto_healed"

    if not state_ctx.get("alert_sent"):
        notify_alert(
            "⚠️ Zigbee2MQTT 自動修復失敗",
            f"已嘗試重建容器但仍無法連線（{err_up or '請檢查 USB 或容器日誌'}）。",
            critical=True,
        )
        state_ctx["alert_sent"] = True
    return "heal_failed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Zigbee2MQTT 橋接器自動巡檢與自愈守護")
    parser.add_argument("--once", action="store_true", help="僅執行單次檢查")
    parser.add_argument("--interval", type=int, default=300, help="巡檢間隔秒數（預設 300 秒）")
    args = parser.parse_args()

    state_ctx: Dict[str, Any] = {"alert_sent": False}
    if args.once:
        res = check_and_heal_once(state_ctx)
        print(f"[ZigbeeWatchdog] 單次檢查結果: {res}")
        return 0

    print(f"[ZigbeeWatchdog] 🚀 啟動 Zigbee2MQTT 守護巡檢（間隔 {args.interval}s）")
    while True:
        res = check_and_heal_once(state_ctx)
        print(f"[ZigbeeWatchdog] 巡檢結果: {res}")
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
