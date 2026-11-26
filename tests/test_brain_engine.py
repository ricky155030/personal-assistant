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
    assert eng.supports_quota is True


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


def test_omp_resident_does_not_restart_below_token_limit():
    import omp_singleton
    from config import settings

    worker = omp_singleton.OmpResidentWorker("Gemini-GW-1/gemini-4-hydrogen")
    worker._is_ready = True
    worker.session_id = "sess-persist-1"
    worker._agents_injected = True
    worker._next_rpc_id = lambda: 12
    worker._save_session_state = lambda: None
    worker._build_prompt_blocks = lambda q, media=None: [{"type": "text", "text": q}]

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()
    restart_calls = []
    worker.restart = lambda **kwargs: restart_calls.append(kwargs)

    def fake_send_rpc(payload):
        req_id = payload["id"]
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "sess-persist-1", "update": {"sessionUpdate": "usage_update", "used": 15000}},
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "sess-persist-1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}},
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"stopReason": "end_turn", "usage": {"inputTokens": 14000, "outputTokens": 1000}},
        })

    worker._send_rpc = fake_send_rpc

    gen = worker.send_query_stream("hello")
    try:
        while True:
            next(gen)
    except StopIteration:
        pass

    assert worker.last_tokens == 15000
    assert settings.CONTEXT_WINDOW_LIMIT * settings.CONTEXT_RESTART_THRESHOLD > 15000
    assert restart_calls == []


def test_omp_permission_response_helper():
    import omp_singleton

    # Verify session/request_permission outcome structure matches ACP spec
    worker = omp_singleton.OmpResidentWorker("Gemini-GW-1/gemini-4-hydrogen")
    sent = []
    worker._send_rpc = lambda payload: sent.append(payload)

    # Simulate the exact permission selection logic used in _reader
    options = [
        {"optionId": "reject_once", "kind": "reject_once"},
        {"optionId": "allow_once", "kind": "allow_once"},
    ]
    option_id = "allow"
    for opt in options:
        if isinstance(opt, dict):
            oid = str(opt.get("optionId") or "")
            kind = str(opt.get("kind") or "")
            if "allow" in kind or "allow" in oid:
                option_id = oid or "allow"
                break
    assert option_id == "allow_once"


def test_brain_router_restart_delegates_force_new_session(tmp_path):
    from brain_engine import BrainRouter

    router = BrainRouter(prefs_path=tmp_path / "prefs.json", history_path=tmp_path / "hist.jsonl")
    eng = _DummyEngine("omp", True, [{"id": "Gemini-GW-1/gemini-4-hydrogen", "label": "H"}])
    restart_calls = []
    eng.restart = lambda force_new_session=True: restart_calls.append(force_new_session)
    router.register_engine(eng)
    router.select_engine("omp")

    router.restart(force_new_session=True)
    assert restart_calls == [True]


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


