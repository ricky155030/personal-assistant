"""
Antigravity CLI (agy) 三守護進程大腦池管理器 (Agy Brain Pool)
透過 stream-json (NDJSON) 維護三常駐獨立進程池：
  - Low Brain: gemini-3.8-flash-low (日常快問快答、家電直連、行程備忘)
  - Med Brain: gemini-3.8-flash-medium (時空交通規劃、決策比較、多條件推導)
  - High Brain: gemini-3.8-flash-high (深度研究、技術架構分析、長篇剖析)
前置透過 thinking_router 進行零延遲路由分流。
內建 Context 上下文閾值防護，達標時背景自動重啟並發送 Telegram 通知。
"""

import json
import logging
import queue
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional, Tuple, Union, Any

from config import settings
from memory_manager import memory_manager
from thinking_router import classify_thinking_level, clean_query_prefix

logger = logging.getLogger("AgyBrainPool")


def format_tool_status(tool_name: str, tool_info: Optional[dict] = None) -> str:
    """將底層工具調用轉化為使用者友善的即時進度狀態文字"""
    tool_info = tool_info or {}
    params = tool_info.get("parameters", {})

    if tool_name == "run_command":
        cmd = params.get("CommandLine", "")
        # 1. 瀏覽器自動化相關
        if "browser_relay" in cmd:
            if "open" in cmd:
                if "inline.app" in cmd or "opentable" in cmd or "eztable" in cmd:
                    return "🌐 正在開啟餐廳訂位頁面並通關驗證盾..."
                return "🌐 正在開啟目標網頁並通關驗證盾..."
            elif "inspect" in cmd:
                return "📋 正在掃描網頁可互動欄位..."
            elif "batch" in cmd:
                return "⚡ 正在流水線填寫資料與送出..."
            elif "click" in cmd:
                return "🖱️ 正在點擊網頁元素..."
            elif "type" in cmd:
                return "⌨️ 正在輸入表單資料..."
            elif "wait" in cmd:
                return "⏳ 正在等待頁面載入..."
            return "🌐 正在操控瀏覽器..."

        # 2. Google Calendar
        if "calendar_tool" in cmd or "calendar" in cmd:
            if "add" in cmd or "create" in cmd:
                return "📅 正在同步寫入 Google Calendar 行事曆..."
            elif "list" in cmd or "get" in cmd or "query" in cmd:
                return "📅 正在查詢 Google Calendar 行事曆..."
            return "📅 正在同步 Google Calendar..."

        # 4. 購物清單
        if "shopping_tool" in cmd or "shopping" in cmd:
            return "🛒 正在更新家庭待買清單..."

        # 5. iMessage
        if "imessage_tool" in cmd or "imessage" in cmd:
            return "💬 正在透過 iMessage 發送訊息..."

        # 6. Task Manager / 腳本脫鉤啟動
        if "task_manager" in cmd:
            return "⚙️ 正在啟動背景監控任務..."

        # 7. Google Sheets / gws
        if "sheets" in cmd or "gws" in cmd:
            return "📊 正在查詢 Google 試算表..."

        # 8. Gmail
        if "gmail" in cmd:
            return "✉️ 正在檢索 Gmail 信件..."

        # 10. 網路請求 / curl
        if "curl" in cmd or "wget" in cmd or "http" in cmd:
            return "🌐 正在請求網路資料..."

        # 11. git 操作
        if "git " in cmd:
            return "📦 正在執行 Git 同步操作..."

        return "⚙️ 正在執行系統指令..."

    elif tool_name == "search_web":
        q = params.get("query", "").strip()
        if q:
            short_q = q if len(q) <= 20 else q[:20] + "..."
            return f"🔍 正在搜尋網路：{short_q}"
        return "🔍 正在搜尋網路資訊..."

    elif tool_name in ("read_url_content", "read_browser_page", "open_browser_url"):
        url = params.get("Url", "") or params.get("url", "")
        if url:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if domain:
                    return f"🌐 正在讀取網頁 ({domain})..."
            except Exception:
                pass
        return "🌐 正在讀取網頁內容..."

    elif tool_name in ("view_file", "replace_file_content", "write_to_file", "sed_file"):
        target = params.get("TargetFile") or params.get("AbsolutePath") or ""
        if target:
            try:
                fname = Path(target).name
                if fname:
                    return f"📁 正在讀寫檔案 ({fname})..."
            except Exception:
                pass
        return "📁 正在存取本機檔案..."

    elif tool_name == "invoke_subagent":
        subagents = params.get("Subagents", [])
        role = ""
        if subagents and isinstance(subagents, list) and len(subagents) > 0:
            role = subagents[0].get("Role", "")
        if role:
            return f"🤖 正在委派 Subagent「{role}」深度調研..."
        return "🤖 正在委派 Subagent 子代理深度調研..."

    elif tool_name == "manage_task":
        action = params.get("Action", "")
        return f"⚙️ 正在管理背景任務 ({action})..." if action else "⚙️ 正在管理背景任務..."

    elif tool_name in ("browser_click_element", "click_browser_pixel"):
        return "🖱️ 正在點擊網頁元素..."

    elif tool_name in ("browser_input", "browser_select_option"):
        return "⌨️ 正在填寫表單欄位..."

    elif tool_name == "schedule":
        return "⏰ 正在設定背景定時排程..."

    return f"⚙️ 正在執行 {tool_name}..."


