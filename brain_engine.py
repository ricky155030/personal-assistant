"""Abstract Brain Engine interface and unified BrainRouter for multi-backend AI switching."""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

from config import settings
from memory_manager import memory_manager
from thinking_router import classify_thinking_level, clean_query_prefix
from widget_html_utils import extract_rich_blocks


def clean_query_for_history(raw_query: str) -> str:
    """Strip system prefixes and cross-brain handoff blocks before writing history.jsonl."""
    q = (raw_query or "").strip()
    handoff_end = "[交接結束，以下為主人本次新訊息]"
    if handoff_end in q:
        q = q.split(handoff_end, 1)[1].strip()
    markers = (
        "[跨大腦記憶交接提要",
        "[來源管道:",
        "【語音輸出嚴格規範】",
        "【回覆長度約束",
    )
    if any(m in q for m in markers):
        parts = q.split("\n\n")
        while len(parts) > 1 and any(m in parts[0] for m in markers):
            parts.pop(0)
        q = "\n\n".join(parts).strip()
    return q


class BaseBrainEngine(ABC):
    """Unified interface for resident or subprocess AI brain engines (agy, omp, claude, etc.)."""

    engine_id: str = "base"
    display_name: str = "Base Engine"
    supports_quota: bool = False
    handles_own_postprocess: bool = False

    @property
    def primary_worker(self):
        return self

    def is_busy(self) -> bool:
        return False

    @abstractmethod
    def start(self, force_new_session: bool = False) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def restart(self, force_new_session: bool = True) -> None:
        ...

    @abstractmethod
    def cancel_active_turns(self, level: Optional[str] = None) -> dict:
        ...

    @abstractmethod
    def compact_session(self, timeout: float = 45.0) -> dict:
        ...

    @abstractmethod
    def get_status(self) -> dict:
        ...

    def trigger_restart_with_notification(self, reason: str = "手動觸發") -> None:
        self.restart(force_new_session=True)

    @abstractmethod
    def get_available_models(self) -> List[dict]:
        """Return curated models for this engine: [{'id': str, 'label': str}]."""
        ...

    @abstractmethod
    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Generator[Tuple[str, str], None, dict]:
        ...

    def send_query(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, List[str], float, float]:
        gen = self.send_query_stream(
            query,
            media=media,
            level=level,
            model_override=model_override,
        )
        reply_parts: List[str] = []
        turn_result: dict = {}
        try:
            while True:
                ev_type, ev_data = next(gen)
                if ev_type == "text":
                    reply_parts.append(str(ev_data))
        except StopIteration as stop_ex:
            turn_result = stop_ex.value or {}
        clean_reply = (turn_result.get("clean_reply") or "".join(reply_parts)).strip()
        actions = turn_result.get("actions") or [f"engine:{self.engine_id}"]
        ttft = float(turn_result.get("time_to_first_token") or 0.0)
        total = float(turn_result.get("total_latency") or 0.0)
        return clean_reply, actions, ttft, total


