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
    handles_own_postprocess = True

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
        {"id": "gpt-5.4", "label": "GPT 5.4", "thinking_levels": ["low", "med", "high"]},
        {"id": "claude-sonnet", "label": "Sonnet", "thinking_levels": ["low", "med", "high"]},
    ]
    assert eng.supports_quota is False


def test_clean_query_for_history_strips_multiline_handoff():
    from brain_engine import clean_query_for_history

    raw = (
        "[跨大腦記憶交接提要（由 omp 切換至 agy）]\n"
        "- 主人：hi\n"
        "  助理：第一段\n\n"
        "第二段內容\n"
        "[交接結束，以下為主人本次新訊息]\n\n"
        "[來源管道: iOS]\n"
        "【回覆長度約束：細節模式】\n\n"
        "問一下廁所燈"
    )
    assert clean_query_for_history(raw) == "問一下廁所燈"


def test_safe_reload_endpoint_schedules_task(monkeypatch):
    from fastapi.testclient import TestClient
    import main as app_main

    class _FakeProc:
        returncode = 0
        stdout = '{"success": true, "task_id": "task_test_reload"}'
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeProc())
    app_main.app.dependency_overrides[app_main.verify_token] = lambda: True
    try:
        client = TestClient(app_main.app)
        resp = client.post("/api/system/safe-reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "scheduled"
        assert data["task_id"] == "task_test_reload"
    finally:
        app_main.app.dependency_overrides.pop(app_main.verify_token, None)


def test_history_search_endpoint_filters_user_and_assistant(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    import main as app_main

    hist_file = tmp_path / "history.jsonl"
    rows = [
        {"timestamp": "2026-11-17T10:00:00", "query": "竹北天氣如何", "reply": "竹北今天晴時多雲"},
        {"timestamp": "2026-11-17T11:00:00", "query": "幫我看廁所電燈", "reply": "已偵測到 Zigbee 路由異常"},
        {"timestamp": "2026-11-17T12:00:00", "query": "酷澎價格多少", "reply": "目前特價 50730 元"},
    ]
    hist_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    monkeypatch.setattr(app_main.settings, "HISTORY_LOG_PATH", hist_file)
    app_main.app.dependency_overrides[app_main.verify_token] = lambda: True
    try:
        client = TestClient(app_main.app)
        # 搜助理回覆
        r1 = client.get("/api/history/search", params={"q": "Zigbee", "role": "assistant"})
        assert r1.status_code == 200
        assert len(r1.json()["results"]) == 1
        assert r1.json()["results"][0]["matched_in"] == "assistant"

        # 搜提問
        r2 = client.get("/api/history/search", params={"q": "竹北 天氣", "role": "user"})
        assert r2.status_code == 200
        assert len(r2.json()["results"]) == 1
        assert r2.json()["results"][0]["matched_in"] == "user"

        # 空查詢
        r3 = client.get("/api/history/search", params={"q": "   "})
        assert r3.status_code == 200
        assert r3.json()["results"] == []
    finally:
        app_main.app.dependency_overrides.pop(app_main.verify_token, None)


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
        {
            "type": "agent_end",
            "messages": [
                {
                    "usage": {
                        "input": 200,
                        "output": 80,
                        "reasoningTokens": 21,
                        "cacheRead": 20,
                        "totalTokens": 321,
                        "cost": {"total": 0.001234},
                    }
                }
            ],
        },
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
        gen = eng._send_query_stream_subprocess("hi", level="med", model_override="muse-spark-1.3-contributor")
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
    meta = result["metadata"]
    assert meta["model"] == "muse-spark-1.3-contributor"
    assert meta["thinking_level"] == "med"
    assert meta["estimated_cost_usd"] == 0.001234
    assert meta["usage"] == {
        "input_tokens": 200,
        "output_tokens": 80,
        "thought_tokens": 21,
        "cached_read_tokens": 20,
        "total_tokens": 321,
    }
    stat = eng.get_status()
    assert stat["current_tokens"] == 321
    assert stat["usage"]["input_tokens"] == 200


def test_omp_resident_dispatch_uses_worker():
    eng = OmpBrainEngine()
    expected_meta = {
        "model": "muse-spark-1.3-contributor",
        "thinking_level": "med",
        "ttft_sec": 0.1,
        "total_sec": 0.2,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "thought_tokens": 0,
            "cached_read_tokens": 0,
            "total_tokens": 15,
        },
        "estimated_cost_usd": 0.0,
        "engine": "omp-resident",
    }
    expected_result = {
        "status": "OK",
        "clean_reply": "常駐回覆",
        "thought": "",
        "actions": ["engine:omp-resident (muse-spark-1.3-contributor)"],
        "total_latency": 0.2,
        "time_to_first_token": 0.1,
        "thinking_level": "med",
        "model": "muse-spark-1.3-contributor",
        "metadata": expected_meta,
    }

    def _fake_worker_stream(*args, **kwargs):
        yield ("text", "常駐回覆")
        return expected_result

    eng._worker.send_query_stream = _fake_worker_stream
    eng._worker.last_usage = dict(expected_meta["usage"])
    eng._worker.last_tokens = 15
    eng._worker.model = "muse-spark-1.3-contributor"
    gen = eng.send_query_stream("hi", level="med")
    collected = []
    result = {}
    try:
        while True:
            collected.append(next(gen))
    except StopIteration as stop_ex:
        result = stop_ex.value
    assert ("text", "常駐回覆") in collected
    assert result["metadata"]["engine"] == "omp-resident"
    assert result["metadata"]["usage"]["total_tokens"] == 15
    assert eng._last_tokens == 15


def test_cross_brain_handoff_injected_once(tmp_path):
    prefs = tmp_path / "brain_prefs.json"
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps({"query": "幫我查酷澎價格", "reply": "目前尿布為 799 元"}, ensure_ascii=False) + "\n"
        + json.dumps({"query": "那幫我記一下", "reply": "已記錄備忘"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    seen_queries = []

    class _CaptureEngine(_DummyEngine):
        def send_query_stream(self, query, media=None, level=None, model_override=None):
            seen_queries.append(query)
            return (yield from super().send_query_stream(query, media=media, level=level, model_override=model_override))

    router = BrainRouter(prefs_path=prefs, history_path=history)
    router.register_engine(_CaptureEngine("agy", True, [{"id": "hydrogen", "label": "Hydrogen"}]))
    router.register_engine(_CaptureEngine("omp", False, [{"id": "m1", "label": "M1"}]))

    router.select_engine("omp", "m1")
    router.send_query("接下來呢？")
    router.send_query("第二句話")

    assert len(seen_queries) == 2
    assert "[跨大腦記憶交接提要（由 agy 切換至 omp）]" in seen_queries[0]
    assert "幫我查酷澎價格" in seen_queries[0]
    assert seen_queries[0].endswith("接下來呢？")
    assert seen_queries[1] == "第二句話"


def test_omp_cleans_history_query_and_extracts_widget_html():
    from omp_singleton import _clean_query_for_history, _finalize_reply_and_metadata
    from main import _clean_history_query

    wrapped = (
        "[跨大腦記憶交接提要（由 agy 切換至 omp）]\n背景...\n\n"
        "[來源管道: iOS 隨身語音對話 App]\n"
        "【語音輸出嚴格規範】\n1. 精簡\n\n"
        "【回覆長度約束：細節模式】250~350字\n\n"
        "幫我看一下狀態"
    )
    assert _clean_query_for_history(wrapped) == "幫我看一下狀態"
    assert _clean_history_query(wrapped) == "幫我看一下狀態"

    meta = {}
    cleaned = _finalize_reply_and_metadata("目前正常<widget_html><div>OK</div></widget_html>", meta)
    assert cleaned == "目前正常"
    assert meta["widget_html"] == "<div>OK</div>"


def test_brain_router_shared_postprocess_pipeline(tmp_path):
    class _RawEngine(_DummyEngine):
        handles_own_postprocess = False

        def send_query_stream(self, query, media=None, level=None, model_override=None):
            yield ("text", "ok<widget_html><span>hi</span></widget_html>")
            return {
                "status": "OK",
                "clean_reply": "ok<widget_html><span>hi</span></widget_html>",
                "actions": ["engine:omp"],
                "metadata": {"model": "m1"},
            }

    router = BrainRouter(prefs_path=tmp_path / "prefs.json", history_path=tmp_path / "h.jsonl")
    router.register_engine(_RawEngine("omp", False, [{"id": "m1", "label": "M1"}]))
    router.select_engine("omp", "m1")

    with patch("brain_engine.memory_manager.log_interaction") as mock_log, patch(
        "agy_singleton.extract_quick_replies", return_value=["再問詳細點"]
    ):
        gen = router.send_query_stream("【回覆長度約束：細節模式】250字\n\n查狀態")
        res = {}
        try:
            while True:
                next(gen)
        except StopIteration as stop_ex:
            res = stop_ex.value

    assert res["clean_reply"] == "ok"
    assert res["metadata"]["widget_html"] == "<span>hi</span>"
    assert res["quick_replies"] == ["再問詳細點"]
    mock_log.assert_called_once()
    assert mock_log.call_args.args[0] == "查狀態"
    assert mock_log.call_args.args[1] == "ok"


def test_agy_brain_pool_resolves_family_model_override():
    from agy_singleton import AgyBrainPool

    pool = object.__new__(AgyBrainPool)
    pool.low_model = "gemini-4-hydrogen-low"
    pool.med_model = "gemini-4-hydrogen-medium"
    pool.high_model = "gemini-4-hydrogen-high"

    captured_targets = []

    class DummyWorker:
        name = "UNIFIED"
        session_id = "dummy-session"
        last_turn_result = {}
        last_usage = {}

        def send_query_stream(self, query, media=None, target_model=None):
            captured_targets.append(target_model)
            yield ("text", "ok")
            return {"status": "OK", "clean_reply": "ok", "model": target_model}

    pool.primary_worker = DummyWorker()

    with patch("agy_singleton.memory_manager.log_interaction"):
        gen = pool.send_query_stream("hi", level="argon:med", model_override="argon")
        try:
            while True:
                next(gen)
        except StopIteration:
            pass

        gen2 = pool.send_query_stream("hi", level="high", model_override="argon")
        try:
            while True:
                next(gen2)
        except StopIteration:
            pass

    assert captured_targets == ["gemini-4-argon-medium", "gemini-4-argon-high"]


def test_agy_worker_preserves_markdown_code_blocks():
    import queue
    import threading
    from agy_singleton import AgyWorker

    worker = object.__new__(AgyWorker)
    worker.name = "TEST"
    worker.model = "gemini-4-hydrogen-medium"
    worker.session_id = "sess-1"
    worker._is_ready = True
    worker.last_usage = {}
    worker.last_tokens = 0
    worker.last_active_time = 0.0
    worker._active_turn_id = None
    worker._cancel_requested = False
    worker.last_turn_result = {}
    worker.lock = threading.RLock()
    worker.stdout_queue = queue.Queue()
    worker._rpc_id_counter = 10
    worker._next_rpc_id = lambda: 11
    worker._get_actual_context_tokens = lambda tool_calls_count=0: 100
    worker._save_session_state = lambda: None
    worker._build_acp_prompt_blocks = lambda q, media=None: [{"type": "text", "text": q}]

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()

    sample_md = "### 測試標題\n\n**重點**：\n\n```swift\nlet x = 1\n```"

    def fake_send_rpc(payload):
        req_id = payload["id"]
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": sample_md}}},
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 20}},
        })

    worker._send_rpc = fake_send_rpc

    gen = worker.send_query_stream("test")
    result = None
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = stop.value

    assert result is not None
    assert "### 測試標題" in result["clean_reply"]
    assert "**重點**" in result["clean_reply"]
    assert "```swift\nlet x = 1\n```" in result["clean_reply"]


