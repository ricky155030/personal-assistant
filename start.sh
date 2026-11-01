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

# 初次啟動檢查：若尚未建立 .env，引導啟動設定嚮導
if [ ! -f ".env" ]; then
    echo "⚠️ 尚未偵測到 .env 設定檔！"
    read -p "是否立即啟動初始化設定嚮導？(Y/n): " choice
    choice=${choice:-Y}
    if [[ "$choice" =~ ^[Yy]$ ]]; then
        python3 scripts/setup_wizard.py
    else
        echo "💡 已略過設定，使用預設配置啟動。"
    fi
fi

echo "=== 正在啟動 Personal Assistant ==="
echo "工作目錄: $DIR"
echo "監聽埠號: 8899"

exec uv run uvicorn main:app --host 0.0.0.0 --port 8899
