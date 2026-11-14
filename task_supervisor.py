"""
Mac mini Assistant 中控任務監督協程 (Task Supervisor)
掛載於 main.py startup_event()，在不綁死子進程 (維持 start_new_session=True 脫鉤) 的前提下提供：
1. 定期巡檢 TaskManager：主動執行 TTL 超時回收，防止進程失控。
2. 異常退出自癒 (auto_restart)：對標記 auto_restart 的長期監控任務，非手動/非超時退出時自動重啟並通知。
3. 主大腦記憶同步待送佇列：當 /api/internal/memory-sync 遇上主大腦忙碌 (brain_busy) 時暫存排隊，閒置時自動補注。
"""

import asyncio
import html
import threading
import time
from collections import deque
from typing import Deque, Dict, Any, Tuple

from task_manager import task_manager


_MEMO_QUEUE_LOCK = threading.Lock()
_PENDING_MEMOS: Deque[Dict[str, Any]] = deque(maxlen=50)
_RESTART_COOLDOWN_SEC = 60.0
_LAST_RESTART_ATTEMPT: Dict[str, float] = {}


def get_pending_memo_count() -> int:
    with _MEMO_QUEUE_LOCK:
        return len(_PENDING_MEMOS)


def enqueue_memory_sync(summary: str, source: str) -> int:
    """將主大腦忙碌時無法即時注入的備忘加入待送佇列，回傳目前佇列長度"""
    with _MEMO_QUEUE_LOCK:
        _PENDING_MEMOS.append({
            "summary": summary,
            "source": source,
            "queued_at": time.time(),
        })
        return len(_PENDING_MEMOS)


def try_inject_memory_sync(summary: str, source: str) -> Tuple[bool, str]:
    """嘗試趁主大腦閒置時靜默注入備忘；回傳 (是否成功, 狀態碼)"""
    from agy_singleton import agy_singleton

    worker = agy_singleton.brain_pool.primary_worker
    if not worker.lock.acquire(blocking=False):
        return False, "brain_busy"
    worker.lock.release()

    if not worker._is_ready or worker.proc is None or worker.proc.poll() is not None:
        return False, "worker_not_ready"

    memo_prompt = (
        f"[系統背景備忘（來源: {source}），請僅回覆「收到」兩字，勿呼叫任何工具]\n"
        f"{summary}"
    )
    gen = worker.send_query_stream(memo_prompt, target_model=None)
    try:
        for _ in gen:
            pass
        return True, "ok"
    except Exception as ex:
        print(f"[TaskSupervisor] memory-sync 注入失敗: {ex}")
        return False, f"error:{ex}"
    finally:
        try:
            gen.close()
        except Exception:
            pass


def flush_memory_sync_queue() -> int:
    """嘗試消化待送備忘佇列，回傳本次成功注入筆數"""
    flushed = 0
    while True:
        with _MEMO_QUEUE_LOCK:
            if not _PENDING_MEMOS:
                break
            item = _PENDING_MEMOS[0]

        ok, reason = try_inject_memory_sync(item["summary"], item["source"])
        if ok:
            with _MEMO_QUEUE_LOCK:
                if _PENDING_MEMOS and _PENDING_MEMOS[0] is item:
                    _PENDING_MEMOS.popleft()
            flushed += 1
        elif reason in ("brain_busy", "worker_not_ready"):
            break
        else:
            # 不可恢復錯誤則移除該筆，避免阻塞後續佇列
            with _MEMO_QUEUE_LOCK:
                if _PENDING_MEMOS and _PENDING_MEMOS[0] is item:
                    _PENDING_MEMOS.popleft()
    return flushed


def patrol_tasks_once() -> Dict[str, Any]:
    """執行單次背景任務巡檢：檢查 TTL 與異常退出之 auto_restart 任務"""
    tasks = task_manager.list_tasks(include_finished=True)
    restarted: list[str] = []
    now_ts = time.time()

    for t in tasks:
        if not t.get("auto_restart"):
            continue
        if t.get("status") != "COMPLETED":
            continue
        spec = t.get("restart_spec")
        if not isinstance(spec, dict) or spec.get("kind") != "spawn":
            continue

        task_id = t.get("task_id", "")
        last_ts = _LAST_RESTART_ATTEMPT.get(task_id, 0.0)
        if now_ts - last_ts < _RESTART_COOLDOWN_SEC:
            continue
        _LAST_RESTART_ATTEMPT[task_id] = now_ts

        # 先取消舊紀錄的 auto_restart 旗標避免重複拉起
        t["auto_restart"] = False
        t["status"] = "RESTARTED"
        task_manager._save_tasks(tasks)

        res = task_manager.spawn(
            name=spec.get("name", t.get("name", "auto_task")),
            script=spec["script"],
            args=spec.get("args") or [],
            ttl_minutes=spec.get("ttl_minutes", 120),
            notify=spec.get("notify"),
            sync_memory=bool(spec.get("sync_memory", False)),
            on_success_cmd=spec.get("on_success_cmd"),
            on_fail_cmd=spec.get("on_fail_cmd"),
            auto_restart=True,
        )
        if res.get("success"):
            new_id = res.get("task_id", "")
            restarted.append(new_id)
            try:
                from telegram_service import send_telegram_sync
                send_telegram_sync(
                    f"🔄 <b>中控自癒重啟</b>\n"
                    f"• 任務：<code>{html.escape(str(t.get('name', '')))}</code>\n"
                    f"• 原 ID：<code>{html.escape(task_id)}</code> → 新 ID：<code>{html.escape(new_id)}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    running_count = sum(1 for t in tasks if t.get("status") == "RUNNING")
    return {"running_count": running_count, "restarted": restarted}


async def run_task_supervisor(interval_sec: int = 60) -> None:
    """常駐中控巡檢迴圈（由 main.py startup_event 啟動）"""
    print(f"[TaskSupervisor] 🛡️ 中控背景任務監督協程已啟動（巡檢週期: {interval_sec}s）")
    # 啟動初期稍等服務就緒再開始首輪巡檢
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(patrol_tasks_once)
            await asyncio.to_thread(flush_memory_sync_queue)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[TaskSupervisor] ⚠️ 巡檢異常: {e}")
        await asyncio.sleep(interval_sec)
