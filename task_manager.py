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
import json
import time
import signal
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

from common.venv_guard import ensure_venv
ensure_venv()

from config import settings

TASKS_JSON_PATH = settings.DATA_DIR / "background_tasks.json"
TASK_LOGS_DIR = settings.DATA_DIR / "task_logs"

def is_pid_alive(pid: int) -> bool:
    """檢查指定 PID 是否仍在運行中"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

class TaskManager:
    def __init__(self):
        TASK_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        if not TASKS_JSON_PATH.exists():
            self._save_tasks([])

    def _load_tasks(self) -> List[Dict[str, Any]]:
        try:
            if TASKS_JSON_PATH.exists():
                with open(TASKS_JSON_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[TaskManager] 讀取 tasks.json 失敗: {e}", file=sys.stderr)
        return []

    def _save_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        try:
            with open(TASKS_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TaskManager] 儲存 tasks.json 失敗: {e}", file=sys.stderr)

    def spawn(self, name: str, script: str, args: Optional[List[str]] = None, ttl_minutes: int = 120) -> Dict[str, Any]:
        """
        以完全脫鉤 (Detached) 方式啟動背景長任務
        """
        script_path = Path(script)
        if not script_path.is_absolute():
            script_path = settings.BASE_DIR / script_path

        if not script_path.exists():
            return {
                "success": False,
                "error": f"腳本檔案不存在: {script_path}"
            }

        task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        log_file_path = TASK_LOGS_DIR / f"{task_id}.log"

        # 開啟專屬日誌檔案
        log_fp = open(log_file_path, "a", encoding="utf-8")
        log_fp.write(f"=== 任務啟動: {name} (ID: {task_id}) ===\n")
        log_fp.write(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_fp.write(f"腳本: {script_path}\n")
        log_fp.write(f"TTL 壽命限制: {ttl_minutes} 分鐘\n")
        log_fp.write("=" * 40 + "\n\n")
        log_fp.flush()

        cmd = [sys.executable, "-u", str(script_path)]
        if args:
            cmd.extend(args)

        # start_new_session=True 讓其成為完全獨立的進程群組 (Detached OS Daemon)
        proc = subprocess.Popen(
            cmd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            cwd=str(settings.BASE_DIR),
            start_new_session=True
        )
        log_fp.close()

        record = {
            "task_id": task_id,
            "pid": proc.pid,
            "name": name,
            "script": str(script_path.relative_to(settings.BASE_DIR) if script_path.is_relative_to(settings.BASE_DIR) else script_path),
            "log_file": str(log_file_path.relative_to(settings.BASE_DIR) if log_file_path.is_relative_to(settings.BASE_DIR) else log_file_path),
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ttl_minutes": ttl_minutes,
            "status": "RUNNING"
        }

        tasks = self._load_tasks()
        tasks.append(record)
        self._save_tasks(tasks)

        return {
            "success": True,
            "task_id": task_id,
            "pid": proc.pid,
            "name": name,
            "log_file": record["log_file"],
            "ttl_minutes": ttl_minutes
        }

    def list_tasks(self, include_finished: bool = False) -> List[Dict[str, Any]]:
        """
        查詢當前任務清單，並自動執行生命週期檢查與自動修剪 (Pruning)
        """
        tasks = self._load_tasks()
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
        """優雅關閉進程，若未退出則強制 SIGKILL"""
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
    sp_spawn = subparsers.add_parser("spawn", help="啟動獨立背景任務")
    sp_spawn.add_argument("--name", required=True, help="任務名稱")
    sp_spawn.add_argument("--script", required=True, help="腳本路徑")
    sp_spawn.add_argument("--ttl", type=int, default=120, help="生命週期 (分鐘，預設 120)")
    sp_spawn.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="傳入腳本之參數 (請置於指令最後)")

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
        res = task_manager.spawn(name=args.name, script=args.script, args=args.args, ttl_minutes=args.ttl)
        print(json.dumps(res, ensure_ascii=False, indent=2))
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
