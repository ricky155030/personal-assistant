#!/usr/bin/env python3
"""
[INFRA-01] Google Workspace CLI (gws) 共用安全客戶端 (Unified GWS Client)
職責：
1. 統一收斂 gmail_tool / calendar_tool / sheets_tool / shopping-list 的 gws 呼叫邏輯。
2. 具備 Thread Lock + OS File Lock (fcntl.flock) 雙層鎖定保護，防止多執行緒 (ThreadPoolExecutor)
   或背景排程並發讀寫 ~/.config/gws/credentials.enc 時發生競態損毀。
3. 智慧增量備份：僅當主憑證檔案有變更或備份不存在時才寫入磁碟，消除並發查詢時的重複 I/O。
4. 內建 401 / Token 過期偵測與 Telegram 節流告警，以及暫時性網路錯誤 (429/5xx) 自動重試。
"""

import os
import sys
import json
import time
import fcntl
import shutil
import threading
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GWS_DIR = Path.home() / ".config" / "gws"
GWS_BACKUP_DIR = GWS_DIR / ".backup"
GWS_LOCK_FILE = Path("/tmp/mac_mini_gws_cred.lock")
GWS_AUTH_ALERT_STAMP = Path("/tmp/mac_mini_gws_auth_alert.stamp")
AUTH_ALERT_COOLDOWN_SEC = 6 * 3600  # 6 小時內最多發送一次過期告警

_THREAD_LOCK = threading.Lock()


class _FileLockContext:
    """跨進程與跨執行緒安全的憑證檔案鎖"""
    def __init__(self, lock_path: Path = GWS_LOCK_FILE):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def __enter__(self):
        _THREAD_LOCK.acquire()
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None
        except Exception:
            pass
        finally:
            _THREAD_LOCK.release()


def _ensure_credentials_backup() -> None:
    """
    若存在有效 credentials.enc，於檔案鎖保護下建立安全備份。
    僅在備份不存在或檔案大小/修改時間不同時才執行複製，避免多執行緒高頻寫入。
    """
    cred_file = GWS_DIR / "credentials.enc"
    key_file = GWS_DIR / ".encryption_key"
    if not (cred_file.exists() and key_file.exists()):
        return

    with _FileLockContext():
        if not (cred_file.exists() and key_file.exists()):
            return
        GWS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bak_cred = GWS_BACKUP_DIR / "credentials.enc"
        bak_key = GWS_BACKUP_DIR / ".encryption_key"

        try:
            if cred_file.stat().st_size > 0:
                need_copy_cred = (
                    not bak_cred.exists()
                    or bak_cred.stat().st_size != cred_file.stat().st_size
                    or int(bak_cred.stat().st_mtime) != int(cred_file.stat().st_mtime)
                )
                if need_copy_cred:
                    shutil.copy2(cred_file, bak_cred)

            if key_file.stat().st_size > 0:
                need_copy_key = (
                    not bak_key.exists()
                    or bak_key.stat().st_size != key_file.stat().st_size
                    or int(bak_key.stat().st_mtime) != int(key_file.stat().st_mtime)
                )
                if need_copy_key:
                    shutil.copy2(key_file, bak_key)
        except Exception as e:
            print(f"[GWSClient] ⚠️ 備份憑證時發生非致命異常: {e}", file=sys.stderr)


def _restore_credentials_if_needed() -> bool:
    """若主憑證遺失但安全備份存在，於檔案鎖保護下自動還原"""
    cred_file = GWS_DIR / "credentials.enc"
    if cred_file.exists() and cred_file.stat().st_size > 0:
        return False

    with _FileLockContext():
        if cred_file.exists() and cred_file.stat().st_size > 0:
            return False
        bak_cred = GWS_BACKUP_DIR / "credentials.enc"
        bak_key = GWS_BACKUP_DIR / ".encryption_key"
        if bak_cred.exists() and bak_cred.stat().st_size > 0:
            GWS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak_cred, cred_file)
            if bak_key.exists():
                shutil.copy2(bak_key, GWS_DIR / ".encryption_key")
            print("[GWSClient] 🛡️ 偵測到主憑證被誤刪，已自安全備份自動還原！", file=sys.stderr)
            return True
    return False


