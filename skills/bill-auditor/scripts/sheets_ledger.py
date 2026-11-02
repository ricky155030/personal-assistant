#!/usr/bin/env python3
"""
Google Sheets Master Ledger Synchronizer
負責將信用卡帳單與逐筆消費明細同步至 Google Sheets 主帳本
"""

import sys
import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

# 引用共用的安全 gws 工具
BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.gws_client import run_gws_command

SHEET_CONFIG_PATH = BASE_DIR / "data" / "bills" / "sheet_config.json"
LEDGER_TITLE = "個人與家庭信用卡財務總帳"

# 標頭定義
HEADERS = {
    "各月帳單概覽": ["帳單月份", "銀行名稱", "結帳日期", "繳款截止日", "本期應繳總額", "扣繳狀態", "扣繳帳戶", "自動扣繳", "備註"],
    "逐筆消費明細": ["消費日", "入帳日", "銀行", "卡號末四碼", "特店交易說明", "幣別", "外幣金額", "新臺幣金額", "分類標籤", "指紋代碼"],
    "固定訂閱追蹤": ["訂閱服務名稱", "分類類別", "每期金額", "扣款銀行", "卡號末四碼", "最近扣款日", "扣繳週期"]
}


def normalize_month(val: Any) -> str:
    """將月份格式正規化為 YYYY-MM (例如 2026-9 -> 2026-09)"""
    if not val or not isinstance(val, str):
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})$", val.strip())
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return val.strip()


def normalize_date(val: Any, ref_year: str = "2026") -> str:
    """將民國年 (115/xx/xx 或 0115/xx/xx) 或簡寫月日 (08/xx, 09/xx) 或各式日期正規化為 YYYY/MM/DD"""
    if not val or not isinstance(val, str):
        return ""
    v = val.strip().replace("-", "/").replace(".", "/")
    
    # 1. 民國年格式: 115/09/22 或 0115/09/22
    m_roc = re.match(r'^(?:0)?(\d{2,3})/(\d{1,2})/(\d{1,2})$', v)
    if m_roc:
        roc_y = int(m_roc.group(1))
        m = int(m_roc.group(2))
        d = int(m_roc.group(3))
        y = roc_y + 1911 if roc_y < 1900 else roc_y
        return f"{y:04d}/{m:02d}/{d:02d}"
        
    # 2. 西元年完整格式: 2026/09/05 或 2026/9/5
    m_west = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', v)
    if m_west:
        y = int(m_west.group(1))
        m = int(m_west.group(2))
        d = int(m_west.group(3))
        return f"{y:04d}/{m:02d}/{d:02d}"
        
    # 3. 簡寫月日格式: 08/15, 9/5, 10/12
    m_mmdd = re.match(r'^(\d{1,2})/(\d{1,2})$', v)
    if m_mmdd:
        m = int(m_mmdd.group(1))
        d = int(m_mmdd.group(2))
        if 1 <= m <= 12 and 1 <= d <= 31:
            return f"{ref_year}/{m:02d}/{d:02d}"
            
    return v


