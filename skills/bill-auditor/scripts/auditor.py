#!/usr/bin/env python3
"""
Credit Card Bill Auditor & Ledger CLI (auditor.py)
專為個人財務設計的信用卡帳單自動審計與 Google Sheets 總帳同步工具
"""

import sys
import os
import json
import base64
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# 載入專案路徑與環境變數
BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from common.venv_guard import ensure_venv
    ensure_venv()
except Exception:
    pass

load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.gws_client import run_gws_command
from parsers import get_parser_for_email, PARSERS
from subscription_tracker import analyze_subscriptions
from sheets_ledger import sync_month_to_sheets, sync_subscriptions_to_sheets, get_or_create_ledger_sheet, clean_and_deduplicate_ledger

DATA_DIR = BASE_DIR / "data" / "bills"
PROCESSED_DB_PATH = DATA_DIR / "processed_bills.json"

USER_ID_NO = os.getenv("USER_ID_NO", "").strip().upper()
USER_BIRTH_MMDD = os.getenv("USER_BIRTH_MMDD", "").strip()

def load_processed_db() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PROCESSED_DB_PATH.exists():
        try:
            return json.loads(PROCESSED_DB_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed_messages": {}, "paid_bills": {}, "transaction_hashes": []}

def save_processed_db(db: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def get_month_cache_path(statement_month: str) -> Path:
    return DATA_DIR / f"{statement_month}.json"

def fetch_email_attachments(msg_id: str) -> list:
    """遞迴找出信件內的所有附件"""
    res = run_gws_command(["gmail", "users", "messages", "get", "--params", json.dumps({"userId": "me", "id": msg_id})])
    payload = res.get("payload", {})
    atts = []

    def walk(part):
        fname = part.get("filename")
        att_id = part.get("body", {}).get("attachmentId")
        if fname and att_id:
            atts.append({"filename": fname, "attachmentId": att_id, "size": part.get("body", {}).get("size")})
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    return atts, res

def sync_month(statement_month: str = "", force: bool = False) -> dict:
    """同步指定月份（如 2026-10）之帳單信件並解析"""
    if not statement_month:
        now = datetime.now()
        statement_month = now.strftime("%Y-%m")

    cache_path = get_month_cache_path(statement_month)
    if cache_path.exists() and not force:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    year, month = statement_month.split("-")
    y_int = int(year)
    m_int = int(month)

    # 計算信件搜尋日期區間 (約前月 25 號到次月 5 號)
    before_m = m_int + 1 if m_int < 12 else 1
    before_y = y_int if m_int < 12 else y_int + 1
    query = f"subject:(電子帳單 OR 信用卡帳單 OR 電子對帳單 OR 繳款通知 OR 入帳通知) after:{year}/{m_int:02d}/01 before:{before_y}/{before_m:02d}/05"

    search_res = run_gws_command(["gmail", "users", "messages", "list", "--params", json.dumps({"userId": "me", "q": query, "maxResults": 35})])
    messages = search_res.get("messages", [])
    print(f"[Auditor] 🔍 檢索 {statement_month} 帳單郵件，找到 {len(messages)} 則相關通知...")

    processed_db = load_processed_db()
    banks_data = {}

    for m in messages:
        msg_id = m["id"]
        atts, email_full = fetch_email_attachments(msg_id)
        payload = email_full.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        email_meta = {
            "id": msg_id,
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": email_full.get("snippet", ""),
            "body": email_full.get("body", "")
        }

        parser = get_parser_for_email(email_meta, user_id=USER_ID_NO, birth_mmdd=USER_BIRTH_MMDD)
        if not parser:
            continue

        # 檢查是否有 PDF 附件
        pdf_bytes = None
        for a in atts:
            fname = a["filename"].lower()
            if fname.endswith(".pdf") and not any(k in fname for k in ["繳款", "payment", "slip"]):
                att_res = run_gws_command(["gmail", "users", "messages", "attachments", "get", "--params", json.dumps({"userId": "me", "messageId": msg_id, "id": a["attachmentId"]})])
                if "data" in att_res:
                    pdf_bytes = base64.urlsafe_b64decode(att_res["data"] + "===")
                    break

        if not pdf_bytes and atts:
            # 備用首個 PDF
            for a in atts:
                if a["filename"].lower().endswith(".pdf"):
                    att_res = run_gws_command(["gmail", "users", "messages", "attachments", "get", "--params", json.dumps({"userId": "me", "messageId": msg_id, "id": a["attachmentId"]})])
                    if "data" in att_res:
                        pdf_bytes = base64.urlsafe_b64decode(att_res["data"] + "===")
                        break

        try:
            parsed = parser.parse(email_meta, pdf_bytes)
            if parsed and parsed.get("due_amount", 0) > 0:
                bcode = parsed["bank_code"]
                # 嚴格核對帳單月份，防止跨月雜訊（例如上期扣款通知於本月收到）混入本期快取
                parsed_m = parsed.get("statement_month")
                if parsed_m and parsed_m != statement_month:
                    continue
                # 若該銀行已有資料，且新資料包含更多明細則覆蓋或更新
                if bcode not in banks_data or (len(parsed.get("transactions", [])) > len(banks_data[bcode].get("transactions", []))):
                    banks_data[bcode] = parsed
                    print(f"  ✅ 成功解析 {parsed['bank_name']}: NT${parsed['due_amount']:,} (交易筆數: {len(parsed['transactions'])})")
        except Exception as e:
            print(f"  ⚠️ 解析 {parser.bank_name} 出錯: {e}")

    # 計算總額
    total_due = sum(b.get("due_amount", 0) for b in banks_data.values())
    month_data = {
        "statement_month": statement_month,
        "generated_at": datetime.now().isoformat(),
        "total_due_amount": total_due,
        "banks": banks_data
    }

    cache_path.write_text(json.dumps(month_data, ensure_ascii=False, indent=2), encoding="utf-8")
    save_processed_db(processed_db)
    return month_data


def show_summary(statement_month: str = ""):
    """輸出易讀的摘要報表"""
    data = sync_month(statement_month)
    m = data.get("statement_month")
    total = data.get("total_due_amount", 0)
    banks = data.get("banks", {})

    print(f"\n==================================================")
    print(f"💳 【{m} 信用卡帳單彙總報告】")
    print(f"💰 本月合計應繳總額：NT$ {total:,}")
    print(f"==================================================")
    print(f"{'銀行名稱':<10} | {'應繳總額':<12} | {'扣繳日/截止日':<12} | {'自動扣繳':<8} | {'備註'}")
    print(f"--------------------------------------------------")

    for bcode, b in banks.items():
        bname = b.get("bank_name", bcode)
        amt = f"NT$ {b.get('due_amount', 0):,}"
        due = b.get("due_date", "-")
        auto = "✅ 是" if b.get("auto_pay") else "❌ 否"
        acc = b.get("auto_pay_account", "")
        note = f"帳號:{acc}" if acc else ""
        print(f"{bname:<10} | {amt:<12} | {due:<12} | {auto:<8} | {note}")

    print(f"==================================================\n")


def show_subscriptions():
    """輸出固定訂閱雷達"""
    # 讀取最近 2 個月的本地快取
    all_txs = []
    for f in sorted(DATA_DIR.glob("*.json")):
        if f.name.startswith("202"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                for b in d.get("banks", {}).values():
                    all_txs.extend(b.get("transactions", []))
            except Exception:
                pass

    subs = analyze_subscriptions(all_txs)
    print(f"\n==================================================")
    print(f"🔄 【固定訂閱與公用事業扣款雷達】")
    print(f"==================================================")
    print(f"{'服務名稱':<22} | {'最近金額':<10} | {'扣款銀行/卡別':<12} | {'最近扣款日'}")
    print(f"--------------------------------------------------")
    for s in subs:
        print(f"{s['name']:<22} | NT${s['latest_amount']:<8} | {s['bank']} ({s['card_last4']}) | {s['latest_date']}")
    print(f"==================================================\n")


def sync_to_google_sheets(statement_month: str = ""):
    """手動全量同步至 Google Sheets"""
    data = sync_month(statement_month)
    processed_db = load_processed_db()
    res = sync_month_to_sheets(data["statement_month"], data["banks"], processed_db)
    
    # 訂閱同步
    all_txs = []
    for b in data["banks"].values():
        all_txs.extend(b.get("transactions", []))
    subs = analyze_subscriptions(all_txs)
    if subs:
        sync_subscriptions_to_sheets(subs)

    save_processed_db(processed_db)
    print(f"\n✅ 成功同步至 Google Sheets 主帳本！")
    print(f"📊 新增消費明細：{res['new_transactions_count']} 筆")
    print(f"🌐 試算表連結：{res['spreadsheet_url']}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "summary"
    target_month = args[1] if len(args) > 1 else ""

    if cmd == "sync":
        sync_month(target_month, force=True)
    elif cmd == "summary":
        show_summary(target_month)
    elif cmd == "subscriptions":
        show_subscriptions()
    elif cmd == "sheets-sync":
        sync_to_google_sheets(target_month)
    elif cmd in ("dedup", "cleanup"):
        res = clean_and_deduplicate_ledger()
        print(f"\n✨ 成功完成全量財務總帳清洗與去重！")
        print(f"📊 帳單概覽：{res['overview_count']} 筆")
        print(f"💳 逐筆明細：{res['transactions_count']} 筆（按消費日排序，0 重複）")
        print(f"🌐 試算表連結：{res['spreadsheet_url']}\n")
    else:
        print("用法: auditor.py [sync | summary | subscriptions | sheets-sync | dedup] [YYYY-MM]")