def _is_auth_expired_error(err_text: str) -> bool:
    """判定錯誤訊息是否屬於 OAuth 憑證過期或失效"""
    if not err_text:
        return False
    lower = err_text.lower()
    keywords = (
        "unauthenticated",
        "invalid_grant",
        "token has been expired or revoked",
        "invalid credentials",
        "login required",
        "autherror",
        "no credentials found",
    )
    return any(k in lower for k in keywords)


def _is_transient_error(err_text: str) -> bool:
    """判定是否為可重試之暫時性錯誤 (429 Rate Limit / 5xx / 網路波動)"""
    if not err_text:
        return False
    lower = err_text.lower()
    keywords = (
        "429",
        "rate limit",
        "ratelimitexceeded",
        "userratelimitexceeded",
        "500",
        "502",
        "503",
        "504",
        "backend error",
        "connection reset",
        "timed out",
        "temporary failure",
    )
    return any(k in lower for k in keywords)


def _notify_auth_expired_throttled(detail: str) -> None:
    """當偵測到 GWS OAuth Token 失效時，以 6 小時冷卻機制發送 Telegram 告警"""
    try:
        if GWS_AUTH_ALERT_STAMP.exists():
            age = time.time() - GWS_AUTH_ALERT_STAMP.stat().st_mtime
            if age < AUTH_ALERT_COOLDOWN_SEC:
                return
        GWS_AUTH_ALERT_STAMP.write_text(str(int(time.time())), encoding="utf-8")

        env_path = PROJECT_ROOT / ".env"
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if (not token or not chat_id) and env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TELEGRAM_CHAT_ID="):
                    chat_id = line.split("=", 1)[1].strip().strip('"').strip("'")

        if not token or not chat_id:
            return

        msg = (
            "🚨 【GWS 憑證異常預警】\n"
            "Mac mini 助理偵測到 Google Workspace CLI (gws) OAuth 授權可能已過期或失效。\n"
            f"錯誤摘要：{detail[:200]}\n"
            "請於 Mac mini 終端機執行 `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login` 重新授權。"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": msg}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5.0):
            pass
    except Exception:
        pass


def run_gws_command(args: List[str], max_retries: int = 2, timeout: int = 45) -> Dict[str, Any]:
    """
    以安全環境變數與雙層鎖定保護執行 `gws` CLI 指令。
    - 自動防護並還原遭誤刪之 `~/.config/gws/credentials.enc`
    - 遇 429 / 5xx 暫時性錯誤自動指數退避重試
    - 遇 401 / invalid_grant 自動標記 `auth_expired: True` 並觸發節流告警
    """
    _restore_credentials_if_needed()

    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = "file"
    env.pop("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", None)

    cmd = ["gws"] + list(args)
    last_err = ""

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last_err = f"gws command timed out after {timeout}s"
            if attempt < max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            return {"error": last_err, "retryable": True}
        except Exception as e:
            return {"error": f"gws execution failed: {e}"}

        # 若執行過程中主憑證遭 gws 異常清除，立即嘗試從備份還原並重試一次
        if result.returncode != 0:
            err_msg = (result.stderr or result.stdout or "").strip()
            last_err = err_msg
            restored = _restore_credentials_if_needed()
            if restored and attempt < max_retries:
                time.sleep(0.3)
                continue
            if _is_auth_expired_error(err_msg):
                _notify_auth_expired_throttled(err_msg)
                return {"error": err_msg, "auth_expired": True}
            if _is_transient_error(err_msg) and attempt < max_retries:
                time.sleep(0.8 * (attempt + 1))
                continue
            return {"error": err_msg}

        # 執行成功：確保備份最新有效憑證
        _ensure_credentials_backup()

        stdout = result.stdout.strip()
        lines = stdout.splitlines()
        json_lines = [l for l in lines if not l.startswith("Using keyring backend:")]
        clean_json = "\n".join(json_lines).strip()
        try:
            parsed = json.loads(clean_json) if clean_json else {}
            # 檢查 API JSON 內部是否回傳 401 / 429 error object
            if isinstance(parsed, dict) and "error" in parsed:
                err_obj = parsed["error"]
                err_str = json.dumps(err_obj, ensure_ascii=False) if isinstance(err_obj, dict) else str(err_obj)
                if _is_auth_expired_error(err_str):
                    _notify_auth_expired_throttled(err_str)
                    parsed["auth_expired"] = True
                elif _is_transient_error(err_str) and attempt < max_retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
            return parsed
        except Exception as e:
            return {"raw": stdout, "error": f"JSON parse error: {e}"}

    return {"error": last_err or "Unknown gws error"}
