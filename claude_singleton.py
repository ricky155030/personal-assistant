"""
Claude Code CLI (claude) 常駐守護進程大腦管理器 (Claude Singleton / Brain Pool)
透過 stream-json (NDJSON) 維護常駐獨立進程池：
  - Low Brain: 輕量/快速任務 (例如 claude-3-5-haiku 或預設)
  - Med Brain: 標準推導/操作任務 (例如 claude-3-7-sonnet 或預設)
  - High Brain: 深度長篇剖析 (例如 claude-3-7-sonnet 或預設)
串接 Claude Code CLI 之 `-p --verbose --input-format stream-json --output-format stream-json`。
內建 Context 上下文閾值防護、Session Handoff 自動歸檔、與工具調用即時狀態轉化。
向下與 AgyBrainPool 保持 100% 呼叫介面相容。
"""

import os
import sys
import json
import time
import queue
import logging
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional, Tuple, Union, Any, Dict

from config import settings
from memory_manager import memory_manager
from thinking_router import classify_thinking_level, clean_query_prefix

logger = logging.getLogger("ClaudeBrainPool")


def format_claude_tool_status(tool_name: str, tool_input: Optional[dict] = None) -> str:
    """將 Claude Code 底層工具調用轉化為使用者友善的即時進度狀態文字"""
    tool_input = tool_input or {}

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
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

        # 3. 購物清單
        if "shopping_tool" in cmd or "shopping" in cmd:
            return "🛒 正在更新家庭待買清單..."

        # 4. iMessage
        if "imessage_tool" in cmd or "imessage" in cmd:
            return "💬 正在透過 iMessage 發送訊息..."

        # 5. Task Manager / 腳本脫鉤啟動
        if "task_manager" in cmd:
            return "⚙️ 正在啟動背景監控任務..."

        # 6. Google Sheets / gws
        if "sheets" in cmd or "gws" in cmd:
            return "📊 正在查詢 Google 試算表..."

        # 7. Gmail
        if "gmail" in cmd:
            return "✉️ 正在檢索 Gmail 信件..."

        # 8. 網路請求 / curl
        if "curl" in cmd or "wget" in cmd or "http" in cmd:
            return "🌐 正在請求網路資料..."

        # 9. git 操作
        if "git " in cmd:
            return "📦 正在執行 Git 同步操作..."

        return "⚙️ 正在執行系統指令..."

    elif tool_name in ("Read", "View"):
        file_path = tool_input.get("file_path", "")
        name = Path(file_path).name if file_path else ""
        return f"📖 正在讀取檔案 ({name})..." if name else "📖 正在讀取檔案..."

    elif tool_name in ("Edit", "Write", "Replace"):
        file_path = tool_input.get("file_path", "")
        name = Path(file_path).name if file_path else ""
        return f"📁 正在編輯檔案 ({name})..." if name else "📁 正在編輯檔案..."

    elif tool_name in ("Glob", "Find"):
        pattern = tool_input.get("pattern", "")
        return f"🔍 正在搜尋檔案 ({pattern})..." if pattern else "🔍 正在搜尋檔案..."

    elif tool_name in ("Grep", "Search"):
        query = tool_input.get("pattern", "") or tool_input.get("query", "")
        return f"🔍 正在檢索代碼 ({query})..." if query else "🔍 正在檢索代碼..."

    elif tool_name in ("WebSearch", "search_web"):
        q = tool_input.get("query", "").strip()
        short_q = q if len(q) <= 20 else q[:20] + "..."
        return f"🔍 正在搜尋網路：{short_q}" if short_q else "🔍 正在搜尋網路資訊..."

    elif tool_name in ("VisitPage", "read_url_content", "read_browser_page"):
        url = tool_input.get("url", "")
        return f"🌐 正在讀取網頁 ({url[:30]}...)" if url else "🌐 正在讀取網頁內容..."

    return f"⚙️ 正在執行 {tool_name}..."


