"""
Mac mini Assistant 背景任務管理器 (Background Task Manager)
職責：
1. 登記與分離啟動 (Spawn) 背景長任務腳本 (Detached OS Process)
2. 輸出日誌導流至 data/task_logs/<task_id>.log，隨時支援 tail 追查
3. 支援生命週期安全閥 (TTL)，超時自動終止防範死鎖
4. 智慧巡檢與自動修剪 (Pruning)，定期清理已結束的 PID 與過期日誌，防止 json 膨脹
5. 提供 CLI 指令與 Telegram 介面隨時查詢狀態與一鍵停止 (Kill Switch)
"""

import os
import sys

# 跨平台 UTF-8 輸出防護：防止 Windows 終端預設 cp1252 編碼拋出 UnicodeEncodeError
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import re
import html
import json
import time
import signal
import argparse
import tempfile
import threading
import subprocess
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

from common.venv_guard import ensure_venv
ensure_venv()

from config import settings

TASKS_JSON_PATH = settings.DATA_DIR / "background_tasks.json"
TASK_LOGS_DIR = settings.DATA_DIR / "task_logs"
_TASK_JSON_LOCK = threading.Lock()
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _extract_clean_log_tail(log_path: Path, max_lines: int = 20, max_chars: int = 1200) -> str:
    """讀取任務日誌尾端，清除 ANSI 控制碼與空白雜訊"""
    if not log_path.exists():
        return ""
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    cleaned_lines = []
    for line in _strip_ansi(raw).splitlines():
        s = line.rstrip()
        if s.strip():
            cleaned_lines.append(s)
    tail = "\n".join(cleaned_lines[-max_lines:])
    if len(tail) > max_chars:
        tail = "...\n" + tail[-max_chars:]
    return tail


