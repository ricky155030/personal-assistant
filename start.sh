#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# 支援手動觸發設定嚮導與技能管理: ./start.sh setup [args...]
if [ "$1" = "setup" ] || [ "$1" = "--setup" ]; then
    shift
    python3 scripts/setup_wizard.py "$@"
    exit 0
fi

TARGET_ENV="${ENV_FILE:-.env}"

# 初次啟動檢查：若尚未建立設定檔，引導啟動設定嚮導
if [ ! -f "$TARGET_ENV" ]; then
    echo "⚠️ 尚未偵測到 $TARGET_ENV 設定檔！"
    read -p "是否立即啟動初始化設定嚮導？(Y/n): " choice
    choice=${choice:-Y}
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        python3 scripts/setup_wizard.py
    else
        echo "💡 已略過設定，使用預設配置啟動。"
    fi
fi

# 動態解析監聽埠號 (支援 shell 環境變數或從 TARGET_ENV 檔案讀取)
ENV_PORT=""
if [ -f "$TARGET_ENV" ]; then
    ENV_PORT=$(grep -E '^(ASSISTANT_PORT|PORT)=' "$TARGET_ENV" | tail -n 1 | cut -d '=' -f2 | tr -d '"'\'' ' | tr -d '\r')
fi
PORT="${ASSISTANT_PORT:-${PORT:-${ENV_PORT:-8899}}}"

echo "=== 正在啟動 Personal Assistant ==="
echo "工作目錄: $DIR"
echo "設定檔案: $TARGET_ENV"
echo "監聽埠號: $PORT"

exec uv run uvicorn main:app --host 0.0.0.0 --port "$PORT"
