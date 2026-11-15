from unittest.mock import MagicMock

import agy_singleton


def test_extract_quick_replies_skips_llm_without_question(monkeypatch):
    mock_run = MagicMock()
    monkeypatch.setattr(agy_singleton.subprocess, "run", mock_run)

    assert agy_singleton.extract_quick_replies("已完成任務，全部測試通過。") == []
    mock_run.assert_not_called()


def test_extract_quick_replies_uses_llm_semantic_options(monkeypatch):
    fake_res = MagicMock()
    fake_res.returncode = 0
    fake_res.stdout = '```json\n{"options": ["屋馬文心店", "屋馬崇德店", "屋馬文心店", "好"]}\n```'
    mock_run = MagicMock(return_value=fake_res)
    monkeypatch.setattr(agy_singleton.subprocess, "run", mock_run)

    opts = agy_singleton.extract_quick_replies("請問您想訂哪一間分店？", user_query="幫我訂屋馬")
    assert opts == ["屋馬文心店", "屋馬崇德店"]
    mock_run.assert_called_once()


def test_extract_quick_replies_returns_empty_when_llm_declines(monkeypatch):
    fake_res = MagicMock()
    fake_res.returncode = 0
    fake_res.stdout = '{"options": []}'
    monkeypatch.setattr(agy_singleton.subprocess, "run", MagicMock(return_value=fake_res))

    opts = agy_singleton.extract_quick_replies("目前機制是用正則抽取的，這樣清楚嗎？")
    assert opts == []


def test_extract_quick_replies_handles_timeout_gracefully(monkeypatch):
    monkeypatch.setattr(
        agy_singleton.subprocess,
        "run",
        MagicMock(side_effect=agy_singleton.subprocess.TimeoutExpired(cmd="agy", timeout=8)),
    )

    opts = agy_singleton.extract_quick_replies("要幫您執行嗎？")
    assert opts == []
