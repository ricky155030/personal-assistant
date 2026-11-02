#!/usr/bin/env python3
"""
Daily Bill Sync Script (daily_bill_sync.py)
專供 TaskManager / Cron 每日背景定時巡檢之輕量腳本
1. 檢索近 2 天之信用卡帳單與入帳通知 (newer_than:2d)
2. 遇新出帳自動解密、增量寫入 Google Sheets，並發送 Telegram 通知
3. 遇繳款成功通知，自動回寫 Google Sheets 為「✅ 已扣款」
"""

import sys
import os
import json
import asyncio
from datetime import datetime
from pathlib import Path

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from common.venv_guard import ensure_venv
    ensure_venv()
except Exception:
    pass

import httpx
from dotenv import load_dotenv

load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.gws_client import run_gws_command
from auditor import sync_month, load_processed_db, save_processed_db
from sheets_ledger import sync_month_to_sheets, get_or_create_ledger_sheet

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

async def send_telegram_alert(text: str):
    """發送 Telegram 推播通知給主人"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[DailySync] 未設定 Telegram Token，省略推播: {text}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"[DailySync] Telegram 發送失敗: {e}")


def run_daily_check():
    print(f"[DailySync] ⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 啟動每日帳單巡檢...")
    processed_db = load_processed_db()
    
    # 1. 搜尋近 3 天的信件
    query = "subject:(電子帳單 OR 信用卡帳單 OR 電子對帳單 OR 繳款通知 OR 繳款入帳通知) newer_than:3d"
    search_res = run_gws_command(["gmail", "users", "messages", "list", "--params", json.dumps({"userId": "me", "q": query, "maxResults": 10})])
    messages = search_res.get("messages", [])

    if not messages:
        print("[DailySync] 💤 近 3 天無新增信用卡帳務相關信件，秒級退出。")
        return

    new_bill_count = 0
    updated_paid_count = 0
    now_month = datetime.now().strftime("%Y-%m")

    # 執行增量同步
    month_data = sync_month(now_month, force=True)
    res_sync = sync_month_to_sheets(now_month, month_data["banks"], processed_db)
    save_processed_db(processed_db)

    # 檢查是否有新發現之未繳帳單，組合 Telegram 訊息
    pending_banks = []
    total_pending = 0
    for b in month_data["banks"].values():
        if not b.get("is_paid"):
            pending_banks.append(f"• *{b['bank_name']}*：NT$ {b['due_amount']:,} (截止/扣款: {b.get('due_date', '-')})")
            total_pending += b["due_amount"]

    if pending_banks:
        sheet_url = res_sync.get("spreadsheet_url", "")
        msg = (
            f"💳 *【信用卡帳單每日巡檢回報】*\n"
            f"已自動同步最新對帳單至 Google Sheets 總帳：\n\n"
            + "\n".join(pending_banks) + "\n\n"
            f"💰 *待扣繳合計*：`NT$ {total_pending:,}`\n"
            f"📊 [查看 Google Sheets 財務總帳]({sheet_url})"
        )
        asyncio.run(send_telegram_alert(msg))
        print(f"[DailySync] 📢 已發送 Telegram 帳單巡檢彙總！")
    else:
        print("[DailySync] ✅ 本期所有帳單皆已繳清或無待繳項目。")


if __name__ == "__main__":
    run_daily_check()