def test_omp_discovers_gemini_gw_providers_and_excludes_muse(tmp_path, monkeypatch):
    import omp_singleton

    models_yml = tmp_path / "models.yml"
    models_yml.write_text(
        """providers:
  Gemini-GW-1:
    auth: none
    models:
      - id: gemini-4-hydrogen
        name: Gemini 4 Hydrogen (GW-1)
      - id: gemini-4-neon
        name: Gemini 4 Neon (GW-1)
  gemini-gw-2:
    auth: none
    models:
      - id: gemini-4-neon
        name: Gemini 4 Neon (GW-2)
      - id: gemini-auto
        name: Gemini Auto (GW-2)
  other-provider:
    auth: none
    models:
      - id: muse-spark-1.3
        name: Muse Spark 1.3
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OMP_CURATED_MODELS", raising=False)
    monkeypatch.setenv("OMP_MODELS_YML", str(models_yml))

    models = omp_singleton._load_curated_omp_models()
    ids = [m["id"] for m in models]
    labels = [m["label"] for m in models]
    assert ids == [
        "Gemini-GW-1/gemini-4-hydrogen",
        "Gemini-GW-1/gemini-4-neon",
        "gemini-gw-2/gemini-4-neon",
    ]
    assert labels == [
        "Gemini 4 Hydrogen (GW-1)",
        "Gemini 4 Neon (GW-1)",
        "Gemini 4 Neon (GW-2)",
    ]
    assert all("muse" not in mid.lower() for mid in ids)
    assert all("muse" not in m["id"].lower() for m in omp_singleton.DEFAULT_OMP_MODELS)


def test_omp_models_yml_fallback_parser():
    import omp_singleton

    sample = """providers:
  Gemini-GW-1:
    auth: none
    models:
      - id: gemini-4-hydrogen
        name: Gemini 4 Hydrogen (GW-1)
      - id: gemini-web
        name: Gemini Web (GW-1)
"""
    parsed = omp_singleton._parse_omp_models_yml_fallback(sample)
    assert "Gemini-GW-1" in parsed["providers"]
    assert parsed["providers"]["Gemini-GW-1"]["models"] == [
        {"id": "gemini-4-hydrogen", "name": "Gemini 4 Hydrogen (GW-1)"},
        {"id": "gemini-web", "name": "Gemini Web (GW-1)"},
    ]
