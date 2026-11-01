"""語音轉錄服務：whisper-cli (whisper.cpp + Metal GPU) ＋繁體中文後處理。

供手錶／捷徑錄音直傳（OpenAI 相容 endpoint 定義在 main.py）。
設計要點：
- 推理交給 whisper.cpp 的 Metal 後端，Python 端 subprocess 呼叫；
  實測 5.65s zh-TW 音檔 warm ~1.5s（faster-whisper CPU int8 為 ~4.9s）
- 模型不常駐：每次請求由程序載入（OS 檔案快取下載入 ~0.9s，已含在實測數字內）
- 輸入任意音檔先 ffmpeg 轉 16kHz 單聲道 wav（whisper-cli 要求的格式）
- Whisper 對 zh 常輸出簡體 → opencc s2t 後處理
- 空轉錄結果視為失敗（沿用 iOS 端 STT 慣例），呼叫端回 4xx
"""
import re
import subprocess
import tempfile
import time
from pathlib import Path

WHISPER_BIN = "/opt/homebrew/bin/whisper-cli"
MODEL_PATH = Path.home() / ".cache" / "whisper" / "ggml-large-v3-turbo.bin"
# Silero VAD：寂靜/純噪音先擋，避免 Whisper 對無語音內容的幻覺
#（zh 常見固定幻覺如「悠悠獨播劇場」——字幕訓練資料殘留）
VAD_MODEL_PATH = Path.home() / ".cache" / "whisper" / "ggml-silero-v6.2.0.bin"
_SEGMENT_RE = re.compile(r"^\[[\d:.]+\s*-->\s*[\d:.]+\]\s*(.*)$")

_cc = None


def _to_traditional(text: str) -> str:
    global _cc
    if _cc is None:
        from opencc import OpenCC
        _cc = OpenCC("s2t")
    return _cc.convert(text)


def warm_transcriber():
    """啟動檢查：binary 與模型存在，缺了在 startup 就爆（模型不需常駐預熱）。"""
    if not Path(WHISPER_BIN).exists():
        raise FileNotFoundError(f"找不到 {WHISPER_BIN}（brew install whisper-cpp）")
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"找不到模型 {MODEL_PATH}")
    if not VAD_MODEL_PATH.exists():
        raise FileNotFoundError(f"找不到 VAD 模型 {VAD_MODEL_PATH}")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def transcribe_file(path: str, language: str = "zh") -> dict:
    """轉錄音檔，回傳 {"text", "language", "duration", "elapsed"}。

    language: BCP-47 前綴（"zh" / "en"…），傳入即跳過語言偵測；zh 走繁體後處理。
    音檔無法解碼或空結果拋 ValueError，由 endpoint 轉 400。
    """
    start = time.monotonic()
    warm_transcriber()
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "a16k.wav")
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-i", path,
                  "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav])
            duration = float(_run(["ffprobe", "-v", "error", "-show_entries",
                                   "format=duration", "-of", "csv=p=0", wav]).stdout.strip())
            cmd = [WHISPER_BIN, "-m", str(MODEL_PATH), "-f", wav,
                   "--vad", "-vm", str(VAD_MODEL_PATH),
                   # 預設 min-silence 100ms 太激進：中文語流稍停就切段，會吃字
                   #（實測「幫我設定」變「我設定」）。放寬後品質與關 VAD 相同，
                   # 仍保留寂靜擋除幻覺的功能
                   "--vad-min-silence-duration-ms", "500",
                   "--vad-speech-pad-ms", "150",
                   "--vad-threshold", "0.4",
                   "-np"]
            if language:
                cmd += ["-l", language]
            proc = _run(cmd)
        except subprocess.CalledProcessError as e:
            if e.cmd and e.cmd[0].endswith("ffmpeg"):
                raise ValueError("音檔格式無法解碼") from e
            raise
        text = "".join(m.group(1) for l in proc.stdout.splitlines()
                       if (m := _SEGMENT_RE.match(l))).strip()
    if not text:
        raise ValueError("沒有聽到語音內容")
    if (language or "").startswith("zh"):
        text = _to_traditional(text)
    return {
        "text": text,
        "language": language or "auto",
        "duration": round(duration, 2),
        "elapsed": round(time.monotonic() - start, 2),
    }