def _format_duration(seconds: float) -> str:
    secs = max(0, int(round(seconds)))
    mins, rem = divmod(secs, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m {rem}s"
    if mins > 0:
        return f"{mins}m {rem}s"
    return f"{rem}s"


def _send_telegram_notification(message_html: str) -> bool:
    """帶外發送 Telegram HTML 通知"""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": str(chat_id),
        "text": message_html[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TaskManager] ⚠️ Telegram 推播失敗: {e}", file=sys.stderr)
        return False


def _send_ios_notification(title: str, message: str) -> bool:
    """帶外發送 iOS Critical Alert（靜音置頂，透過 HA Companion App）"""
    try:
        from scripts.ios_notify import IOSNotifier
        return IOSNotifier().send_critical(title=title, message=message, volume=0.0)
    except Exception as e:
        print(f"[TaskManager] ℹ️ iOS 推播略過: {e}", file=sys.stderr)
        return False


def _dispatch_notifications(
    notify_channels: str,
    name: str,
    task_id: str,
    returncode: Optional[int],
    timed_out: bool,
    elapsed_sec: float,
    tail_text: str,
) -> None:
    """依設定之通道列表主動推播任務結果"""
    channels = {c.strip().lower() for c in (notify_channels or "").split(",") if c.strip() and c.strip().lower() != "none"}
    if not channels:
        return

    dur_str = _format_duration(elapsed_sec)
    if timed_out:
        icon, status_label = "⏱️", "逾時終止 (TTL Expired)"
    elif returncode == 0:
        icon, status_label = "✅", "執行完成"
    else:
        icon, status_label = "❌", f"執行失敗 (Exit {returncode})"

    if "telegram" in channels:
        msg = (
            f"{icon} <b>背景任務{status_label}</b>\n"
            f"• 任務：<code>{html.escape(name)}</code>\n"
            f"• ID：<code>{html.escape(task_id)}</code>（耗時 {dur_str}）"
        )
        if tail_text:
            msg += f"\n\n<pre>{html.escape(tail_text[-1000:])}</pre>"
        _send_telegram_notification(msg)

    if "ios" in channels:
        short_tail = (tail_text.splitlines()[-1] if tail_text else status_label)[:180]
        _send_ios_notification(title=f"{icon} {name}", message=f"{status_label}（{dur_str}）\n{short_tail}")


def _sync_task_result_to_memory(
    task_id: str,
    name: str,
    returncode: Optional[int],
    timed_out: bool,
    elapsed_sec: float,
    tail_text: str,
) -> None:
    """寫入 data/history.jsonl 並於主大腦閒置時靜默同步 3 行摘要"""
    dur_str = _format_duration(elapsed_sec)
    status_str = "逾時終止" if timed_out else ("成功完成" if returncode == 0 else f"失敗 (exit={returncode})")
    summary = f"背景任務「{name}」（{task_id}）{status_str}，耗時 {dur_str}。"
    if tail_text:
        last_line = tail_text.splitlines()[-1][:220]
        summary += f" 最後輸出：{last_line}"

    try:
        from memory_manager import memory_manager
        memory_manager.log_interaction(
            f"[背景任務 · {name}]",
            summary,
            actions=[f"task_manager ({task_id})", f"status={status_str}"],
        )
    except Exception as e:
        print(f"[TaskManager] ⚠️ 寫入 history.jsonl 失敗: {e}", file=sys.stderr)

    try:
        port = getattr(settings, "PORT", 8899)
        api_key = getattr(settings, "ASSISTANT_API_KEY", "") or os.getenv("ASSISTANT_API_KEY", "")
        url = f"http://127.0.0.1:{port}/api/internal/memory-sync"
        memo = (
            f"背景任務：{name}（{task_id}）\n"
            f"執行結果：{status_str}（耗時 {dur_str}）\n"
            f"摘要輸出：{(tail_text.splitlines()[-1] if tail_text else '無輸出')[:220]}"
        )
        req_data = json.dumps({"summary": memo, "source": f"task:{task_id}"}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Agent-Key"] = api_key
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            resp.read()
    except Exception as e:
        print(f"[TaskManager] ℹ️ 主大腦靜默記憶同步略過: {e}", file=sys.stderr)


def _run_lifecycle_hook(
    hook_cmd: Optional[str],
    cwd: str,
    task_id: str,
    name: str,
    returncode: Optional[int],
    log_path: Path,
) -> None:
    """執行成功/失敗後續鉤子指令"""
    if not hook_cmd:
        return
    env = os.environ.copy()
    env.update({
        "TASK_ID": task_id,
        "TASK_NAME": name,
        "TASK_EXIT_CODE": "" if returncode is None else str(returncode),
        "TASK_LOG_FILE": str(log_path),
    })
    try:
        with open(log_path, "a", encoding="utf-8") as log_fp:
            log_fp.write(f"\n=== 觸發生命週期鉤子: {hook_cmd} ===\n")
            log_fp.flush()
            subprocess.run(hook_cmd, shell=True, cwd=cwd, env=env, stdout=log_fp, stderr=subprocess.STDOUT, timeout=300)
    except Exception as e:
        print(f"[TaskManager] ⚠️ 生命週期鉤子執行失敗: {e}", file=sys.stderr)


def _run_wrapped_task(
    task_id: str,
    name: str,
    log_file: str,
    ttl_minutes: int,
    notify: str = "",
    sync_memory: bool = False,
    cwd: Optional[str] = None,
    cmd: Optional[str] = None,
    script: Optional[str] = None,
    script_args_json: Optional[str] = None,
    on_success_cmd: Optional[str] = None,
    on_fail_cmd: Optional[str] = None,
) -> int:
    """通用背景任務監督包裹器：執行 -> 寫入日誌 -> 通知 -> 記憶同步 -> Hook"""
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = settings.BASE_DIR / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = cwd or str(settings.BASE_DIR)

    start_ts = time.time()
    returncode: Optional[int] = None
    timed_out = False

    with open(log_path, "a", encoding="utf-8") as log_fp:
        if cmd:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=work_dir,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
            )
        elif script:
            s_args = json.loads(script_args_json) if script_args_json else []
            proc = subprocess.Popen(
                [sys.executable, "-u", script, *s_args],
                cwd=work_dir,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
            )
        else:
            log_fp.write("[TaskManager] ❌ 未提供 cmd 或 script。\n")
            return 2

        try:
            timeout_sec = max(1, ttl_minutes) * 60 if ttl_minutes and ttl_minutes > 0 else None
            returncode = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            returncode = proc.returncode if proc.returncode is not None else 124
            log_fp.write(f"\n=== 任務超過 TTL ({ttl_minutes} 分鐘)，已終止 ===\n")

        elapsed = time.time() - start_ts
        log_fp.write(f"\n=== 任務結束 (Exit={returncode}, 耗時={_format_duration(elapsed)}) ===\n")
        log_fp.flush()

    elapsed = time.time() - start_ts
    tail_text = _extract_clean_log_tail(log_path)

    _dispatch_notifications(
        notify_channels=notify,
        name=name,
        task_id=task_id,
        returncode=returncode,
        timed_out=timed_out,
        elapsed_sec=elapsed,
        tail_text=tail_text,
    )

    if sync_memory:
        _sync_task_result_to_memory(
            task_id=task_id,
            name=name,
            returncode=returncode,
            timed_out=timed_out,
            elapsed_sec=elapsed,
            tail_text=tail_text,
        )

    if not timed_out and returncode == 0:
        _run_lifecycle_hook(on_success_cmd, work_dir, task_id, name, returncode, log_path)
    else:
        _run_lifecycle_hook(on_fail_cmd, work_dir, task_id, name, returncode, log_path)

    return returncode if returncode is not None else 1

def is_pid_alive(pid: int) -> bool:
    """檢查指定 PID 是否仍在運行中 (跨平台支援 macOS / Linux / Windows)"""
    if pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
            )
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        except Exception:
            return False

    try:
        # 若為當前進程之子進程，嘗試非阻塞檢查並回收 zombie
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                return False
        except (ChildProcessError, OSError):
            pass

        os.kill(pid, 0)
        return True
    except OSError:
        return False

