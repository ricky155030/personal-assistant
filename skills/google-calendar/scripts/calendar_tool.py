#!/usr/bin/env python3
import sys
import os
import json
import subprocess
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
import shutil

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])

# 載入環境變數以讀取日曆設定
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"), override=False)
except Exception:
    pass

CALENDAR_MAP = {
    "primary": "primary",
    "個人": os.getenv("GOOGLE_CALENDAR_PRIMARY_ID", "primary"),
    "待辦事項": os.getenv("GOOGLE_CALENDAR_TODO_ID", "primary"),
    "待辦": os.getenv("GOOGLE_CALENDAR_TODO_ID", "primary"),
    "todo": os.getenv("GOOGLE_CALENDAR_TODO_ID", "primary"),
}
try:
    custom_cals = json.loads(os.getenv("GOOGLE_CALENDAR_ALIASES_JSON", "{}"))
    if isinstance(custom_cals, dict):
        CALENDAR_MAP.update(custom_cals)
except Exception:
    pass

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

def resolve_calendar_id(cal_name: str) -> str:
    """解析行事曆 ID：優先由 CALENDAR_MAP 或環境變數取得，若無則由 gws calendarList 動態比對同名日曆"""
    if not cal_name:
        return CALENDAR_MAP.get("待辦事項", "primary")
    if "@" in cal_name or cal_name.endswith(".calendar.google.com"):
        return cal_name
    
    cal_lower = cal_name.strip().lower()
    for k, v in CALENDAR_MAP.items():
        if k.lower() == cal_lower and v:
            return v

    try:
        res = run_gws_command(["calendar", "calendarList", "list"])
        items = res.get("items", [])
        for item in items:
            summary = item.get("summary", "")
            if summary.strip().lower() == cal_lower:
                return item.get("id", "primary")
    except Exception:
        pass

    return CALENDAR_MAP.get(cal_name) or "primary"

def list_calendars():
    res = run_gws_command(["calendar", "calendarList", "list"])
    if "error" in res:
        print(f"Error: {res['error']}")
        return
    items = res.get("items", [])
    print(f"找到 {len(items)} 本 Google 行事曆：")
    for item in items:
        summary = item.get("summary")
        cid = item.get("id")
        role = item.get("accessRole")
        print(f"• {summary} ({cid}) - 權限: {role}")

def list_events(cal_name: str = "個人", date_filter: str = "today"):
    cid = resolve_calendar_id(cal_name)
    now = datetime.now()

    if date_filter == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=1)
    elif date_filter == "week":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=7)
    elif date_filter == "upcoming":
        start_dt = now
        end_dt = start_dt + timedelta(days=30)
    else:
        # 指定日期 YYYY-MM-DD
        try:
            start_dt = datetime.strptime(date_filter, "%Y-%m-%d")
            end_dt = start_dt + timedelta(days=1)
        except Exception:
            start_dt = now
            end_dt = start_dt + timedelta(days=7)

    time_min = start_dt.isoformat() + "+08:00"
    time_max = end_dt.isoformat() + "+08:00"

    params = {
        "calendarId": cid,
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": True,
        "orderBy": "startTime"
    }

    res = run_gws_command(["calendar", "events", "list", "--params", json.dumps(params)])
    if "error" in res:
        print(f"Error: {res['error']}")
        return

    items = res.get("items", [])
    if not items:
        print(f"「{cal_name}」行事曆在該時段無任何排程。")
        return

    print(f"「{cal_name}」行事曆共有 {len(items)} 則排程：")
    for item in items:
        summary = item.get("summary", "(無標題)")
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
        loc = item.get("location")
        loc_str = f" | 地點: {loc}" if loc else ""
        print(f"• [{start} ~ {end}] {summary}{loc_str}")