class ClaudeWorker:
    """單一常駐 Claude Code stream-json 進程工作者"""

    def __init__(self, name: str, model: str = ""):
        self.name = name
        self.model = model
        self.proc: Optional[subprocess.Popen] = None
        self.session_id: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.lock = threading.RLock()
        self._is_ready = False
        self._is_restarting = False
        self.last_tokens = 0
        self.last_usage: Dict[str, Any] = {}
        self.last_active_time = time.time()
        self.pending_handoff = False
        self.cwd = str(settings.BASE_DIR)
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

        claude_bin = getattr(settings, "CLAUDE_BIN", "claude")
        cmd = [
            claude_bin,
            "-p",
            "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        model_label = self.model or "預設模型"
        print(f"[ClaudeWorker:{self.name.upper()}] 🚀 正在啟動常駐進程 ({model_label})...")
        t0 = time.time()

        stderr_log_path = Path(self.cwd) / "data" / f"claude_{self.name}.stderr.log"
        stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.stderr_file and not self.stderr_file.closed:
            try:
                self.stderr_file.close()
            except Exception:
                pass
        self.stderr_file = open(stderr_log_path, "a", encoding="utf-8")

        popen_kwargs: Dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": self.stderr_file,
            "text": True,
            "bufsize": 1,
            "cwd": self.cwd,
        }
        if sys.platform == "win32":
            creationflags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            popen_kwargs["creationflags"] = creationflags

        try:
            self.proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as e:
            print(f"[ClaudeWorker:{self.name.upper()}] ❌ 啟動失敗 ({cmd[0]}): {e}")
            self._is_ready = False
            return

        self.stdout_queue = queue.Queue()

        def _reader(proc, out_q):
            try:
                for line in iter(proc.stdout.readline, ""):
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

        # 等待 Claude Code 拋出 init 系統事件
        try:
            deadline = time.time() + 60.0
            while time.time() < deadline:
                line = self.stdout_queue.get(timeout=2.0)
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                ev_type = data.get("type")
                ev_sub = data.get("subtype")
                if ev_type == "system" and ev_sub == "init":
                    self.session_id = data.get("session_id")
                    self.conversation_id = self.session_id
                    elapsed = time.time() - t0
                    self._is_ready = True
                    print(f"[ClaudeWorker:{self.name.upper()}] ✅ 就緒！耗時 {elapsed:.2f}s | Session ID: {self.session_id}")
                    # 檢查並消化交接備忘
                    self._consume_and_archive_handoff()
                    return

            print(f"[ClaudeWorker:{self.name.upper()}] ❌ 啟動超時 (未收到 init 事件)")
            self._is_ready = False
        except queue.Empty:
            print(f"[ClaudeWorker:{self.name.upper()}] ❌ 啟動超時 (60s)")
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
            self._is_ready = False
        except Exception as e:
            print(f"[ClaudeWorker:{self.name.upper()}] ❌ 初始化解析錯誤：{e}")
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
        """產出結構化 session_handoff.md 並存入 data/ 目錄"""
        handoff_prompt = (
            "【系統維護指令：Session Handoff 交接備忘】\n"
            "本交談進程即將達到維護週期並交接給下一個 Session。請檢視剛才所有的對話與工具執行結果，產出結構化交接備忘錄（Markdown 格式），嚴格包含以下四個區塊：\n\n"
            "## 1. 進行中或未完成的任務 (Active Objectives)\n"
            "- 具體描述是否有任何尚未完成的事項、訂位、查詢或開發任務；若全部皆已完成則註明「無進行中任務，所有事項均已結清」。\n\n"
            "## 2. 已確定的關鍵實體與現場參數 (Key Entities & Parameters)\n"
            "- 請保留具體參數：實體名稱、店家門市、日期時間、人數、電話、姓名、檔案路徑、或 Chrome 分頁 ID 等。\n\n"
            "## 3. 目前狀態與是否等待主人輸入 (Current Status & Pending Input)\n"
            "- 註明目前是否正在等待主人提供特定資訊；若無則註明「閒置中，等待主人新指令」。\n\n"
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

                req = {"type": "user", "message": {"role": "user", "content": handoff_prompt}}
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
                        if data.get("type") == "assistant":
                            msg = data.get("message", {})
                            for part in msg.get("content", []):
                                if part.get("type") == "text":
                                    collected.append(part.get("text", ""))
                        elif data.get("type") == "result":
                            res_text = data.get("result", "")
                            if not collected and res_text:
                                collected.append(res_text)
                            break
                    except queue.Empty:
                        continue
                    except Exception:
                        break
                content = "".join(collected).strip()
            except Exception as e:
                print(f"[ClaudeWorker:{self.name.upper()}] ⚠️ 呼叫進程產出交接備忘失敗: {e}")

        if not content:
            content = self._synthesize_fallback_handoff()

        stat = self.get_status()
        header = (
            f"# 📋 Session Handoff 交接備忘錄\n"
            f"- **來源 Worker**：Claude {self.name.upper()} Brain ({self.model or 'default'})\n"
            f"- **交接時間**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- **最後 Token 用量**：{stat['current_tokens']:,} tokens ({stat['percent']}%)\n\n"
        )
        full_doc = header + content
        handoff_path = settings.DATA_DIR / "session_handoff.md"
        handoff_path.write_text(full_doc, encoding="utf-8")
        print(f"[ClaudeWorker:{self.name.upper()}] 📝 已成功產出 {handoff_path.name}")
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
            summary.append("\n## 2. 備註\n- 本備忘由系統對話日誌自動合成。")
            return "\n".join(summary)
        except Exception as e:
            return f"交接備忘合成異常: {e}"

    def _consume_and_archive_handoff(self):
        """讀取 session_handoff.md 並將其改名留存至 data/handoffs/"""
        handoff_path = settings.DATA_DIR / "session_handoff.md"
        if not handoff_path.exists():
            return

        try:
            handoff_text = handoff_path.read_text(encoding="utf-8").strip()
            if not handoff_text:
                handoff_path.unlink(missing_ok=True)
                return

            print(f"[ClaudeWorker:{self.name.upper()}] 📖 偵測到 session_handoff.md，正在向新進程注入交接記憶...")
            prime_prompt = (
                "【系統前置記憶：Session Handoff 交接備忘】\n"
                f"{handoff_text}\n\n"
                "請完全理解並記住以上所有事實與當前進度。後續若主人傳來任何訊息，請自然無縫接續此情境處理。請僅簡短確認：「✅ 已讀取交接備忘，隨時可接續處理。」"
            )

            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            req = {"type": "user", "message": {"role": "user", "content": prime_prompt}}
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

            t0 = time.time()
            while time.time() - t0 < 15.0:
                try:
                    line = self.stdout_queue.get(timeout=1.0)
                    if not line:
                        break
                    data = json.loads(line)
                    if data.get("type") == "result":
                        break
                except queue.Empty:
                    continue
                except Exception:
                    break

            settings.HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"session_handoff_{ts_str}_claude_{self.name}.md"
            archive_path = settings.HANDOFFS_DIR / archive_name
            handoff_path.rename(archive_path)
            print(f"[ClaudeWorker:{self.name.upper()}] 📦 已將交接備忘改名留存至 {archive_path.name}")
        except Exception as e:
            print(f"[ClaudeWorker:{self.name.upper()}] ⚠️ 讀取並歸檔 session_handoff 失敗: {e}")

    def get_status(self) -> dict:
        limit = settings.CONTEXT_WINDOW_LIMIT
        current = self.last_tokens
        ratio = current / limit if limit > 0 else 0.0
        idle_sec = time.time() - self.last_active_time
        return {
            "name": f"claude_{self.name}",
            "model": self.model or "claude-default",
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
                f"🔄 <b>[系統提示]</b> Claude {self.name.upper()} Brain Context 已達 <b>{stat['percent']}%</b>（原因：{reason}），正在整理 <code>session_handoff.md</code> 並重啟...",
                parse_mode="HTML"
            )
            t0 = time.time()
            self._generate_session_handoff()
            self.restart()
            elapsed = time.time() - t0
            send_telegram_sync(
                f"✅ <b>[系統提示]</b> Claude {self.name.upper()} Brain 重啟完成！耗時 {elapsed:.2f}s，Context 已重置。",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[ClaudeWorker:{self.name.upper()}] ❌ 自動交接重啟失敗: {e}")
        finally:
            self.pending_handoff = False
            self._is_restarting = False

    def send_query_stream(self, query: str, media: Optional[List[dict]] = None) -> Generator[Tuple[str, str], None, dict]:
        with self.lock:
            if not self._is_ready or self.proc is None or self.proc.poll() is not None:
                self._start_unlocked()

            if not self._is_ready or self.proc is None:
                yield ("text", f"Claude {self.name.upper()} 守護進程未就緒，請稍後重試。")
                return {"status": "ERROR", "error": "process_not_ready"}

            start_t = time.perf_counter()
            req = {"type": "user", "message": {"role": "user", "content": query}}

            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            try:
                self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                print(f"[ClaudeWorker:{self.name.upper()}] 寫入 stdin 失敗，嘗試重啟：{e}")
                self._restart_unlocked()
                yield ("text", "通訊管線重連中，請再試一次。")
                return {"status": "ERROR", "error": str(e)}

            first_token_time = None
            full_reply_parts = []
            turn_result: Dict[str, Any] = {}
            last_data_time = time.perf_counter()

            while True:
                try:
                    line = self.stdout_queue.get(timeout=1.0)
                except queue.Empty:
                    time_since_last_data = time.perf_counter() - last_data_time
                    total_time = time.perf_counter() - start_t
                    if time_since_last_data > 120.0 or (total_time > 600.0 and time_since_last_data > 30.0):
                        print(f"[ClaudeWorker:{self.name.upper()}] ⚠️ 超時，自動重啟...")
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

                msg_type = data.get("type")

                # Assistant 輸出與工具調用
                if msg_type == "assistant":
                    msg = data.get("message", {})
                    content_list = msg.get("content", [])
                    for part in content_list:
                        ptype = part.get("type")
                        if ptype == "text":
                            txt = part.get("text", "")
                            if txt:
                                if first_token_time is None:
                                    first_token_time = time.perf_counter() - start_t
                                full_reply_parts.append(txt)
                                yield ("text", txt)
                        elif ptype == "tool_use":
                            tool_name = part.get("name", "")
                            tool_input = part.get("input", {})
                            status_text = format_claude_tool_status(tool_name, tool_input)
                            yield ("status", status_text)

                # 結果結算
                elif msg_type == "result":
                    turn_result = data
                    res_text = data.get("result", "")
                    if not full_reply_parts and res_text:
                        full_reply_parts.append(res_text)
                        yield ("text", res_text)
                    break

            total_latency = time.perf_counter() - start_t
            self.last_active_time = time.time()
            full_reply = "".join(full_reply_parts)
            clean_reply = full_reply.replace("**", "").replace("#", "").replace("`", "").strip()

            turn_result["clean_reply"] = clean_reply
            turn_result["time_to_first_token"] = first_token_time or total_latency
            turn_result["total_latency"] = total_latency

            # 統計用量
            usage = turn_result.get("usage", {})
            self.last_usage = usage
            self.last_tokens = usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))

            limit = settings.CONTEXT_WINDOW_LIMIT
            threshold = settings.CONTEXT_RESTART_THRESHOLD
            if limit > 0 and self.last_tokens > 0:
                ratio = self.last_tokens / limit
                if ratio >= threshold:
                    pct = round(ratio * 100, 1)
                    if not self.pending_handoff:
                        self.pending_handoff = True
                        print(f"[ClaudeWorker:{self.name.upper()}] ⚠️ Context 達 {pct}%，已標記待交接...")

            return turn_result


