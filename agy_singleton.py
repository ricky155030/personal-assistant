"""
Antigravity CLI (agy) 三守護進程大腦池管理器 (Agy Brain Pool - ACP 引擎版)
透過 `agy acp` (Agent Client Protocol, JSON-RPC 2.0 over stdio) 維護三常駐獨立進程池：
  - Low Brain: gemini-3.8-flash-low (日常快問快答、家電直連、行程備忘)
  - Med Brain: gemini-3.8-flash-medium (時空交通規劃、決策比較、多條件推導)
  - High Brain: gemini-3.8-flash-high (深度研究、技術架構分析、長篇剖析)
支援：
  - 原生 `session/cancel` 毫秒級 (6~15ms) 強制煞車中斷思考與執行中工具（等同終端機按 ESC，免重啟進程）
  - 前置透過 thinking_router 進行零延遲路由分流
  - 內建 Context 上下文閾值防護，達標時背景自動重啟並發送 Telegram 通知
"""

import base64
import json
import logging
import mimetypes
import queue
import re
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
    params = tool_info.get("parameters", {}) or tool_info.get("args", {}) or tool_info

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

        # 3. Home Assistant
        if "ha_tool" in cmd or "homeassistant" in cmd:
            return "🏠 正在控制 Home Assistant 智慧家電..."

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

        # 9. GPS 定位
        if "current-location" in cmd or "location" in cmd:
            return "📍 正在取得即時 GPS 定位..."

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

    elif tool_name in ("view_file", "read_file", "replace_file_content", "edit_file", "write_to_file", "write_file", "sed_file"):
        target = params.get("TargetFile") or params.get("AbsolutePath") or params.get("Path") or ""
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
        role = params.get("Role", "")
        if not role and subagents and isinstance(subagents, list) and len(subagents) > 0:
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


def extract_quick_replies(reply_text: str) -> List[str]:
    """
    從助理回覆中智慧提取「快速回覆選項 (Quick-Reply Chips)」：
    當助理遵照「先問再查」原則反問主人（例如提供分店、時間、規格選項，或是非確認題）時，
    自動萃取最多 4 個精簡可點選膠囊，供 iOS App 與 Telegram 一鍵點選回覆。
    """
    if not reply_text:
        return []
    text = reply_text.strip()
    if "？" not in text and "?" not in text:
        return []

    options: List[str] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. 偵測條列或編號選項（例如：1. 屋馬文心店、- 12:00、1️⃣ 方案 A）
    bullet_pat = re.compile(
        r"^(?:[1-6][\.\、\)]|[1-6]️⃣|[-•▪▫▸▹]|選項\s*[1-6A-D]|方案\s*[A-D1-6])\s*(.+)$"
    )
    for ln in lines:
        if ln.startswith("📝 秘書備忘") or ln.startswith("───"):
            continue
        m = bullet_pat.match(ln)
        if m:
            item = m.group(1).strip()
            item = re.sub(r"\*\*(.+?)\*\*", r"\1", item)
            item = re.split(r"\s*(?:[—–\-:：\(（]|，)", item)[0].strip()
            item = item.strip("。！？!?、， ")
            if 1 < len(item) <= 18 and item not in options:
                options.append(item)

    if 2 <= len(options) <= 5:
        return options[:4]

    # 2. 偵測句內引號並列選項（例如：「文心店」、「國安店」或「崇德店」）
    tail_text = "\n".join(lines[-4:])
    quoted = re.findall(r"[「『]([^」』]{2,16})[」』]", tail_text)
    clean_quoted = []
    for q in quoted:
        q_clean = q.strip()
        if q_clean and q_clean not in clean_quoted and not q_clean.startswith("http"):
            clean_quoted.append(q_clean)
    if 2 <= len(clean_quoted) <= 4:
        return clean_quoted[:4]

    # 3. 偵測常見是非 / 執行確認反問句
    last_two = " ".join(lines[-2:])
    confirm_patterns = (
        r"(要幫您|需要我|是否要|要不要|確定要|要現在|需要幫您|請問要|可以嗎|好嗎|對嗎|要繼續嗎).*?[？\?]",
    )
    for pat in confirm_patterns:
        if re.search(pat, last_two):
            return ["好，請執行", "先不用"]

    return []