def apply_header_styles(sheet_id: str):
    """為三大分頁套用專業標頭樣式：深藍背景、白字粗體、凍結第 1 列、置中"""
    meta = run_gws_command(["sheets", "spreadsheets", "get", "--params", json.dumps({"spreadsheetId": sheet_id})])
    sheets = meta.get("sheets", [])
    requests = []

    header_color = {"red": 0.12, "green": 0.23, "blue": 0.54}  # 深海軍藍 #1E3A8A
    white_text = {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True, "fontSize": 10}

    for s in sheets:
        sid = s["properties"]["sheetId"]
        # 1. 凍結第 1 列
        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sid,
                    "gridProperties": {"frozenRowCount": 1}
                },
                "fields": "gridProperties.frozenRowCount"
            }
        })
        # 2. 標頭深藍底白字
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": 0,
                    "endRowIndex": 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": header_color,
                        "textFormat": white_text,
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
            }
        })
        # 3. 標頭列高 36px
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sid,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1
                },
                "properties": {"pixelSize": 36},
                "fields": "pixelSize"
            }
        })

    # 4. 全欄位對齊與格式規範
    sheets_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in sheets}
    tx_sid = sheets_map.get("逐筆消費明細")
    overview_sid = sheets_map.get("各月帳單概覽")
    sub_sid = sheets_map.get("固定訂閱追蹤")

    def make_align_request(sid, col_idx, align, num_format=None):
        cell_format = {
            "horizontalAlignment": align,
            "verticalAlignment": "MIDDLE"
        }
        fields = "userEnteredFormat(horizontalAlignment,verticalAlignment"
        if num_format:
            cell_format["numberFormat"] = num_format
            fields += ",numberFormat"
        fields += ")"
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1
                },
                "cell": {"userEnteredFormat": cell_format},
                "fields": fields
            }
        }

    date_fmt = {"type": "DATE", "pattern": "yyyy/mm/dd"}

    if tx_sid is not None:
        requests.append(make_align_request(tx_sid, 0, "CENTER", date_fmt))  # 消費日
        requests.append(make_align_request(tx_sid, 1, "CENTER", date_fmt))  # 入帳日
        requests.append(make_align_request(tx_sid, 2, "CENTER"))            # 銀行
        requests.append(make_align_request(tx_sid, 3, "CENTER"))            # 卡號末四碼
        requests.append(make_align_request(tx_sid, 4, "LEFT"))              # 特店說明
        requests.append(make_align_request(tx_sid, 5, "CENTER"))            # 幣別
        requests.append(make_align_request(tx_sid, 6, "RIGHT"))             # 外幣金額
        requests.append(make_align_request(tx_sid, 7, "RIGHT"))             # 新臺幣金額
        requests.append(make_align_request(tx_sid, 8, "CENTER"))            # 分類標籤

    if overview_sid is not None:
        for c in [0, 1]:
            requests.append(make_align_request(overview_sid, c, "CENTER"))
        requests.append(make_align_request(overview_sid, 2, "CENTER", date_fmt)) # 結帳日
        requests.append(make_align_request(overview_sid, 3, "CENTER", date_fmt)) # 截止日
        requests.append(make_align_request(overview_sid, 4, "RIGHT"))            # 應繳總額
        for c in [5, 6, 7]:
            requests.append(make_align_request(overview_sid, c, "CENTER"))

    if sub_sid is not None:
        requests.append(make_align_request(sub_sid, 0, "LEFT"))
        requests.append(make_align_request(sub_sid, 1, "CENTER"))
        requests.append(make_align_request(sub_sid, 2, "RIGHT"))
        requests.append(make_align_request(sub_sid, 3, "CENTER"))
        requests.append(make_align_request(sub_sid, 4, "CENTER"))
        requests.append(make_align_request(sub_sid, 5, "CENTER", date_fmt))
        requests.append(make_align_request(sub_sid, 6, "CENTER"))

    if requests:
        run_gws_command([
            "sheets", "spreadsheets", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": sheet_id}),
            "--json", json.dumps({"requests": requests})
        ])