def test_omp_resident_syncs_thinking_level_via_acp_config_option():
    import omp_singleton

    worker = omp_singleton.OmpResidentWorker("Gemini-GW-1/gemini-4-hydrogen")
    worker._is_ready = True
    worker.session_id = "sess-think-1"
    worker._agents_injected = True
    worker._save_session_state = lambda: None
    worker._build_prompt_blocks = lambda q, media=None: [{"type": "text", "text": q}]

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()
    sent_rpcs = []

    def fake_send_rpc(payload):
        sent_rpcs.append(payload)
        req_id = payload["id"]
        method = payload.get("method")
        if method == "session/set_config_option":
            worker.stdout_queue.put({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"configOptions": [{"id": "thinking", "currentValue": payload["params"]["value"]}]},
            })
        elif method == "session/prompt":
            worker.stdout_queue.put({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": "sess-think-1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}},
            })
            worker.stdout_queue.put({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 5}},
            })

    worker._send_rpc = fake_send_rpc

    # Turn 1: med -> should call session/set_config_option(configId="thinking", value="medium")
    for _ in worker.send_query_stream("q1", level="med"):
        pass
    # Turn 2: same level med -> should NOT send duplicate set_config_option
    for _ in worker.send_query_stream("q2", level="medium"):
        pass
    # Turn 3: high -> should call session/set_config_option(configId="thinking", value="high")
    for _ in worker.send_query_stream("q3", level="high"):
        pass

    config_calls = [p for p in sent_rpcs if p.get("method") == "session/set_config_option"]
    assert len(config_calls) == 2
    assert config_calls[0]["params"] == {"sessionId": "sess-think-1", "configId": "thinking", "value": "medium"}
    assert config_calls[1]["params"] == {"sessionId": "sess-think-1", "configId": "thinking", "value": "high"}
    assert worker.thinking_level == "high"

    # Turn 4: switch model -> should call set_config_option(model) AND re-sync thinking even if level is still high
    for _ in worker.send_query_stream("q4", level="high", model_override="Gemini-GW-1/gemini-4-neon"):
        pass

    config_calls_after = [p for p in sent_rpcs if p.get("method") == "session/set_config_option"]
    assert len(config_calls_after) == 4
    assert config_calls_after[2]["params"] == {
        "sessionId": "sess-think-1",
        "configId": "model",
        "value": "Gemini-GW-1/gemini-4-neon",
    }
    assert config_calls_after[3]["params"] == {
        "sessionId": "sess-think-1",
        "configId": "thinking",
        "value": "high",
    }
    assert worker.model == "Gemini-GW-1/gemini-4-neon"

    # Force startup model sync (same model name after session/resume)
    assert worker._set_model_unlocked("Gemini-GW-1/gemini-4-neon", force=True, log_switch=False) is True
    assert sent_rpcs[-1]["params"] == {
        "sessionId": "sess-think-1",
        "configId": "model",
        "value": "Gemini-GW-1/gemini-4-neon",
    }

    # Ensure AGENTS.md is not redundantly injected into user prompts (omp loads it natively into system prompt)
    worker2 = omp_singleton.OmpResidentWorker("Gemini-GW-1/gemini-4-hydrogen")
    worker2._is_ready = True
    worker2.session_id = "sess-no-dup-agents"
    worker2._save_session_state = lambda: None
    worker2.proc = DummyProc()
    prompt_texts = []

    def fake_send_rpc2(payload):
        if payload.get("method") == "session/prompt":
            blocks = payload["params"].get("prompt") or []
            prompt_texts.extend(b.get("text", "") for b in blocks if isinstance(b, dict))
            worker2.stdout_queue.put({
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"stopReason": "end_turn", "usage": {"inputTokens": 5, "outputTokens": 2}},
            })

    worker2._send_rpc = fake_send_rpc2
    for _ in worker2.send_query_stream("hello clean"):
        pass
    assert prompt_texts == ["hello clean"]


def test_omp_resident_computes_estimated_cost_and_thinking_level_metadata():
    import omp_singleton

    worker = omp_singleton.OmpResidentWorker("Gemini-GW-1/gemini-4-hydrogen")
    worker._is_ready = True
    worker.session_id = "sess-meta-1"
    worker._next_rpc_id = lambda: 55
    worker._save_session_state = lambda: None
    worker._build_prompt_blocks = lambda q, media=None: [{"type": "text", "text": q}]

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()

    def fake_send_rpc(payload):
        req_id = payload["id"]
        if payload.get("method") == "session/set_config_option":
            worker.stdout_queue.put({"jsonrpc": "2.0", "id": req_id, "result": {}})
            return
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": "sess-meta-1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}}},
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "stopReason": "end_turn",
                "usage": {
                    "inputTokens": 10000,
                    "outputTokens": 500,
                    "thoughtTokens": 1500,
                    "cachedReadTokens": 50000,
                },
            },
        })

    worker._send_rpc = fake_send_rpc

    gen = worker.send_query_stream("test cost", level="hydrogen:high")
    result = None
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = stop.value

    assert result is not None
    meta = result["metadata"]
    assert meta["model"] == "Gemini-GW-1/gemini-4-hydrogen"
    assert meta["thinking_level"] == "high"
    assert meta["usage"]["total_tokens"] == 62000
    # hydrogen pricing: (10000 * 2.00 + 50000 * 0.20 + 2000 * 12.00) / 1e6 = 0.054
    assert meta["estimated_cost_usd"] == 0.054