def add_event(cal_name: str, title: str, start_str: str, end_str: Optional[str] = None, location: Optional[str] = None, dry_run: bool = False, recurrence: Optional[list] = None):
    cid = resolve_calendar_id(cal_name)
    
    # 解析日期時間
    # 支援格式: 
    # 全天: "YYYY-MM-DD"
    # 時間: "YYYY-MM-DD HH:MM" 或 "YYYY-MM-DDTHH:MM:SS"
    is_all_day = len(start_str.strip()) == 10 and "-" in start_str

    if is_all_day:
        start_obj = {"date": start_str.strip()}
        if end_str:
            end_obj = {"date": end_str.strip()}
        else:
            # 全天事件若未提供 end，預設為次日
            dt = datetime.strptime(start_str.strip(), "%Y-%m-%d") + timedelta(days=1)
            end_obj = {"date": dt.strftime("%Y-%m-%d")}
    else:
        # 轉換為標準 ISO 格式
        clean_start = start_str.replace(" ", "T")
        if len(clean_start) == 16: # "YYYY-MM-DDTHH:MM"
            clean_start += ":00"
        if "+08:00" not in clean_start and "Z" not in clean_start:
            clean_start += "+08:00"
        start_obj = {"dateTime": clean_start, "timeZone": "Asia/Taipei"}

        if end_str:
            clean_end = end_str.replace(" ", "T")
            if len(clean_end) == 16:
                clean_end += ":00"
            if "+08:00" not in clean_end and "Z" not in clean_end:
                clean_end += "+08:00"
            end_obj = {"dateTime": clean_end, "timeZone": "Asia/Taipei"}
        else:
            # 預設 1 小時後
            s_dt = datetime.fromisoformat(clean_start.replace("+08:00", ""))
            e_dt = s_dt + timedelta(hours=1)
            end_obj = {"dateTime": e_dt.isoformat() + "+08:00", "timeZone": "Asia/Taipei"}

    body = {
        "summary": title,
        "start": start_obj,
        "end": end_obj,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 1440}, # 前一天
                {"method": "popup", "minutes": 60}    # 前一小時
            ]
        }
    }
    if location:
        body["location"] = location
    if recurrence:
        body["recurrence"] = recurrence

    params = {"calendarId": cid}
    args = ["calendar", "events", "insert", "--params", json.dumps(params), "--json", json.dumps(body)]
    if dry_run:
        args.append("--dry-run")

    res = run_gws_command(args)
    if "error" in res:
        print(f"新增失敗: {res['error']}")
        return False

    if dry_run:
        print(f"[Dry-Run] 驗證成功！即將寫入「{cal_name}」行事曆：{title} ({start_obj} ~ {end_obj})")
        return True

    html_link = res.get("htmlLink", "")
    print(f"✅ 成功新增至「{cal_name}」行事曆：{title}")
    if html_link:
        print(f"連結: {html_link}")
    return True

def delete_event(cal_name: str, event_id: str):
    cid = resolve_calendar_id(cal_name)
    params = {"calendarId": cid, "eventId": event_id}
    res = run_gws_command(["calendar", "events", "delete", "--params", json.dumps(params)])
    if "error" in res:
        print(f"刪除失敗: {res['error']}")
        return False
    print(f"✅ 成功自「{cal_name}」行事曆刪除事件 (ID: {event_id})")
    return True

def _is_date_time_str(s: str) -> bool:
    s = s.strip()
    return bool("-" in s and (len(s) == 10 or ":" in s or "T" in s))

def main():
    if len(sys.argv) < 2:
        print("Usage: calendar_tool.py [calendars | list [cal_name] <today|week|upcoming|YYYY-MM-DD> | add [cal_name] <title> <start> [end] [location] | delete [cal_name] <event_id>]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd in ("calendars", "list_cals"):
        list_calendars()
    elif cmd == "list":
        date_keywords = ("today", "week", "upcoming")
        arg2 = sys.argv[2] if len(sys.argv) > 2 else ""
        is_date_arg = False
        if arg2:
            if arg2 in date_keywords or (len(arg2) == 10 and arg2.count("-") == 2):
                is_date_arg = True

        if is_date_arg:
            cal = "待辦事項"
            date_filter = arg2
        else:
            cal = arg2 or "待辦事項"
            date_filter = sys.argv[3] if len(sys.argv) > 3 else "today"
        list_events(cal, date_filter)
    elif cmd == "add":
        args = sys.argv[2:]
        recurrence = None
        dry_run = False
        
        # 提取選項
        if "--dry-run" in args:
            dry_run = True
            args.remove("--dry-run")
        if "--recurrence" in args:
            idx = args.index("--recurrence")
            if idx + 1 < len(args):
                rrule_val = args[idx + 1]
                recurrence = [rrule_val]
                del args[idx:idx + 2]

        if len(args) < 2:
            print("Usage: calendar_tool.py add [cal_name] <title> <start_time> [end_time] [location] [--recurrence <RRULE>] [--dry-run]")
            sys.exit(1)
        # 若第 2 個參數即為日期時間字串，表示第 1 個參數是 title，日曆預設為「待辦事項」
        if len(args) >= 2 and _is_date_time_str(args[1]):
            cal = "待辦事項"
            title = args[0]
            start = args[1]
            end = args[2] if len(args) > 2 and args[2].lower() != "none" else None
            loc = args[3] if len(args) > 3 else None
        else:
            cal = args[0]
            title = args[1]
            start = args[2] if len(args) > 2 else ""
            end = args[3] if len(args) > 3 and args[3].lower() != "none" else None
            loc = args[4] if len(args) > 4 else None
        add_event(cal, title, start, end, loc, dry_run=dry_run, recurrence=recurrence)
    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("Usage: calendar_tool.py delete [cal_name] <event_id>")
            sys.exit(1)
        if len(sys.argv) >= 4:
            cal = sys.argv[2]
            eid = sys.argv[3]
        else:
            cal = "待辦事項"
            eid = sys.argv[2]
        delete_event(cal, eid)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
