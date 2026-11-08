"""
TTS 類神經語音合成服務 (Edge-TTS)
支援台灣繁體中文自然神經語音：
- zh-TW-HsiaoChenNeural (曉臻 Female - 溫暖親切女聲，預設)
- zh-TW-YunJheNeural (雲哲 Male - 自然沉穩男聲)
- zh-TW-HsiaoYuNeural (曉雨 Female - 溫柔輕聲女聲)
提供記憶體 LRU 快取與 Base64 MP3 編碼輸出。
"""
import io
import re
import base64
import hashlib
from typing import Optional, Dict
import edge_tts

DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"

# 簡易 LRU 快取（以 md5(voice+text) 為 key，避免重複合成短語）
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

async def synthesize_to_bytes(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz"
) -> bytes:
    clean_text = sanitize_text_for_tts(text)
    if not clean_text:
        return b""
    
    comm = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    out = io.BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            out.write(chunk["data"])
    return out.getvalue()

async def synthesize_to_base64(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz"
) -> Optional[str]:
    clean_text = sanitize_text_for_tts(text)
    if not clean_text:
        return None
    
    cache_key = hashlib.md5(f"{voice}:{rate}:{clean_text}".encode("utf-8")).hexdigest()
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