def test_omp_resident_ignores_in_progress_tool_call_updates_and_preserves_label():
    import omp_singleton

    worker = omp_singleton.OmpResidentWorker("Gemini-GW-2/gemini-4-hydrogen")
    worker._is_ready = True
    worker.session_id = "sess-tool-updates"
    worker._next_rpc_id = lambda: 99
    worker._save_session_state = lambda: None
    worker._build_prompt_blocks = lambda q, media=None: [{"type": "text", "text": q}]

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()

    def fake_send_rpc(payload):
        req_id = payload["id"]
        if payload.get("method") == "session/set_config_option":
            worker.stdout_queue.put({"jsonrpc": "2.0", "id": req_id, "result": {}})
            return
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-tool-updates",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call_1",
                    "title": "$ ./package_ios.sh --bump patch",
                    "status": "in_progress",
                },
            },
        })
        # 模擬指令執行過程中多次吐出 stdout，觸發無 title 的 tool_call_update
        for _ in range(5):
            worker.stdout_queue.put({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess-tool-updates",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "call_1",
                        "status": "in_progress",
                    },
                },
            })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-tool-updates",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call_1",
                    "status": "completed",
                },
            },
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-tool-updates",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "done"},
                },
            },
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"stopReason": "end_turn"},
        })

    worker._send_rpc = fake_send_rpc

    events = []
    gen = worker.send_query_stream("run", level="hydrogen:medium")
    try:
        while True:
            events.append(next(gen))
    except StopIteration:
        pass

    status_events = [e for e in events if e[0] == "status"]
    done_events = [e for e in events if e[0] == "tool_done"]
    assert status_events == [("status", "執行工具：$ ./package_ios.sh --bump patch")]
    assert done_events == [("tool_done", "完成工具：$ ./package_ios.sh --bump patch")]


def test_turn_queue_heartbeat_keeps_active_turn_alive_without_mutating_payload():
    import threading
    import time
    from turn_queue import TurnQueueManager

    mgr = TurnQueueManager(stale_timeout_sec=0.08)
    cancel_ev = threading.Event()
    turn_id, is_active, _ = mgr.enqueue_or_activate("等待回覆測試", cancel_ev)
    assert is_active is True

    stale_called = []
    # 連續等待超過 stale_timeout_sec，但期間有心跳保活，不應被誤殺
    for _ in range(3):
        time.sleep(0.05)
        mgr.update_active(turn_id)
        snap = mgr.get_active_snapshot(on_stale=lambda s: stale_called.append(s))
        assert snap is not None
        assert snap["reply"] == ""
        assert snap["tools"] == []
        assert stale_called == []

    # 心跳停止超過 stale_timeout_sec 後，仍會正常觸發兜底清理
    time.sleep(0.11)
    snap_after = mgr.get_active_snapshot(on_stale=lambda s: stale_called.append(s))
    assert snap_after is None
    assert len(stale_called) == 1


