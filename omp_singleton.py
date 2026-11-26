"""OMP (Oh-My-Pi) Brain Engine implementation conforming to BaseBrainEngine."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from brain_engine import BaseBrainEngine, clean_query_for_history
from config import settings
from widget_html_utils import extract_rich_blocks


_clean_query_for_history = clean_query_for_history

OMP_STREAM_IDLE_TIMEOUT_SEC = max(30.0, float(os.getenv("OMP_STREAM_IDLE_TIMEOUT_SEC", "300")))
OMP_STREAM_MAX_TOTAL_SEC = max(OMP_STREAM_IDLE_TIMEOUT_SEC, float(os.getenv("OMP_STREAM_MAX_TOTAL_SEC", "1800")))


def _finalize_reply_and_metadata(raw_reply: str, metadata: dict) -> str:
    """抽離 <widget_html> 與 <image_urls> 區塊並寫入 metadata，避免原始標籤污染 history.jsonl。"""
    clean_reply, widget_html, image_urls = extract_rich_blocks(raw_reply)
    if widget_html:
        metadata["widget_html"] = widget_html
    if image_urls:
        metadata["image_urls"] = image_urls
    return clean_reply.strip()

def _quick_replies_for(clean_reply: str, user_query: str = "", was_cancelled: bool = False) -> List[str]:
    """與 agy 同規格的候選回覆：取消/空回覆直接回空，否則 lazy 復用 agy 輕量判斷。"""
    if was_cancelled or not clean_reply:
        return []
    try:
        from agy_singleton import extract_quick_replies
        return extract_quick_replies(clean_reply, user_query=user_query) or []
    except Exception:
        return []


def _estimate_omp_turn_cost(model: str, usage: Optional[dict]) -> float:
    """依據模型定價與 Token 用量估算單輪花費 (USD)，優先復用 agy_singleton 定價表。"""
    if not usage:
        return 0.0
    try:
        from agy_singleton import _estimate_turn_cost_usd
        return _estimate_turn_cost_usd(model, usage)
    except Exception:
        inp = max(0, int(usage.get("input_tokens", 0) or 0))
        c_read = max(0, int(usage.get("cached_read_tokens", 0) or 0))
        out = max(0, int(usage.get("output_tokens", 0) or 0))
        thought = max(0, int(usage.get("thought_tokens", 0) or 0))
        return round((inp * 2.00 + c_read * 0.20 + (out + thought) * 12.00) / 1_000_000.0, 6)


def _format_metadata_thinking_level(raw_level: Optional[str], active_level: Optional[str] = None) -> str:
    """標準化寫入 metadata 的思考深度標籤（low / med / high / xhigh 等）。"""
    val = (active_level or raw_level or "med").strip().lower()
    if ":" in val:
        val = val.split(":", 1)[1].strip()
    if val == "medium":
        return "med"
    if val == "max":
        return "xhigh"
    return val or "med"



def _load_agents_md_text() -> str:
    try:
        agents_path = settings.BASE_DIR / "AGENTS.md"
        if agents_path.exists():
            return agents_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""
DEFAULT_OMP_MODELS: List[dict] = [
    {"id": "Gemini-GW-1/gemini-4-hydrogen", "label": "Gemini 4 Hydrogen (GW-1)", "thinking_levels": ["low", "med", "high"]},
    {"id": "Gemini-GW-1/gemini-4-neon", "label": "Gemini 4 Neon (GW-1)", "thinking_levels": ["low", "med", "high"]},
]

OMP_ALLOWED_MODEL_IDS = frozenset({"gemini-4-hydrogen", "gemini-4-neon"})


def _parse_omp_models_yml_fallback(text: str) -> dict:
    """Minimal fallback parser for ~/.omp/agent/models.yml when PyYAML is unavailable."""
    providers: dict = {}
    in_providers = False
    cur_provider: Optional[str] = None
    in_models = False
    cur_model: Optional[dict] = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0:
            in_providers = stripped == "providers:"
            cur_provider = None
            in_models = False
            cur_model = None
            continue
        if not in_providers:
            continue
        if indent == 2 and stripped.endswith(":"):
            cur_provider = stripped[:-1].strip()
            providers[cur_provider] = {"models": []}
            in_models = False
            cur_model = None
        elif cur_provider and indent == 4:
            if stripped == "models:":
                in_models = True
            else:
                in_models = False
                if ":" in stripped:
                    k, v = stripped.split(":", 1)
                    providers[cur_provider][k.strip()] = v.strip().strip("\"'")
            cur_model = None
        elif cur_provider and in_models:
            if stripped.startswith("- "):
                cur_model = {}
                providers[cur_provider]["models"].append(cur_model)
                rest = stripped[2:].strip()
                if ":" in rest:
                    k, v = rest.split(":", 1)
                    cur_model[k.strip()] = v.strip().strip("\"'")
            elif cur_model is not None and ":" in stripped and indent >= 8:
                k, v = stripped.split(":", 1)
                cur_model[k.strip()] = v.strip().strip("\"'")
    return {"providers": providers}


def _read_omp_models_yml_providers(models_yml_path: Optional[Path] = None) -> dict:
    """Read `providers` mapping from ~/.omp/agent/models.yml (or OMP_MODELS_YML)."""
    env_path = os.getenv("OMP_MODELS_YML", "").strip()
    path = models_yml_path or (Path(env_path) if env_path else (Path.home() / ".omp" / "agent" / "models.yml"))
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text) or {}
        except ImportError:
            data = _parse_omp_models_yml_fallback(text)
    except Exception:
        return {}
    providers = data.get("providers") if isinstance(data, dict) else None
    return providers if isinstance(providers, dict) else {}


def _load_models_from_omp_yml(models_yml_path: Optional[Path] = None) -> List[dict]:
    """Discover allowed models under `gemini-gw-*` providers from ~/.omp/agent/models.yml."""
    providers = _read_omp_models_yml_providers(models_yml_path)
    if not providers:
        return []

    models: List[dict] = []
    for provider_name, provider_cfg in providers.items():
        pname = str(provider_name or "").strip()
        if not pname.lower().startswith("gemini-gw-"):
            continue
        if not isinstance(provider_cfg, dict):
            continue
        for entry in provider_cfg.get("models") or []:
            if not isinstance(entry, dict):
                continue
            mid = str(entry.get("id") or "").strip()
            if not mid or mid.lower() not in OMP_ALLOWED_MODEL_IDS:
                continue
            label = str(entry.get("name") or "").strip() or f"{mid} ({pname})"
            models.append(
                {
                    "id": f"{pname}/{mid}",
                    "label": label,
                    "thinking_levels": ["low", "med", "high"],
                }
            )
    return models


def get_gemini_gw_endpoints(models_yml_path: Optional[Path] = None) -> List[dict]:
    """Return status endpoints for all configured `gemini-gw-*` providers."""
    providers = _read_omp_models_yml_providers(models_yml_path)
    endpoints: List[dict] = []
    for provider_name, provider_cfg in sorted(providers.items(), key=lambda kv: str(kv[0]).lower()):
        pname = str(provider_name or "").strip()
        if not pname.lower().startswith("gemini-gw-") or not isinstance(provider_cfg, dict):
            continue
        base_url = str(provider_cfg.get("baseUrl") or "").strip().rstrip("/")
        if not base_url:
            continue
        if base_url.endswith("/v1"):
            status_url = f"{base_url}/status"
        else:
            status_url = f"{base_url}/v1/status"
        short_label = pname[7:] if pname.lower().startswith("gemini-") else pname
        endpoints.append({
            "id": pname,
            "label": short_label,
            "status_url": status_url,
        })
    return endpoints


def _load_curated_omp_models() -> List[dict]:
    raw = os.getenv("OMP_CURATED_MODELS", "").strip()
    if raw:
        models: List[dict] = []
        for entry in raw.split(","):
            item = entry.strip()
            if not item:
                continue
            if "|" in item:
                mid, label = item.split("|", 1)
                models.append({
                    "id": mid.strip(),
                    "label": label.strip() or mid.strip(),
                    "thinking_levels": ["low", "med", "high"],
                })
            else:
                models.append({"id": item, "label": item, "thinking_levels": ["low", "med", "high"]})
        if models:
            return models
    discovered = _load_models_from_omp_yml()
    if discovered:
        return discovered
    return [dict(m) for m in DEFAULT_OMP_MODELS]
class OmpResidentWorker:
    """常駐 omp acp 進程（與 agy 同協定）：開機啟動、長連線問答、毫秒級取消。"""

    def __init__(self, model: str) -> None:
        self.model = model
        self.thinking_level: Optional[str] = None
        self.proc: Optional[subprocess.Popen] = None
        self.session_id: Optional[str] = None
        self.lock = threading.RLock()
        self._stdin_lock = threading.Lock()
        self._rpc_id_lock = threading.Lock()
        self._rpc_seq = 0
        self._active_turn_id: Optional[int] = None
        self._cancel_requested = False
        self._is_ready = False
        self.last_tokens = 0
        self.last_usage: dict = {}
        self.last_cost_usd = 0.0
        self.last_active_time = time.time()
        self.cwd = str(settings.BASE_DIR)
        self.session_state_path = settings.DATA_DIR / "omp_acp_session.json"
        self.stderr_file = None
        self.stdout_queue: queue.Queue = queue.Queue()
        self.reader_thread: Optional[threading.Thread] = None

    def _next_rpc_id(self) -> int:
        with self._rpc_id_lock:
            self._rpc_seq += 1
            return self._rpc_seq

    def _send_rpc(self, payload: dict) -> None:
        with self._stdin_lock:
            if self.proc is None or self.proc.stdin is None or self.proc.poll() is not None:
                raise RuntimeError("OMP ACP stdin 不可用")
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def _wait_for_rpc_response(self, req_id: int, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.1, min(1.0, deadline - time.time()))
            try:
                msg = self.stdout_queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if msg is None:
                raise RuntimeError("OMP ACP 輸出意外關閉")
            if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise RuntimeError(f"OMP ACP 回應錯誤 (id={req_id}): {msg['error']}")
                return msg
        raise TimeoutError(f"等待 OMP ACP 回應逾時 id={req_id}")

    def _load_saved_session(self) -> Optional[dict]:
        try:
            if self.session_state_path.exists():
                data = json.loads(self.session_state_path.read_text(encoding="utf-8"))
                if data.get("session_id"):
                    return data
        except Exception as ex:
            print(f"[OmpResident] ⚠️ 讀取 omp_acp_session.json 失敗: {ex}")
        return None

    def _save_session_state(self) -> None:
        if not self.session_id:
            return
        try:
            self.session_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_state_path.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "last_tokens": self.last_tokens,
                        "last_model": self.model,
                        "last_thinking_level": self.thinking_level,
                        "last_usage": self.last_usage,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as ex:
            print(f"[OmpResident] ⚠️ 儲存 omp_acp_session.json 失敗: {ex}")

    def _clear_saved_session(self) -> None:
        try:
            self.session_state_path.unlink(missing_ok=True)
        except Exception:
            pass

    @property
    def is_busy(self) -> bool:
        return self._active_turn_id is not None

    @staticmethod
    def _resolve_omp_bin() -> Optional[str]:
        return shutil.which("omp") or (
            str(Path.home() / ".local" / "bin" / "omp")
            if (Path.home() / ".local" / "bin" / "omp").exists()
            else None
        )

    def start(self, force_new_session: bool = False) -> None:
        with self.lock:
            self._start_unlocked(force_new_session=force_new_session)

    def _start_unlocked(self, force_new_session: bool = False) -> None:
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
            self._agents_injected = False
        omp_bin = self._resolve_omp_bin()
        if not omp_bin:
            print("[OmpResident] ❌ 找不到 omp 執行檔，無法啟動常駐進程")
            return
        print(f"[OmpResident] 🚀 正在啟動 omp acp 常駐進程 (模型: {self.model})...")
        t0 = time.time()
        stderr_log_path = Path(self.cwd) / "data" / "omp_acp.stderr.log"
        stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.stderr_file and not self.stderr_file.closed:
            try:
                self.stderr_file.close()
            except Exception:
                pass
        self.stderr_file = open(stderr_log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [omp_bin, "acp"] if not self.model else [omp_bin, "--model", self.model, "acp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_file,
            text=True,
            bufsize=1,
            cwd=self.cwd,
        )
        self.stdout_queue = queue.Queue()
        self._active_turn_id = None
        self._cancel_requested = False

        def _reader(proc, out_q) -> None:
            try:
                for raw_line in iter(proc.stdout.readline, ""):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if "method" in msg and "id" in msg and "result" not in msg and "error" not in msg:
                        method = msg.get("method", "")
                        req_id = msg.get("id")
                        if method == "session/request_permission":
                            options = (msg.get("params") or {}).get("options") or []
                            option_id = "allow"
                            for opt in options:
                                if isinstance(opt, dict):
                                    oid = str(opt.get("optionId") or "")
                                    kind = str(opt.get("kind") or "")
                                    if "allow" in kind or "allow" in oid:
                                        option_id = oid or "allow"
                                        break
                            result_payload = {"outcome": {"outcome": "selected", "optionId": option_id}}
                        else:
                            result_payload = {}
                        try:
                            self._send_rpc({"jsonrpc": "2.0", "id": req_id, "result": result_payload})
                        except Exception:
                            pass
                        continue
                    out_q.put(msg)
            except Exception:
                pass
            finally:
                out_q.put(None)

        self.reader_thread = threading.Thread(
            target=_reader, args=(self.proc, self.stdout_queue), daemon=True
        )
        self.reader_thread.start()
        try:
            init_id = self._next_rpc_id()
            self._send_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientInfo": {"name": "macmini-assistant-omp", "version": "2.0"},
                    },
                }
            )
            self._wait_for_rpc_response(init_id, timeout=30.0)
            resumed = False
            saved = None if force_new_session else self._load_saved_session()
            if saved and saved.get("session_id"):
                try:
                    resume_id = self._next_rpc_id()
                    self._send_rpc(
                        {
                            "jsonrpc": "2.0",
                            "id": resume_id,
                            "method": "session/resume",
                            "params": {
                                "sessionId": saved["session_id"],
                                "cwd": self.cwd,
                                "mcpServers": [],
                            },
                        }
                    )
                    resume_resp = self._wait_for_rpc_response(resume_id, timeout=20.0)
                    self.session_id = resume_resp.get("result", {}).get("sessionId") or saved["session_id"]
                    self.last_tokens = int(saved.get("last_tokens", 0) or 0)
                    self.last_usage = dict(saved.get("last_usage", {}) or {})
                    resumed = True
                except Exception as resume_err:
                    print(f"[OmpResident] ℹ️ 無法恢復舊 Session，將建立新 Session: {resume_err}")
            if not resumed:
                new_id = self._next_rpc_id()
                self._send_rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": new_id,
                        "method": "session/new",
                        "params": {"cwd": self.cwd, "mcpServers": []},
                    }
                )
                new_resp = self._wait_for_rpc_response(new_id, timeout=45.0)
                self.session_id = new_resp.get("result", {}).get("sessionId")
                self.last_tokens = 0
                self.last_usage = {}
                self.last_cost_usd = 0.0
            if not self.session_id:
                raise RuntimeError("OMP ACP 未能取得有效 sessionId")
            self.thinking_level = None
            self._is_ready = True
            self._set_model_unlocked(self.model, force=True, log_switch=False)
            elapsed = time.time() - t0
            self._save_session_state()
            print(
                f"[OmpResident] ✅ 常駐進程就緒！耗時 {elapsed:.2f}s | Session: {self.session_id[:8]} | 模型: {self.model}"
            )
        except Exception as ex:
            print(f"[OmpResident] ❌ 啟動失敗：{ex}")
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
            self._is_ready = False

    def _run_internal_prompt(self, prompt_text: str, timeout: float = 60.0) -> str:
        """透過常駐 ACP 發送內部指令並收集文字回覆（不寫入對話歷史）。"""
        if not self._is_ready or not self.session_id or self.proc is None or self.proc.poll() is not None:
            return ""
        while not self.stdout_queue.empty():
            try:
                self.stdout_queue.get_nowait()
            except queue.Empty:
                break
        req_id = self._next_rpc_id()
        self._send_rpc(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "session/prompt",
                "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": prompt_text}]},
            }
        )
        collected: List[str] = []
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                msg = self.stdout_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if msg is None:
                break
            if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                break
            if msg.get("method") == "session/update":
                params = msg.get("params", {}) or {}
                if params.get("sessionId") != self.session_id:
                    continue
                update = params.get("update", {}) or {}
                if update.get("sessionUpdate") == "agent_message_chunk":
                    delta = str((update.get("content", {}) or {}).get("text") or "")
                    if delta:
                        collected.append(delta)
        return "".join(collected).strip()

    def _set_model_unlocked(self, target_model: str, log_switch: bool = True, force: bool = False) -> bool:
        if not target_model:
            return False
        if not force and target_model == self.model and self._is_ready:
            return True
        old_model = self.model
        if (
            self.proc is not None
            and self.proc.poll() is None
            and self._is_ready
            and self.session_id
            and not self.is_busy
        ):
            switched = False
            try:
                req_id = self._next_rpc_id()
                self._send_rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "session/set_config_option",
                        "params": {
                            "sessionId": self.session_id,
                            "configId": "model",
                            "value": target_model,
                        },
                    }
                )
                self._wait_for_rpc_response(req_id, timeout=15.0)
                switched = True
            except Exception as cfg_ex:
                print(f"[OmpResident] ℹ️ set_config_option(model) 失敗，改用 /switch: {cfg_ex}")
            if not switched:
                try:
                    switch_out = self._run_internal_prompt(f"/switch {target_model}", timeout=60.0)
                except Exception as ex:
                    print(f"[OmpResident] ⚠️ /switch 指令發送失敗: {ex}")
                    return False
                if "Failed to switch" in switch_out or "No API key" in switch_out:
                    print(f"[OmpResident] ⚠️ 模型切換失敗，沿用 {old_model}: {switch_out[:120]}")
                    return False
            self.model = target_model
            self.thinking_level = None
            self._save_session_state()
            if log_switch:
                print(f"[OmpResident] 🔀 同 Session 模型切換: {old_model} ➔ {target_model}")
            return True
        self.model = target_model
        self.thinking_level = None
        if log_switch:
            print(f"[OmpResident] 🔀 常駐模型切換: {old_model} ➔ {target_model}")
        return self._is_ready

    @staticmethod
    def _normalize_thinking_level(level: Optional[str]) -> Optional[str]:
        if not level:
            return None
        norm = str(level).strip().lower()
        if ":" in norm:
            norm = norm.split(":", 1)[1].strip()
        mapping = {
            "off": "off",
            "none": "off",
            "minimal": "minimal",
            "low": "low",
            "med": "medium",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
            "max": "xhigh",
            "auto": "auto",
        }
        return mapping.get(norm)

    def _set_thinking_level_unlocked(self, level: Optional[str], log_switch: bool = True) -> bool:
        target = self._normalize_thinking_level(level)
        if not target:
            return False
        if target == self.thinking_level and self._is_ready:
            return True
        if (
            self.proc is not None
            and self.proc.poll() is None
            and self._is_ready
            and self.session_id
            and not self.is_busy
        ):
            old_level = self.thinking_level
            try:
                req_id = self._next_rpc_id()
                self._send_rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "session/set_config_option",
                        "params": {
                            "sessionId": self.session_id,
                            "configId": "thinking",
                            "value": target,
                        },
                    }
                )
                self._wait_for_rpc_response(req_id, timeout=15.0)
                self.thinking_level = target
                self._save_session_state()
                if log_switch and old_level != target:
                    print(f"[OmpResident] 🧠 同 Session 思考程度切換: {old_level or 'default'} ➔ {target}")
                return True
            except Exception as ex:
                print(f"[OmpResident] ⚠️ 思考程度切換失敗 ({target}): {ex}")
                return False
        return False

    def stop(self) -> None:
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

    def restart(self, force_new_session: bool = True) -> None:
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
            self._start_unlocked(force_new_session=force_new_session)

    def cancel_current_turn(self) -> bool:
        if not self._is_ready or self.proc is None or self.proc.poll() is not None:
            return False
        if self._active_turn_id is None:
            return False
        try:
            self._cancel_requested = True
            self._send_rpc(
                {
                    "jsonrpc": "2.0",
                    "method": "session/cancel",
                    "params": {"sessionId": self.session_id},
                }
            )
            print(f"[OmpResident] 🛑 已發送 session/cancel (turn_id={self._active_turn_id})")
            return True
        except Exception as ex:
            print(f"[OmpResident] ⚠️ 發送 session/cancel 失敗: {ex}")
            return False

    def compact_session(self, timeout: float = 45.0) -> dict:
        """
        執行 ACP 原生 `/compact` 無損上下文壓縮：
        - 在不中斷當前 Session ID 的前提下，由 OMP 底層將歷史長對話精煉壓縮
        - 釋放 Context Window Token 空間並更新最新 Token 用量
        """
        if self.is_busy:
            return {"success": False, "error": "助理目前正在處理對話中，請稍候再執行壓縮。"}

        with self.lock:
            if not self._is_ready or not self.session_id or self.proc is None or self.proc.poll() is not None:
                self._start_unlocked(force_new_session=False)
                if not self._is_ready or not self.session_id:
                    return {"success": False, "error": "OMP ACP 大腦尚未就緒"}

            before_tokens = int(self.last_tokens or 0)
            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break

            req_id = self._next_rpc_id()
            self._active_turn_id = req_id
            t0 = time.perf_counter()
            try:
                self._send_rpc({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": self.session_id,
                        "prompt": [{"type": "text", "text": "/compact"}],
                    },
                })
                deadline = time.time() + timeout
                resp = None
                latest_used_tokens = 0
                compact_summary_text = ""
                while time.time() < deadline:
                    remaining = max(0.1, min(1.0, deadline - time.time()))
                    try:
                        msg = self.stdout_queue.get(timeout=remaining)
                    except queue.Empty:
                        if self.proc is None or self.proc.poll() is not None:
                            raise RuntimeError("OMP ACP 進程意外中斷")
                        continue
                    if msg is None:
                        raise RuntimeError("OMP ACP 輸出意外關閉")
                    if msg.get("method") == "session/update":
                        params = msg.get("params", {}) or {}
                        if params.get("sessionId") == self.session_id:
                            update = params.get("update", {}) or {}
                            kind = str(update.get("sessionUpdate") or "")
                            if kind == "usage_update":
                                try:
                                    used = int(update.get("used") or 0)
                                    if used > 0:
                                        latest_used_tokens = used
                                except (TypeError, ValueError):
                                    pass
                            elif kind == "agent_message_chunk":
                                content = update.get("content", {}) or {}
                                delta = str(content.get("text") or "")
                                if delta:
                                    compact_summary_text += delta
                        continue
                    if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                        if "error" in msg:
                            raise RuntimeError(f"OMP ACP 回應錯誤 (id={req_id}): {msg['error']}")
                        resp = msg
                        break
                if resp is None:
                    raise TimeoutError(f"等待 OMP ACP 回應逾時 id={req_id}")

                elapsed = round(time.perf_counter() - t0, 2)
                res = (resp.get("result") or {})
                raw_usage = res.get("usage") or {}

                # omp acp 執行 /compact 時不會送出 usage_update 與 result.usage，
                # 而是在 agent_message_chunk 回傳 "Compaction complete. Tokens: <before> -> <after> (saved <saved>)."
                parsed_before_tokens = 0
                parsed_after_tokens = 0
                if compact_summary_text:
                    m_compact = re.search(
                        r"Tokens:\s*([\d,]+)\s*(?:->|➔|→)\s*([\d,]+)",
                        compact_summary_text,
                    )
                    if m_compact:
                        try:
                            parsed_before_tokens = int(m_compact.group(1).replace(",", ""))
                            parsed_after_tokens = int(m_compact.group(2).replace(",", ""))
                        except ValueError:
                            parsed_before_tokens = 0
                            parsed_after_tokens = 0

                if parsed_before_tokens > 0:
                    before_tokens = parsed_before_tokens

                if raw_usage:
                    in_tok = int(raw_usage.get("inputTokens", 0) or 0)
                    out_tok = int(raw_usage.get("outputTokens", 0) or 0)
                    thought_tok = int(raw_usage.get("thoughtTokens", 0) or 0)
                    cache_tok = int(raw_usage.get("cachedReadTokens", 0) or 0)
                    total_tok = int(raw_usage.get("totalTokens", 0) or 0) or (
                        in_tok + out_tok + thought_tok + cache_tok
                    )
                    mapped_usage = {
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "thought_tokens": thought_tok,
                        "cached_read_tokens": cache_tok,
                        "total_tokens": total_tok,
                    }
                    self.last_usage = mapped_usage
                    new_tokens = (
                        parsed_after_tokens
                        if parsed_after_tokens > 0
                        else (latest_used_tokens if latest_used_tokens > 0 else (total_tok or in_tok))
                    )
                    if new_tokens > 0:
                        self.last_tokens = new_tokens
                    self.last_usage["context_tokens"] = self.last_tokens
                else:
                    new_tokens = parsed_after_tokens if parsed_after_tokens > 0 else latest_used_tokens
                    if new_tokens > 0:
                        self.last_tokens = new_tokens
                        self.last_usage = {
                            "total_tokens": new_tokens,
                            "context_tokens": new_tokens,
                        }

                self._save_session_state()
                after_tokens = int(self.last_tokens or 0)
                print(
                    f"[OmpResident] 🧹 ACP 原生 /compact 壓縮完成！耗時 {elapsed}s | Tokens: {before_tokens:,} ➔ {after_tokens:,}"
                )
                result_payload = {
                    "success": True,
                    "session_id": self.session_id,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "elapsed_sec": elapsed,
                }
                if compact_summary_text.strip():
                    result_payload["message"] = compact_summary_text.strip()
                return result_payload
            except Exception as e:
                print(f"[OmpResident] ⚠️ 執行 /compact 失敗: {e}")
                return {"success": False, "error": str(e)}
            finally:
                self._active_turn_id = None
                self.last_active_time = time.time()

    @staticmethod
    def _build_prompt_blocks(query: str, media: Optional[List[dict]] = None) -> List[dict]:
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
                        print(f"[OmpResident] ⚠️ 讀取圖片轉 Base64 失敗 ({uri}): {ex}")
                if b64_data:
                    blocks.append({"type": "image", "mimeType": mime_type, "data": b64_data})
        blocks.append({"type": "text", "text": query or "你好"})
        return blocks

    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Generator[Tuple[str, str], None, dict]:
        with self.lock:
            if not self._is_ready or self.proc is None or self.proc.poll() is not None or not self.session_id:
                self._start_unlocked(force_new_session=False)
            if not self._is_ready or self.proc is None or not self.session_id:
                yield ("text", "OMP 常駐進程未就緒，請稍後重試。")
                return {"status": "ERROR", "error": "process_not_ready"}
            target_model = (model_override or "").strip() or self.model
            if target_model != self.model:
                self._set_model_unlocked(target_model, log_switch=True)
            else:
                self.model = target_model
            if level:
                self._set_thinking_level_unlocked(level, log_switch=True)
            while not self.stdout_queue.empty():
                try:
                    self.stdout_queue.get_nowait()
                except queue.Empty:
                    break
            req_id = self._next_rpc_id()
            self._active_turn_id = req_id
            self._cancel_requested = False
            # omp acp 啟動時已自動由 cwd 載入專案 AGENTS.md 至系統 <repo-rules>，不再重複注入 [USER] 訊息
            prompt_text = query
            self._send_rpc(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "session/prompt",
                    "params": {"sessionId": self.session_id, "prompt": self._build_prompt_blocks(prompt_text, media)},
                }
            )
        start_t = time.perf_counter()
        last_activity_t = start_t
        last_heartbeat_t = start_t
        first_token_t: Optional[float] = None
        reply_parts: List[str] = []
        thought_parts: List[str] = []
        turn_usage: dict = {}
        turn_cost_usd = 0.0
        was_cancelled = False
        timed_out = False
        timeout_reason = ""
        timed_out_by_idle = False
        tool_calls_count = 0
        tool_labels_by_id: dict[str, str] = {}
        idle_limit = float(OMP_STREAM_IDLE_TIMEOUT_SEC)
        max_total_limit = float(OMP_STREAM_MAX_TOTAL_SEC)
        try:
            while True:
                if self._cancel_requested:
                    was_cancelled = True
                    break
                try:
                    msg = self.stdout_queue.get(timeout=1.0)
                except queue.Empty:
                    if self.proc is None or self.proc.poll() is not None:
                        break
                    now_hb = time.perf_counter()
                    idle_elapsed = now_hb - last_activity_t
                    total_elapsed = now_hb - start_t
                    if idle_elapsed >= idle_limit or total_elapsed >= max_total_limit:
                        timed_out = True
                        timed_out_by_idle = idle_elapsed >= idle_limit
                        timeout_reason = (
                            f"無活動達 {idle_elapsed:.0f}s"
                            if timed_out_by_idle
                            else f"總執行時長達 {total_elapsed:.0f}s"
                        )
                        print(
                            f"[OmpResident] ⚠️ 串流逾時 ({timeout_reason}, idle_limit={idle_limit:.0f}s, max_total={max_total_limit:.0f}s)，主動中止本輪..."
                        )
                        try:
                            self._send_rpc(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "session/cancel",
                                    "params": {"sessionId": self.session_id},
                                }
                            )
                        except Exception:
                            pass
                        break
                    if now_hb - last_heartbeat_t >= 15.0:
                        last_heartbeat_t = now_hb
                        yield ("heartbeat", "")
                    continue
                if msg is None:
                    break
                if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                    last_activity_t = time.perf_counter()
                    last_heartbeat_t = last_activity_t
                    if "error" in msg:
                        raise RuntimeError(f"OMP ACP prompt 錯誤: {msg['error']}")
                    result = msg.get("result", {}) or {}
                    usage = result.get("usage") or {}
                    if usage:
                        in_tok = int(usage.get("inputTokens", 0) or 0)
                        out_tok = int(usage.get("outputTokens", 0) or 0)
                        thought_tok = int(usage.get("thoughtTokens", 0) or 0)
                        cache_tok = int(usage.get("cachedReadTokens", 0) or 0)
                        total_tok = int(usage.get("totalTokens", 0) or 0) or (
                            in_tok + out_tok + thought_tok + cache_tok
                        )
                        turn_usage = {
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "thought_tokens": thought_tok,
                            "cached_read_tokens": cache_tok,
                            "total_tokens": total_tok,
                        }
                    if result.get("stopReason") == "cancelled":
                        was_cancelled = True
                    break
                if msg.get("method") == "session/update":
                    params = msg.get("params", {}) or {}
                    if params.get("sessionId") != self.session_id:
                        continue
                    last_activity_t = time.perf_counter()
                    last_heartbeat_t = last_activity_t
                    update = params.get("update", {}) or {}
                    kind = str(update.get("sessionUpdate") or "")
                    if kind == "agent_message_chunk":
                        content = update.get("content", {}) or {}
                        delta = str(content.get("text") or "")
                        if delta:
                            if first_token_t is None:
                                first_token_t = time.perf_counter()
                            reply_parts.append(delta)
                            yield ("text", delta)
                    elif kind in ("agent_thought_chunk", "thought_chunk", "reasoning_chunk"):
                        content = update.get("content", {}) or {}
                        delta = str(content.get("text") or "")
                        if delta:
                            thought_parts.append(delta)
                            yield ("thought", delta)
                    elif kind == "usage_update":
                        try:
                            used = int(update.get("used") or 0)
                            if used > 0:
                                self.last_tokens = used
                        except (TypeError, ValueError):
                            pass
                        cost = update.get("cost") or {}
                        try:
                            if cost.get("amount") is not None:
                                turn_cost_usd = round(float(cost["amount"]), 6)
                        except (TypeError, ValueError):
                            pass
                    elif kind == "tool_call":
                        tool_calls_count += 1
                        call_id = str(update.get("toolCallId") or update.get("id") or "").strip()
                        label = str(
                            update.get("title")
                            or update.get("toolName")
                            or update.get("name")
                            or "工具"
                        ).strip()
                        if call_id:
                            tool_labels_by_id[call_id] = label
                        status_val = str(update.get("status") or "").lower()
                        if status_val in ("completed", "failed", "cancelled"):
                            yield ("tool_done", f"完成工具：{label[:40]}")
                        else:
                            yield ("status", f"執行工具：{label[:40]}")
                    elif kind == "tool_call_update":
                        call_id = str(update.get("toolCallId") or update.get("id") or "").strip()
                        raw_label = str(
                            update.get("title")
                            or update.get("toolName")
                            or update.get("name")
                            or ""
                        ).strip()
                        if call_id and raw_label:
                            tool_labels_by_id[call_id] = raw_label
                        label = raw_label or (tool_labels_by_id.get(call_id) if call_id else "") or "工具"
                        status_val = str(update.get("status") or "").lower()
                        if status_val in ("completed", "failed", "cancelled"):
                            yield ("tool_done", f"完成工具：{label[:40]}")
                            if call_id:
                                tool_labels_by_id.pop(call_id, None)
                    elif "tool" in kind:
                        raw_label = str(
                            update.get("title")
                            or update.get("toolName")
                            or update.get("name")
                            or ""
                        ).strip()
                        status_val = str(update.get("status") or "").lower()
                        if status_val in ("completed", "failed", "cancelled"):
                            yield ("tool_done", f"完成工具：{(raw_label or '工具')[:40]}")
                        elif raw_label:
                            tool_calls_count += 1
                            yield ("status", f"執行工具：{raw_label[:40]}")
        finally:
            with self.lock:
                self._active_turn_id = None
                self.last_active_time = time.time()
        total_latency = time.perf_counter() - start_t
        ttft = (first_token_t - start_t) if first_token_t else total_latency
        clean_reply = "".join(reply_parts).strip()
        full_thought = "".join(thought_parts).strip()
        if turn_usage and turn_usage.get("total_tokens"):
            self.last_tokens = int(turn_usage["total_tokens"])
        self.last_usage = dict(turn_usage) if turn_usage else {"total_tokens": self.last_tokens}
        self.last_usage["context_tokens"] = self.last_tokens
        if turn_cost_usd <= 0 and turn_usage:
            turn_cost_usd = _estimate_omp_turn_cost(self.model, turn_usage)
        if turn_cost_usd > 0 or (turn_usage and turn_usage.get("total_tokens")):
            self.last_cost_usd = turn_cost_usd
        self._save_session_state()
        level_norm = _format_metadata_thinking_level(level, self.thinking_level)
        metadata = {
            "model": self.model,
            "thinking_level": level_norm,
            "ttft_sec": round(ttft, 2),
            "total_sec": round(total_latency, 2),
            "usage": {
                "input_tokens": int(self.last_usage.get("input_tokens", 0) or 0),
                "output_tokens": int(self.last_usage.get("output_tokens", 0) or 0),
                "thought_tokens": int(self.last_usage.get("thought_tokens", 0) or 0),
                "cached_read_tokens": int(self.last_usage.get("cached_read_tokens", 0) or 0),
                "total_tokens": int(self.last_usage.get("total_tokens", 0) or 0),
            },
            "estimated_cost_usd": turn_cost_usd,
            "engine": "omp-resident",
        }
        if timed_out:
            timeout_note = f"助理處理逾時（{timeout_reason}），請再試一次。"
            prefix_note = f"\n\n{timeout_note}" if reply_parts else timeout_note
            reply_parts.append(prefix_note)
            yield ("text", prefix_note)
            if timed_out_by_idle:
                self.restart(force_new_session=False)
        clean_reply = _finalize_reply_and_metadata("".join(reply_parts).strip(), metadata)
        quick_replies = _quick_replies_for(
            clean_reply,
            user_query=query,
            was_cancelled=was_cancelled or timed_out,
        )
        if timed_out:
            return {
                "status": "TIMEOUT",
                "error": "stream_timeout",
                "clean_reply": clean_reply,
                "thought": full_thought,
                "actions": [f"engine:omp-resident ({self.model})"],
                "total_latency": total_latency,
                "time_to_first_token": ttft,
                "thinking_level": level_norm,
                "model": self.model,
                "metadata": metadata,
                "quick_replies": quick_replies,
            }
        if was_cancelled and not clean_reply:
            return {
                "status": "CANCELLED",
                "cancelled": True,
                "clean_reply": clean_reply,
                "thought": full_thought,
                "total_latency": total_latency,
                "time_to_first_token": ttft,
                "thinking_level": level_norm,
                "model": self.model,
                "metadata": metadata,
                "quick_replies": quick_replies,
            }
        token_limit = settings.CONTEXT_WINDOW_LIMIT * settings.CONTEXT_RESTART_THRESHOLD
        if self.last_tokens >= token_limit:
            print(f"[OmpResident] 🔄 Context 已達門檻 ({self.last_tokens} >= {int(token_limit)})，背景重啟全新 Session...")
            threading.Thread(target=self.restart, kwargs={"force_new_session": True}, daemon=True).start()
        return {
            "status": "OK",
            "clean_reply": clean_reply,
            "thought": full_thought,
            "actions": [f"engine:omp-resident ({self.model})"],
            "total_latency": total_latency,
            "time_to_first_token": ttft,
            "thinking_level": level_norm,
            "model": self.model,
            "metadata": metadata,
            "quick_replies": quick_replies,
        }




class OmpBrainEngine(BaseBrainEngine):
    """常駐 omp acp 進程引擎（同 agy 協定），異常時自動退回按需子行程模式。"""

    engine_id = "omp"
    display_name = "Oh-My-Pi (omp)"
    supports_quota = True

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_proc: Optional[subprocess.Popen] = None
        self._cancel_requested = False
        self._session_dir = settings.DATA_DIR / "omp_sessions"
        self._session_state_path = settings.DATA_DIR / "omp_session.json"
        self._session_id: Optional[str] = self._load_session_id()
        self._last_tokens: int = 0
        self._last_usage: dict = {}
        self._last_model: str = _load_curated_omp_models()[0]["id"]
        self._worker = OmpResidentWorker(self._last_model)

    def _load_session_id(self) -> Optional[str]:
        try:
            if self._session_state_path.exists():
                data = json.loads(self._session_state_path.read_text(encoding="utf-8"))
                sid = str(data.get("session_id") or "").strip()
                return sid or None
        except Exception:
            pass
        return None

    def _save_session_id(self, session_id: Optional[str]) -> None:
        self._session_id = session_id
        try:
            self._session_state_path.parent.mkdir(parents=True, exist_ok=True)
            if session_id:
                self._session_state_path.write_text(
                    json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            elif self._session_state_path.exists():
                self._session_state_path.unlink()
        except Exception:
            pass

    def _resolve_omp_bin(self) -> Optional[str]:
        return shutil.which("omp") or (
            str(Path.home() / ".local" / "bin" / "omp")
            if (Path.home() / ".local" / "bin" / "omp").exists()
            else None
        )

    def is_busy(self) -> bool:
        try:
            if self._worker.is_busy:
                return True
        except Exception:
            pass
        with self._lock:
            return self._active_proc is not None and self._active_proc.poll() is None

    def start(self, force_new_session: bool = False) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        if force_new_session:
            self._save_session_id(None)
        try:
            self._worker.start(force_new_session=force_new_session)
        except Exception as ex:
            print(f"[OmpBrainEngine] ⚠️ 常駐進程啟動失敗，退回按需模式: {ex}")

    def stop(self) -> None:
        self.cancel_active_turns()
        try:
            self._worker.stop()
        except Exception:
            pass

    def restart(self, force_new_session: bool = True) -> None:
        self.cancel_active_turns()
        if force_new_session:
            self._save_session_id(None)
        try:
            self._worker.restart(force_new_session=force_new_session)
        except Exception as ex:
            print(f"[OmpBrainEngine] ⚠️ 常駐進程重啟失敗: {ex}")

    def cancel_active_turns(self, level: Optional[str] = None) -> dict:
        cancelled = False
        try:
            if self._worker.cancel_current_turn():
                cancelled = True
        except Exception:
            pass
        with self._lock:
            proc = self._active_proc
            self._cancel_requested = True
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    cancelled = True
                except Exception as ex:
                    return {"cancelled": cancelled, "engine": self.engine_id, "error": str(ex)}
            return {"cancelled": cancelled, "engine": self.engine_id}

    def compact_session(self, timeout: float = 45.0) -> dict:
        res = self._worker.compact_session(timeout=timeout)
        res["engine"] = self.engine_id
        if res.get("success"):
            after_tokens = int(res.get("after_tokens", 0) or 0)
            if after_tokens > 0:
                self._last_tokens = after_tokens
            elif getattr(self._worker, "last_tokens", 0):
                self._last_tokens = int(self._worker.last_tokens)
            self._last_usage = dict(getattr(self._worker, "last_usage", {}) or {})
        return res

    def get_status(self) -> dict:
        omp_bin = self._resolve_omp_bin()
        limit = settings.CONTEXT_WINDOW_LIMIT
        worker_tokens = int(getattr(self._worker, "last_tokens", 0) or 0)
        current = int(self._last_tokens or worker_tokens or 0)
        ratio = current / limit if limit > 0 else 0.0
        resident_ready = bool(getattr(self._worker, "_is_ready", False))
        ready = bool(omp_bin)
        busy = self.is_busy()
        return {
            "engine": self.engine_id,
            "ready": ready,
            "is_ready": ready,
            "resident_ready": resident_ready,
            "busy": busy,
            "is_busy": busy,
            "session_id": self._worker.session_id or self._session_id,
            "model": self._worker.model,
            "current_tokens": current,
            "limit": limit,
            "ratio": ratio,
            "percent": round(ratio * 100, 1),
            "usage": dict(self._last_usage),
            "supports_quota": self.supports_quota,
        }

    def get_available_models(self) -> List[dict]:
        return _load_curated_omp_models()

    @staticmethod
    def _map_thinking_level(level: Optional[str]) -> Optional[str]:
        if not level:
            return None
        norm = level.lower().strip()
        if ":" in norm:
            norm = norm.split(":", 1)[1].strip()
        mapping = {"low": "low", "med": "medium", "medium": "medium", "high": "high"}
        return mapping.get(norm)

    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Generator[Tuple[str, str], None, dict]:
        try:
            chosen = (model_override or "").strip()
            if chosen:
                self._last_model = chosen
            gen = self._worker.send_query_stream(
                query, media=media, level=level, model_override=model_override
            )
            result = yield from gen
            self._last_tokens = int(
                ((result or {}).get("metadata") or {}).get("usage", {}).get("total_tokens")
                or self._worker.last_tokens
                or self._last_tokens
            )
            self._last_usage = dict(self._worker.last_usage or self._last_usage)
            self._last_model = self._worker.model
            return result
        except Exception as ex:
            print(f"[OmpBrainEngine] ⚠️ 常駐問答失敗，退回按需模式: {ex}")
        result = yield from self._send_query_stream_subprocess(
            query, media=media, level=level, model_override=model_override
        )
        return result

    def _send_query_stream_subprocess(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Generator[Tuple[str, str], None, dict]:
        omp_bin = self._resolve_omp_bin()
        if not omp_bin:
            msg = "找不到 omp 執行檔，請確認已安裝並加入 PATH。"
            yield ("text", msg)
            chosen_fallback = (model_override or "").strip() or self._last_model
            return {
                "status": "ERROR",
                "clean_reply": msg,
                "error": "omp_not_found",
                "thinking_level": level or "med",
                "model": chosen_fallback,
                "metadata": {
                    "model": chosen_fallback,
                    "thinking_level": level or "med",
                    "ttft_sec": 0.0,
                    "total_sec": 0.0,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "thought_tokens": 0,
                        "cached_read_tokens": 0,
                        "total_tokens": 0,
                    },
                    "estimated_cost_usd": 0.0,
                    "engine": "omp",
                },
                "quick_replies": [],
            }

        self._session_dir.mkdir(parents=True, exist_ok=True)
        chosen_model = (model_override or "").strip() or self._last_model
        self._last_model = chosen_model

        cmd = [
            omp_bin,
            "-p",
            "--mode=json",
            f"--session-dir={self._session_dir}",
            f"--cwd={settings.BASE_DIR}",
        ]
        agents_path = settings.BASE_DIR / "AGENTS.md"
        if agents_path.exists():
            cmd.append(f"--append-system-prompt={agents_path}")
        if chosen_model:
            cmd.append(f"--model={chosen_model}")
        thinking = self._map_thinking_level(level)
        if thinking:
            cmd.append(f"--thinking={thinking}")
        if self._session_id:
            cmd.append(f"--resume={self._session_id}")

        effective_prompt = query
        if media:
            paths = [str(m.get("uri")) for m in media if m.get("uri")]
            if paths:
                effective_prompt = f"{query}\n\n（附加檔案路徑：\n" + "\n".join(paths) + "）"
        cmd.append(effective_prompt)

        start_t = time.perf_counter()
        first_token_t: Optional[float] = None
        reply_parts: List[str] = []
        thought_parts: List[str] = []
        turn_usage: dict = {}
        turn_cost_usd: float = 0.0
        cancelled = False

        with self._lock:
            self._cancel_requested = False
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(settings.BASE_DIR),
            )
            self._active_proc = proc

        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                if self._cancel_requested:
                    cancelled = True
                    break
                line = raw_line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ev_type = ev.get("type")
                if ev_type == "session" and ev.get("id"):
                    self._save_session_id(str(ev["id"]))
                elif ev_type == "message_update":
                    ame = ev.get("assistantMessageEvent") or {}
                    sub_type = ame.get("type")
                    delta = str(ame.get("delta") or "")
                    if sub_type == "thinking_delta" and delta:
                        thought_parts.append(delta)
                        yield ("thought", delta)
                    elif sub_type == "text_delta" and delta:
                        if first_token_t is None:
                            first_token_t = time.perf_counter()
                        reply_parts.append(delta)
                        yield ("text", delta)
                elif ev_type == "tool_execution_start":
                    tool_name = str(ev.get("toolName") or ev.get("name") or "工具")
                    yield ("status", f"執行工具：{tool_name}")
                elif ev_type == "tool_execution_end":
                    tool_name = str(ev.get("toolName") or ev.get("name") or "工具")
                    yield ("tool_done", f"完成工具：{tool_name}")
                elif ev_type in ("turn_end", "agent_end"):
                    target_msg = ev.get("message") if ev_type == "turn_end" else None
                    if target_msg is None:
                        msgs = ev.get("messages") or []
                        if isinstance(msgs, list) and msgs:
                            target_msg = msgs[-1]
                    if isinstance(target_msg, dict):
                        usage = target_msg.get("usage") or {}
                        if isinstance(usage, dict) and usage:
                            in_tok = int(usage.get("input") or 0)
                            out_tok = int(usage.get("output") or 0)
                            thought_tok = int(usage.get("reasoningTokens") or 0)
                            cache_tok = int(usage.get("cacheRead") or 0)
                            total_tok = int(usage.get("totalTokens") or (in_tok + out_tok + thought_tok + cache_tok))
                            turn_usage = {
                                "input_tokens": in_tok,
                                "output_tokens": out_tok,
                                "thought_tokens": thought_tok,
                                "cached_read_tokens": cache_tok,
                                "total_tokens": total_tok,
                            }
                            cost_obj = usage.get("cost") or {}
                            if isinstance(cost_obj, dict) and cost_obj.get("total") is not None:
                                try:
                                    turn_cost_usd = round(float(cost_obj["total"]), 6)
                                except (TypeError, ValueError):
                                    pass
                            if total_tok > 0:
                                self._last_tokens = total_tok
                            self._last_usage = dict(turn_usage)
                            self._last_usage["context_tokens"] = self._last_tokens

            proc.wait(timeout=10)
        finally:
            with self._lock:
                if self._active_proc is proc:
                    self._active_proc = None
            if proc.poll() is None:
                proc.terminate()

        total_latency = time.perf_counter() - start_t
        ttft = (first_token_t - start_t) if first_token_t else total_latency
        clean_reply = "".join(reply_parts).strip()
        level_norm = _format_metadata_thinking_level(level)
        if turn_cost_usd <= 0 and turn_usage:
            turn_cost_usd = _estimate_omp_turn_cost(chosen_model, turn_usage)
        metadata = {
            "model": chosen_model,
            "thinking_level": level_norm,
            "ttft_sec": round(ttft, 2),
            "total_sec": round(total_latency, 2),
            "usage": {
                "input_tokens": int(turn_usage.get("input_tokens") or 0),
                "output_tokens": int(turn_usage.get("output_tokens") or 0),
                "thought_tokens": int(turn_usage.get("thought_tokens") or 0),
                "cached_read_tokens": int(turn_usage.get("cached_read_tokens") or 0),
                "total_tokens": int(turn_usage.get("total_tokens") or self._last_tokens or 0),
            },
            "estimated_cost_usd": turn_cost_usd,
            "engine": "omp",
        }

        if cancelled or self._cancel_requested:
            return {
                "status": "CANCELLED",
                "cancelled": True,
                "clean_reply": clean_reply,
                "thought": "".join(thought_parts),
                "total_latency": total_latency,
                "time_to_first_token": ttft,
                "thinking_level": level_norm,
                "model": chosen_model,
                "metadata": metadata,
                "quick_replies": [],
            }

        if proc.returncode not in (0, None) and not clean_reply:
            err_out = ""
            try:
                err_out = (proc.stderr.read() if proc.stderr else "").strip()
            except Exception:
                pass
            err_msg = f"OMP 執行異常 (exit={proc.returncode}): {err_out[:200]}"
            yield ("text", err_msg)
            clean_reply = err_msg

        clean_reply = _finalize_reply_and_metadata(clean_reply, metadata)
        return {
            "status": "OK",
            "clean_reply": clean_reply,
            "thought": "".join(thought_parts),
            "actions": [f"engine:omp ({chosen_model})"],
            "total_latency": total_latency,
            "time_to_first_token": ttft,
            "thinking_level": level_norm,
            "model": chosen_model,
            "metadata": metadata,
            "quick_replies": _quick_replies_for(clean_reply, user_query=query, was_cancelled=False),
        }


omp_singleton = OmpBrainEngine()
