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
