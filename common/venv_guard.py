#!/usr/bin/env python3
"""
[INFRA-03] 虛擬環境自動切換守衛 (Virtualenv Auto Re-Exec Guard)
職責：
當任何技能腳本被以系統預設 `python3` 呼叫時，若偵測到當前進程並非運行於專案 `.venv`，
自動透過 `os.execv` 無縫切換至 `.venv/bin/python` 重新執行，徹底根除因忘記加 `.venv/bin/python`
而引發之 `ModuleNotFoundError`（如缺少 playwright, pydantic, httpx, PIL 等）。
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
if sys.platform == "win32":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"

# 彈性回退：若在非預期目錄結構中存在另一端的 Python 執行檔
if not VENV_PYTHON.exists():
    alt_python = VENV_DIR / "Scripts" / "python.exe" if sys.platform != "win32" else VENV_DIR / "bin" / "python"
    if alt_python.exists():
        VENV_PYTHON = alt_python


def ensure_venv() -> None:
    """確保當前進程運行於專案 .venv 環境中，否則自動 re-exec 切換"""
    if not VENV_PYTHON.exists():
        return

    # 防止極端情況下無限遞迴
    if os.environ.get("_MAC_MINI_VENV_GUARD_ACTIVE") == "1":
        return

    # 檢查 sys.prefix 是否已指向專案 .venv
    try:
        current_prefix = Path(sys.prefix).resolve()
        target_prefix = VENV_DIR.resolve()
        if current_prefix == target_prefix:
            return
    except Exception:
        pass

    env = os.environ.copy()
    env["_MAC_MINI_VENV_GUARD_ACTIVE"] = "1"
    os.execve(str(VENV_PYTHON), [str(VENV_PYTHON)] + sys.argv, env)
