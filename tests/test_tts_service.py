import base64
import pytest

import tts_service


def test_sanitize_text_for_tts():
    raw = "**你好** `test` https://example.com/abc 😊 世界"
    assert tts_service.sanitize_text_for_tts(raw) == "你好 test 世界"
    assert tts_service.sanitize_text_for_tts("") == ""


def test_parse_pcm_rate():
    assert tts_service._parse_pcm_rate("audio/L16;codec=pcm;rate=24000") == 24000
    assert tts_service._parse_pcm_rate("audio/L16;rate=16000") == 16000
    assert tts_service._parse_pcm_rate("audio/L16") == 24000


@pytest.mark.asyncio
async def test_synthesize_to_bytes_edge_default(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "edge")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    async def fake_edge(clean_text, voice=tts_service.DEFAULT_VOICE, rate="+0%", pitch="+0Hz"):
        return f"edge:{clean_text}".encode("utf-8")

    monkeypatch.setattr(tts_service, "_synthesize_edge_bytes", fake_edge)
    out = await tts_service.synthesize_to_bytes("哈囉")
    assert out == "edge:哈囉".encode("utf-8")


@pytest.mark.asyncio
async def test_synthesize_to_bytes_gemini_success(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setenv("GEMINI_TTS_VOICE", "Kore")

    fake_pcm = b"\x00\x01" * 100
    fake_mp3 = b"ID3-fake-mp3"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/L16;codec=pcm;rate=24000",
                                        "data": base64.b64encode(fake_pcm).decode("ascii"),
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            assert headers == {"x-goog-api-key": "dummy-test-key"}
            assert "請逐字朗讀以下內容" in json["contents"][0]["parts"][0]["text"]
            return FakeResponse()

    async def fake_pcm_to_mp3(pcm_bytes, sample_rate=24000):
        assert pcm_bytes == fake_pcm
        assert sample_rate == 24000
        return fake_mp3

    monkeypatch.setattr(tts_service.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(tts_service, "_pcm_to_mp3", fake_pcm_to_mp3)

    out = await tts_service.synthesize_to_bytes("測試語音")
    assert out == fake_mp3


@pytest.mark.asyncio
async def test_synthesize_to_bytes_gemini_fallback_to_edge(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")

    async def failing_gemini(clean_text, voice=None):
        raise RuntimeError("boom")

    async def fake_edge(clean_text, voice=tts_service.DEFAULT_VOICE, rate="+0%", pitch="+0Hz"):
        return b"edge-fallback"

    monkeypatch.setattr(tts_service, "_synthesize_gemini_bytes", failing_gemini)
    monkeypatch.setattr(tts_service, "_synthesize_edge_bytes", fake_edge)

    out = await tts_service.synthesize_to_bytes("測試降級")
    assert out == b"edge-fallback"


@pytest.mark.asyncio
async def test_synthesize_to_base64_cache_isolated_by_provider(monkeypatch):
    tts_service._AUDIO_CACHE.clear()

    async def fake_synth(text, voice=tts_service.DEFAULT_VOICE, rate="+0%", pitch="+0Hz"):
        provider = tts_service.os.getenv("TTS_PROVIDER", "edge")
        return f"{provider}:{text}".encode("utf-8")

    monkeypatch.setattr(tts_service, "synthesize_to_bytes", fake_synth)

    monkeypatch.setenv("TTS_PROVIDER", "edge")
    b64_edge = await tts_service.synthesize_to_base64("同一句話")

    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    b64_gemini = await tts_service.synthesize_to_base64("同一句話")

    assert base64.b64decode(b64_edge) == "edge:同一句話".encode("utf-8")
    assert base64.b64decode(b64_gemini) == "gemini:同一句話".encode("utf-8")
