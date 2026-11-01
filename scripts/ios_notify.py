#!/usr/bin/env python3
"""
iOS 原生進階推播中心 (scripts/ios_notify.py)

整合 Apple 生態雙核心推播與 macOS 原生訊息通道：
1. Critical Alert (重要置頂通知)：
   - 永遠置頂於鎖定畫面最上方（無視普通通知與勿擾模式）
   - 支援 volume=0.0 達成 100% 靜音置頂
   - 支援 tag 標記，日後可透過 clear 指令遠端撤回消掉（已讀消滅）
   - 不受 Apple Live Activity 啟動次數配額限制
   - 支援原生 App Deep Link（如 FB App 貼文 direct link）
   - 適用：FB 社團贈物、安全防護、緊急事件通知。

2. Live Activity (即時動態 / 靈動島)：
   - 支援進度條 (progress / progress_max)
   - 支援手機本地硬體倒數計時器 (chronometer / when)
   - 靈動島膠囊即時狀態展示 (critical_text)
   - 原地動態跳轉數字不洗版 (同 tag update-in-place)
   - 適用：叫號排隊、批次任務進度條、定時提醒。

3. iMessage 原生通道 (Messages.app)：
   - 透過 Mac mini 本機 AppleScript 原生送達 iPhone 藍色對話泡泡
   - 具備白名單保護，拒絕未授權外部號碼
   - 支援別名解析（「我」、「老婆」）
   - 適用：重要行程提醒備忘、夫妻生活同步、無網路時 SMS 回退備援。
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

HASS_URL = os.getenv("HASS_URL", "").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")
DEFAULT_TARGET_DEVICE = os.getenv("HA_NOTIFY_DEVICE", "mobile_app_my_phone")


class IOSNotifier:
    """iOS 原生推播管理客戶端"""

    def __init__(self, hass_url: str = HASS_URL, hass_token: str = HASS_TOKEN, default_device: str = DEFAULT_TARGET_DEVICE):
        self.hass_url = hass_url.rstrip("/")
        self.hass_token = hass_token
        self.default_device = default_device

    def _call_ha_notify(self, device: str, payload: dict) -> bool:
        if not self.hass_token:
            print("[IOSNotify] ⚠️ 未設定 HASS_TOKEN，無法發送通知。", file=sys.stderr)
            return False

        url = f"{self.hass_url}/api/services/notify/{device}"
        headers = {
            "Authorization": f"Bearer {self.hass_token}",
            "Content-Type": "application/json"
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[IOSNotify] ❌ Home Assistant 錯誤 ({e.code}): {err_body}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[IOSNotify] ❌ 推播連線失敗: {e}", file=sys.stderr)
            return False

    def send_critical(
        self,
        title: str,
        message: str,
        url: Optional[str] = None,
        volume: float = 0.0,
        device: Optional[str] = None,
        tag: Optional[str] = None
    ) -> bool:
        """
        發送 iOS Critical Alert (重要置頂通知)：
        - 永遠霸佔鎖定畫面最頂端
        - 可設 volume: 0.0 達到完全靜音置頂
        - 點擊直達特定 URL 或 App 原生 Deep Link
        - 支援 tag 標籤，日後可透過 clear(tag) 遠端撤回消除
        - 不受 Apple Live Activity 的 Push-to-Start 配額限制
        """
        device = device or self.default_device
        nested_data: Dict[str, Any] = {
            "push": {
                "sound": {
                    "name": "default",
                    "critical": 1,
                    "volume": float(volume)
                }
            }
        }
        if url:
            nested_data["url"] = url
        if tag:
            nested_data["tag"] = tag

        payload = {
            "title": title,
            "message": message,
            "data": nested_data
        }
        return self._call_ha_notify(device, payload)

    def send_live(
        self,
        tag: str,
        title: str,
        message: str,
        url: Optional[str] = None,
        progress: Optional[int] = None,
        progress_max: Optional[int] = None,
        progress_bar_direction: Optional[str] = None,
        chronometer: bool = False,
        when: Optional[int] = None,
        when_relative: bool = True,
        notification_icon: Optional[str] = None,
        notification_icon_color: Optional[str] = None,
        color: Optional[str] = None,
        background_color: Optional[str] = "#1C1C1E",
        text_color: Optional[str] = None,
        critical_text: Optional[str] = None,
        device: Optional[str] = None,
        silent: bool = False
    ) -> bool:
        """
        發起或原地動態更新一則 Live Activity (即時動態 / 靈動島)。
        """
        device = device or self.default_device
        nested_data: Dict[str, Any] = {
            "tag": tag,
            "live_update": True,
            "background_color": background_color
        }

        if url:
            nested_data["url"] = url
        if progress is not None:
            nested_data["progress"] = int(progress)
        if progress_max is not None:
            nested_data["progress_max"] = int(progress_max)
        if progress_bar_direction:
            nested_data["progress_bar_direction"] = progress_bar_direction
        if chronometer:
            nested_data["chronometer"] = True
            if when is not None:
                nested_data["when"] = int(when)
                nested_data["when_relative"] = bool(when_relative)
        if notification_icon:
            nested_data["notification_icon"] = notification_icon
        if notification_icon_color:
            nested_data["notification_icon_color"] = notification_icon_color
        if color:
            nested_data["color"] = color
        if text_color:
            nested_data["text_color"] = text_color
        if critical_text:
            nested_data["critical_text"] = critical_text
        if silent:
            nested_data["silent"] = True
        else:
            nested_data["push"] = {
                "sound": "default",
                "interruption-level": "time-sensitive"
            }

        payload = {
            "title": title,
            "message": message,
            "data": nested_data
        }
        return self._call_ha_notify(device, payload)

    def clear(self, tag: str, device: Optional[str] = None) -> bool:
        """
        遠端撤回清除指定 tag 的通知（支援 Critical Alert 與 Live Activity）。
        在 iOS 設備上將直接從通知中心與鎖定畫面中移除消失。
        """
        device = device or self.default_device
        payload = {
            "message": "clear_notification",
            "data": {
                "tag": tag
            }
        }
        return self._call_ha_notify(device, payload)

    def clear_live(self, tag: str, device: Optional[str] = None) -> bool:
        """向後相容 alias"""
        return self.clear(tag, device)

    def send_imessage(
        self,
        message: str,
        to: str = "me",
        title: Optional[str] = None
    ) -> bool:
        """
        發送 macOS 原生 iMessage 通道訊息
        - 嚴格受白名單保護
        - 支援別名解析（如「我」、「家人」）
        - 若提供 title，自動排版為「【title】\nmessage」
        """
        full_text = f"【{title}】\n{message}" if title else message
        try:
            imsg_script = BASE_DIR / ".agents" / "skills" / "imessage" / "scripts"
            if str(imsg_script) not in sys.path:
                sys.path.insert(0, str(imsg_script))
            from send_imessage import send_imessage as _raw_send_imsg

            res = _raw_send_imsg(to, full_text)
            if res.get("success"):
                return True
            else:
                print(f"[IOSNotify] ❌ iMessage 發送失敗: {res.get('error')}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[IOSNotify] ❌ iMessage 呼叫異常: {e}", file=sys.stderr)
            return False


# 全域實例便於快捷呼叫
_client = IOSNotifier()

def send_critical_alert(*args, **kwargs) -> bool:
    return _client.send_critical(*args, **kwargs)

def send_live_activity(*args, **kwargs) -> bool:
    return _client.send_live(*args, **kwargs)

def clear_notification(tag: str, device: Optional[str] = None) -> bool:
    return _client.clear(tag, device)

def clear_live_activity(*args, **kwargs) -> bool:
    return _client.clear(*args, **kwargs)

def send_imessage(*args, **kwargs) -> bool:
    return _client.send_imessage(*args, **kwargs)


# CLI 介面
def main():
    parser = argparse.ArgumentParser(description="iOS 原生進階推播中心 (Critical Alert, Live Activity, iMessage)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. critical
    crit_parser = subparsers.add_parser("critical", help="發送重要置頂警報 (永遠置頂/可靜音/不受配額限制)")
    crit_parser.add_argument("--title", required=True, help="標題")
    crit_parser.add_argument("--message", required=True, help="主要訊息內容")
    crit_parser.add_argument("--url", help="點擊跳轉的網址或 Deep Link (例如 FB App 原生協議)")
    crit_parser.add_argument("--volume", type=float, default=0.0, help="警報音量 0.0~1.0 (預設 0.0 完全靜音)")
    crit_parser.add_argument("--device", help="目標設備名稱")
    crit_parser.add_argument("--tag", help="唯一識別標籤 ID (用於後續遠端銷掉撤回通知)")

    # 2. live
    live_parser = subparsers.add_parser("live", help="發送或更新即時動態 (靈動島/進度條/倒數碼表)")
    live_parser.add_argument("--tag", required=True, help="唯一識別標籤 ID (必填)")
    live_parser.add_argument("--title", required=True, help="標題")
    live_parser.add_argument("--message", required=True, help="主要訊息內容")
    live_parser.add_argument("--url", help="點擊跳轉網址")
    live_parser.add_argument("--progress", type=int, help="當前進度值")
    live_parser.add_argument("--progress-max", type=int, help="最大進度值")
    live_parser.add_argument("--chronometer", action="store_true", help="啟用本地倒數碼表")
    live_parser.add_argument("--when", type=int, help="倒數秒數")
    live_parser.add_argument("--critical-text", help="靈動島縮寫字")
    live_parser.add_argument("--icon", help="Material Design 圖示")
    live_parser.add_argument("--device", help="目標設備名稱")
    live_parser.add_argument("--silent", action="store_true", help="靜音更新")

    # 3. clear
    clear_parser = subparsers.add_parser("clear", help="遠端撤回清除指定 tag 的通知或 Live Activity (已讀消滅)")
    clear_parser.add_argument("--tag", required=True, help="要清除的標籤 ID")
    clear_parser.add_argument("--device", help="目標設備名稱")

    # 4. imessage
    imsg_parser = subparsers.add_parser("imessage", help="發送 macOS 原生 iMessage 訊息 (受白名單保護)")
    imsg_parser.add_argument("--message", required=True, help="主要訊息文字")
    imsg_parser.add_argument("--to", default="me", help="收件人別名或電話 (預設: me，可填 我/家人)")
    imsg_parser.add_argument("--title", help="訊息標題 (將加於訊息首行)")

    args = parser.parse_args()

    if args.command == "critical":
        ok = send_critical_alert(
            title=args.title,
            message=args.message,
            url=args.url,
            volume=args.volume,
            device=args.device,
            tag=args.tag
        )
        sys.exit(0 if ok else 1)
    elif args.command == "live":
        ok = send_live_activity(
            tag=args.tag,
            title=args.title,
            message=args.message,
            url=args.url,
            progress=args.progress,
            progress_max=args.progress_max,
            chronometer=args.chronometer,
            when=args.when,
            critical_text=args.critical_text,
            notification_icon=args.icon,
            device=args.device,
            silent=args.silent
        )
        sys.exit(0 if ok else 1)
    elif args.command == "clear":
        ok = clear_notification(tag=args.tag, device=args.device)
        sys.exit(0 if ok else 1)
    elif args.command == "imessage":
        ok = send_imessage(
            message=args.message,
            to=args.to,
            title=args.title
        )
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
