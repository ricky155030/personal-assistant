"""
TTS 類神經語音合成服務 (Edge-TTS / Gemini TTS)
支援台灣繁體中文自然神經語音：
- zh-TW-HsiaoChenNeural (曉臻 Female - 溫暖親切女聲，預設)
- zh-TW-YunJheNeural (雲哲 Male - 自然沉穩男聲)
- zh-TW-HsiaoYuNeural (曉雨 Female - 溫柔輕聲女聲)
當環境變數 TTS_PROVIDER=gemini 且已設定 GEMINI_API_KEY 時，優先使用 Gemini TTS 並透過 ffmpeg 轉為 MP3，失敗時自動降級回 Edge-TTS。
提供記憶體 LRU 快取與 Base64 MP3 編碼輸出。
"""
import asyncio
import base64
import hashlib
import io
import os
import re
from typing import Dict, Optional

import edge_tts
import httpx

DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"
DEFAULT_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_GEMINI_TTS_VOICE = "Kore"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# 簡易 LRU 快取（以 md5(provider+voice+rate+text) 為 key，避免重複合成短語）
_AUDIO_CACHE: Dict[str, str] = {}
_CACHE_MAX_SIZE = 100


def sanitize_text_for_tts(text: str) -> str:
    """清理多餘 markdown、表情符號與空白，提高語音朗讀流暢度"""
    if not text:
        return ""
    # 去除 markdown 粗體、斜體、反引號、標題符號
    t = re.sub(r"[\*#`_~>]", "", text)
    # 去除 URL 連結
    t = re.sub(r"https?://\S+", "", t)
    # 去除常用 Emoji 表情（保持純口語文字）
    t = re.sub(r"[\U00010000-\U0010ffff]", "", t)
    # 壓縮空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _parse_pcm_rate(mime_type: str, default: int = 24000) -> int:
    """從 audio/L16;codec=pcm;rate=24000 解析取樣率"""
    match = re.search(r"rate=(\d+)", mime_type or "")
    return int(match.group(1)) if match else default


async def _pcm_to_mp3(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """透過 ffmpeg 將原始 16-bit PCM 音訊轉為 MP3"""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-f",
        "mp3",
        "-b:a",
        "64k",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=pcm_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg pcm->mp3 failed: {stderr.decode(errors='ignore').strip()}")
    return stdout


async def _synthesize_edge_bytes(
    clean_text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes:
    """使用微軟 Edge-TTS 合成 MP3 音訊位元組"""
    comm = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    out = io.BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            out.write(chunk["data"])
    return out.getvalue()


async def _synthesize_gemini_bytes(clean_text: str, voice: Optional[str] = None) -> bytes:
    """呼叫 Gemini TTS API 合成語音並轉為 MP3"""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    model = os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL).strip() or DEFAULT_GEMINI_TTS_MODEL
    # 若傳入的是 Edge-TTS 語音名稱（含 Neural），改用環境設定的 Gemini 預設語音
    gemini_voice = (
        voice
        if voice and "Neural" not in voice
        else os.getenv("GEMINI_TTS_VOICE", DEFAULT_GEMINI_TTS_VOICE).strip() or DEFAULT_GEMINI_TTS_VOICE
    )

    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    prompt_text = f"請逐字朗讀以下內容，不要回覆或補充任何多餘文字：\n{clean_text}"
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": gemini_voice}
                }
            },
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={"x-goog-api-key": api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    inline = parts[0].get("inlineData", {}) if parts else {}
    b64_data = inline.get("data", "")
    if not b64_data:
        raise RuntimeError("Gemini TTS returned empty audio payload")

    raw_bytes = base64.b64decode(b64_data)
    mime_type = inline.get("mimeType", "")
    if "mp3" in mime_type or "mpeg" in mime_type:
        return raw_bytes
    sample_rate = _parse_pcm_rate(mime_type)
    return await _pcm_to_mp3(raw_bytes, sample_rate=sample_rate)


async def synthesize_to_bytes(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes:
    clean_text = sanitize_text_for_tts(text)
    if not clean_text:
        return b""

    provider = os.getenv("TTS_PROVIDER", "edge").strip().lower()
    if provider == "gemini" and os.getenv("GEMINI_API_KEY", "").strip():
        try:
            audio_bytes = await _synthesize_gemini_bytes(clean_text, voice=voice)
            if audio_bytes:
                return audio_bytes
        except Exception as e:
            print(f"[tts_service] Gemini TTS failed, falling back to Edge-TTS: {e}")

    return await _synthesize_edge_bytes(clean_text, voice=voice, rate=rate, pitch=pitch)


async def synthesize_to_base64(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Optional[str]:
    clean_text = sanitize_text_for_tts(text)
    if not clean_text:
        return None

    provider = os.getenv("TTS_PROVIDER", "edge").strip().lower()
    cache_key = hashlib.md5(f"{provider}:{voice}:{rate}:{clean_text}".encode("utf-8")).hexdigest()
    if cache_key in _AUDIO_CACHE:
        return _AUDIO_CACHE[cache_key]

    try:
        audio_bytes = await synthesize_to_bytes(clean_text, voice=voice, rate=rate, pitch=pitch)
        if not audio_bytes:
            return None
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        if len(_AUDIO_CACHE) >= _CACHE_MAX_SIZE:
            _AUDIO_CACHE.pop(next(iter(_AUDIO_CACHE)))
        _AUDIO_CACHE[cache_key] = b64
        return b64
    except Exception as e:
        print(f"[tts_service] Synthesis error for '{text[:20]}': {e}")
        return None