def get_or_create_ledger_sheet() -> str:
    """取得或自動建立信用卡財務總帳 Google Sheet，回傳 Spreadsheet ID"""
    SHEET_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SHEET_CONFIG_PATH.exists():
        try:
            cfg = json.loads(SHEET_CONFIG_PATH.read_text(encoding="utf-8"))
            if cfg.get("spreadsheet_id"):
                return cfg["spreadsheet_id"]
        except Exception:
            pass

    # 1. 搜尋雲端是否已有該試算表 (支援包含 LEDGER_TITLE)
    res = run_gws_command([
        "drive", "files", "list",
        "--params", json.dumps({
            "q": f"name contains '信用卡財務總帳' and trashed = false",
            "fields": "files(id, name)"
        })
    ])
    files = res.get("files", [])
    if files:
        sheet_id = files[0]["id"]
        SHEET_CONFIG_PATH.write_text(json.dumps({"spreadsheet_id": sheet_id, "name": files[0]["name"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return sheet_id

    # 2. 自動新建試算表 (含 3 個分頁與表頭標題)
    body = {
        "properties": {
            "title": LEDGER_TITLE
        },
        "sheets": [
            {
                "properties": {
                    "title": "各月帳單概覽",
                    "gridProperties": {"frozenRowCount": 1}
                }
            },
            {
                "properties": {
                    "title": "逐筆消費明細",
                    "gridProperties": {"frozenRowCount": 1}
                }
            },
            {
                "properties": {
                    "title": "固定訂閱追蹤",
                    "gridProperties": {"frozenRowCount": 1}
                }
            }
        ]
    }
    create_res = run_gws_command([
        "sheets", "spreadsheets", "create",
        "--json", json.dumps(body)
    ])
    sheet_id = create_res.get("spreadsheetId")
    if not sheet_id:
        raise RuntimeError(f"無法建立 Google Sheet: {create_res}")

    # 3. 初始化三大分頁之表頭
    init_headers = [
        {"range": "'各月帳單概覽'!A1:I1", "values": [HEADERS["各月帳單概覽"]]},
        {"range": "'逐筆消費明細'!A1:J1", "values": [HEADERS["逐筆消費明細"]]},
        {"range": "'固定訂閱追蹤'!A1:G1", "values": [HEADERS["固定訂閱追蹤"]]}
    ]
    run_gws_command([
        "sheets", "spreadsheets", "values", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": sheet_id}),
        "--json", json.dumps({"valueInputOption": "USER_ENTERED", "data": init_headers})
    ])
    apply_header_styles(sheet_id)

    SHEET_CONFIG_PATH.write_text(json.dumps({"spreadsheet_id": sheet_id, "name": LEDGER_TITLE}, ensure_ascii=False, indent=2), encoding="utf-8")
    return sheet_id


def ensure_headers_exist(sheet_id: str):
    """確保三張表的第 1 列皆有正確的標頭，若被意外清空則自動復原"""
    headers_to_restore = []
    for sheet_name, header_cols in HEADERS.items():
        v = run_gws_command([
            "sheets", "spreadsheets", "values", "get",
            "--params", json.dumps({"spreadsheetId": sheet_id, "range": f"'{sheet_name}'!A1:B1"})
        ]).get("values", [])
        if not v or v[0][0] != header_cols[0]:
            headers_to_restore.append({
                "range": f"'{sheet_name}'!A1:{chr(64 + len(header_cols))}1",
                "values": [header_cols]
            })

    if headers_to_restore:
        run_gws_command([
            "sheets", "spreadsheets", "values", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": sheet_id}),
            "--json", json.dumps({"valueInputOption": "USER_ENTERED", "data": headers_to_restore})
        ])
        apply_header_styles(sheet_id)


def sync_month_to_sheets(statement_month: str, banks_data: Dict[str, Any], processed_db: Dict[str, Any]) -> dict:
    """將指定月份的所有銀行帳單與明細增量同步至 Google Sheets (以雲端現有指紋為最高準則進行雙向比對去重)"""
    sheet_id = get_or_create_ledger_sheet()
    ensure_headers_exist(sheet_id)
    target_norm = normalize_month(statement_month)
    
    # 1. 讀取現有的「各月帳單概覽」資料以進行 Upsert (從 A2 開始讀取)
    overview_res = run_gws_command([
        "sheets", "spreadsheets", "values", "get",
        "--params", json.dumps({"spreadsheetId": sheet_id, "range": "'各月帳單概覽'!A2:I200"})
    ])
    existing_rows = overview_res.get("values", [])
    
    row_map = {}
    for idx, r in enumerate(existing_rows):
        if len(r) >= 2:
            m = normalize_month(r[0])
            key = f"{m}_{r[1]}"  # e.g. "2026-10_台新銀行"
            row_map[key] = idx + 2  # Row number (1-indexed, starting after header at 2)

    updates = []
    appends_overview = []

    for bcode, bdata in banks_data.items():
        bname = bdata.get("bank_name", bcode)
        month = normalize_month(bdata.get("statement_month") or statement_month)
        
        # 僅同步與目標月份相同的帳單，避免跨月雜訊重複寫入
        if month != target_norm:
            continue
            
        key = f"{month}_{bname}"

        due_amt = bdata.get("due_amount", 0)
        stmt_d = normalize_date(bdata.get("statement_date", ""))
        due_d = normalize_date(bdata.get("due_date", ""))
        auto_pay = "是" if bdata.get("auto_pay") else "否"
        acc = bdata.get("auto_pay_account", "")
        
        # 判定扣繳狀態
        is_paid = bdata.get("is_paid", False) or processed_db.get("paid_bills", {}).get(key, False)
        status = "✅ 已扣款" if is_paid else "⏳ 待扣繳"
        
        row_values = [month, bname, stmt_d, due_d, f"NT$ {due_amt:,}", status, acc, auto_pay, "自動同步"]

        if key in row_map:
            row_num = row_map[key]
            updates.append({
                "range": f"'各月帳單概覽'!A{row_num}:I{row_num}",
                "values": [row_values]
            })
        else:
            appends_overview.append(row_values)
            row_map[key] = len(existing_rows) + len(appends_overview) + 1

    if updates:
        run_gws_command([
            "sheets", "spreadsheets", "values", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": sheet_id}),
            "--json", json.dumps({"valueInputOption": "USER_ENTERED", "data": updates})
        ])

    if appends_overview:
        run_gws_command([
            "sheets", "spreadsheets", "values", "append",
            "--params", json.dumps({
                "spreadsheetId": sheet_id,
                "range": "'各月帳單概覽'!A1",
                "valueInputOption": "USER_ENTERED"
            }),
            "--json", json.dumps({"values": appends_overview})
        ])

    # 2. 逐筆明細增量 Append (以 Google Sheet 雲端現有指紋為最高準則進行雙向比對去重)
    sheet_fps_res = run_gws_command([
        "sheets", "spreadsheets", "values", "get",
        "--params", json.dumps({"spreadsheetId": sheet_id, "range": "'逐筆消費明細'!J2:J10000"})
    ])
    existing_sheet_fps = {row[0].strip() for row in sheet_fps_res.get("values", []) if row and row[0]}
    
    # 整合本機已處理的指紋快取
    all_existing_hashes = existing_sheet_fps.union(set(processed_db.get("transaction_hashes", [])))

    from collections import defaultdict
    bill_seen = defaultdict(int)
    new_tx_rows = []

    for bcode, bdata in banks_data.items():
        bname = bdata.get("bank_name", bcode)
        month = normalize_month(bdata.get("statement_month") or statement_month)
        if month != target_norm:
            continue

        for tx in bdata.get("transactions", []):
            d = normalize_date(tx.get("date", ""))
            post_d = normalize_date(tx.get("post_date", ""))
            desc = tx.get("description", "")
            amt = tx.get("amount", 0)
            curr = tx.get("currency", "TWD")
            f_amt = tx.get("foreign_amount") or ""
            card = tx.get("card_last4", "")

            # 產生唯一指紋 (支援同帳單同條件序號，避免合法同日同額手續費被漏記)
            base_hash = f"{bname}_{d}_{amt}_{desc[:15]}"
            bill_seen[base_hash] += 1
            occ = bill_seen[base_hash]
            tx_hash = f"{base_hash}#{occ}" if occ > 1 else base_hash

            if tx_hash in all_existing_hashes:
                continue

            all_existing_hashes.add(tx_hash)
            new_tx_rows.append([
                d, post_d, bname, card, desc, curr, f_amt, f"NT$ {amt:,}", "日常消費", tx_hash
            ])

    if new_tx_rows:
        run_gws_command([
            "sheets", "spreadsheets", "values", "append",
            "--params", json.dumps({
                "spreadsheetId": sheet_id,
                "range": "'逐筆消費明細'!A1",
                "valueInputOption": "USER_ENTERED"
            }),
            "--json", json.dumps({"values": new_tx_rows})
        ])

    processed_db["transaction_hashes"] = list(all_existing_hashes)
    return {
        "spreadsheet_id": sheet_id,
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        "new_transactions_count": len(new_tx_rows)
    }


def sync_subscriptions_to_sheets(subscriptions: List[Dict[str, Any]]):
    """將辨識出的固定訂閱清單寫入『固定訂閱追蹤』分頁（永遠保留 A1 標頭）"""
    sheet_id = get_or_create_ledger_sheet()
    ensure_headers_exist(sheet_id)

    rows = []
    for s in subscriptions:
        rows.append([
            s["name"],
            s["category"],
            f"NT$ {s['latest_amount']:,}",
            s["bank"],
            s["card_last4"],
            normalize_date(s["latest_date"]),
            "每月固定"
        ])

    if rows:
        # 清除舊有資料 (A2 開始清除，絕對保留 A1 表頭)
        run_gws_command([
            "sheets", "spreadsheets", "values", "clear",
            "--params", json.dumps({"spreadsheetId": sheet_id, "range": "'固定訂閱追蹤'!A2:G200"}),
            "--json", "{}"
        ])
        # 寫入最新訂閱清單
        run_gws_command([
            "sheets", "spreadsheets", "values", "append",
            "--params", json.dumps({
                "spreadsheetId": sheet_id,
                "range": "'固定訂閱追蹤'!A1",
                "valueInputOption": "USER_ENTERED"
            }),
            "--json", json.dumps({"values": rows})
        ])


def clean_and_deduplicate_ledger() -> dict:
    """全量重新掃描本機所有月份快取，完全去重並以標準格式寫入 Google Sheets 主帳本"""
    from collections import defaultdict
    sheet_id = get_or_create_ledger_sheet()
    ensure_headers_exist(sheet_id)

    bills_dir = BASE_DIR / "data" / "bills"
    month_files = sorted(bills_dir.glob("202*.json"))

    overview_rows = []
    all_hashes = []

    # 1. 各月帳單概覽
    for p in month_files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        m_str = normalize_month(d.get("statement_month", ""))
        for bcode, b in d.get("banks", {}).items():
            b_m = normalize_month(b.get("statement_month") or m_str)
            if b_m != m_str:
                continue
            bname = b.get("bank_name", bcode)
            due_amt = b.get("due_amount", 0)
            stmt_d = normalize_date(b.get("statement_date", ""))
            due_d = normalize_date(b.get("due_date", ""))
            auto_pay = "是" if b.get("auto_pay") else "否"
            acc = b.get("auto_pay_account", "")
            is_paid = b.get("is_paid", False) or ("09" in m_str)
            status = "✅ 已扣款" if is_paid else "⏳ 待扣繳"
            overview_rows.append([m_str, bname, stmt_d, due_d, f"NT$ {due_amt:,}", status, acc, auto_pay, "自動同步"])

    # 2. 逐筆消費明細 (按日期排序)
    raw_tx_items = []
    for p in month_files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        m_str = normalize_month(d.get("statement_month", ""))
        for bcode, b in d.get("banks", {}).items():
            b_m = normalize_month(b.get("statement_month") or m_str)
            if b_m != m_str:
                continue
            bname = b.get("bank_name", bcode)
            bill_seen = defaultdict(int)
            for tx in b.get("transactions", []):
                date_str = normalize_date(tx.get("date", ""))
                post_str = normalize_date(tx.get("post_date", ""))
                desc = tx.get("description", "")
                amt = tx.get("amount", 0)
                curr = tx.get("currency", "TWD")
                f_amt = tx.get("foreign_amount") or ""
                card = tx.get("card_last4", "")
                
                base_hash = f"{bname}_{date_str}_{amt}_{desc[:15]}"
                bill_seen[base_hash] += 1
                occ = bill_seen[base_hash]
                tx_hash = f"{base_hash}#{occ}" if occ > 1 else base_hash
                all_hashes.append(tx_hash)
                raw_tx_items.append((
                    date_str, [date_str, post_str, bname, card, desc, curr, f_amt, f"NT$ {amt:,}", "日常消費", tx_hash]
                ))

    # 按消費日升冪排序
    raw_tx_items.sort(key=lambda x: x[0])
    tx_rows = [item[1] for item in raw_tx_items]

    # 清除舊有資料 (A2 開始)
    run_gws_command(["sheets", "spreadsheets", "values", "clear", "--params", json.dumps({"spreadsheetId": sheet_id, "range": "'各月帳單概覽'!A2:I200"}), "--json", "{}" ])
    run_gws_command(["sheets", "spreadsheets", "values", "clear", "--params", json.dumps({"spreadsheetId": sheet_id, "range": "'逐筆消費明細'!A2:J10000"}), "--json", "{}" ])

    # 批量寫入
    run_gws_command([
        "sheets", "spreadsheets", "values", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": sheet_id}),
        "--json", json.dumps({
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": f"'各月帳單概覽'!A2:I{len(overview_rows)+1}", "values": overview_rows},
                {"range": f"'逐筆消費明細'!A2:J{len(tx_rows)+1}", "values": tx_rows}
            ]
        })
    ])

    apply_header_styles(sheet_id)

    db_path = bills_dir / "processed_bills.json"
    if db_path.exists():
        try:
            db = json.loads(db_path.read_text(encoding="utf-8"))
            db["transaction_hashes"] = all_hashes
            db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return {
        "overview_count": len(overview_rows),
        "transactions_count": len(tx_rows),
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    }
