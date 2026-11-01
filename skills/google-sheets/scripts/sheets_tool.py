#!/usr/bin/env python3
"""
Google Sheets 專用安全查詢與操作工具 (Google Sheets Tool)
嚴格封裝環境變數以防護 gws 憑證靜默刪除問題。
支援直接傳入試算表 ID、完整網址或別名 (SHOPPING_SHEET_ID 或 SHEET_ALIASES_JSON)。
"""

import sys
import os
import re
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])

# 載入環境變數以讀取試算表別名
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"), override=False)
except Exception:
    pass

SHEET_ALIASES: Dict[str, str] = {
    "shopping": os.getenv("SHOPPING_SHEET_ID", ""),
    "待買": os.getenv("SHOPPING_SHEET_ID", ""),
    "待買清單": os.getenv("SHOPPING_SHEET_ID", ""),
}
try:
    custom_aliases = json.loads(os.getenv("SHEET_ALIASES_JSON", "{}"))
    if isinstance(custom_aliases, dict):
        SHEET_ALIASES.update(custom_aliases)
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

def resolve_sheet_id(name_or_id_or_url: str) -> str:
    """
    解析試算表標識符：
    1. 若為 Google Sheets 完整網址，自動擷取試算表 ID
    2. 若為別名（如 shopping、finance），從 SHEET_ALIASES 轉換
    3. 其餘情況直接返回原始 ID
    """
    if not name_or_id_or_url:
        return ""
    
    target = name_or_id_or_url.strip()
    
    # 支援直接解析 Google Sheets URL
    url_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", target)
    if url_match:
        return url_match.group(1)
        
    # 別名查詢
    lower_name = target.lower()
    if lower_name in SHEET_ALIASES and SHEET_ALIASES[lower_name]:
        return SHEET_ALIASES[lower_name]
        
    return target

def get_sheet_metadata(sheet_id: str) -> dict:
    """取得試算表整體中繼資料（標題、分頁清單與維度）"""
    sid = resolve_sheet_id(sheet_id)
    res = run_gws_command(["sheets", "spreadsheets", "get", "--params", json.dumps({"spreadsheetId": sid})])
    if "error" in res:
        return res
    properties = res.get("properties", {})
    sheets = res.get("sheets", [])
    sheet_tabs = []
    for s in sheets:
        sp = s.get("properties", {})
        grid = sp.get("gridProperties", {})
        sheet_tabs.append({
            "title": sp.get("title", ""),
            "sheet_id": sp.get("sheetId", 0),
            "rows": grid.get("rowCount", 0),
            "cols": grid.get("columnCount", 0)
        })
    return {
        "title": properties.get("title", ""),
        "spreadsheet_id": sid,
        "sheets": sheet_tabs
    }

def get_first_sheet_title(sheet_id: str) -> str:
    """動態取得試算表第一個工作表的名稱，避免工作表改名導致解析失敗"""
    sid = resolve_sheet_id(sheet_id)
    res = run_gws_command(["sheets", "spreadsheets", "get", "--params", json.dumps({"spreadsheetId": sid})])
    sheets = res.get("sheets", [])
    if sheets:
        return sheets[0].get("properties", {}).get("title", "工作表1")
    return "工作表1"

def get_sheet_values(sheet_id: str, range_name: Optional[str] = None) -> List[List[str]]:
    """讀取指定試算表工作表範圍之二維陣列值"""
    sid = resolve_sheet_id(sheet_id)
    if not range_name:
        title = get_first_sheet_title(sid)
        range_name = f"'{title}'!A1:Z500"
    params = {"spreadsheetId": sid, "range": range_name}
    res = run_gws_command(["sheets", "spreadsheets", "values", "get", "--params", json.dumps(params)])
    if "error" in res:
        print(f"Error: {res['error']}")
        return []
    return res.get("values", [])

def _is_fuzzy_match(kw: str, text: str) -> bool:
    """通用字串模糊比對：支援不區分大小寫、子字串、多關鍵字全匹配與字元分拆比對"""
    kw = kw.strip().lower()
    text = text.lower()
    if not kw or not text:
        return False
    if kw in text or text in kw:
        return True
    # 支援空格分詞 (如 "apple pie" 比對包含 apple 與 pie 的儲存格)
    tokens = kw.split()
    if len(tokens) > 1 and all(t in text for t in tokens):
        return True
    # 支援 2~4 字之短語字元涵蓋比對
    if 2 <= len(kw) <= 4 and all(c in text for c in kw):
        return True
    return False

def query_sheet(sheet_id: str, keyword: str):
    """在試算表中模糊搜尋關鍵字，依據試算表第一列標題通用輸出"""
    rows = get_sheet_values(sheet_id)
    if not rows:
        print("查無資料或試算表為空。")
        return
    
    headers = [str(h).strip() for h in rows[0]]
    matched = []
    kw = keyword.lower()
    
    for r_idx, r in enumerate(rows[1:], start=2):
        row_str = " ".join([str(c) for c in r]).lower()
        if kw in row_str or any(_is_fuzzy_match(kw, str(c)) for c in r):
            matched.append((r_idx, r))
            
    print(f"🔍 搜尋「{keyword}」共找到 {len(matched)} 筆結果：\n")
    for r_idx, row in matched:
        row_fields = []
        for c_idx, cell in enumerate(row):
            val = str(cell).strip()
            if not val or val == "—":
                continue
            h_name = headers[c_idx] if c_idx < len(headers) and headers[c_idx] else f"欄位{c_idx+1}"
            row_fields.append(f"{h_name}: {val}")
        content = " ｜ ".join(row_fields) if row_fields else " (空列)"
        print(f"• [第 {r_idx} 列] {content}")