class AgyWorker:
    """單一常駐 agy acp (Agent Client Protocol) 進程工作者（支援同 Session 動態切模型、session/resume 與 /compact）"""

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.proc: Optional[subprocess.Popen] = None
        self.session_id: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.session_title: str = ""
        self.resumed_from_disk: bool = False
        self.lock = threading.RLock()
        self._stdin_lock = threading.Lock()
        self._rpc_id_lock = threading.Lock()
        self._rpc_seq = 0
        self._active_turn_id: Optional[int] = None
        self._cancel_requested = False
        self._is_ready = False
        self._is_restarting = False
        self.last_tokens = 0
        self.last_usage = {}
        self.last_active_time = time.time()
        self.pending_handoff = False
        self.cwd = str(settings.AGENT_MD_PATH.parent)
        self.session_state_path = settings.DATA_DIR / "acp_session_state.json"
        self.stderr_file = None
        self.stdout_queue: queue.Queue = queue.Queue()
        self.reader_thread: Optional[threading.Thread] = None

    def _load_saved_session(self) -> Optional[dict]:
        """從磁碟讀取前次保存的 ACP Session 狀態"""
        try:
            if self.session_state_path.exists():
                data = json.loads(self.session_state_path.read_text(encoding="utf-8"))
                if data.get("session_id"):
                    return data
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ⚠️ 讀取 acp_session_state.json 失敗: {e}")
        return None

    def _save_session_state(self):
        """將當前 ACP Session ID、Token 用量與標題持久化至磁碟，供服務重啟後無損 resume"""
        if not self.session_id:
            return
        try:
            self.session_state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "session_id": self.session_id,
                "updated_at": datetime.now().isoformat(),
                "last_tokens": self.last_tokens,
                "last_model": self.model,
                "session_title": self.session_title,
                "last_usage": self.last_usage,
            }
            self.session_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ⚠️ 儲存 acp_session_state.json 失敗: {e}")

    def _clear_saved_session(self):
        """清除磁碟保存的 Session 狀態（用於手動 /restart 或 Context 滿載重置）"""
        try:
            self.session_state_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _next_rpc_id(self) -> int:
        with self._rpc_id_lock:
            self._rpc_seq += 1
            return self._rpc_seq

    def _send_rpc(self, payload: dict):
        """執行緒安全寫入 JSON-RPC 訊息至 agy acp stdin"""
        with self._stdin_lock:
            if self.proc is None or self.proc.stdin is None or self.proc.poll() is not None:
                raise RuntimeError("ACP process stdin is not available")
            line = json.dumps(payload, ensure_ascii=False)
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()

    def _wait_for_rpc_response(self, req_id: int, timeout: float = 30.0) -> dict:
        """等待指定 id 的 JSON-RPC 回應（忽略期間的通知事件）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.1, min(1.0, deadline - time.time()))
            try:
                msg = self.stdout_queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if msg is None:
                raise RuntimeError("ACP stdout closed unexpectedly during handshake")
            if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise RuntimeError(f"ACP RPC error (id={req_id}): {msg['error']}")
                return msg
        raise TimeoutError(f"Timeout waiting for ACP response id={req_id}")

    def start(self, force_new_session: bool = False):
        with self.lock:
            self._start_unlocked(force_new_session=force_new_session)

    def _start_unlocked(self, force_new_session: bool = False):
        if self.proc is not None and self.proc.poll() is None:
            if self._is_ready and not force_new_session:
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

        if force_new_session:
            self._clear_saved_session()

        cmd = [
            settings.AGY_BIN,
            "acp",
            "--dangerously-skip-permissions",
        ]

        print(f"[AgyWorker:{self.name.upper()}] 🚀 正在啟動 ACP 常駐進程 (預設模型: {self.model})...")
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
        self._active_turn_id = None
        self._cancel_requested = False

        def _reader(proc, out_q):
            try:
                for raw_line in iter(proc.stdout.readline, ''):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue

                    # 防呆：若 ACP Server 反向對 Client 發出需要回應的 RPC Request (例如 session/request_permission)
                    if "method" in msg and "id" in msg and "result" not in msg and "error" not in msg:
                        method = msg.get("method", "")
                        req_id = msg.get("id")
                        if method == "session/request_permission":
                            try:
                                self._send_rpc({
                                    "jsonrpc": "2.0",
                                    "id": req_id,
                                    "result": {"outcome": {"outcome": "selected", "optionId": "allow"}}
                                })
                            except Exception:
                                pass
                            continue
                        else:
                            # 其他未知 Server-to-Client Request 回傳空結果避免阻塞
                            try:
                                self._send_rpc({"jsonrpc": "2.0", "id": req_id, "result": {}})
                            except Exception:
                                pass
                            continue

                    out_q.put(msg)
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
            # 1. ACP Initialize 握手
            init_id = self._next_rpc_id()
            self._send_rpc({
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": {"name": f"macmini-assistant-{self.name}", "version": "2.0"}
                }
            })
            self._wait_for_rpc_response(init_id, timeout=30.0)

            # 2. 優先嘗試透過 session/resume 無損接續前次未滿載的對話 Session
            resumed = False
            saved_state = None if force_new_session else self._load_saved_session()
            token_limit = settings.CONTEXT_WINDOW_LIMIT * settings.CONTEXT_RESTART_THRESHOLD
            if saved_state and saved_state.get("session_id") and saved_state.get("last_tokens", 0) < token_limit:
                candidate_sid = saved_state["session_id"]
                try:
                    resume_id = self._next_rpc_id()
                    self._send_rpc({
                        "jsonrpc": "2.0",
                        "id": resume_id,
                        "method": "session/resume",
                        "params": {
                            "sessionId": candidate_sid,
                            "cwd": self.cwd,
                            "mcpServers": []
                        }
                    })
                    resume_resp = self._wait_for_rpc_response(resume_id, timeout=20.0)
                    resumed_sid = resume_resp.get("result", {}).get("sessionId") or candidate_sid
                    self.session_id = resumed_sid
                    self.conversation_id = resumed_sid
                    self.last_tokens = saved_state.get("last_tokens", 0)
                    self.last_usage = saved_state.get("last_usage", {})
                    self.session_title = saved_state.get("session_title", "")
                    self.resumed_from_disk = True
                    resumed = True
                except Exception as resume_err:
                    print(f"[AgyWorker:{self.name.upper()}] ℹ️ session/resume 無法恢復舊 Session ({candidate_sid[:8]}): {resume_err}，將建立新 Session")

            # 3. 若無可接續之 Session 或強制新建，呼叫 session/new
            if not resumed:
                new_id = self._next_rpc_id()
                self._send_rpc({
                    "jsonrpc": "2.0",
                    "id": new_id,
                    "method": "session/new",
                    "params": {
                        "cwd": self.cwd,
                        "mcpServers": []
                    }
                })
                new_resp = self._wait_for_rpc_response(new_id, timeout=45.0)
                self.session_id = new_resp.get("result", {}).get("sessionId")
                self.conversation_id = self.session_id
                self.last_tokens = 0
                self.last_usage = {}
                self.session_title = ""
                self.resumed_from_disk = False

            if not self.session_id:
                raise RuntimeError("ACP 未能取得有效 sessionId")

            # 4. 設定當前模型與思考等級 (e.g. gemini-3.8-flash-low / medium / high)
            self._set_model_unlocked(self.model, log_switch=False)

            elapsed = time.time() - t0
            self._is_ready = True
            self._save_session_state()
            if resumed:
                title_hint = f" | 主題: {self.session_title}" if self.session_title else ""
                print(f"[AgyWorker:{self.name.upper()}] ♻️ ACP 無損接續既有 Session 就緒！耗時 {elapsed:.2f}s | Session ID: {self.session_id} | 延續 Tokens: {self.last_tokens:,}{title_hint}")
            else:
                print(f"[AgyWorker:{self.name.upper()}] ✅ ACP 全新 Session 就緒！耗時 {elapsed:.2f}s | Session ID: {self.session_id} | 模型: {self.model}")
                # 檢查是否有未讀取的 session_handoff.md 並消化歸檔
                self._consume_and_archive_handoff()

        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ❌ ACP 啟動失敗：{e}")
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
            self._is_ready = False

    def _set_model_unlocked(self, target_model: str, log_switch: bool = True) -> bool:
        """在同一個 ACP Session 內動態切換模型 (無損保留完整對話上下文)"""
        if not self.session_id or not target_model:
            return False
        if log_switch and target_model == self.model:
            return True
        old_model = self.model
        model_req_id = self._next_rpc_id()
        self._send_rpc({
            "jsonrpc": "2.0",
            "id": model_req_id,
            "method": "session/set_model",
            "params": {
                "sessionId": self.session_id,
                "modelId": target_model
            }
        })
        self._wait_for_rpc_response(model_req_id, timeout=15.0)
        self.model = target_model
        if log_switch and old_model != target_model:
            print(f"[AgyWorker:{self.name.upper()}] 🔀 同 Session 動態切換模型: {old_model} ➔ {target_model} (Session: {self.session_id[:8]})")
        return True

    def cancel_current_turn(self) -> bool:
        """
        毫秒級中斷當前正在執行的 Turn (發送 ACP `session/cancel`)。
        不需等待 self.lock，直接透過 _stdin_lock 送入取消指令，
        使正在執行的 send_query_stream 於 6~15ms 內收到 stopReason=='cancelled' 並釋放鎖定。
        """
        if not self._is_ready or self.proc is None or self.proc.poll() is not None or not self.session_id:
            return False
        if self._active_turn_id is None:
            return False
        try:
            self._cancel_requested = True
            self._send_rpc({
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": self.session_id}
            })
            print(f"[AgyWorker:{self.name.upper()}] 🛑 已發送 ACP session/cancel (turn_id={self._active_turn_id})")
            return True
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ⚠️ 發送 session/cancel 失敗: {e}")
            return False

    def compact_session(self, timeout: float = 45.0) -> dict:
        """
        執行 ACP 原生 `/compact` 無損上下文壓縮：
        - 在不中斷當前 Session ID 的前提下，由 AGY 底層將歷史長對話精煉壓縮
        - 釋放 Context Window Token 空間並降低後續每輪推理延遲
        """
        if self.is_busy:
            return {"success": False, "error": "助理目前正在處理對話中，請稍候再執行壓縮。"}

        with self.lock:
            if not self._is_ready or not self.session_id or self.proc is None or self.proc.poll() is not None:
                self._start_unlocked(force_new_session=False)
                if not self._is_ready or not self.session_id:
                    return {"success": False, "error": "ACP 大腦尚未就緒"}

            before_tokens = self.last_tokens
            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            req_id = self._next_rpc_id()
            t0 = time.perf_counter()
            try:
                self._send_rpc({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": self.session_id,
                        "prompt": [{"type": "text", "text": "/compact"}]
                    }
                })
                resp = self._wait_for_rpc_response(req_id, timeout=timeout)
                elapsed = round(time.perf_counter() - t0, 2)
                res = resp.get("result", {})
                raw_usage = res.get("usage", {})
                if raw_usage:
                    mapped_usage = {
                        "input_tokens": raw_usage.get("inputTokens", 0),
                        "output_tokens": raw_usage.get("outputTokens", 0),
                        "thought_tokens": raw_usage.get("thoughtTokens", 0),
                        "cached_read_tokens": raw_usage.get("cachedReadTokens", 0),
                        "total_tokens": raw_usage.get("totalTokens", 0),
                    }
                    self.last_usage = mapped_usage
                    new_tokens = mapped_usage.get("total_tokens") or mapped_usage.get("input_tokens") or 0
                    if new_tokens > 0:
                        self.last_tokens = new_tokens
                self.pending_handoff = False
                self._save_session_state()
                after_tokens = self.last_tokens
                print(f"[AgyWorker:{self.name.upper()}] 🧹 ACP 原生 /compact 壓縮完成！耗時 {elapsed}s | Tokens: {before_tokens:,} ➔ {after_tokens:,}")
                return {
                    "success": True,
                    "session_id": self.session_id,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "elapsed_sec": elapsed
                }
            except Exception as e:
                print(f"[AgyWorker:{self.name.upper()}] ⚠️ 執行 /compact 失敗: {e}")
                return {"success": False, "error": str(e)}

    @property
    def is_busy(self) -> bool:
        return self._active_turn_id is not None

    def stop(self):
        with self.lock:
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
            self._is_ready = False
            self._active_turn_id = None

    def restart(self, force_new_session: bool = True):
        with self.lock:
            self._restart_unlocked(force_new_session=force_new_session)

    def _restart_unlocked(self, force_new_session: bool = True):
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
        self.session_id = None
        self._active_turn_id = None
        self._start_unlocked(force_new_session=force_new_session)

    def _run_internal_prompt(self, prompt_text: str, timeout: float = 30.0) -> str:
        """內部同步輔助：透過 ACP 發送單次提示並收集文字回覆（供 Handoff 生成與注入使用）"""
        if not self._is_ready or not self.session_id or self.proc is None or self.proc.poll() is not None:
            return ""
        while not self.stdout_queue.empty():
            try:
                self.stdout_queue.get_nowait()
            except queue.Empty:
                break

        req_id = self._next_rpc_id()
        self._send_rpc({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "session/prompt",
            "params": {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": prompt_text}]
            }
        })

        collected = []
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                msg = self.stdout_queue.get(timeout=1.0)
                if msg is None:
                    break
                if msg.get("id") == req_id:
                    break
                if msg.get("method") == "session/update":
                    update = msg.get("params", {}).get("update", {})
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        delta = update.get("content", {}).get("text", "")
                        if delta:
                            collected.append(delta)
            except queue.Empty:
                continue
            except Exception:
                break
        return "".join(collected).strip()

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
        try:
            content = self._run_internal_prompt(handoff_prompt, timeout=30.0)
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

            self._run_internal_prompt(prime_prompt, timeout=15.0)

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
            "engine": "agy-acp",
            "is_ready": self._is_ready,
            "is_busy": self.is_busy,
            "is_restarting": self._is_restarting,
            "pending_handoff": self.pending_handoff,
            "resumed_from_disk": self.resumed_from_disk,
            "session_title": self.session_title,
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
                f"🔄 <b>[系統提示]</b> ACP 統一大腦 Context 已達 <b>{stat['percent']}%</b>（原因：{reason}），正在整理 <code>session_handoff.md</code> 並開啟全新 Session...",
                parse_mode="HTML"
            )
            t0 = time.time()
            self._generate_session_handoff()
            self.restart(force_new_session=True)
            elapsed = time.time() - t0
            send_telegram_sync(
                f"✅ <b>[系統提示]</b> ACP 統一大腦重啟完成！耗時 {elapsed:.2f}s，已讀取交接備忘並歸檔至 <code>handoffs/</code>，Context 已重置為 0。",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[AgyWorker:{self.name.upper()}] ❌ 自動交接重啟失敗: {e}")
        finally:
            self.pending_handoff = False
            self._is_restarting = False

    def trigger_restart_with_notification(self, reason: str = "自動觸發"):
        self.trigger_handoff_and_restart(reason)

    def _build_acp_prompt_blocks(self, query: str, media: Optional[List[dict]] = None) -> List[dict]:
        """將文字與多模態圖片封裝為 ACP ContentBlock 陣列"""
        blocks: List[dict] = []
        if media:
            for item in media:
                mime_type = item.get("mime_type") or item.get("mimeType") or "image/jpeg"
                b64_data = item.get("data")
                uri = item.get("uri") or ""
                if not b64_data and uri:
                    try:
                        file_path = Path(uri.replace("file://", ""))
                        if file_path.exists():
                            guessed, _ = mimetypes.guess_type(str(file_path))
                            if guessed:
                                mime_type = guessed
                            b64_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
                    except Exception as ex:
                        print(f"[AgyWorker:{self.name.upper()}] ⚠️ 讀取圖片轉 Base64 失敗 ({uri}): {ex}")
                if b64_data:
                    blocks.append({
                        "type": "image",
                        "mimeType": mime_type,
                        "data": b64_data
                    })
        if query:
            blocks.append({"type": "text", "text": query})
        elif not blocks:
            blocks.append({"type": "text", "text": "你好"})
        return blocks

    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        target_model: Optional[str] = None
    ) -> Generator[Tuple[str, str], None, dict]:
        with self.lock:
            if not self._is_ready or self.proc is None or self.proc.poll() is not None or not self.session_id:
                self._start_unlocked(force_new_session=False)

            if not self._is_ready or self.proc is None or not self.session_id:
                yield ("text", f"{self.name.upper()} 守護進程未就緒，請稍後重試。")
                return {"status": "ERROR", "error": "process_not_ready"}

            # 1. 同 Session 動態切換模型 (session/set_model)，共享上下文記憶
            if target_model and target_model != self.model:
                try:
                    self._set_model_unlocked(target_model, log_switch=True)
                except Exception as model_err:
                    print(f"[AgyWorker:{self.name.upper()}] ⚠️ 動態切換模型 ({target_model}) 失敗: {model_err}")

            start_t = time.perf_counter()
            prompt_blocks = self._build_acp_prompt_blocks(query, media=media)

            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            req_id = self._next_rpc_id()
            self._active_turn_id = req_id
            self._cancel_requested = False

            try:
                self._send_rpc({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": self.session_id,
                        "prompt": prompt_blocks
                    }
                })
            except Exception as e:
                self._active_turn_id = None
                print(f"[AgyWorker:{self.name.upper()}] 寫入 ACP stdin 失敗，嘗試重啟：{e}")
                self._restart_unlocked(force_new_session=False)
                yield ("text", "通訊管線重連中，請再試一次。")
                return {"status": "ERROR", "error": str(e)}

            first_token_time = None
            full_reply_parts = []
            full_thought_parts = []
            turn_result = {}
            last_data_time = time.perf_counter()
            active_tools = 0
            was_cancelled = False

            try:
                while True:
                    try:
                        msg = self.stdout_queue.get(timeout=1.0)
                    except queue.Empty:
                        if self._cancel_requested:
                            was_cancelled = True
                        time_since_last_data = time.perf_counter() - last_data_time
                        total_time = time.perf_counter() - start_t
                        is_idle_timeout = time_since_last_data > 120.0
                        is_total_timeout = total_time > 600.0 and time_since_last_data > 30.0
                        if is_idle_timeout or is_total_timeout:
                            if full_reply_parts and active_tools == 0:
                                print(f"[AgyWorker:{self.name.upper()}] ℹ️ 已有文字回覆且無進行中工具，正常結束本次 turn")
                                break
                            print(f"[AgyWorker:{self.name.upper()}] ⚠️ 超時 (閒置 {time_since_last_data:.1f}s, 總長 {total_time:.1f}s)，自動重啟...")
                            self._restart_unlocked(force_new_session=False)
                            yield ("text", "助理處理逾時，已自動重啟重連，請再試一次。")
                            return {"status": "TIMEOUT", "error": "stream_timeout"}
                        continue

                    if msg is None:
                        break

                    last_data_time = time.perf_counter()

                    # 1. 處理 ACP session/update 串流通知
                    if msg.get("method") == "session/update":
                        update = msg.get("params", {}).get("update", {})
                        utype = update.get("sessionUpdate", "")

                        if utype == "agent_thought_chunk":
                            thought_delta = update.get("content", {}).get("text", "")
                            if thought_delta:
                                full_thought_parts.append(thought_delta)
                                yield ("thought", thought_delta)

                        elif utype == "agent_message_chunk":
                            delta = update.get("content", {}).get("text", "")
                            if delta:
                                if first_token_time is None:
                                    first_token_time = time.perf_counter() - start_t
                                full_reply_parts.append(delta)
                                yield ("text", delta)

                        elif utype == "tool_call":
                            tool_name = update.get("name", "")
                            tool_args = update.get("args", {})
                            t_status = update.get("status", "in_progress")
                            if t_status == "in_progress":
                                active_tools += 1
                                status_text = format_tool_status(tool_name, {"parameters": tool_args})
                                yield ("status", status_text)

                        elif utype == "tool_call_update":
                            tool_name = update.get("name", "")
                            t_status = update.get("status", "")
                            if t_status in ("completed", "failed", "cancelled"):
                                active_tools = max(0, active_tools - 1)
                                yield ("tool_done", tool_name)

                        elif utype == "session_info_update":
                            title = update.get("title", "")
                            if title:
                                self.session_title = title

                    # 2. 處理當前 Turn 的最終 JSON-RPC Response
                    elif msg.get("id") == req_id:
                        if "error" in msg:
                            err_obj = msg["error"]
                            turn_result = {"status": "ERROR", "error": err_obj.get("message", str(err_obj))}
                        else:
                            res = msg.get("result", {})
                            stop_reason = res.get("stopReason", "end_turn")
                            raw_usage = res.get("usage", {})
                            mapped_usage = {
                                "input_tokens": raw_usage.get("inputTokens", 0),
                                "output_tokens": raw_usage.get("outputTokens", 0),
                                "thought_tokens": raw_usage.get("thoughtTokens", 0),
                                "cached_read_tokens": raw_usage.get("cachedReadTokens", 0),
                                "total_tokens": raw_usage.get("totalTokens", 0),
                            }
                            turn_result = {
                                "status": "CANCELLED" if stop_reason == "cancelled" else "SUCCESS",
                                "stop_reason": stop_reason,
                                "cancelled": stop_reason == "cancelled" or self._cancel_requested,
                                "usage": mapped_usage,
                            }
                            if stop_reason == "cancelled" or self._cancel_requested:
                                was_cancelled = True
                        break
            finally:
                self._active_turn_id = None
                self._cancel_requested = False

            total_latency = time.perf_counter() - start_t
            self.last_active_time = time.time()
            full_reply = "".join(full_reply_parts) or turn_result.get("response", "")
            full_thought = "".join(full_thought_parts).strip()
            clean_reply = full_reply.replace("**", "").replace("#", "").replace("`", "").strip()

            turn_result["clean_reply"] = clean_reply
            turn_result["thought"] = full_thought
            turn_result["quick_replies"] = [] if was_cancelled else extract_quick_replies(clean_reply)
            turn_result["cancelled"] = was_cancelled or turn_result.get("cancelled", False)
            turn_result["time_to_first_token"] = first_token_time or total_latency
            turn_result["total_latency"] = total_latency

            # 更新與持久化 Context 用量
            usage = turn_result.get("usage", {})
            if usage:
                self.last_usage = usage
                self.last_tokens = usage.get("total_tokens") or usage.get("input_tokens") or self.last_tokens

            self._save_session_state()

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
    統一常駐大腦管理器 (Unified ACP Brain Pool)
    - 單一常駐 `agy acp` 進程，共享同一個 `sessionId` 上下文記憶
    - 依據 Thinking Level (Low / Med / High) 透過 `session/set_model` 毫秒級動態切換模型
    - 啟動時自動透過 `session/resume` 無損恢復重啟前的對話記憶
    - 即時串流 `agent_thought_chunk` 推理思考過程與 `session/cancel` 毫秒級中斷
    """

    def __init__(self):
        self.low_model = getattr(settings, "AGY_MODEL_LOW", getattr(settings, "AGY_MODEL_FAST", "gemini-3.8-flash-low"))
        self.med_model = getattr(settings, "AGY_MODEL_MED", getattr(settings, "AGY_MODEL_DEEP", "gemini-3.8-flash-medium"))
        self.high_model = getattr(settings, "AGY_MODEL_HIGH", "gemini-3.8-flash-high")

        # 單一共享常駐進程（動態切換模型）
        self.primary_worker = AgyWorker("unified", self.low_model)

        # 向下相容屬性別名（指向同一個統一大腦進程）
        self.low_worker = self.primary_worker
        self.med_worker = self.primary_worker
        self.high_worker = self.primary_worker
        self.fast_worker = self.primary_worker
        self.deep_worker = self.primary_worker
        self.lock = threading.RLock()
        self._idle_monitor_thread: Optional[threading.Thread] = None

    def start(self, force_new_session: bool = False):
        """啟動統一 ACP 常駐大腦（預設優先嘗試 session/resume 接續記憶）"""
        print(f"[AgyBrainPool] 🚀 正在啟動統一 ACP 大腦 (支援同 Session 動態切模型: {self.low_model} / {self.med_model} / {self.high_model})...")
        self.primary_worker.start(force_new_session=force_new_session)
        print(f"[AgyBrainPool] ✅ 統一 ACP 大腦啟動完成！(Ready: {self.primary_worker._is_ready} | Session: {self.primary_worker.session_id})")

        if self._idle_monitor_thread is None or not self._idle_monitor_thread.is_alive():
            self._idle_monitor_thread = threading.Thread(target=self._idle_monitor_loop, daemon=True)
            self._idle_monitor_thread.start()
            print(f"[AgyBrainPool] ⏰ 閒置 Handoff 監控線程已啟動 (檢測週期: 15s | 閒置門檻: {settings.CONTEXT_IDLE_MINUTES} 分鐘)")

    def stop(self):
        """停止常駐大腦進程"""
        self.primary_worker.stop()

    def cancel_active_turns(self, level: Optional[str] = None) -> dict:
        """
        對目前正在執行 Turn 的統一大腦發送 ACP `session/cancel` 原生中斷訊號。
        """
        cancelled_workers = []
        if self.primary_worker.is_busy and self.primary_worker.cancel_current_turn():
            cancelled_workers.append(self.primary_worker.name)

        return {
            "cancelled": len(cancelled_workers) > 0,
            "workers": cancelled_workers
        }

    def compact_session(self, timeout: float = 45.0) -> dict:
        """對統一 ACP 大腦執行原生 `/compact` 無損上下文壓縮"""
        return self.primary_worker.compact_session(timeout=timeout)

    def _idle_monitor_loop(self):
        """背景定時檢查閒置超過 CONTEXT_IDLE_MINUTES 且待交接之統一大腦"""
        while True:
            time.sleep(15)
            try:
                idle_limit_sec = settings.CONTEXT_IDLE_MINUTES * 60
                now = time.time()
                worker = self.primary_worker
                if worker.pending_handoff and not worker._is_restarting and worker._is_ready and not worker.is_busy:
                    idle_sec = now - worker.last_active_time
                    if idle_sec >= idle_limit_sec:
                        idle_min = round(idle_sec / 60, 1)
                        print(f"[AgyBrainPool] ⏰ ACP 統一大腦已連續閒置 {idle_min} 分鐘 (門檻 {settings.CONTEXT_IDLE_MINUTES} 分鐘)，啟動 Session Handoff 重啟程序！")
                        worker.trigger_handoff_and_restart(reason=f"Context 達標且已連續閒置 {idle_min} 分鐘")
            except Exception as e:
                print(f"[AgyBrainPool] ⚠️ 閒置監聽器異常: {e}")

    def restart(self, force_new_session: bool = True):
        """重啟統一大腦並建立全新 Session"""
        self.primary_worker.restart(force_new_session=force_new_session)

    def get_status(self) -> dict:
        p_stat = self.primary_worker.get_status()
        low_stat = dict(p_stat, name="low", model=self.low_model)
        med_stat = dict(p_stat, name="med", model=self.med_model)
        high_stat = dict(p_stat, name="high", model=self.high_model)
        return {
            "engine": "agy-acp-unified",
            "unified_mode": True,
            "is_ready": p_stat["is_ready"],
            "current_model": p_stat["model"],
            "resumed_from_disk": p_stat.get("resumed_from_disk", False),
            "session_title": p_stat.get("session_title", ""),
            "low_brain": low_stat,
            "med_brain": med_stat,
            "high_brain": high_stat,
            # 向下相容歷史欄位
            "fast_brain": low_stat,
            "deep_brain": med_stat,
            "conversation_id": p_stat["conversation_id"],
            "current_tokens": p_stat["current_tokens"],
            "limit": settings.CONTEXT_WINDOW_LIMIT,
            "percent": p_stat["percent"],
            "threshold": settings.CONTEXT_RESTART_THRESHOLD,
            "pending_handoff": p_stat["pending_handoff"],
            "idle_minutes_threshold": settings.CONTEXT_IDLE_MINUTES,
            "usage": p_stat["usage"],
        }

    def trigger_restart_with_notification(self, reason: str = "手動觸發"):
        self.primary_worker.trigger_handoff_and_restart(reason)

    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None
    ) -> Generator[Tuple[str, str], None, dict]:
        """
        核心串流發送介面：
        1. 自動執行 thinking_router 判定 (若外部未指定)
        2. 在同一個統一 Session 內透過 session/set_model 動態切換對應模型 (Low / Med / High)
        3. 即時串流 thought / status / tool_done / text
        """
        raw_level = level or classify_thinking_level(query)
        chosen_level = "med" if raw_level == "medium" else raw_level.lower()
        clean_q = clean_query_prefix(query)

        if chosen_level == "low":
            target_model = self.low_model
        elif chosen_level == "high":
            target_model = self.high_model
        else:
            target_model = self.med_model
            chosen_level = "med"

        worker = self.primary_worker
        print(f"[AgyBrainPool] 🧠 請求派發至統一大腦 [ACP] (等級: {chosen_level.upper()} | 目標模型: {target_model} | 共享 Session: {(worker.session_id or 'N/A')[:8]})")

        stream_gen = worker.send_query_stream(clean_q, media=media, target_model=target_model)
        turn_result = {}
        try:
            while True:
                item = next(stream_gen)
                yield item
        except StopIteration as e:
            turn_result = e.value or {}

        turn_result["thinking_level"] = chosen_level
        turn_result["model"] = target_model
        turn_result["worker"] = worker.name

        if not turn_result.get("cancelled"):
            ttft = turn_result.get("time_to_first_token", 0)
            tot = turn_result.get("total_latency", 0)
            actions_str = f"singleton_agy_acp ({chosen_level}) (ttft: {ttft:.2f}s, total: {tot:.2f}s)"
            memory_manager.log_interaction(
                query,
                turn_result.get("clean_reply", ""),
                [actions_str]
            )
        else:
            print(f"[AgyBrainPool] 🛑 統一大腦本次 Turn 已被使用者中途取消 (耗時: {turn_result.get('total_latency', 0):.2f}s)")

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
                f"engine: agy-acp ({chosen_level})",
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
        return self.primary_worker.conversation_id

    @property
    def _is_ready(self) -> bool:
        return self.primary_worker._is_ready


# 導出單例實例，命名維持 agy_singleton 以向下完全相容
agy_singleton = AgyBrainPool()