class AgyEngineAdapter(BaseBrainEngine):
    """Wraps existing AgyBrainPool under the BaseBrainEngine contract without altering its internals."""

    engine_id = "agy"
    display_name = "Antigravity (agy)"
    supports_quota = True
    handles_own_postprocess = True

    def __init__(self, pool) -> None:
        self._pool = pool

    @property
    def primary_worker(self):
        return self._pool.primary_worker

    def start(self, force_new_session: bool = False) -> None:
        self._pool.start(force_new_session=force_new_session)

    def stop(self) -> None:
        self._pool.stop()

    def restart(self, force_new_session: bool = True) -> None:
        self._pool.restart(force_new_session=force_new_session)

    def cancel_active_turns(self, level: Optional[str] = None) -> dict:
        return self._pool.cancel_active_turns(level=level)

    def compact_session(self, timeout: float = 45.0) -> dict:
        return self._pool.compact_session(timeout=timeout)

    def get_status(self) -> dict:
        return self._pool.get_status()

    def trigger_restart_with_notification(self, reason: str = "手動觸發") -> None:
        self._pool.trigger_restart_with_notification(reason=reason)

    def get_available_models(self) -> List[dict]:
        return [
            {"id": "hydrogen", "label": "Gemini 4 Hydrogen", "thinking_levels": ["low", "med", "high", "xhigh"]},
            {"id": "argon", "label": "Gemini 4 Argon", "thinking_levels": ["low", "med", "high"]},
            {"id": "flash", "label": "Gemini 3.8 Flash", "thinking_levels": ["low", "med", "high"]},
            {"id": "claude", "label": "Claude Sonnet 5.5", "thinking_levels": ["low", "med", "high"]},
            {"id": "opus", "label": "Claude Opus 5.5", "thinking_levels": ["low", "med", "high"]},
        ]

    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Generator[Tuple[str, str], None, dict]:
        effective_override = None if model_override in (None, "", "hydrogen", "gemini3") else model_override
        return (
            yield from self._pool.send_query_stream(
                query,
                media=media,
                level=level,
                model_override=effective_override,
            )
        )

    def send_query(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, List[str], float, float]:
        return self._pool.send_query(query, media=media, level=level)


