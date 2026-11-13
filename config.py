import os
import sys
import shutil
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv

# 優先載入專案內 .env (支援以 ENV_FILE 自訂指定多實例設定)
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE_NAME = os.getenv("ENV_FILE", ".env")
load_dotenv(BASE_DIR / ENV_FILE_NAME, override=True)

class Settings(BaseModel):
    # 服務網路設定
    HOST: str = os.getenv("ASSISTANT_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("ASSISTANT_PORT", os.getenv("PORT", "8899")))
    
    # 捷徑對接安全性密鑰 (Tailscale 私網環境下留空即可免金鑰直通)
    API_KEY: str = os.getenv("ASSISTANT_API_KEY", "")
    
    # Tailscale 資訊
    TAILSCALE_DOMAIN: str = os.getenv("TAILSCALE_DOMAIN", "your-mac-mini.ts.net")
    
    # 核心引擎模式：'agy' (Antigravity CLI), 'omp' (Oh My Pi) 或 'direct'
    AGENT_ENGINE: str = os.getenv("AGENT_ENGINE", "agy")
    AGY_BIN: str = os.getenv("AGY_BIN", shutil.which("agy") or os.path.expanduser("~/.local/bin/agy"))
    AGY_MODEL: str = os.getenv("AGY_MODEL", "gemini-4-hydrogen-low")
    AGY_MODEL_LOW: str = os.getenv("AGY_MODEL_LOW", "gemini-4-hydrogen-low")
    AGY_MODEL_MED: str = os.getenv("AGY_MODEL_MED", "gemini-4-hydrogen-medium")
    AGY_MODEL_HIGH: str = os.getenv("AGY_MODEL_HIGH", "gemini-4-hydrogen-high")
    AGY_MODEL_FAST: str = os.getenv("AGY_MODEL_FAST", "gemini-4-hydrogen-low")
    AGY_MODEL_DEEP: str = os.getenv("AGY_MODEL_DEEP", "gemini-4-hydrogen-medium")

    OMP_BIN: str = os.getenv("OMP_BIN", shutil.which("omp") or os.path.expanduser("~/.local/bin/omp"))
    OMP_MODEL: str = os.getenv("OMP_MODEL", "minimax/MiniMax-M2.5-lightning")
    
    # Claude Code CLI 設定
    CLAUDE_BIN: str = os.getenv(
        "CLAUDE_BIN",
        shutil.which("claude")
        or (shutil.which("claude.cmd") if sys.platform == "win32" else None)
        or os.path.expanduser("~/.local/bin/claude")
    )
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "")
    CLAUDE_MODEL_LOW: str = os.getenv("CLAUDE_MODEL_LOW", "")
    CLAUDE_MODEL_MED: str = os.getenv("CLAUDE_MODEL_MED", "")
    CLAUDE_MODEL_HIGH: str = os.getenv("CLAUDE_MODEL_HIGH", "")
    
    # LLM 設定 (當使用 direct 模式時)
    # 支援 MiniMax / Gemini / OpenAI / Ollama / DeepSeek 等
    LLM_API_KEY: str = (
        os.getenv("LLM_API_KEY") 
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("MINIMAX_API_KEY")
        or ""
    )
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    
    # Telegram Bot 設定
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Context 上下文閾值與自動重啟設定
    CONTEXT_WINDOW_LIMIT: int = int(os.getenv("CONTEXT_WINDOW_LIMIT", "1000000"))
    CONTEXT_RESTART_THRESHOLD: float = float(os.getenv("CONTEXT_RESTART_THRESHOLD", "0.30"))
    CONTEXT_IDLE_MINUTES: int = int(os.getenv("CONTEXT_IDLE_MINUTES", "10"))

    # 每日記憶庫審查與歸檔排程設定 (預設 23:30)
    MEMORY_SYNC_HOUR: int = int(os.getenv("MEMORY_SYNC_HOUR", "23"))
    MEMORY_SYNC_MINUTE: int = int(os.getenv("MEMORY_SYNC_MINUTE", "30"))

    # 檔案路徑
    BASE_DIR: Path = BASE_DIR
    MEMORY_MD_PATH: Path = BASE_DIR / "MEMORY.md"
    AGENT_MD_PATH: Path = MEMORY_MD_PATH  # 兼容別名
    MEMORY_DIR: Path = BASE_DIR / "memory"
    DATA_DIR: Path = BASE_DIR / "data"
    HANDOFFS_DIR: Path = BASE_DIR / "data" / "handoffs"
    TELEGRAM_UPLOADS_DIR: Path = BASE_DIR / "data" / "telegram_uploads"
    REPORTS_DIR: Path = BASE_DIR / "reports"
    DEMANDS_LOG_PATH: Path = BASE_DIR / "data" / "demands.jsonl"
    HISTORY_LOG_PATH: Path = BASE_DIR / "data" / "history.jsonl"

    def get_report_url(self, filename: str) -> str:
        return f"http://{self.TAILSCALE_DOMAIN}:{self.PORT}/reports/{filename}"

settings = Settings()
settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
settings.TELEGRAM_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