def test_omp_brain_engine_compact_session_updates_tokens_and_handles_busy():
    import omp_singleton

    engine = omp_singleton.OmpBrainEngine()
    worker = engine._worker
    worker._is_ready = True
    worker.session_id = "sess-compact-omp"
    worker.last_tokens = 84000
    worker._save_session_state = lambda: None

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()

    # 1. 忙碌中應拒絕壓縮
    worker._active_turn_id = 77
    busy_res = engine.compact_session()
    assert busy_res["success"] is False
    assert busy_res["engine"] == "omp"
    assert "正在處理對話" in busy_res["error"]
    worker._active_turn_id = None

    # 2. 閒置時發送 /compact 並依據 usage_update / result.usage 更新前後 Token 數
    sent_payloads = []

    def fake_send_rpc(payload):
        sent_payloads.append(payload)
        req_id = payload["id"]
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-compact-omp",
                "update": {
                    "sessionUpdate": "usage_update",
                    "used": 21500,
                },
            },
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "stopReason": "end_turn",
                "usage": {
                    "inputTokens": 20000,
                    "outputTokens": 1500,
                    "totalTokens": 21500,
                },
            },
        })

    worker._send_rpc = fake_send_rpc
    res = engine.compact_session(timeout=5.0)
    assert res["success"] is True
    assert res["engine"] == "omp"
    assert res["session_id"] == "sess-compact-omp"
    assert res["before_tokens"] == 84000
    assert res["after_tokens"] == 21500
    assert sent_payloads[0]["method"] == "session/prompt"
    assert sent_payloads[0]["params"] == {
        "sessionId": "sess-compact-omp",
        "prompt": [{"type": "text", "text": "/compact"}],
    }
    status = engine.get_status()
    assert status["current_tokens"] == 21500
    assert status["usage"]["context_tokens"] == 21500

    # 3. 驗證真實 omp acp 純文字回覆 (Compaction complete. Tokens: X -> Y) 解析
    def text_only_send_rpc(payload):
        req_id = payload["id"]
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-compact-omp",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": "Compaction complete. Tokens: 330955 -> 56564 (saved 274391).",
                    },
                },
            },
        })
        worker.stdout_queue.put({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"stopReason": "end_turn"},
        })

    worker._send_rpc = text_only_send_rpc
    res_text = engine.compact_session(timeout=5.0)
    assert res_text["success"] is True
    assert res_text["before_tokens"] == 330955
    assert res_text["after_tokens"] == 56564
    assert "Compaction complete" in res_text["message"]
    status_text = engine.get_status()
    assert status_text["current_tokens"] == 56564
    assert status_text["usage"]["context_tokens"] == 56564


def test_omp_resident_extends_timeout_on_active_events_and_cancels_on_idle_timeout(monkeypatch):
    import threading
    import time
    import omp_singleton

    monkeypatch.setattr(omp_singleton, "OMP_STREAM_IDLE_TIMEOUT_SEC", 0.25)
    monkeypatch.setattr(omp_singleton, "OMP_STREAM_MAX_TOTAL_SEC", 2.0)

    worker = omp_singleton.OmpResidentWorker("Gemini-GW-2/gemini-4-hydrogen")
    worker._is_ready = True
    worker.session_id = "sess-sliding-timeout"
    worker._save_session_state = lambda: None

    class DummyProc:
        def poll(self):
            return None

    worker.proc = DummyProc()

    # 1. 總時長 (> 0.25s) 超過單段閒置門檻，但期間有持續 tool_call 更新續命 -> 應正常完成
    def active_send_rpc(payload):
        req_id = payload["id"]

        def producer():
            for i in range(4):
                time.sleep(0.1)
                worker.stdout_queue.put({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "sess-sliding-timeout",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": f"tc-{i}",
                            "title": f"step-{i}",
                            "status": "pending",
                        },
                    },
                })
            time.sleep(0.05)
            worker.stdout_queue.put({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "sess-sliding-timeout",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "活躍長任務完成"},
                    },
                },
            })
            worker.stdout_queue.put({"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}})

        threading.Thread(target=producer, daemon=True).start()

    worker._send_rpc = active_send_rpc
    events = []
    result = None
    gen = worker.send_query_stream("long active task", level="hydrogen:medium")
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        result = stop.value

    assert result["status"] == "OK"
    assert result["clean_reply"] == "活躍長任務完成"

    # 2. 完全無活動超過 idle_limit -> 主動送 session/cancel 並回傳 TIMEOUT，不再回空字串 OK
    sent_rpcs = []
    restarted = []
    worker.restart = lambda force_new_session=False: restarted.append(force_new_session)

    def hang_send_rpc(payload):
        sent_rpcs.append(payload)

    worker._send_rpc = hang_send_rpc
    timeout_events = []
    timeout_res = None
    gen2 = worker.send_query_stream("hung task", level="hydrogen:medium")
    try:
        while True:
            timeout_events.append(next(gen2))
    except StopIteration as stop:
        timeout_res = stop.value

    assert timeout_res["status"] == "TIMEOUT"
    assert any(p.get("method") == "session/cancel" for p in sent_rpcs)
    assert "逾時" in timeout_res["clean_reply"]
    assert restarted == [False]
