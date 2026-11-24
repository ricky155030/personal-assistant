"""Unit tests for dynamic voice recognition vocabulary endpoints."""

from fastapi.testclient import TestClient

import main


def test_voice_vocabulary_get_and_post(tmp_path, monkeypatch):
    vocab_file = tmp_path / "voice_vocabulary.json"
    monkeypatch.setattr(main, "VOICE_VOCABULARY_PATH", vocab_file)
    monkeypatch.setattr(main.settings, "API_KEY", "")

    client = TestClient(main.app)

    # 1. Default fallback when JSON does not exist yet
    res = client.get("/api/voice/vocabulary")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "FinLab" in body["vocabulary"]
    assert "P/C Ratio" in body["vocabulary"]

    # 2. Incremental add & remove
    res2 = client.post(
        "/api/voice/vocabulary",
        json={"add": ["CustomTerm", "FinLab"], "remove": ["Overfit"]},
    )
    assert res2.status_code == 200
    body2 = res2.json()
    assert "CustomTerm" in body2["vocabulary"]
    assert "Overfit" not in body2["vocabulary"]
    assert body2["vocabulary"].count("FinLab") == 1

    # 3. Persisted on subsequent GET
    res3 = client.get("/api/voice/vocabulary")
    assert res3.status_code == 200
    assert "CustomTerm" in res3.json()["vocabulary"]
    assert "Overfit" not in res3.json()["vocabulary"]
