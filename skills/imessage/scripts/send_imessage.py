#!/usr/bin/env python3
"""
Mac mini Assistant iMessage 專屬安全發送腳本 (iMessage Sender)
功能：
- 透過 macOS Messages.app 原生 AppleScript 發送 iMessage 訊息
- 嚴格落實聯絡人白名單保護 (Security Whitelist)，僅允許發送給已登記的電話號碼
- 支援別名匹配（例如：「我」、「家人」、「自己」）
"""

import sys
import os
import re
import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

def load_contacts() -> dict:
    """載入聯絡人白名單，優先自 data/imessage_contacts.json 讀取，亦支援環境變數自訂"""
    contacts_file = BASE_DIR / "data" / "imessage_contacts.json"
    if contacts_file.exists():
        try:
            with open(contacts_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    owner_phone = os.getenv("OWNER_PHONE", "+886900000000")
    family_phone = os.getenv("FAMILY_PHONE", "+886911111111")
    return {
        "owner": {
            "name": "主人",
            "phone": owner_phone,
            "aliases": ["我", "自己", "主人", "我自己", owner_phone]
        },
        "family": {
            "name": "家人",
            "phone": family_phone,
            "aliases": ["家人", "老婆", "先生", family_phone]
        }
    }

CONTACTS_WHITELIST = load_contacts()

def clean_phone(phone_str: str) -> str:
    """清理電話號碼格式（移除空白、橫線、括號）"""
    return re.sub(r"[\s\-\(\)]+", "", phone_str).strip()

def resolve_contact(target: str) -> dict:
    """
    根據目標字串（別名或電話號碼）查詢白名單
    """
    norm_target = clean_phone(target).lower()

    for key, info in CONTACTS_WHITELIST.items():
        # 比對電話
        if clean_phone(info["phone"]) == norm_target:
            return info
        # 比對別名
        for alias in info["aliases"]:
            if clean_phone(alias).lower() == norm_target or alias == target.strip():
                return info

    return None

def send_imessage(target: str, message: str) -> dict:
    contact = resolve_contact(target)
    if not contact:
        return {
            "success": False,
            "error": f"安全性拒絕：目標「{target}」未在安全白名單中。本系統僅允許傳送至已登記聯絡人（主人、老婆），拒絕未授權發送。"
        }

    phone = contact["phone"]
    clean_msg = message.strip()
    if not clean_msg:
        return {
            "success": False,
            "error": "訊息內容不得為空。"
        }

    # 構造安全傳參的 osascript 指令 (使用 argv 傳遞參數，免除引號逃逸漏洞)
    cmd = [
        "osascript",
        "-e", "on run argv",
        "-e", "    set targetAddress to item 1 of argv",
        "-e", "    set msgText to item 2 of argv",
        "-e", '    tell application "Messages"',
        "-e", '        set targetService to 1st account whose service type = iMessage',
        "-e", "        set targetBuddy to participant targetAddress of targetService",
        "-e", "        send msgText to targetBuddy",
        "-e", "    end tell",
        "-e", "end run",
        phone,
        clean_msg
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return {
                "success": True,
                "recipient": contact["name"],
                "phone": phone,
                "message": clean_msg,
                "timestamp": datetime.now().isoformat()
            }
        else:
            stderr = res.stderr.strip()
            if "-1743" in stderr or "Not authorized" in stderr or "未獲授權" in stderr:
                err_msg = (
                    "macOS 自動化權限尚未授權。\n"
                    "請在 Mac mini 上點擊跳出的對話方塊「好（允許）」，"
                    "或至「系統設定 -> 隱私權與安全性 -> 自動化」勾選允許控制「訊息」。"
                )
            else:
                err_msg = f"AppleScript 執行失敗: {stderr}"
            return {
                "success": False,
                "error": err_msg,
                "phone": phone
            }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "發送超時 (15s)。可能 Mac mini 正在等待使用者在螢幕上點擊「允許控制訊息」的權限確認對話方塊。"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"發送異常: {e}"
        }

def main():
    parser = argparse.ArgumentParser(description="Mac mini Assistant iMessage 安全發送工具")
    parser.add_argument("--to", required=True, help="收件人別名或電話號碼 (例如: 我, 家人, +886900000000)")
    parser.add_argument("--message", required=True, help="要發送的訊息文字")

    args = parser.parse_args()
    result = send_imessage(args.to, args.message)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        sys.exit(1)

if __name__ == "__main__":
    main()