class BrainRouter:
    """Routes all assistant channels (App, Watch, HomePod, Telegram, Share) to the active brain engine."""

    def __init__(self, prefs_path: Optional[Path] = None, history_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._engines: Dict[str, BaseBrainEngine] = {}
        self._prefs_path = prefs_path or (settings.DATA_DIR / "brain_prefs.json")
        self._history_path = history_path or settings.HISTORY_LOG_PATH
        self._active_engine_id: str = "agy"
        self._active_models: Dict[str, str] = {}
        self._pending_handoff: Optional[Tuple[str, str]] = None
        self._load_prefs()

    def _load_prefs(self) -> None:
        try:
            if self._prefs_path.exists():
                data = json.loads(self._prefs_path.read_text(encoding="utf-8"))
                eng = str(data.get("active_engine") or "agy").strip()
                if eng:
                    self._active_engine_id = eng
                models = data.get("active_models")
                if isinstance(models, dict):
                    self._active_models = {str(k): str(v) for k, v in models.items() if v}
        except Exception:
            pass

    def _save_prefs(self) -> None:
        try:
            self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "active_engine": self._active_engine_id,
                "active_models": self._active_models,
            }
            self._prefs_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as ex:
            print(f"[BrainRouter] ⚠️ 儲存偏好失敗: {ex}")

    def register_engine(self, engine: BaseBrainEngine) -> None:
        with self._lock:
            self._engines[engine.engine_id] = engine

    def get_engine(self, engine_id: Optional[str] = None) -> BaseBrainEngine:
        with self._lock:
            target = engine_id or self._active_engine_id
            if target in self._engines:
                return self._engines[target]
            if "agy" in self._engines:
                return self._engines["agy"]
            if self._engines:
                return next(iter(self._engines.values()))
            raise RuntimeError("No brain engines registered in BrainRouter")

    @property
    def active_engine_id(self) -> str:
        with self._lock:
            if self._active_engine_id in self._engines:
                return self._active_engine_id
            return "agy" if "agy" in self._engines else (next(iter(self._engines)) if self._engines else "agy")

    @property
    def primary_worker(self):
        return self.get_engine().primary_worker

    def select_engine(self, engine_id: str, model_id: Optional[str] = None) -> dict:
        with self._lock:
            if engine_id not in self._engines:
                raise ValueError(f"Unknown brain engine: {engine_id}")
            prev_engine_id = self._active_engine_id
            self._active_engine_id = engine_id
            if model_id is not None and model_id.strip():
                self._active_models[engine_id] = model_id.strip()
            if prev_engine_id != engine_id:
                ctx = self._build_recent_handoff_context(prev_engine_id, engine_id)
                self._pending_handoff = (engine_id, ctx) if ctx else None
            self._save_prefs()
            return self.get_catalog()

    def _build_recent_handoff_context(self, from_engine: str, to_engine: str, max_turns: int = 6) -> str:
        """Read the latest N turns from history.jsonl so the newly selected engine continues seamlessly."""
        try:
            if not self._history_path.exists():
                return ""
            lines = [ln.strip() for ln in self._history_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            recent = lines[-max_turns:]
            snippets: List[str] = []
            for raw in recent:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                q = str(rec.get("query") or "").strip()
                r = str(rec.get("reply") or "").strip()
                if not q and not r:
                    continue
                if len(q) > 200:
                    q = q[:200] + "..."
                if len(r) > 350:
                    r = r[:350] + "..."
                snippets.append(f"- 主人：{q}\n  助理：{r}")
            if not snippets:
                return ""
            joined = "\n".join(snippets)
            return (
                f"[跨大腦記憶交接提要（由 {from_engine} 切換至 {to_engine}）]\n"
                f"以下為切換前最近 {len(snippets)} 輪對話紀錄，請直接銜接脈絡繼續服務：\n"
                f"{joined}\n"
                f"[交接結束，以下為主人本次新訊息]"
            )
        except Exception:
            return ""

    def _consume_handoff_prefix(self, engine_id: str, query: str) -> str:
        with self._lock:
            pending = self._pending_handoff
            if pending and pending[0] == engine_id:
                self._pending_handoff = None
                ctx = pending[1]
                if ctx:
                    return f"{ctx}\n\n{query}"
        return query

    def get_selected_model(self, engine_id: Optional[str] = None) -> Optional[str]:
        with self._lock:
            eid = engine_id or self.active_engine_id
            return self._active_models.get(eid)

    def get_catalog(self) -> dict:
        with self._lock:
            active_id = self.active_engine_id
            engines_out = []
            for eid, eng in self._engines.items():
                models = eng.get_available_models()
                selected_m = self._active_models.get(eid) or (models[0]["id"] if models else "")
                engines_out.append({
                    "id": eid,
                    "label": eng.display_name,
                    "supports_quota": eng.supports_quota,
                    "selected_model": selected_m,
                    "models": models,
                })
            return {
                "active_engine": active_id,
                "active_model": self._active_models.get(active_id, ""),
                "supports_quota": self.get_engine(active_id).supports_quota,
                "engines": engines_out,
            }

    def start(self, force_new_session: bool = False) -> None:
        self.get_engine().start(force_new_session=force_new_session)

    def stop(self) -> None:
        with self._lock:
            engines = list(self._engines.values())
        for eng in engines:
            try:
                eng.stop()
            except Exception:
                pass

    def restart(self, force_new_session: bool = True) -> None:
        self.get_engine().restart(force_new_session=force_new_session)

    def cancel_active_turns(self, level: Optional[str] = None) -> dict:
        results = {}
        with self._lock:
            engines = list(self._engines.items())
        any_cancelled = False
        for eid, eng in engines:
            try:
                res = eng.cancel_active_turns(level=level)
                results[eid] = res
                if res.get("cancelled"):
                    any_cancelled = True
            except Exception as ex:
                results[eid] = {"cancelled": False, "error": str(ex)}
        return {"cancelled": any_cancelled, "active_engine": self.active_engine_id, "details": results}

    def compact_session(self, timeout: float = 45.0) -> dict:
        return self.get_engine().compact_session(timeout=timeout)

    def get_status(self) -> dict:
        eng = self.get_engine()
        status = eng.get_status()
        status["active_engine"] = eng.engine_id
        status["supports_quota"] = eng.supports_quota
        return status

    def trigger_restart_with_notification(self, reason: str = "手動觸發") -> None:
        self.get_engine().trigger_restart_with_notification(reason=reason)

    def send_query_stream(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Generator[Tuple[str, str], None, dict]:
        eng = self.get_engine()
        effective_override = model_override or self.get_selected_model(eng.engine_id)
        resolved_level = level or classify_thinking_level(query)
        raw_clean_query = clean_query_for_history(query)

        if eng.handles_own_postprocess:
            effective_query = self._consume_handoff_prefix(eng.engine_id, query)
            return (
                yield from eng.send_query_stream(
                    effective_query,
                    media=media,
                    level=resolved_level,
                    model_override=effective_override,
                )
            )

        # 共用前處理：移除前綴路由標籤並於首輪注入跨大腦交接提要
        cleaned_prompt = clean_query_prefix(query)
        effective_query = self._consume_handoff_prefix(eng.engine_id, cleaned_prompt)
        stream_gen = eng.send_query_stream(
            effective_query,
            media=media,
            level=resolved_level,
            model_override=effective_override,
        )
        turn_result: dict = {}
        collected_text: List[str] = []
        try:
            while True:
                item = next(stream_gen)
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "text":
                    collected_text.append(str(item[1]))
                yield item
        except StopIteration as stop_ex:
            turn_result = stop_ex.value or {}
        finally:
            try:
                stream_gen.close()
            except Exception:
                pass

            raw_reply = (turn_result.get("clean_reply") or "".join(collected_text)).strip()
            meta = dict(turn_result.get("metadata") or {})
            clean_reply, widget_html, image_urls = extract_rich_blocks(raw_reply)
            clean_reply = clean_reply.strip()
            if widget_html and not meta.get("widget_html"):
                meta["widget_html"] = widget_html
            if image_urls and not meta.get("image_urls"):
                meta["image_urls"] = image_urls
            turn_result["clean_reply"] = clean_reply
            if meta:
                turn_result["metadata"] = meta

            if clean_reply and "quick_replies" not in turn_result:
                try:
                    from agy_singleton import extract_quick_replies
                    qr = extract_quick_replies(clean_reply, raw_clean_query)
                    if qr:
                        turn_result["quick_replies"] = qr
                except Exception:
                    pass

            if not turn_result.get("cancelled") or clean_reply:
                try:
                    saved_images = memory_manager.save_compressed_history_images(media)
                    actions = turn_result.get("actions") or [f"engine:{eng.engine_id}"]
                    memory_manager.log_interaction(
                        raw_clean_query or query,
                        clean_reply,
                        actions,
                        images=saved_images,
                        metadata=meta or None,
                    )
                except Exception as log_ex:
                    print(f"[BrainRouter] ⚠️ 寫入對話紀錄失敗: {log_ex}")

        return turn_result

    def send_query(
        self,
        query: str,
        media: Optional[List[dict]] = None,
        level: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, List[str], float, float]:
        gen = self.send_query_stream(
            query,
            media=media,
            level=level,
            model_override=model_override,
        )
        reply_parts: List[str] = []
        turn_result: dict = {}
        try:
            while True:
                item = next(gen)
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "text":
                    reply_parts.append(str(item[1]))
        except StopIteration as stop_ex:
            turn_result = stop_ex.value or {}
        clean_reply = (turn_result.get("clean_reply") or "".join(reply_parts)).strip()
        actions = turn_result.get("actions") or [f"engine:{self.active_engine_id}"]
        ttft = float(turn_result.get("time_to_first_token") or 0.0)
        total = float(turn_result.get("total_latency") or 0.0)
        return clean_reply, actions, ttft, total


def create_default_brain_router() -> BrainRouter:
    from agy_singleton import agy_singleton
    from omp_singleton import omp_singleton

    router = BrainRouter()
    router.register_engine(AgyEngineAdapter(agy_singleton))
    router.register_engine(omp_singleton)
    return router


brain_router = create_default_brain_router()
