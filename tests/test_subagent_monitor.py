import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import main
from subagent_monitor import get_subagent_logs, list_active_subagents


def _create_subagent_dir(
    root: Path,
    conv_id: str,
    role: str,
    events: list[dict],
    mtime: float | None = None,
    with_invoke_message: bool = True,
) -> Path:
    conv_dir = root / conv_id
    logs_dir = conv_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    transcript = logs_dir / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(transcript, (mtime, mtime))

    if with_invoke_message:
        msg_dir = conv_dir / ".system_generated" / "messages"
        msg_dir.mkdir(parents=True, exist_ok=True)
        msg_payload = {
            "id": "msg-1",
            "recipient": conv_id,
            "sender": "parent-conv-1234",
            "sourceMetadata": {
                "tool": {
                    "conversationId": "parent-conv-1234",
                    "toolCall": {
                        "name": "invoke_subagent",
                        "argumentsJson": json.dumps({
                            "Role": role,
                            "TypeName": "self",
                            "Prompt": "Do some work",
                        }),
                    },
                }
            },
        }
        (msg_dir / "msg-1.json").write_text(json.dumps(msg_payload), encoding="utf-8")
    return conv_dir


def test_list_active_subagents_filters_finished_and_stale(tmp_path: Path):
    now = 1_800_000_000.0

    # 1. Working subagent (last event is PLANNER_RESPONSE with tool_calls)
    _create_subagent_dir(
        tmp_path,
        "sub-working-1",
        "Backend Test Runner",
        [
            {"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "[Message] sender=parent-1"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T10:00:05Z", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "uv run pytest"}}]},
        ],
        mtime=now - 5,
    )

    # 2. Finished subagent (last event is PLANNER_RESPONSE DONE without tool_calls)
    _create_subagent_dir(
        tmp_path,
        "sub-finished-1",
        "Finished Researcher",
        [
            {"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "[Message] sender=parent-1"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T10:00:10Z", "content": "All done!"},
        ],
        mtime=now - 3,
    )

    # 3. Stale subagent (> 120s old even if tool_calls present)
    _create_subagent_dir(
        tmp_path,
        "sub-stale-1",
        "Stale Worker",
        [
            {"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-24T09:50:00Z", "content": "[Message] sender=parent-1"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:50:05Z", "tool_calls": [{"name": "read_file", "args": {"Path": "/tmp/a.py"}}]},
        ],
        mtime=now - 200,
    )

    # 4. Non-subagent (main conversation without invoke_subagent or parent SYSTEM_MESSAGE)
    _create_subagent_dir(
        tmp_path,
        "main-conv-1",
        "Main",
        [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "hello"},
            {"step_index": 1, "source": "MODEL", "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T10:00:02Z", "content": "Command exited with code 0"},
        ],
        mtime=now - 2,
        with_invoke_message=False,
    )

    active = list_active_subagents(brain_dir=tmp_path, stale_seconds=120, now=now)
    assert len(active) == 1
    item = active[0]
    assert item["conversation_id"] == "sub-working-1"
    assert item["role"] == "Backend Test Runner"
    assert item["status"] == "working"
    assert item["current_tool"] == "run_command"
    assert "uv run pytest" in item["current_step"]
    assert item["tool_count"] == 1
    assert any("run_command: uv run pytest" in ln for ln in item["logs"])


def test_get_subagent_logs_sanitizes_secrets_and_validates_id(tmp_path: Path):
    now = time.time()
    _create_subagent_dir(
        tmp_path,
        "sub-log-abc1",
        "Security Auditor",
        [
            {"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "[Message] sender=parent-1"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T10:00:01Z", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "curl -H 'Authorization: Bearer sk-secret1234567890abcdef'"}}]},
            {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T10:00:03Z", "content": "Command exited with code 0\nok api_key=my_super_secret_token_999"},
        ],
        mtime=now - 2,
    )

    detail = get_subagent_logs("sub-log-abc1", brain_dir=tmp_path, now=now)
    assert detail is not None
    assert detail["status"] == "working"
    assert "sk-secret1234567890abcdef" not in detail["log_text"]
    assert "my_super_secret_token_999" not in detail["log_text"]
    assert "[REDACTED]" in detail["log_text"]

    # Path traversal protection
    assert get_subagent_logs("../etc", brain_dir=tmp_path, now=now) is None


def test_subagent_api_endpoints(monkeypatch, tmp_path: Path):
    now = time.time()
    _create_subagent_dir(
        tmp_path,
        "sub-api-001",
        "API Tester",
        [
            {"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "[Message] sender=parent-1"},
            {"step_index": 1, "source": "MODEL", "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T10:00:02Z", "content": "Command exited with code 0\n5 passed"},
        ],
        mtime=now - 1,
    )

    import subagent_monitor
    monkeypatch.setattr(subagent_monitor, "DEFAULT_BRAIN_DIR", tmp_path)
    monkeypatch.setattr(main.settings, "API_KEY", "")

    client = TestClient(main.app)
    resp = client.get("/api/subagents/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["subagents"][0]["conversation_id"] == "sub-api-001"
    assert body["subagents"][0]["role"] == "API Tester"

    log_resp = client.get("/api/subagents/sub-api-001/logs?tail=20")
    assert log_resp.status_code == 200
    assert "5 passed" in log_resp.json()["log_text"]

    missing_resp = client.get("/api/subagents/nonexistent-id/logs")
    assert missing_resp.status_code == 404
