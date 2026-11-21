#!/usr/bin/env python3
"""閒置空檔安全重載守望者.

等語音串流完全閒置一段時間後,才用 launchd 重啟服務,
避免在語音串流進行中強殺. 供 TaskManager 短期任務呼叫.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

def launchd_target() -> str:
    """動態查出本機助理 launchd 標籤,不在倉庫寫死使用者名稱."""
    import os

    uid = os.getuid()
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "mac-mini-assistant" in line:
            return f"gui/{uid}/{line.split()[-1]}"
    raise RuntimeError("找不到 mac-mini-assistant launchd 標籤")




def load_env() -> dict:
    env: dict = {}
    for line in (BASE / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api_get(env: dict, path: str, timeout: int = 10) -> dict | list:
    import json

    port = env.get("ASSISTANT_PORT") or env.get("PORT") or "8899"
    key = env.get("ASSISTANT_API_KEY") or env.get("API_KEY") or ""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": f"Bearer {key}", "X-API-Key": key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def is_idle(env: dict) -> tuple[bool, str]:
    try:
        snap = api_get(env, "/api/voice-chat/queue")
    except Exception as ex:  # noqa: BLE001 - 守望者只記錄不中斷
        return False, f"queue-query-failed:{ex!r}"[:120]
    active = snap.get("active_turn")
    pending = snap.get("pending_queue") or []
    if active or pending:
        q = (active or {}).get("query", "")[:30] if isinstance(active, dict) else ""
        return False, f"busy:active={'Y' if active else '-'} pending={len(pending)} q={q}"
    return True, "idle"


def wait_idle(env: dict, quiet: int, interval: int, max_wait_min: int) -> bool:
    deadline = time.time() + max_wait_min * 60
    idle_since: float | None = None
    while time.time() < deadline:
        ok, why = is_idle(env)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] idle-watch: {why}", flush=True)
        now = time.time()
        if ok:
            idle_since = idle_since if idle_since is not None else now
            if now - idle_since >= quiet:
                return True
        else:
            idle_since = None
        time.sleep(interval)
    return False


def restart() -> None:
    subprocess.run(["launchctl", "kickstart", "-k", launchd_target()], check=True)


def wait_healthy(env: dict, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api_get(env, "/health", timeout=5)
            return True
        except Exception:  # noqa: BLE001 - 起來前必然連線失敗,繼續等
            time.sleep(3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", type=int, default=180, help="連續閒置秒數才重載")
    ap.add_argument("--interval", type=int, default=15, help="輪詢秒數")
    ap.add_argument("--max-wait-min", type=int, default=90, help="最多等幾分鐘")
    ap.add_argument("--check-once", action="store_true", help="只查一次閒置狀態")
    args = ap.parse_args()

    env = load_env()
    if args.check_once:
        ok, why = is_idle(env)
        print(f"idle={ok} {why}")
        return 0 if ok else 1

    print(f"idle-watch start: quiet={args.quiet}s interval={args.interval}s", flush=True)
    if not wait_idle(env, args.quiet, args.interval, args.max_wait_min):
        print("idle-watch: timeout, 本輪放棄重載", flush=True)
        return 2
    print("idle-watch: 空檔確認,執行 launchd kickstart -k", flush=True)
    restart()
    if wait_healthy(env):
        print("idle-watch: DONE 重載完成, /health 已恢復", flush=True)
        return 0
    print("idle-watch: FAIL 重載後 health 未恢復,請人工檢查", flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
