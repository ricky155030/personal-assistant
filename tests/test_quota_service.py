import sys
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest
from unittest.mock import patch
from quota_service import QuotaService

SAMPLE_CACHE = {
    "models": {
        "gemini4argonmedium": {
            "name": "Gemini 4 Argon (Medium)",
            "remaining_percentage": 20.0,
            "reset_time": "2026-11-14T00:57:56Z",
            "refreshes_in": "4d"
        },
        "gemini": {
            "remaining_percentage": 95.0,
            "reset_time": "2026-11-10T01:03:34Z",
            "refreshes_in": "30m"
        },
        "3p": {
            "remaining_percentage": 80.0,
            "refreshes_in": "3h"
        }
    },
    "weekly": {
        "gemini": {
            "remaining_percentage": 88.0,
            "reset_time": "2026-11-14T00:56:00Z",
            "refreshes_in": "4d"
        },
        "gemini-argon-wtu": {
            "remaining_percentage": 20.0,
            "reset_time": "2026-11-14T00:57:56Z",
            "refreshes_in": "4d"
        },
        "3p": {
            "remaining_percentage": 50.0,
            "refreshes_in": "2d"
        }
    },
    "email": "test@example.com",
    "planTier": "Google AI Pro",
    "updatedAt": 1700000000000,
    "planStatus": {
        "availablePromptCredits": 500,
        "availableFlowCredits": 100,
        "planInfo": {
            "monthlyPromptCredits": 50000,
            "monthlyFlowCredits": 150000,
            "planName": "Pro"
        }
    }
}

def test_quota_summary_expansion():
    qs = QuotaService()
    with patch.object(qs, "fetch_live_quota", return_value=SAMPLE_CACHE):
        summary = qs.get_quota_summary(force_refresh=False)

        # Gemini
        assert "gemini" in summary
        assert summary["gemini"]["rolling_5h"]["remaining_pct"] == 95.0
        assert summary["gemini"]["weekly_7d"]["remaining_pct"] == 88.0

        # Argon
        assert "argon" in summary
        assert summary["argon"]["remaining_pct"] == 20.0
        assert summary["argon"]["refreshes_in"] == "4d"
        assert "reset_time_taipei" in summary["argon"]

        # Credits
        assert "credits" in summary
        assert summary["credits"]["available_prompt_credits"] == 500
        assert summary["credits"]["available_flow_credits"] == 100
        assert summary["credits"]["monthly_prompt_credits"] == 50000

        # Background tasks & Subagents
        assert "background_tasks" in summary
        assert "subagents" in summary
        assert "running_count" in summary["background_tasks"]
        assert "tasks" in summary["background_tasks"]

def test_format_telegram_message():
    qs = QuotaService()
    with patch.object(qs, "fetch_live_quota", return_value=SAMPLE_CACHE):
        msg = qs.format_telegram_message()
        assert "Gemini 4 Argon" in msg
        assert "Prompt Credits" in msg
        assert "Flow Credits" in msg


def test_fetch_gateway_quotas_parses_usage_and_offline(monkeypatch):
    import io
    import json
    from unittest.mock import MagicMock
    from quota_service import QuotaService, _format_minutes_duration, _used_to_remaining_pct

    assert _used_to_remaining_pct(25) == 75.0
    assert _used_to_remaining_pct(120) == 0.0
    assert _used_to_remaining_pct(None) is None
    assert _format_minutes_duration(195) == "3 小時 15 分"
    assert _format_minutes_duration(1500) == "1 天 1 小時"

    endpoints = [
        {"id": "Gemini-GW-1", "label": "GW-1", "status_url": "http://127.0.0.1:8787/v1/status"},
        {"id": "Gemini-GW-2", "label": "GW-2", "status_url": "http://127.0.0.1:8788/v1/status"},
    ]

    def fake_urlopen(req, timeout=2.0):
        if "8787" in req.full_url:
            body = json.dumps({
                "ok": True,
                "cdpConnected": True,
                "usage": {
                    "plan": "Google AI Pro",
                "currentPercent": 20,
                "currentResetMinutes": 135,
                "weeklyPercent": 45,
                "weeklyResetMinutes": 2880,
                "fetchedAt": "2026-11-24T14:00:00Z",
                "pageLoadedAt": "2026-11-24T13:50:00Z",
                "dataUpdatedAt": "2026-11-24T13:55:00Z",
                "ageMinutes": 5,
                "stale": True,
            },
            }).encode("utf-8")
            cm = MagicMock()
            cm.__enter__.return_value = io.BytesIO(body)
            return cm
        raise ConnectionRefusedError("offline")

    qs = QuotaService()
    with patch("quota_service.urllib.request.urlopen", side_effect=fake_urlopen):
        result = qs.fetch_gateway_quotas(endpoints=endpoints)

    assert len(result) == 2
    gw1, gw2 = result
    assert gw1["id"] == "Gemini-GW-1"
    assert gw1["label"] == "GW-1"
    assert gw1["online"] is True
    assert gw1["weekly_refreshes_in"] == "2 天"
    assert gw1["data_updated_at_taipei"] == "11-24 21:55"
    assert gw1["page_loaded_at_taipei"] == "11-24 21:50"
    assert gw1["age_minutes"] == 5
    assert gw1["stale"] is True
    assert gw1["weekly_remaining_pct"] == 55.0
    assert gw1["weekly_refreshes_in"] == "2 天"

    assert gw2["id"] == "Gemini-GW-2"
    assert gw2["online"] is False
    assert gw2["has_usage"] is False
    assert gw2["rolling_5h_remaining_pct"] is None


def test_get_gemini_gw_endpoints_from_models_yml(tmp_path, monkeypatch):
    import brain_engine  # noqa: F401
    import omp_singleton

    yml = tmp_path / "models.yml"
    yml.write_text(
        """providers:
  Gemini-GW-2:
    baseUrl: http://127.0.0.1:8788/v1
    models:
      - id: gemini-4-hydrogen
  Gemini-GW-1:
    baseUrl: http://127.0.0.1:8787/v1
    models:
      - id: gemini-4-hydrogen
  other:
    baseUrl: http://127.0.0.1:9999/v1
""",
        encoding="utf-8",
    )
    eps = omp_singleton.get_gemini_gw_endpoints(yml)
    assert eps == [
        {"id": "Gemini-GW-1", "label": "GW-1", "status_url": "http://127.0.0.1:8787/v1/status"},
        {"id": "Gemini-GW-2", "label": "GW-2", "status_url": "http://127.0.0.1:8788/v1/status"},
    ]