class AgyWorker:
    """單一常駐 agy stream-json 進程工作者"""

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.proc: Optional[subprocess.Popen] = None
        self.conversation_id: Optional[str] = None
        self.lock = threading.RLock()
        self._is_ready = False
        self._is_restarting = False
        self.last_tokens = 0
        self.last_usage = {}
        self.last_active_time = time.time()
        self.pending_handoff = False
        self.cwd = str(settings.AGENT_MD_PATH.parent)
        self.stderr_file = None
        self.stdout_queue: queue.Queue = queue.Queue()
        self.reader_thread: Optional[threading.Thread] = None

    def start(self):
        with self.lock:
            self._start_unlocked()

    def _start_unlocked(self):
        if self.proc is not None and self.proc.poll() is None:
            if self._is_ready:
                return
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
            self._is_ready = False

        cmd = [
            settings.AGY_BIN,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--model", self.model,
        ]

        print(f"[AgyWorker:{self.name.upper()}] 🚀 正在啟動常駐進程 ({self.model})...")
        t0 = time.time()

        stderr_log_path = Path(self.cwd) / "data" / f"agy_{self.name}.stderr.log"
        stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.stderr_file and not self.stderr_file.closed:
            try:
                self.stderr_file.close()
            except Exception:
                pass
        self.stderr_file = open(stderr_log_path, "a", encoding="utf-8")

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_file,
            text=True,
            bufsize=1,
            cwd=self.cwd
        )

        self.stdout_queue = queue.Queue()

        def _reader(proc, out_q):
            try:
                for line in iter(proc.stdout.readline, ''):
                    out_q.put(line)
            except Exception:
                pass
            finally:
                out_q.put(None)

        self.reader_thread = threading.Thread(
            target=_reader,
            args=(self.proc, self.stdout_queue),
            daemon=True
        )
        self.reader_thread.start()

        try:
            init_line = self.stdout_queue.get(timeout=60.0)
            if init_line:
                init_data = json.loads(init_line)
                self.conversation_id = init_data.get("conversation_id")
                elapsed = time.time() - t0
                self._is_ready = True
                print(f"[AgyWorker:{self.name.upper()}] ✅ 就緒！耗時 {elapsed:.2f}s | 對話 ID: {self.conversation_id}")
                # 檢查是否有未讀取的 session_handoff.md 並消化歸檔
                self._consume_and_archive_handoff()
            else:
                print(f"[AgyWorker:{self.name.upper()}] ❌ 啟動失敗：無輸出訊號")
                self._is_ready = False
        except queue.Empty:
            print(f"[AgyWorker:{self.name.upper()}] ❌ 啟動超時 (60s)")
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
            self._is_ready = False
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ❌ 初始化解析錯誤：{e}")
            self._is_ready = False

    def restart(self):
        with self.lock:
            self._restart_unlocked()

    def _restart_unlocked(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        if self.stderr_file and not self.stderr_file.closed:
            try:
                self.stderr_file.close()
            except Exception:
                pass
        self.stderr_file = None
        self._is_ready = False
        self._start_unlocked()

    def _generate_session_handoff(self) -> str:
        """
        在現有常駐進程關閉前，向其發出交接備忘指令，
        產出結構化的 session_handoff.md 並存入 data/ 目錄。
        """
        handoff_prompt = (
            "【系統維護指令：Session Handoff 交接備忘】\n"
            "本交談進程即將達到維護週期並交接給下一個 Session。請檢視剛才所有的對話與工具執行結果，產出結構化交接備忘錄（Markdown 格式），嚴格包含以下四個區塊：\n\n"
            "## 1. 進行中或未完成的任務 (Active Objectives)\n"
            "- 具體描述是否有任何尚未完成的事項、訂位、查詢或開發任務；若全部皆已完成則註明「無進行中任務，所有事項均已結清」。\n\n"
            "## 2. 已確定的關鍵實體與現場參數 (Key Entities & Parameters)\n"
            "- 請保留具體參數：實體名稱、店家門市、日期時間、人數、電話、姓名、檔案路徑、或 Chrome 分頁 ID 等。\n\n"
            "## 3. 目前狀態與是否等待主人輸入 (Current Status & Pending Input)\n"
            "- 註明目前是否正在等待主人提供特定資訊（例如：簡訊驗證碼、二選一確認、時段選擇）；若無則註明「閒置中，等待主人新指令」。\n\n"
            "## 4. 下一步預期行動 (Next Actions)\n"
            "- 當主人再次傳送訊息時，新 Session 應如何接續。\n\n"
            "請直接輸出 Markdown 內容，切勿輸出任何其他前綴寒暄。"
        )

        content = ""
        if self._is_ready and self.proc and self.proc.poll() is None:
            try:
                while not self.stdout_queue.empty():
                    try:
                        self.stdout_queue.get_nowait()
                    except queue.Empty:
                        break

                req = {"event": "user", "message": {"content": handoff_prompt}}
                self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()

                collected = []
                t0 = time.time()
                while time.time() - t0 < 30.0:
                    try:
                        line = self.stdout_queue.get(timeout=1.0)
                        if not line:
                            break
                        data = json.loads(line)
                        ev = data.get("event")
                        if ev == "step_update":
                            step = data.get("step_update", {})
                            delta = step.get("text_delta", "")
                            if delta:
                                collected.append(delta)
                        elif ev == "result":
                            res = data.get("result", {})
                            if not collected and res.get("response"):
                                collected.append(res["response"])
                            break
                    except queue.Empty:
                        continue
                    except Exception:
                        break
                content = "".join(collected).strip()
            except Exception as e:
                print(f"[AgyWorker:{self.name.upper()}] ⚠️ 呼叫進程產出交接備忘失敗: {e}")

        if not content:
            content = self._synthesize_fallback_handoff()

        stat = self.get_status()
        header = (
            f"# 📋 Session Handoff 交接備忘錄\n"
            f"- **來源 Worker**：{self.name.upper()} Brain ({self.model})\n"
            f"- **交接時間**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- **最後 Token 用量**：{stat['current_tokens']:,} tokens ({stat['percent']}%)\n\n"
        )
        full_doc = header + content
        handoff_path = settings.DATA_DIR / "session_handoff.md"
        handoff_path.write_text(full_doc, encoding="utf-8")
        print(f"[AgyWorker:{self.name.upper()}] 📝 已成功產出 {handoff_path.name} (長度: {len(full_doc)} 字元)")
        return full_doc

    def _synthesize_fallback_handoff(self) -> str:
        """從 history.jsonl 提取最近幾筆對話作為 Fallback 備忘"""
        try:
            if not settings.HISTORY_LOG_PATH.exists():
                return "無近期對話紀錄。"
            lines = settings.HISTORY_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
            recent = [json.loads(l) for l in lines[-4:] if l.strip()]
            summary = ["## 1. 最近對話歷程 (Recent Interaction History)"]
            for item in recent:
                ts = item.get("timestamp", "")
                q = item.get("query", "")
                r = item.get("reply", "")[:120].replace("\n", " ")
                summary.append(f"- 【{ts}】主人：「{q}」➔ 助理：「{r}...」")
            summary.append("\n## 2. 備註\n- 本備忘由系統對話日誌自動合成，請依據上述對話脈絡繼續為主人服務。")
            return "\n".join(summary)
        except Exception as e:
            return f"交接備忘合成異常: {e}"

    def _consume_and_archive_handoff(self):
        """在新進程就緒時，讀取 session_handoff.md 並將其改名留存至 data/handoffs/ 供未來除錯"""
        handoff_path = settings.DATA_DIR / "session_handoff.md"
        if not handoff_path.exists():
            return

        try:
            handoff_text = handoff_path.read_text(encoding="utf-8").strip()
            if not handoff_text:
                handoff_path.unlink(missing_ok=True)
                return

            worker_header = f"{self.name.upper()} Brain"
            if "來源 Worker" in handoff_text and worker_header not in handoff_text:
                return

            print(f"[AgyWorker:{self.name.upper()}] 📖 偵測到 session_handoff.md，正在向新進程注入交接記憶...")
            prime_prompt = (
                "【系統前置記憶：Session Handoff 交接備忘】\n"
                "以下為前一個 Session 達標閒置整理之進度備忘錄：\n\n"
                f"{handoff_text}\n\n"
                "請完全理解並記住以上所有事實、現場參數與當前進度。後續若主人傳來任何訊息，請自然無縫接續此情境處理。請僅簡短確認：「✅ 已讀取交接備忘，隨時可接續處理。」"
            )

            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            req = {"event": "user", "message": {"content": prime_prompt}}
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

            t0 = time.time()
            while time.time() - t0 < 15.0:
                try:
                    line = self.stdout_queue.get(timeout=1.0)
                    if not line:
                        break
                    data = json.loads(line)
                    if data.get("event") == "result":
                        break
                except queue.Empty:
                    continue
                except Exception:
                    break

            # 讀完之後，將 session_handoff.md 改名留存至 data/handoffs/
            settings.HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"session_handoff_{ts_str}_{self.name}.md"
            archive_path = settings.HANDOFFS_DIR / archive_name
            handoff_path.rename(archive_path)
            print(f"[AgyWorker:{self.name.upper()}] 📦 已將交接備忘改名留存至 {archive_path.name}，新 Session 已無縫就緒！")
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ⚠️ 讀取並歸檔 session_handoff 失敗: {e}")

    def get_status(self) -> dict:
        limit = settings.CONTEXT_WINDOW_LIMIT
        current = self.last_tokens
        ratio = current / limit if limit > 0 else 0.0
        idle_sec = time.time() - self.last_active_time
        return {
            "name": self.name,
            "model": self.model,
            "is_ready": self._is_ready,
            "is_restarting": self._is_restarting,
            "pending_handoff": self.pending_handoff,
            "idle_seconds": round(idle_sec, 1),
            "idle_minutes": round(idle_sec / 60, 1),
            "conversation_id": self.conversation_id or "N/A",
            "current_tokens": current,
            "limit": limit,
            "ratio": ratio,
            "percent": round(ratio * 100, 1),
            "threshold": settings.CONTEXT_RESTART_THRESHOLD,
            "usage": self.last_usage,
        }

    def trigger_handoff_and_restart(self, reason: str = "自動閒置交接"):
        with self.lock:
            if self._is_restarting:
                return
            self._is_restarting = True

        threading.Thread(
            target=self._notify_and_handoff_restart_worker,
            args=(reason,),
            daemon=True
        ).start()

    def _notify_and_handoff_restart_worker(self, reason: str):
        try:
            from telegram_service import send_telegram_sync
            stat = self.get_status()
            send_telegram_sync(
                f"🔄 <b>[系統提示]</b> {self.name.upper()} Brain Context 已達 <b>{stat['percent']}%</b> 且已連續閒置 {settings.CONTEXT_IDLE_MINUTES} 分鐘（原因：{reason}），正在整理 <code>session_handoff.md</code> 並重啟...",
                parse_mode="HTML"
            )
            t0 = time.time()
            self._generate_session_handoff()
            self.restart()
            elapsed = time.time() - t0
            send_telegram_sync(
                f"✅ <b>[系統提示]</b> {self.name.upper()} Brain 重啟完成！耗時 {elapsed:.2f}s，已讀取交接備忘並歸檔至 <code>handoffs/</code>，Context 已重置為 0。",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ❌ 自動交接重啟失敗: {e}")
        finally:
            self.pending_handoff = False
            self._is_restarting = False

    def trigger_restart_with_notification(self, reason: str = "自動觸發"):
        self.trigger_handoff_and_restart(reason)

    def send_query_stream(self, query: str, media: Optional[List[dict]] = None) -> Generator[Tuple[str, str], None, dict]:
        with self.lock:
            if not self._is_ready or self.proc is None or self.proc.poll() is not None:
                self._start_unlocked()

            if not self._is_ready or self.proc is None:
                yield ("text", f"{self.name.upper()} 守護進程未就緒，請稍後重試。")
                return {"status": "ERROR", "error": "process_not_ready"}

            start_t = time.perf_counter()
            msg_payload = {"content": query}
            if media:
                msg_payload["media"] = media
            req = {"event": "user", "message": msg_payload}

            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            try:
                self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                print(f"[AgyWorker:{self.name.upper()}] 寫入 stdin 失敗，嘗試重啟：{e}")
                self._restart_unlocked()
                yield ("text", "通訊管線重連中，請再試一次。")
                return {"status": "ERROR", "error": str(e)}

            first_token_time = None
            full_reply_parts = []
            turn_result = {}
            last_data_time = time.perf_counter()
            active_tools = 0
            has_agent_responded = False

            while True:
                try:
                    line = self.stdout_queue.get(timeout=1.0)
                except queue.Empty:
                    time_since_last_data = time.perf_counter() - last_data_time
                    total_time = time.perf_counter() - start_t
                    # 只有在完全無資料閒置超過 120 秒，或總耗時超過 600 秒且至少閒置 30 秒時才判定超時
                    is_idle_timeout = time_since_last_data > 120.0
                    is_total_timeout = total_time > 600.0 and time_since_last_data > 30.0
                    if is_idle_timeout or is_total_timeout:
                        if full_reply_parts and active_tools == 0:
                            print(f"[AgyWorker:{self.name.upper()}] ℹ️ 已有文字回覆且無進行中工具，正常結束本次 turn")
                            break
                        print(f"[AgyWorker:{self.name.upper()}] ⚠️ 超時 (閒置 {time_since_last_data:.1f}s, 總長 {total_time:.1f}s)，自動重啟...")
                        self._restart_unlocked()
                        yield ("text", "助理處理逾時，已自動重啟重連，請再試一次。")
                        return {"status": "TIMEOUT", "error": "stream_timeout"}
                    continue

                if line is None:
                    break

                last_data_time = time.perf_counter()

                try:
                    data = json.loads(line)
                except Exception:
                    continue

                ev = data.get("event")
                if ev == "step_update":
                    step = data.get("step_update", {})
                    stype = step.get("step_type")
                    sstate = step.get("state")

                    if stype == "tool":
                        tool_name = step.get("tool_name", "")
                        tool_info = step.get("tool_info", {})
                        if sstate == "ACTIVE":
                            active_tools += 1
                            has_agent_responded = False
                            status_text = format_tool_status(tool_name, tool_info)
                            yield ("status", status_text)
                        elif sstate == "DONE":
                            active_tools = max(0, active_tools - 1)
                            yield ("tool_done", tool_name)

                    delta = step.get("text_delta", "")
                    if delta:
                        if first_token_time is None:
                            first_token_time = time.perf_counter() - start_t
                        full_reply_parts.append(delta)
                        yield ("text", delta)

                    if stype == "agent_response" and sstate == "DONE":
                        has_agent_responded = True
                        if step.get("usage"):
                            turn_result["usage"] = step["usage"]

                elif ev == "result":
                    turn_result = data.get("result", {})
                    break

            total_latency = time.perf_counter() - start_t
            self.last_active_time = time.time()
            full_reply = "".join(full_reply_parts) or turn_result.get("response", "")
            clean_reply = full_reply.replace("**", "").replace("#", "").replace("`", "").strip()

            turn_result["clean_reply"] = clean_reply
            turn_result["time_to_first_token"] = first_token_time or total_latency
            turn_result["total_latency"] = total_latency

            # 更新與檢查 Context 用量
            usage = turn_result.get("usage", {})
            self.last_usage = usage
            self.last_tokens = usage.get("total_tokens") or usage.get("input_tokens") or 0

            limit = settings.CONTEXT_WINDOW_LIMIT
            threshold = settings.CONTEXT_RESTART_THRESHOLD
            if limit > 0 and self.last_tokens > 0:
                ratio = self.last_tokens / limit
                if ratio >= threshold:
                    pct = round(ratio * 100, 1)
                    if not self.pending_handoff:
                        self.pending_handoff = True
                        print(f"[AgyWorker:{self.name.upper()}] ⚠️ Context 達 {pct}%（已達 {int(threshold*100)}% 門檻），已標記待交接，將於連續閒置 {settings.CONTEXT_IDLE_MINUTES} 分鐘後產出 session_handoff.md 並自動重啟...")

            return turn_result


class AgyBrainPool:
    """
    三守護進程大腦池管理器 (Agy Brain Pool)
    整合 Low、Med 與 High 三個完全獨立的常駐進程，依據 Thinking Level 智能派發。
    向下完全相容原本的 AgyStreamSingleton 呼叫簽章。
    """

    def __init__(self):
        low_model = getattr(settings, "AGY_MODEL_LOW", getattr(settings, "AGY_MODEL_FAST", "gemini-3.8-flash-low"))
        med_model = getattr(settings, "AGY_MODEL_MED", getattr(settings, "AGY_MODEL_DEEP", "gemini-3.8-flash-medium"))
        high_model = getattr(settings, "AGY_MODEL_HIGH", "gemini-3.8-flash-high")

        self.low_worker = AgyWorker("low", low_model)
        self.med_worker = AgyWorker("med", med_model)
        self.high_worker = AgyWorker("high", high_model)

        # 向下相容屬性別名
        self.fast_worker = self.low_worker
        self.deep_worker = self.med_worker
        self.lock = threading.RLock()
        self._idle_monitor_thread: Optional[threading.Thread] = None

    def start(self):
        """同步預熱啟動三大腦進程池 (Low, Med, High)"""
        print(f"[AgyBrainPool] 🚀 正在啟動三大腦進程池 (Low: {self.low_worker.model} ｜ Med: {self.med_worker.model} ｜ High: {self.high_worker.model})...")
        t_low = threading.Thread(target=self.low_worker.start, daemon=True)
        t_med = threading.Thread(target=self.med_worker.start, daemon=True)
        t_high = threading.Thread(target=self.high_worker.start, daemon=True)
        t_low.start()
        t_med.start()
        t_high.start()
        t_low.join(timeout=65.0)
        t_med.join(timeout=65.0)
        t_high.join(timeout=65.0)
        print(f"[AgyBrainPool] ✅ 三大腦池啟動完成！(Low ready: {self.low_worker._is_ready}, Med ready: {self.med_worker._is_ready}, High ready: {self.high_worker._is_ready})")

        if self._idle_monitor_thread is None or not self._idle_monitor_thread.is_alive():
            self._idle_monitor_thread = threading.Thread(target=self._idle_monitor_loop, daemon=True)
            self._idle_monitor_thread.start()
            print(f"[AgyBrainPool] ⏰ 閒置 Handoff 監控線程已啟動 (檢測週期: 15s | 閒置門檻: {settings.CONTEXT_IDLE_MINUTES} 分鐘)")

    def _idle_monitor_loop(self):
        """背景定時檢查閒置超過 CONTEXT_IDLE_MINUTES 且待交接之 Worker"""
        while True:
            time.sleep(15)
            try:
                idle_limit_sec = settings.CONTEXT_IDLE_MINUTES * 60
                now = time.time()
                for worker in [self.low_worker, self.med_worker, self.high_worker]:
                    if worker.pending_handoff and not worker._is_restarting and worker._is_ready:
                        idle_sec = now - worker.last_active_time
                        if idle_sec >= idle_limit_sec:
                            idle_min = round(idle_sec / 60, 1)
                            print(f"[AgyBrainPool] ⏰ {worker.name.upper()} Brain 已連續閒置 {idle_min} 分鐘 (門檻 {settings.CONTEXT_IDLE_MINUTES} 分鐘)，啟動 Session Handoff 重啟程序！")
                            worker.trigger_handoff_and_restart(reason=f"Context 達標且已連續閒置 {idle_min} 分鐘")
            except Exception as e:
                print(f"[AgyBrainPool] ⚠️ 閒置監聽器異常: {e}")

    def restart(self):
        """重啟三大腦池"""
        t_low = threading.Thread(target=self.low_worker.restart, daemon=True)
        t_med = threading.Thread(target=self.med_worker.restart, daemon=True)
        t_high = threading.Thread(target=self.high_worker.restart, daemon=True)
        t_low.start()
        t_med.start()
        t_high.start()
        t_low.join(timeout=65.0)
        t_med.join(timeout=65.0)
        t_high.join(timeout=65.0)

    def get_status(self) -> dict:
        low_stat = self.low_worker.get_status()
        med_stat = self.med_worker.get_status()
        high_stat = self.high_worker.get_status()
        all_ready = self.low_worker._is_ready and self.med_worker._is_ready and self.high_worker._is_ready
        total_tokens = self.low_worker.last_tokens + self.med_worker.last_tokens + self.high_worker.last_tokens
        max_percent = max(low_stat["percent"], med_stat["percent"], high_stat["percent"])
        any_pending = low_stat["pending_handoff"] or med_stat["pending_handoff"] or high_stat["pending_handoff"]
        return {
            "is_ready": all_ready,
            "low_brain": low_stat,
            "med_brain": med_stat,
            "high_brain": high_stat,
            # 向下相容歷史欄位
            "fast_brain": low_stat,
            "deep_brain": med_stat,
            "conversation_id": self.low_worker.conversation_id or "N/A",
            "current_tokens": total_tokens,
            "limit": settings.CONTEXT_WINDOW_LIMIT,
            "percent": max_percent,
            "threshold": settings.CONTEXT_RESTART_THRESHOLD,
            "pending_handoff": any_pending,
            "idle_minutes_threshold": settings.CONTEXT_IDLE_MINUTES,
            "usage": self.low_worker.last_usage,
        }

    def trigger_restart_with_notification(self, reason: str = "手動觸發"):
        self.low_worker.trigger_handoff_and_restart(reason)
        self.med_worker.trigger_handoff_and_restart(reason)
        self.high_worker.trigger_handoff_and_restart(reason)

    def send_query_stream(
        self, 
        query: str, 
        media: Optional[List[dict]] = None, 
        level: Optional[str] = None
    ) -> Generator[Tuple[str, str], None, dict]:
        """
        核心串流發送介面：
        1. 自動執行 thinking_router 判定 (若外部未指定)
        2. 動態派發至獨立三大腦之一 (Low / Med / High)
        3. 記錄歷史資料庫包含 thinking_level
        """
        # 前置判斷 thinking level (low / med / high)
        raw_level = level or classify_thinking_level(query)
        chosen_level = "med" if raw_level == "medium" else raw_level.lower()
        clean_q = clean_query_prefix(query)

        if chosen_level == "low":
            worker = self.low_worker
        elif chosen_level == "high":
            worker = self.high_worker
        else:
            worker = self.med_worker
            chosen_level = "med"

        print(f"[AgyBrainPool] 🧠 請求已派發至 {worker.name.upper()} Brain (等級: {chosen_level.upper()} | 模型: {worker.model})")

        stream_gen = worker.send_query_stream(clean_q, media=media)
        turn_result = {}
        try:
            while True:
                item = next(stream_gen)
                yield item
        except StopIteration as e:
            turn_result = e.value or {}

        turn_result["thinking_level"] = chosen_level
        turn_result["model"] = worker.model
        turn_result["worker"] = worker.name

        # 記錄對話互動至 history.jsonl
        ttft = turn_result.get("time_to_first_token", 0)
        tot = turn_result.get("total_latency", 0)
        actions_str = f"singleton_agy ({chosen_level}) (ttft: {ttft:.2f}s, total: {tot:.2f}s)"
        memory_manager.log_interaction(
            query,
            turn_result.get("clean_reply", ""),
            [actions_str]
        )

        return turn_result

    def send_query(
        self, 
        query: str, 
        media: Optional[List[dict]] = None, 
        level: Optional[str] = None
    ) -> Tuple[str, List[str], float, float]:
        """非串流呼叫封裝"""
        gen = self.send_query_stream(query, media=media, level=level)
        collected = []
        try:
            while True:
                item = next(gen)
                if isinstance(item, tuple) and len(item) == 2:
                    ev_type, ev_data = item
                    if ev_type == "text":
                        collected.append(ev_data)
                elif isinstance(item, str):
                    collected.append(item)
        except StopIteration as e:
            res = e.value or {}
            clean_reply = res.get("clean_reply", "".join(collected).strip())
            chosen_level = res.get("thinking_level", "low")
            actions = [
                f"engine: agy-singleton ({chosen_level})",
                f"ttft: {res.get('time_to_first_token', 0):.2f}s",
                f"total: {res.get('total_latency', 0):.2f}s"
            ]
            if media:
                if len(media) > 1:
                    actions.append(f"multimodal: {len(media)} images")
                else:
                    actions.append("multimodal: image")
            return (
                clean_reply or "好的，已為您處理完畢。",
                actions,
                res.get("time_to_first_token", 0),
                res.get("total_latency", 0)
            )

    # 代理屬性供舊代碼向下相容
    @property
    def conversation_id(self) -> Optional[str]:
        return self.low_worker.conversation_id

    @property
    def _is_ready(self) -> bool:
        return self.low_worker._is_ready and self.med_worker._is_ready and self.high_worker._is_ready


# 導出單例實例，命名維持 agy_singleton 以向下完全相容
agy_singleton = AgyBrainPool()
