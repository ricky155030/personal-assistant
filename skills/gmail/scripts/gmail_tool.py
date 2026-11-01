#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from common.gws_client import (
    GWS_BACKUP_DIR,
    _ensure_credentials_backup,
    _restore_credentials_if_needed,
    run_gws_command,
)

import base64
import html
import re

def extract_email_body(payload: dict) -> str:
    """遞迴從 Gmail payload 中提取純文字或乾淨的 HTML 純文字，自動完成 Base64URL 解碼"""
    text_parts = []
    html_parts = []

    def walk(part):
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data", "")
        if body_data:
            try:
                decoded = base64.urlsafe_b64decode(body_data + "=" * (-len(body_data) % 4)).decode("utf-8", errors="replace")
                if mime == "text/plain":
                    text_parts.append(decoded)
                elif mime == "text/html":
                    html_parts.append(decoded)
            except Exception:
                pass
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)

    def clean_html(raw_html: str) -> str:
        clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"<(br|p|div|tr|li)[^>]*>", "\n", clean, flags=re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", "", clean)
        clean = html.unescape(clean)
        return re.sub(r"\n\s*\n+", "\n\n", clean).strip()

    res_text = "\n\n".join(text_parts).strip()
    res_html = clean_html("\n\n".join(html_parts)) if html_parts else ""

    chosen = res_text if len(res_text) > 150 else (res_html or res_text)
    if len(chosen) > 30000:
        chosen = chosen[:30000] + "\n...(內文過長已截斷)..."
    return chosen

def parse_header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""

def get_message_detail(msg_id: str, format_type: str = "metadata") -> dict:
    params = {
        "userId": "me",
        "id": msg_id,
        "format": format_type
    }
    if format_type == "metadata":
        params["metadataHeaders"] = ["Subject", "From", "To", "Date"]

    res = run_gws_command(["gmail", "users", "messages", "get", "--params", json.dumps(params)])
    if "error" in res:
        return {"id": msg_id, "error": res["error"]}

    payload = res.get("payload", {})
    headers = payload.get("headers", [])
    subject = parse_header(headers, "Subject") or "(無標題)"
    sender = parse_header(headers, "From") or "(無寄件人)"
    date_str = parse_header(headers, "Date") or ""
    snippet = res.get("snippet", "")
    labels = res.get("labelIds", [])

    result = {
        "id": msg_id,
        "threadId": res.get("threadId"),
        "subject": subject,
        "from": sender,
        "date": date_str,
        "snippet": snippet,
        "labels": labels,
        "is_unread": "UNREAD" in labels
    }

    if format_type == "full":
        result["body"] = extract_email_body(payload)

    return result

from concurrent.futures import ThreadPoolExecutor

def search_messages(query: str, max_results: int = 15) -> list:
    params = {
        "userId": "me",
        "q": query,
        "maxResults": max_results
    }
    res = run_gws_command(["gmail", "users", "messages", "list", "--params", json.dumps(params)])
    if "error" in res:
        print(f"查詢錯誤: {res['error']}", file=sys.stderr)
        return []

    messages = res.get("messages", [])
    if not messages:
        return []

    msg_ids = [m.get("id") for m in messages if m.get("id")]
    if not msg_ids:
        return []

    # 批次平行抓取：多線程並發提取郵件摘要，耗時從 20 秒縮減至 1.5 秒
    max_workers = min(len(msg_ids), 12)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # executor.map 保證回傳結果順序與輸入 msg_ids 一致
        results = list(executor.map(get_message_detail, msg_ids))

    return results

def main():
    if len(sys.argv) < 2:
        print("Usage: gmail_tool.py [recent <days> [max] | search <query> [max] | important <days> [max] | get <id>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "recent":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        max_results = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        q = f"newer_than:{days}d"
        msgs = search_messages(q, max_results=max_results)
        print(json.dumps(msgs, ensure_ascii=False, indent=2))

    elif cmd == "important":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        max_results = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        # 排除促銷與社交媒體雜訊
        q = f"newer_than:{days}d -category:promotions -category:social"
        msgs = search_messages(q, max_results=max_results)
        print(json.dumps(msgs, ensure_ascii=False, indent=2))

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("請指定搜尋關鍵字，例如: gmail_tool.py search 'is:unread' 10")
            sys.exit(1)
        query = sys.argv[2]
        max_results = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        msgs = search_messages(query, max_results=max_results)
        print(json.dumps(msgs, ensure_ascii=False, indent=2))

    elif cmd == "get":
        if len(sys.argv) < 3:
            print("請指定 message id")
            sys.exit(1)
        msg_id = sys.argv[2]
        detail = get_message_detail(msg_id, format_type="full")
        print(json.dumps(detail, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
