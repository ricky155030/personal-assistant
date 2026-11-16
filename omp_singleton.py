"""OMP (Oh-My-Pi) Brain Engine implementation conforming to BaseBrainEngine."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from brain_engine import BaseBrainEngine
from config import settings


# 精選給 App / Telegram 顯示的 OMP 模型白名單（可隨時於此增減，或透過 OMP_CURATED_MODELS 環境變數覆寫）
DEFAULT_OMP_MODELS: List[dict] = [
    {"id": "muse-spark-1.3-contributor", "label": "Muse Spark 1.3"},
    {"id": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
]


def _load_curated_omp_models() -> List[dict]:
    raw = os.getenv("OMP_CURATED_MODELS", "").strip()
    if not raw:
        return list(DEFAULT_OMP_MODELS)
    models: List[dict] = []
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        if "|" in item:
            mid, label = item.split("|", 1)
            models.append({"id": mid.strip(), "label": label.strip() or mid.strip()})
        else:
            models.append({"id": item, "label": item})
    return models or list(DEFAULT_OMP_MODELS)


class OmpBrainEngine(BaseBrainEngine):
    """Subprocess-based OMP engine with session resume, JSONL streaming, and instant cancellation."""

    engine_id = "omp"
    display_name = "Oh-My-Pi (omp)"
    supports_quota = False

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_proc: Optional[subprocess.Popen] = None
        self._cancel_requested = False
        self._session_dir = settings.DATA_DIR / "omp_sessions"
        self._session_state_path = settings.DATA_DIR / "omp_session.json"
        self._session_id: Optional[str] = self._load_session_id()
        self._last_tokens: int = 0
        self._last_model: str = _load_curated_omp_models()[0]["id"]

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
        with self._lock:
            return self._active_proc is not None and self._active_proc.poll() is None

    def start(self, force_new_session: bool = False) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        if force_new_session:
            self._save_session_id(None)

    def stop(self) -> None:
        self.cancel_active_turns()

    def restart(self, force_new_session: bool = True) -> None:
        self.cancel_active_turns()
        if force_new_session:
            self._save_session_id(None)

    def cancel_active_turns(self, level: Optional[str] = None) -> dict:
        with self._lock:
            proc = self._active_proc
            self._cancel_requested = True
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    return {"cancelled": True, "engine": self.engine_id, "pid": proc.pid}
                except Exception as ex:
                    return {"cancelled": False, "engine": self.engine_id, "error": str(ex)}
            return {"cancelled": False, "engine": self.engine_id}

    def compact_session(self, timeout: float = 45.0) -> dict:
        return {
            "success": True,
            "engine": self.engine_id,
            "note": "OMP 自動管理對話上下文，無需手動壓縮。",
        }

    def get_status(self) -> dict:
        omp_bin = self._resolve_omp_bin()
        return {
            "engine": self.engine_id,
            "ready": bool(omp_bin),
            "busy": self.is_busy(),
            "session_id": self._session_id,
            "model": self._last_model,
            "context_tokens": self._last_tokens,
            "supports_quota": False,
        }

    def get_available_models(self) -> List[dict]:
        return _load_curated_omp_models()

    @staticmethod
    def _map_thinking_level(level: Optional[str]) -> Optional[str]:
        if not level:
            return None
        mapping = {"low": "low", "med": "medium", "medium": "medium", "high": "high"}
        return mapping.get(level.lower().strip())

    def send_query_stream(
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
            return {"status": "ERROR", "clean_reply": msg, "error": "omp_not_found"}

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
                elif ev_type == "agent_end":
                    msgs = ev.get("messages") or []
                    if isinstance(msgs, list) and msgs:
                        last_msg = msgs[-1]
                        usage = last_msg.get("usage") or {}
                        total_tok = usage.get("totalTokens")
                        if isinstance(total_tok, int) and total_tok > 0:
                            self._last_tokens = total_tok

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

        if cancelled or self._cancel_requested:
            return {
                "status": "CANCELLED",
                "cancelled": True,
                "clean_reply": clean_reply,
                "thought": "".join(thought_parts),
                "total_latency": total_latency,
                "time_to_first_token": ttft,
                "thinking_level": level or "med",
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

        return {
            "status": "OK",
            "clean_reply": clean_reply,
            "thought": "".join(thought_parts),
            "actions": [f"engine:omp ({chosen_model})"],
            "total_latency": total_latency,
            "time_to_first_token": ttft,
            "thinking_level": level or "med",
        }


omp_singleton = OmpBrainEngine()
