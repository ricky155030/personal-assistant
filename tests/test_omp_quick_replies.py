from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unittest.mock import MagicMock

import brain_engine  # noqa: F401 必須先載入，避免 omp/brain 循環引用
import omp_singleton


def test_quick_replies_for_cancelled_returns_empty(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("agy_singleton.extract_quick_replies", mock)
    assert omp_singleton._quick_replies_for("要執行嗎？", user_query="q", was_cancelled=True) == []
    mock.assert_not_called()


def test_quick_replies_for_delegates_to_agy(monkeypatch):
    mock = MagicMock(return_value=["好，請執行", "先不用"])
    monkeypatch.setattr("agy_singleton.extract_quick_replies", mock)
    out = omp_singleton._quick_replies_for("要幫您執行嗎？", user_query="幫我訂位")
    assert out == ["好，請執行", "先不用"]
    mock.assert_called_once_with("要幫您執行嗎？", user_query="幫我訂位")


def test_quick_replies_for_handles_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("agy missing")
    monkeypatch.setattr("agy_singleton.extract_quick_replies", _boom)
    assert omp_singleton._quick_replies_for("要幫您執行嗎？") == []
