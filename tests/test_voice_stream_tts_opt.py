"""Tests for ChatRequest enable_tts backward compatibility and stream TTS bypass."""

from main import ChatRequest


def test_chat_request_enable_tts_defaults_true_for_backward_compat():
    req = ChatRequest(query="hello")
    assert req.enable_tts is True


def test_chat_request_enable_tts_can_be_disabled():
    req = ChatRequest(query="hello", enable_tts=False)
    assert req.enable_tts is False
