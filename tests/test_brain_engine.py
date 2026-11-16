"""Tests for BaseBrainEngine, BrainRouter, and OmpBrainEngine."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain_engine import BaseBrainEngine, BrainRouter
from omp_singleton import OmpBrainEngine


class _DummyEngine(BaseBrainEngine):
    def __init__(self, eid: str, supports_quota: bool, models: list[dict]) -> None:
        self.engine_id = eid
        self.display_name = f"Engine {eid}"
        self.supports_quota = supports_quota
        self._models = models
        self.last_model_override = None

    def start(self, force_new_session: bool = False) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self, force_new_session: bool = True) -> None:
        pass

    def cancel_active_turns(self, level=None) -> dict:
        return {"cancelled": True, "engine": self.engine_id}

    def compact_session(self, timeout: float = 45.0) -> dict:
        return {"success": True}

    def get_status(self) -> dict:
        return {"engine": self.engine_id, "ready": True}

    def get_available_models(self) -> list[dict]:
        return list(self._models)

    def send_query_stream(self, query, media=None, level=None, model_override=None):
        self.last_model_override = model_override
        yield ("thought", "thinking...")
        yield ("text", f"reply from {self.engine_id}:{model_override}")
        return {"status": "OK", "clean_reply": f"reply from {self.engine_id}:{model_override}"}


def test_brain_router_switch_and_persistence(tmp_path):
    prefs = tmp_path / "brain_prefs.json"
    router = BrainRouter(prefs_path=prefs)
    agy = _DummyEngine("agy", True, [{"id": "hydrogen", "label": "Hydrogen"}])
    omp = _DummyEngine("omp", False, [{"id": "m1", "label": "Model 1"}, {"id": "m2", "label": "Model 2"}])
    router.register_engine(agy)
    router.register_engine(omp)

    assert router.active_engine_id == "agy"
    cat1 = router.get_catalog()
    assert cat1["supports_quota"] is True
    assert len(cat1["engines"]) == 2

    cat2 = router.select_engine("omp", "m2")
    assert cat2["active_engine"] == "omp"
    assert cat2["active_model"] == "m2"
    assert cat2["supports_quota"] is False

    reply, _, _, _ = router.send_query("hello")
    assert reply == "reply from omp:m2"
    assert omp.last_model_override == "m2"

    # Reload from disk preserves selection
    router2 = BrainRouter(prefs_path=prefs)
    router2.register_engine(agy)
    router2.register_engine(omp)
    assert router2.active_engine_id == "omp"
    assert router2.get_selected_model("omp") == "m2"


def test_omp_curated_models_override(monkeypatch):
    monkeypatch.setenv("OMP_CURATED_MODELS", "gpt-5.4|GPT 5.4,claude-sonnet|Sonnet")
    eng = OmpBrainEngine()
    models = eng.get_available_models()
    assert models == [
        {"id": "gpt-5.4", "label": "GPT 5.4"},
        {"id": "claude-sonnet", "label": "Sonnet"},
    ]
    assert eng.supports_quota is False


def test_omp_stream_parses_jsonl_events(tmp_path):
    eng = OmpBrainEngine()
    eng._session_dir = tmp_path / "omp_sessions"
    eng._session_state_path = tmp_path / "omp_session.json"

    events = [
        {"type": "session", "id": "sess-abc-123"},
        {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "分析中"}},
        {"type": "tool_execution_start", "toolName": "bash"},
        {"type": "tool_execution_end", "toolName": "bash"},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "完成回覆"}},
        {"type": "agent_end", "messages": [{"usage": {"totalTokens": 321}}]},
    ]
    fake_stdout = io.StringIO("\n".join(json.dumps(e) for e in events) + "\n")
    fake_proc = MagicMock()
    fake_proc.stdout = fake_stdout
    fake_proc.stderr = io.StringIO("")
    fake_proc.returncode = 0
    fake_proc.poll.return_value = 0
    fake_proc.wait.return_value = 0

    with patch.object(eng, "_resolve_omp_bin", return_value="/usr/local/bin/omp"), patch(
        "omp_singleton.subprocess.Popen", return_value=fake_proc
    ):
        gen = eng.send_query_stream("hi", level="med", model_override="muse-spark-1.3-contributor")
        collected = []
        result = {}
        try:
            while True:
                collected.append(next(gen))
        except StopIteration as stop_ex:
            result = stop_ex.value

    assert ("thought", "分析中") in collected
    assert ("status", "執行工具：bash") in collected
    assert ("tool_done", "完成工具：bash") in collected
    assert ("text", "完成回覆") in collected
    assert result["clean_reply"] == "完成回覆"
    assert eng._session_id == "sess-abc-123"
    assert eng._last_tokens == 321
