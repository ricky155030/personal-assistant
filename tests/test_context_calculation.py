import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agy_singleton import AgyWorker


def test_get_actual_context_tokens_from_transcript(tmp_path, monkeypatch):
    """測試從 transcript.jsonl 精確解析最後一筆 PLANNER_RESPONSE 的上下文長度"""
    sid = "test-session-123"
    brain_dir = tmp_path / ".gemini" / "antigravity-cli" / "brain" / sid / ".system_generated" / "logs"
    brain_dir.mkdir(parents=True, exist_ok=True)
    transcript_file = brain_dir / "transcript.jsonl"

    steps = [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "hello"},
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "cache_read_tokens": 50000, "input_tokens": 2000, "output_tokens": 500, "content": "res1"},
        {"step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE", "cache_read_tokens": 70000, "input_tokens": 3000, "output_tokens": 800, "content": "res2"},
    ]
    with open(transcript_file, "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    worker = AgyWorker("test", "gemini-3.8-flash-medium")
    worker.session_id = sid
    worker.last_usage = {"total_tokens": 2500000, "cached_read_tokens": 2400000, "input_tokens": 5000, "output_tokens": 800}

    # 應精確取得最後一筆 PLANNER_RESPONSE: 70000 + 3000 + 800 = 73800，而非 total_tokens 2,500,000
    actual = worker._get_actual_context_tokens(tool_calls_count=5)
    assert actual == 73800


def test_get_actual_context_tokens_fallback_with_tools(monkeypatch, tmp_path):
    """測試當無 transcript 時，依據 tool_calls_count 平攤重複累計的 Prompt Cache"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    worker = AgyWorker("test", "gemini-3.8-flash-medium")
    worker.session_id = "non-existent-session"
    # 假設 4 次工具調用，cached_read_tokens 被累加了 5 次 (4 tools + 1 final)
    worker.last_usage = {
        "cached_read_tokens": 500000,  # 實際單次約 100,000
        "input_tokens": 10000,
        "output_tokens": 1500,
        "total_tokens": 511500,
    }

    # tool_calls_count = 4 -> 平攤除以 5 = 100,000 + 10,000 + 1,500 = 111,500
    actual = worker._get_actual_context_tokens(tool_calls_count=4)
    assert actual == 111500


def test_get_actual_context_tokens_single_turn_no_tools(monkeypatch, tmp_path):
    """測試單輪對話（無工具呼叫）正常退回 total_tokens"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    worker = AgyWorker("test", "gemini-3.8-flash-medium")
    worker.session_id = "non-existent-session"
    worker.last_usage = {
        "cached_read_tokens": 0,
        "input_tokens": 12000,
        "output_tokens": 500,
        "total_tokens": 12500,
    }

    actual = worker._get_actual_context_tokens(tool_calls_count=0)
    assert actual == 12500