class TaskStoreError(Exception):
    """任務登記檔讀取或解析異常（避免以空清單覆寫原檔）"""


class TaskManager:
    def __init__(self):
        TASK_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        if not TASKS_JSON_PATH.exists():
            self._save_tasks([])

    def _load_tasks(self) -> List[Dict[str, Any]]:
        with _TASK_JSON_LOCK:
            if not TASKS_JSON_PATH.exists():
                return []
            try:
                with open(TASKS_JSON_PATH, "r", encoding="utf-8") as f:
                    if _fcntl is not None:
                        _fcntl.flock(f.fileno(), _fcntl.LOCK_SH)
                    try:
                        raw = f.read()
                    finally:
                        if _fcntl is not None:
                            _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
                if not raw.strip():
                    return []
                data = json.loads(raw)
                if not isinstance(data, list):
                    raise TaskStoreError(f"tasks.json 格式非 list: {type(data).__name__}")
                return data
            except TaskStoreError:
                raise
            except Exception as e:
                print(f"[TaskManager] 讀取 tasks.json 失敗: {e}", file=sys.stderr)
                raise TaskStoreError(str(e)) from e

    def _save_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        with _TASK_JSON_LOCK:
            tmp_path: Optional[Path] = None
            lock_fp = None
            try:
                TASKS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
                if _fcntl is not None:
                    lock_path = TASKS_JSON_PATH.with_suffix(".lock")
                    lock_fp = open(lock_path, "a+", encoding="utf-8")
                    _fcntl.flock(lock_fp.fileno(), _fcntl.LOCK_EX)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=str(TASKS_JSON_PATH.parent),
                    prefix=".tasks_",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    json.dump(tasks, tmp, ensure_ascii=False, indent=2)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_path, TASKS_JSON_PATH)
                tmp_path = None
            except Exception as e:
                print(f"[TaskManager] 儲存 tasks.json 失敗: {e}", file=sys.stderr)
            finally:
                if tmp_path is not None and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                if lock_fp is not None:
                    try:
                        if _fcntl is not None:
                            _fcntl.flock(lock_fp.fileno(), _fcntl.LOCK_UN)
                        lock_fp.close()
                    except Exception:
                        pass

    def _generate_task_id(self) -> str:
        base = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        candidate = base
        idx = 1
        while (TASK_LOGS_DIR / f"{candidate}.log").exists():
            candidate = f"{base}_{idx}"
            idx += 1
        return candidate

    def _spawn_detached(
        self,
        task_id: str,
        name: str,
        target_label: str,
        cmd: List[str],
        log_file_path: Path,
        ttl_minutes: int,
        cwd: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        log_fp = open(log_file_path, "a", encoding="utf-8")
        log_fp.write(f"=== 任務啟動: {name} (ID: {task_id}) ===\n")
        log_fp.write(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_fp.write(f"目標: {target_label}\n")
        log_fp.write(f"TTL 壽命限制: {ttl_minutes} 分鐘\n")
        log_fp.write("=" * 40 + "\n\n")
        log_fp.flush()

        popen_kwargs: Dict[str, Any] = {
            "stdout": log_fp,
            "stderr": subprocess.STDOUT,
            "cwd": cwd or str(settings.BASE_DIR),
        }
        if sys.platform == "win32":
            creationflags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creationflags |= subprocess.DETACHED_PROCESS
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        log_fp.close()

        rel_log = str(log_file_path.relative_to(settings.BASE_DIR) if log_file_path.is_relative_to(settings.BASE_DIR) else log_file_path)
        record = {
            "task_id": task_id,
            "pid": proc.pid,
            "name": name,
            "script": target_label,
            "log_file": rel_log,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ttl_minutes": ttl_minutes,
            "status": "RUNNING",
        }
        if extra_fields:
            record.update(extra_fields)

        try:
            tasks = self._load_tasks()
        except TaskStoreError:
            # 損壞時先備份再重建，避免靜默遺失既有狀態
            if TASKS_JSON_PATH.exists():
                backup = TASKS_JSON_PATH.with_suffix(f".corrupt.{int(time.time())}.json")
                try:
                    TASKS_JSON_PATH.replace(backup)
                except Exception:
                    pass
            tasks = []
        tasks.append(record)
        self._save_tasks(tasks)

        return {
            "success": True,
            "task_id": task_id,
            "pid": proc.pid,
            "name": name,
            "log_file": rel_log,
            "ttl_minutes": ttl_minutes,
        }

    def adopt_task(
        self,
        task_id: str,
        pid: int,
        name: str,
        script: str,
        log_file: str,
        started_at: str,
        ttl_minutes: int,
        auto_restart: bool = False,
        restart_spec: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """將已存活於 OS 中的脫鉤背景進程補登回任務名冊（不重啟進程）"""
        if not is_pid_alive(pid):
            return {"success": False, "error": f"PID {pid} 未存活，無法補登"}
        try:
            tasks = self._load_tasks()
        except TaskStoreError as e:
            return {"success": False, "error": str(e)}

        for t in tasks:
            if t.get("task_id") == task_id or t.get("pid") == pid:
                return {"success": True, "already_registered": True, "task_id": t.get("task_id")}

        record: Dict[str, Any] = {
            "task_id": task_id,
            "pid": pid,
            "name": name,
            "script": script,
            "log_file": log_file,
            "started_at": started_at,
            "ttl_minutes": ttl_minutes,
            "status": "RUNNING",
        }
        if auto_restart:
            record["auto_restart"] = True
        if restart_spec:
            record["restart_spec"] = restart_spec
        tasks.append(record)
        self._save_tasks(tasks)
        return {"success": True, "task_id": task_id, "pid": pid}

    def spawn(
        self,
        name: str,
        script: str,
        args: Optional[List[str]] = None,
        ttl_minutes: int = 120,
        notify: Optional[str] = None,
        sync_memory: bool = False,
        on_success_cmd: Optional[str] = None,
        on_fail_cmd: Optional[str] = None,
        auto_restart: bool = False,
    ) -> Dict[str, Any]:
        """
        以完全脫鉤 (Detached) 方式啟動背景長任務腳本；
        若指定 notify / sync_memory / hook，將自動透過通用監督包裹器執行。
        """
        script_path = Path(script)
        if not script_path.is_absolute():
            script_path = settings.BASE_DIR / script_path

        if not script_path.exists():
            return {
                "success": False,
                "error": f"腳本檔案不存在: {script_path}"
            }

        task_id = self._generate_task_id()
        log_file_path = TASK_LOGS_DIR / f"{task_id}.log"
        target_label = str(script_path.relative_to(settings.BASE_DIR) if script_path.is_relative_to(settings.BASE_DIR) else script_path)

        use_wrapper = bool(notify or sync_memory or on_success_cmd or on_fail_cmd)
        if not use_wrapper:
            cmd = [sys.executable, "-u", str(script_path)]
            if args:
                cmd.extend(args)
        else:
            cmd = [
                sys.executable, "-u", str(Path(__file__).resolve()), "_wrap",
                "--task-id", task_id,
                "--name", name,
                "--log-file", str(log_file_path),
                "--ttl", str(ttl_minutes),
                "--script", str(script_path),
                "--script-args-json", json.dumps(args or []),
            ]
            if notify:
                cmd.extend(["--notify", notify])
            if sync_memory:
                cmd.append("--sync-memory")
            if on_success_cmd:
                cmd.extend(["--on-success-cmd", on_success_cmd])
            if on_fail_cmd:
                cmd.extend(["--on-fail-cmd", on_fail_cmd])

        extra_fields: Dict[str, Any] = {}
        if auto_restart:
            extra_fields["auto_restart"] = True
            extra_fields["restart_spec"] = {
                "kind": "spawn",
                "name": name,
                "script": str(script_path),
                "args": list(args or []),
                "ttl_minutes": ttl_minutes,
                "notify": notify,
                "sync_memory": sync_memory,
                "on_success_cmd": on_success_cmd,
                "on_fail_cmd": on_fail_cmd,
            }

        return self._spawn_detached(
            task_id, name, target_label, cmd, log_file_path, ttl_minutes, extra_fields=extra_fields or None
        )

    def spawn_cmd(
        self,
        name: str,
        cmd: str,
        cwd: Optional[str] = None,
        ttl_minutes: int = 120,
        notify: Optional[str] = "telegram",
        sync_memory: bool = True,
        on_success_cmd: Optional[str] = None,
        on_fail_cmd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """以完全脫鉤方式啟動任意 Shell / CLI 命令，並於結束時主動通知與同步記憶"""
        if not cmd or not cmd.strip():
            return {"success": False, "error": "cmd 不可為空"}

        task_id = self._generate_task_id()
        log_file_path = TASK_LOGS_DIR / f"{task_id}.log"
        work_dir = cwd or str(settings.BASE_DIR)

        wrap_cmd = [
            sys.executable, "-u", str(Path(__file__).resolve()), "_wrap",
            "--task-id", task_id,
            "--name", name,
            "--log-file", str(log_file_path),
            "--ttl", str(ttl_minutes),
            "--cwd", work_dir,
            "--cmd", cmd,
        ]
        if notify and notify.lower() != "none":
            wrap_cmd.extend(["--notify", notify])
        if sync_memory:
            wrap_cmd.append("--sync-memory")
        if on_success_cmd:
            wrap_cmd.extend(["--on-success-cmd", on_success_cmd])
        if on_fail_cmd:
            wrap_cmd.extend(["--on-fail-cmd", on_fail_cmd])

        return self._spawn_detached(task_id, name, cmd[:180], wrap_cmd, log_file_path, ttl_minutes, cwd=work_dir)

    def list_tasks(self, include_finished: bool = False) -> List[Dict[str, Any]]:
        """
        查詢當前任務清單，並自動執行生命週期檢查與自動修剪 (Pruning)
        """
        try:
            tasks = self._load_tasks()
        except TaskStoreError:
            # 讀取失敗時絕不覆寫原檔，回傳空清單供呼叫端安全降級
            return []
        now = datetime.now()
        updated_tasks = []

        for t in tasks:
            pid = t.get("pid", 0)
            status = t.get("status", "UNKNOWN")

            if status == "RUNNING":
                alive = is_pid_alive(pid)
                if not alive:
                    t["status"] = "COMPLETED"
                    t["finished_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # 檢查 TTL 是否超時
                    try:
                        started = datetime.strptime(t["started_at"], "%Y-%m-%d %H:%M:%S")
                        ttl = timedelta(minutes=t.get("ttl_minutes", 120))
                        if now - started > ttl:
                            # 超時強制終止
                            print(f"[TaskManager] ⚠️ 任務 {t['task_id']} (PID: {pid}) 超過 TTL ({t.get('ttl_minutes')}m)，強制終止！")
                            self._terminate_process(pid)
                            t["status"] = "TTL_EXPIRED"
                            t["finished_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception as e:
                        print(f"[TaskManager] TTL 檢查異常: {e}", file=sys.stderr)

            updated_tasks.append(t)

        # 自動修剪 (Pruning)：
        # 1. 所有 RUNNING 任務必定保留
        # 2. 已結束的任務只保留最近 5 筆，過期刪除，防止 json 膨脹
        running_tasks = [t for t in updated_tasks if t.get("status") == "RUNNING"]
        finished_tasks = [t for t in updated_tasks if t.get("status") != "RUNNING"]
        finished_tasks.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        pruned_finished = finished_tasks[:5]

        cleaned_tasks = running_tasks + pruned_finished
        self._save_tasks(cleaned_tasks)

        # 定期清理 3 天前的舊日誌檔案
        self._clean_old_logs(days=3)

        if include_finished:
            return cleaned_tasks
        return running_tasks

    def kill_task(self, target: str) -> Dict[str, Any]:
        """
        依 task_id, pid 或名稱終止指定任務
        """
        tasks = self.list_tasks(include_finished=True)
        matched = None
        target_str = str(target).strip()

        for t in tasks:
            if t.get("task_id") == target_str or str(t.get("pid")) == target_str:
                matched = t
                break
            if target_str.lower() in t.get("name", "").lower():
                matched = t
                break

        if not matched:
            return {"success": False, "error": f"找不到符合的任務：{target}"}

        pid = matched.get("pid", 0)
        task_id = matched.get("task_id")

        if matched.get("status") != "RUNNING" or not is_pid_alive(pid):
            matched["status"] = "COMPLETED"
            self._save_tasks(tasks)
            return {"success": True, "message": f"任務 {matched['name']} (PID: {pid}) 其實已經結束。"}

        success = self._terminate_process(pid)
        matched["status"] = "KILLED"
        matched["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_tasks(tasks)

        # 記錄至日誌檔
        try:
            log_path = settings.BASE_DIR / matched.get("log_file", "")
            if log_path.exists():
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n=== 任務已被手動終止 (時間: {matched['finished_at']}) ===\n")
        except Exception:
            pass

        return {
            "success": success,
            "task_id": task_id,
            "pid": pid,
            "name": matched["name"],
            "message": f"已成功終止任務「{matched['name']}」（PID: {pid}）"
        }

    def kill_all(self) -> List[Dict[str, Any]]:
        """終止所有正在運行的背景任務"""
        running = self.list_tasks(include_finished=False)
        results = []
        for t in running:
            res = self.kill_task(t["task_id"])
            results.append(res)
        return results

    def get_log(self, target: str, lines: int = 25) -> Dict[str, Any]:
        """讀取指定任務的最新 Log"""
        tasks = self.list_tasks(include_finished=True)
        matched = None
        target_str = str(target).strip()

        for t in tasks:
            if t.get("task_id") == target_str or str(t.get("pid")) == target_str:
                matched = t
                break
            if target_str.lower() in t.get("name", "").lower():
                matched = t
                break

        if not matched:
            return {"success": False, "error": f"找不到任務：{target}"}

        log_path = settings.BASE_DIR / matched.get("log_file", "")
        if not log_path.exists():
            return {"success": False, "error": f"日誌檔案不存在: {log_path}"}

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
                return {
                    "success": True,
                    "task_id": matched["task_id"],
                    "name": matched["name"],
                    "status": matched["status"],
                    "log_content": "".join(tail_lines).strip()
                }
        except Exception as e:
            return {"success": False, "error": f"讀取日誌失敗: {e}"}

    def _terminate_process(self, pid: int) -> bool:
        """優雅關閉進程，若未退出則強制關閉 (跨平台支援 macOS / Linux / Windows)"""
        if sys.platform == "win32":
            try:
                # Windows: 先嘗試溫和 taskkill，若未結束則加上 /F (強制) 與 /T (遞迴子進程樹)
                subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, check=False)
                for _ in range(10):
                    time.sleep(0.1)
                    if not is_pid_alive(pid):
                        return True
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
                time.sleep(0.1)
                return not is_pid_alive(pid)
            except Exception as e:
                print(f"[TaskManager] Windows 終止進程 {pid} 失敗: {e}", file=sys.stderr)
                return False

        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.1)
                if not is_pid_alive(pid):
                    return True
            os.kill(pid, signal.SIGKILL)
            return True
        except Exception as e:
            print(f"[TaskManager] 終止進程 {pid} 失敗: {e}", file=sys.stderr)
            return False

    def _clean_old_logs(self, days: int = 3):
        """清除超過 N 天的舊任務日誌"""
        try:
            cutoff = datetime.now() - timedelta(days=days)
            for f in TASK_LOGS_DIR.glob("task_*.log"):
                if f.stat().st_mtime < cutoff.timestamp():
                    f.unlink(missing_ok=True)
        except Exception as e:
            print(f"[TaskManager] 清理舊日誌異常: {e}", file=sys.stderr)

task_manager = TaskManager()

def main():
    parser = argparse.ArgumentParser(description="Mac mini Assistant 背景任務管理器")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # spawn
    sp_spawn = subparsers.add_parser("spawn", help="啟動獨立背景 Python 腳本任務")
    sp_spawn.add_argument("--name", required=True, help="任務名稱")
    sp_spawn.add_argument("--script", required=True, help="腳本路徑")
    sp_spawn.add_argument("--ttl", type=int, default=120, help="生命週期 (分鐘，預設 120)")
    sp_spawn.add_argument("--notify", default=None, help="完成通知通道 (例如 telegram 或 telegram,ios)")
    sp_spawn.add_argument("--sync-memory", action="store_true", help="完成時寫入 history.jsonl 並靜默同步主大腦")
    sp_spawn.add_argument("--on-success-cmd", default=None, help="成功時執行的鉤子指令")
    sp_spawn.add_argument("--on-fail-cmd", default=None, help="失敗/逾時時執行的鉤子指令")
    sp_spawn.add_argument("--auto-restart", action="store_true", help="異常退出時由中控自動重啟")
    sp_spawn.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="傳入腳本之參數 (請置於指令最後)")

    # spawn-cmd
    sp_cmd = subparsers.add_parser("spawn-cmd", help="啟動任意 Shell/CLI 背景任務（自動通知與同步記憶）")
    sp_cmd.add_argument("--name", required=True, help="任務名稱")
    sp_cmd.add_argument("--cmd", required=True, help="要執行的 Shell/CLI 指令")
    sp_cmd.add_argument("--cwd", default=None, help="執行目錄 (預設專案根目錄)")
    sp_cmd.add_argument("--ttl", type=int, default=120, help="生命週期 (分鐘，預設 120)")
    sp_cmd.add_argument("--notify", default="telegram", help="完成通知通道 (預設 telegram，設 none 關閉)")
    sp_cmd.add_argument("--no-sync-memory", action="store_true", help="停用完成後記憶同步")
    sp_cmd.add_argument("--on-success-cmd", default=None, help="成功時執行的鉤子指令")
    sp_cmd.add_argument("--on-fail-cmd", default=None, help="失敗/逾時時執行的鉤子指令")

    # _wrap (internal supervisor wrapper)
    sp_wrap = subparsers.add_parser("_wrap", help=argparse.SUPPRESS)
    sp_wrap.add_argument("--task-id", required=True)
    sp_wrap.add_argument("--name", required=True)
    sp_wrap.add_argument("--log-file", required=True)
    sp_wrap.add_argument("--ttl", type=int, default=120)
    sp_wrap.add_argument("--notify", default="")
    sp_wrap.add_argument("--sync-memory", action="store_true")
    sp_wrap.add_argument("--cwd", default=None)
    sp_wrap.add_argument("--cmd", default=None)
    sp_wrap.add_argument("--script", default=None)
    sp_wrap.add_argument("--script-args-json", default=None)
    sp_wrap.add_argument("--on-success-cmd", default=None)
    sp_wrap.add_argument("--on-fail-cmd", default=None)

    # list
    sp_list = subparsers.add_parser("list", help="列出背景任務")
    sp_list.add_argument("--all", action="store_true", help="包含已結束之任務")

    # kill
    sp_kill = subparsers.add_parser("kill", help="終止指定任務")
    sp_kill.add_argument("target", help="task_id, pid 或任務名稱")

    # kill-all
    subparsers.add_parser("kill-all", help="終止所有正在運行的任務")

    # log
    sp_log = subparsers.add_parser("log", help="查看任務最新日誌")
    sp_log.add_argument("target", help="task_id, pid 或任務名稱")
    sp_log.add_argument("--lines", type=int, default=25, help="讀取行數 (預設 25)")

    args = parser.parse_args()

    if args.command == "spawn":
        res = task_manager.spawn(
            name=args.name,
            script=args.script,
            args=args.args,
            ttl_minutes=args.ttl,
            notify=args.notify,
            sync_memory=args.sync_memory,
            on_success_cmd=args.on_success_cmd,
            on_fail_cmd=args.on_fail_cmd,
            auto_restart=args.auto_restart,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "spawn-cmd":
        res = task_manager.spawn_cmd(
            name=args.name,
            cmd=args.cmd,
            cwd=args.cwd,
            ttl_minutes=args.ttl,
            notify=args.notify,
            sync_memory=not args.no_sync_memory,
            on_success_cmd=args.on_success_cmd,
            on_fail_cmd=args.on_fail_cmd,
            auto_restart=args.auto_restart,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "_wrap":
        rc = _run_wrapped_task(
            task_id=args.task_id,
            name=args.name,
            log_file=args.log_file,
            ttl_minutes=args.ttl,
            notify=args.notify,
            sync_memory=args.sync_memory,
            cwd=args.cwd,
            cmd=args.cmd,
            script=args.script,
            script_args_json=args.script_args_json,
            on_success_cmd=args.on_success_cmd,
            on_fail_cmd=args.on_fail_cmd,
            auto_restart=args.auto_restart,
        )
        sys.exit(rc)
    elif args.command == "list":
        tasks = task_manager.list_tasks(include_finished=args.all)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    elif args.command == "kill":
        res = task_manager.kill_task(args.target)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "kill-all":
        res = task_manager.kill_all()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "log":
        res = task_manager.get_log(args.target, lines=args.lines)
        if res.get("success"):
            print(f"=== {res['name']} ({res['task_id']}, 狀態: {res['status']}) 最新日誌 ===")
            print(res["log_content"])
        else:
            print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