class ClaudeBrainPool:
    """
    Claude Code 大腦池管理器 (ClaudeBrainPool)
    介面與 AgyBrainPool 完全一致，支援動態路由分流 (Low / Med / High)。
    """

    def __init__(self):
        low_model = getattr(settings, "CLAUDE_MODEL_LOW", "") or getattr(settings, "CLAUDE_MODEL", "")
        med_model = getattr(settings, "CLAUDE_MODEL_MED", "") or getattr(settings, "CLAUDE_MODEL", "")
        high_model = getattr(settings, "CLAUDE_MODEL_HIGH", "") or getattr(settings, "CLAUDE_MODEL", "")

        self.low_worker = ClaudeWorker("low", low_model)
        self.med_worker = ClaudeWorker("med", med_model)
        self.high_worker = ClaudeWorker("high", high_model)

        self.fast_worker = self.low_worker
        self.deep_worker = self.med_worker
        self.lock = threading.RLock()
        self._idle_monitor_thread: Optional[threading.Thread] = None

    def start(self):
        """同步預熱啟動大腦進程池"""
        print(f"[ClaudeBrainPool] 🚀 正在啟動 Claude 大腦進程池...")
        t_low = threading.Thread(target=self.low_worker.start, daemon=True)
        t_med = threading.Thread(target=self.med_worker.start, daemon=True)
        t_high = threading.Thread(target=self.high_worker.start, daemon=True)
        t_low.start()
        t_med.start()
        t_high.start()
        t_low.join(timeout=65.0)
        t_med.join(timeout=65.0)
        t_high.join(timeout=65.0)
        print(f"[ClaudeBrainPool] ✅ Claude 大腦池啟動完成！(Low: {self.low_worker._is_ready}, Med: {self.med_worker._is_ready}, High: {self.high_worker._is_ready})")

        if self._idle_monitor_thread is None or not self._idle_monitor_thread.is_alive():
            self._idle_monitor_thread = threading.Thread(target=self._idle_monitor_loop, daemon=True)
            self._idle_monitor_thread.start()

    def _idle_monitor_loop(self):
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
                            print(f"[ClaudeBrainPool] ⏰ {worker.name.upper()} Brain 已閒置 {idle_min} 分鐘，觸發交接重啟！")
                            worker.trigger_handoff_and_restart(reason=f"Context 達標且已閒置 {idle_min} 分鐘")
            except Exception as e:
                print(f"[ClaudeBrainPool] ⚠️ 閒置監聽器異常: {e}")

    def restart(self):
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
            "engine": "claude",
            "is_ready": all_ready,
            "low_brain": low_stat,
            "med_brain": med_stat,
            "high_brain": high_stat,
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

        print(f"[ClaudeBrainPool] 🧠 請求派發至 Claude {worker.name.upper()} Brain (等級: {chosen_level.upper()})")

        stream_gen = worker.send_query_stream(clean_q, media=media)
        turn_result = {}
        try:
            while True:
                item = next(stream_gen)
                yield item
        except StopIteration as e:
            turn_result = e.value or {}

        turn_result["thinking_level"] = chosen_level
        turn_result["model"] = worker.model or "claude-default"
        turn_result["worker"] = worker.name

        ttft = turn_result.get("time_to_first_token", 0)
        tot = turn_result.get("total_latency", 0)
        actions_str = f"singleton_claude ({chosen_level}) (ttft: {ttft:.2f}s, total: {tot:.2f}s)"
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
                f"engine: claude-singleton ({chosen_level})",
                f"ttft: {res.get('time_to_first_token', 0):.2f}s",
                f"total: {res.get('total_latency', 0):.2f}s"
            ]
            if media:
                actions.append(f"multimodal: {len(media)} items")
            return (
                clean_reply or "好的，已為您處理完畢。",
                actions,
                res.get("time_to_first_token", 0),
                res.get("total_latency", 0)
            )

    @property
    def conversation_id(self) -> Optional[str]:
        return self.low_worker.conversation_id

    @property
    def _is_ready(self) -> bool:
        return self.low_worker._is_ready or self.med_worker._is_ready or self.high_worker._is_ready


# 導出單例實例
claude_singleton = ClaudeBrainPool()
