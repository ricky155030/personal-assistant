#!/usr/bin/env python3
"""Wait until a target HH:MM today and send an iOS Critical Alert."""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.ios_notify import send_critical_alert


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule an iOS Critical Alert at HH:MM today.")
    parser.add_argument("--time", required=True, help="Target time in HH:MM format (24h)")
    parser.add_argument("--title", required=True, help="Notification title")
    parser.add_argument("--message", required=True, help="Notification message")
    args = parser.parse_args()

    hour_str, min_str = args.time.strip().split(":", 1)
    now = datetime.now()
    target = now.replace(hour=int(hour_str), minute=int(min_str), second=0, microsecond=0)
    wait_seconds = max(0.0, (target - now).total_seconds())

    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 距離 {args.time} 還有 {wait_seconds:.1f} 秒，進入倒數等待...",
        flush=True,
    )
    time.sleep(wait_seconds)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 時間到！發送重要通知...", flush=True)
    ok = send_critical_alert(title=args.title, message=args.message, volume=0.0)
    print(f"發送結果: {'✅ 成功' if ok else '❌ 失敗'}", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