def list_sheet(sheet_id: str, max_rows: int = 40):
    """通用列出試算表前 N 筆項目"""
    rows = get_sheet_values(sheet_id)
    if not rows:
        print("查無資料或試算表為空。")
        return
    headers = [str(h).strip() for h in rows[0]]
    display_rows = rows[1:max_rows+1]
    print(f"📋 試算表內容 (共 {len(rows)-1} 筆，顯示前 {len(display_rows)} 筆)：\n")
    for r_idx, row in enumerate(display_rows, start=2):
        row_fields = []
        for c_idx, cell in enumerate(row):
            val = str(cell).strip()
            if not val or val == "—":
                continue
            h_name = headers[c_idx] if c_idx < len(headers) and headers[c_idx] else f"欄位{c_idx+1}"
            row_fields.append(f"{h_name}: {val}")
        content = " ｜ ".join(row_fields) if row_fields else " (空列)"
        print(f"• [第 {r_idx} 列] {content}")

def append_sheet_rows(sheet_id: str, rows: List[List[str]], range_name: Optional[str] = None) -> dict:
    """追加資料行至試算表尾端"""
    sid = resolve_sheet_id(sheet_id)
    if not range_name:
        title = get_first_sheet_title(sid)
        range_name = f"'{title}'!A1"
    params = {
        "spreadsheetId": sid,
        "range": range_name,
        "valueInputOption": "USER_ENTERED"
    }
    body = {
        "values": rows
    }
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "append",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])
    return res

def update_sheet_values(sheet_id: str, range_name: str, values: List[List[Any]]) -> dict:
    """更新指定範圍之儲存格數值"""
    sid = resolve_sheet_id(sheet_id)
    params = {
        "spreadsheetId": sid,
        "range": range_name,
        "valueInputOption": "USER_ENTERED"
    }
    body = {
        "range": range_name,
        "majorDimension": "ROWS",
        "values": values
    }
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])
    return res

def batch_update_values(sheet_id: str, data: List[Dict[str, Any]]) -> dict:
    """以 Google Sheets values:batchUpdate 批次更新多個不同區域的資料"""
    sid = resolve_sheet_id(sheet_id)
    params = {
        "spreadsheetId": sid
    }
    body = {
        "valueInputOption": "USER_ENTERED",
        "data": data
    }
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "batchUpdate",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])
    return res

def _parse_input_json(raw: str):
    """支援直接傳 JSON 字串、@檔案路徑、或 - (stdin)"""
    raw = raw.strip()
    if raw.startswith("@"):
        with open(raw[1:], "r", encoding="utf-8") as f:
            return json.load(f)
    elif raw == "-":
        return json.load(sys.stdin)
    return json.loads(raw)

def main():
    if len(sys.argv) < 2:
        print("Usage: sheets_tool.py <read|get|search|query|list|metadata|append|batch_append|update|batch_update> <sheet_alias_or_url_or_id> [params...]")
        sys.exit(1)
        
    cmd = sys.argv[1].lower()
    
    # 判斷是否提供試算表 ID / 別名
    if len(sys.argv) > 2:
        sheet_id = sys.argv[2]
    else:
        sheet_id = SHEET_ALIASES.get("shopping") or ""
        if not sheet_id:
            print("請提供試算表別名、網址或 ID。例如：sheets_tool.py search shopping '關鍵字'")
            sys.exit(1)
            
    if cmd in ("query", "search"):
        kw = sys.argv[3] if len(sys.argv) > 3 else ""
        if not kw:
            print("請提供搜尋關鍵字。例如：sheets_tool.py search <sheet> '鮮奶'")
            sys.exit(1)
        query_sheet(sheet_id, kw)
    elif cmd == "list":
        max_rows = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 40
        list_sheet(sheet_id, max_rows)
    elif cmd in ("get", "read"):
        rng = sys.argv[3] if len(sys.argv) > 3 else None
        vals = get_sheet_values(sheet_id, rng)
        print(json.dumps(vals, ensure_ascii=False, indent=2))
    elif cmd == "metadata":
        meta = get_sheet_metadata(sheet_id)
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    elif cmd in ("append", "batch_append"):
        # 支援 append <sheet> <json> 或 append <sheet> <range> <json>
        if len(sys.argv) < 4:
            print("Usage: sheets_tool.py append <sheet_alias_or_url_or_id> [range] '<json_array_of_rows|@file>'")
            sys.exit(1)
        if len(sys.argv) >= 5:
            rng = sys.argv[3]
            raw_input = sys.argv[4]
        else:
            raw_input = sys.argv[3]
            rng = None
        rows = _parse_input_json(raw_input)
        if rows and isinstance(rows, list) and not isinstance(rows[0], list):
            rows = [rows]  # 單列轉二維陣列
        res = append_sheet_rows(sheet_id, rows, rng)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "update":
        if len(sys.argv) < 5:
            print("Usage: sheets_tool.py update <sheet_alias_or_url_or_id> <range> '<json_array_of_rows|@file>'")
            sys.exit(1)
        rng = sys.argv[3]
        rows = _parse_input_json(sys.argv[4])
        res = update_sheet_values(sheet_id, rng, rows)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "batch_update":
        if len(sys.argv) < 4:
            print("Usage: sheets_tool.py batch_update <sheet_alias_or_url_or_id> '<json_data_array|@file>'")
            sys.exit(1)
        data = _parse_input_json(sys.argv[3])
        res = batch_update_values(sheet_id, data)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print("Available commands: read, get, search, query, list, metadata, append, update, batch_update")
        sys.exit(1)

if __name__ == "__main__":
    main()
