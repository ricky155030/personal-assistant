"""
Mac mini Assistant 非同步任務排隊序列處理器 (Async Task Queue)
提供 FIFO 循序處理機制：
- 接收 Apple Watch 等穿戴設備的非同步請求，立即向設備回覆 ACK (<0.05s)
- 背景單一 Worker 執行緒依序消化 AI 任務，確保 agy_singleton 進程對話脈絡完整、零競爭死鎖
- 任務完成後自動呼叫 TelegramService 將完整格式化內容推播至 Telegram
"""

import time
import queue
import json
import uuid
import threading
from datetime import datetime
from typing import Optional
from config import settings
from telegram_service import (
    send_telegram_sync,
    format_telegram_response,
    send_formatted_telegram_reply,
    send_telegram_message_return_id,
    edit_telegram_message_sync,
    delete_telegram_message_sync,
    TelegramLiveCard
)
from agent import personal_agent

class TaskQueue:
    """非同步任務佇列，附帶可輪詢的結果儲存 (data/chat_results.json)

    - enqueue() 回傳 task_id，客戶端可用 GET /api/chat/result/{task_id} 輪詢真正的回覆
    - 結果持久化於磁碟（服務重啟不丢失），僅保留最新 MAX_RESULTS 筆
    - Telegram 推播行為維持不變（作為到達通知與備援）
    """

    MAX_RESULTS = 50

    def __init__(self):
        self._queue = queue.Queue()
        self._results_lock = threading.Lock()
        self._results = self._load_results()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    @property
    def _results_path(self):
        return settings.DATA_DIR / "chat_results.json"

    def _load_results(self) -> dict:
        try:
            with open(self._results_path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _prune_results_locked(self):
        if len(self._results) <= self.MAX_RESULTS:
            return
        kept = sorted(
            self._results.items(),
            key=lambda kv: kv[1].get("created_at", ""),
            reverse=True,
        )[: self.MAX_RESULTS]
        self._results = dict(kept)

    def enqueue(self, user_query: str, client_source: str, received_at: str, media: Optional[list] = None) -> str:
        """將請求放入先進先出佇列，回傳 task_id 供結果輪詢"""
        task_id = uuid.uuid4().hex[:12]
        with self._results_lock:
            self._results[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "query": user_query,
                "client": client_source,
                "created_at": datetime.now().isoformat(),
                "reply": None,
                "actions": [],
                "finished_at": None,
                "latency_seconds": None,
            }
            self._prune_results_locked()
            self._save_results_locked()
        self._queue.put((task_id, user_query, client_source, received_at, media))
        return task_id

    def get_result(self, task_id: str) -> Optional[dict]:
        with self._results_lock:
            result = self._results.get(task_id)
            return dict(result) if result else None

    def get_queue_size(self) -> int:
        return self._queue.qsize()

    def _save_results_locked(self):
        with open(self._results_path, "w", encoding="utf-8") as f:
            json.dump(self._results, f, ensure_ascii=False, indent=2)

    def _worker_loop(self):
        while True:
            task_id = None
            try:
                task_id, user_query, client_source, received_at, media = self._queue.get()
                self._process_task(task_id, user_query, client_source, received_at, media)
            except Exception as e:
                print(f"[TaskQueue] ❌ 非同步任務處理出錯: {e}")
                if task_id is not None:
                    self._mark_result(task_id, status="error", reply=f"任務處理失敗: {e}")
            finally:
                self._queue.task_done()

    def _mark_result(self, task_id: str, **fields):
        with self._results_lock:
            entry = self._results.get(task_id)
            if entry is not None:
                entry.update(fields)
                self._save_results_locked()

    def _process_task(self, task_id: str, user_query: str, client_source: str, received_at: str, media: Optional[list] = None):
        start_ts = time.perf_counter()
        existing_reports = {f.name: f.stat().st_mtime for f in settings.REPORTS_DIR.glob("*.html")}

        stream_gen = personal_agent.stream(user_query, client=client_source, media=media)
        card = TelegramLiveCard()
        turn_result = {}

        try:
            while True:
                item = next(stream_gen)
                if isinstance(item, tuple) and len(item) == 2:
                    ev_type, ev_data = item
                else:
                    ev_type, ev_data = "text", item

                if ev_type == "status":
                    card.on_status(str(ev_data))
                elif ev_type == "tool_done":
                    card.on_tool_done(str(ev_data))
                elif ev_type == "text":
                    card.on_text(str(ev_data))
        except StopIteration as e:
            turn_result = e.value or {}
        except Exception as e:
            if card.msg_id:
                delete_telegram_message_sync(card.msg_id)
            self._mark_result(task_id, status="error", reply=f"任務處理失敗: {e}",
                              finished_at=datetime.now().isoformat())
            raise

        card.finalize()
        msg_id = card.msg_id
        end_ts = time.perf_counter()
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        latency = round(end_ts - start_ts, 3)

        final_result_text = turn_result.get("result") or turn_result.get("clean_reply")
        raw_reply = final_result_text.strip() if final_result_text else "".join(card.collected_text).strip()
        clean_reply = raw_reply.replace("[TELEGRAM_FULL_REPORT]", "\n\n").strip()

        thinking_level = turn_result.get("thinking_level", "low")
        actions = [
            f"engine: agy-singleton ({thinking_level})",
            f"ttft: {turn_result.get('time_to_first_token', 0):.2f}s",
            f"total: {latency:.2f}s"
        ]
        if media:
            actions.append(f"multimodal: {len(media)} images" if len(media) > 1 else "multimodal: image")

        self._mark_result(
            task_id,
            status="done",
            reply=clean_reply,
            actions=actions,
            finished_at=finished_at,
            latency_seconds=latency,
        )

        # 檢查是否有新生成之 HTML 報告
        new_reports = [
            f for f in settings.REPORTS_DIR.glob("*.html")
            if f.name not in existing_reports or f.stat().st_mtime > existing_reports.get(f.name, 0)
        ]
        report_link_html = ""
        if new_reports:
            latest_report = max(new_reports, key=lambda f: f.stat().st_mtime)
            report_url = settings.get_report_url(latest_report.name)
            portal_url = f"http://{settings.TAILSCALE_DOMAIN}:{settings.PORT}/reports"
            report_link_html = f"\n\n🌐 <b>線上報告已生成</b>：\n👉 <a href=\"{report_url}\">點此開啟報告</a> ｜ 📂 <a href=\"{portal_url}\">報告總覽中心</a>"

        # 推送至 Telegram (自動支援長篇分段與置底 Quote 卡片)
        send_formatted_telegram_reply(
            reply=clean_reply,
            user_query=user_query,
            latency=latency,
            actions=actions,
            report_link_html=report_link_html,
            thinking_level=thinking_level,
            msg_id=msg_id
        )

        print(
            f"\n┌─────────────────────────────────────────────────────────────\n"
            f"│ ⏱️  [Timing Benchmark] 非同步任務完成 (來源: /api/chat, 裝置: {client_source})\n"
            f"│ 📥 收到 Prompt 時間:   {received_at}\n"
            f"│ 📤 agy 處理完畢時間:  {finished_at}\n"
            f"│ ⚡ 總處理耗時 (Latency): {latency:.2f} 秒\n"
            f"│ 💬 Prompt 內容:        \"{user_query}\"\n"
            f"│ 🤖 助理回覆:           \"{clean_reply[:60]}{'...' if len(clean_reply) > 60 else ''}\"\n"
            f"│ 📱 Telegram 同步:     {len(clean_reply)} 字元 (已推送)\n"
            f"└─────────────────────────────────────────────────────────────\n",
            flush=True
        )

task_queue = TaskQueue()
