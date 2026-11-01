#!/usr/bin/env python3
"""
家庭通用待買清單工具 (Universal Shopping List Tool) - 批次高效能升級版
管理獨立的《家庭通用待買清單》Google 試算表，支援生活採買、日本代購與母嬰待買，
並提供與《嬰兒用品與待產備品清單》的高效雙向批次原子化更新與勾銷連動。
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from common.gws_client import run_gws_command

sys.path.append(str(BASE_DIR / ".agents" / "skills" / "google-sheets" / "scripts"))

try:
    from sheets_tool import (
        get_sheet_values,
        resolve_sheet_id,
        _is_fuzzy_match,
        batch_update_values,
        _parse_input_json
    )
except ImportError:
    from .sheets_tool import (
        get_sheet_values,
        resolve_sheet_id,
        _is_fuzzy_match,
        batch_update_values,
        _parse_input_json
    )

SHOPPING_SHEET_ID = os.getenv("SHOPPING_SHEET_ID", "YOUR_SHOPPING_SHEET_ID")
CACHE_FILE = BASE_DIR / "data" / "shopping_cache.json"


def _load_cache() -> List[Dict[str, Any]]:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_cache(items: List[Dict[str, Any]]):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fetch_all_items(force_remote: bool = False) -> List[Dict[str, Any]]:
    """從 Google Sheets 讀取待買清單所有資料"""
    values = get_sheet_values(SHOPPING_SHEET_ID, "'待買清單'!A1:H200")
    if not values or len(values) <= 1:
        return []

    items = []
    for idx, row in enumerate(values[1:], start=2):
        status = row[0].strip() if len(row) > 0 else "待買"
        category = row[1].strip() if len(row) > 1 else "日常消耗"
        item = row[2].strip() if len(row) > 2 else ""
        qty = row[3].strip() if len(row) > 3 else "1"
        channel = row[4].strip() if len(row) > 4 else ""
        note = row[5].strip() if len(row) > 5 else ""
        created_at = row[6].strip() if len(row) > 6 else ""
        completed_at = row[7].strip() if len(row) > 7 else ""

        if item:
            items.append({
                "row_index": idx,
                "status": status,
                "category": category,
                "item": item,
                "qty": qty,
                "channel": channel,
                "note": note,
                "created_at": created_at,
                "completed_at": completed_at
            })

    _save_cache(items)
    return items


def batch_add_items(items: List[Any]) -> Dict[str, Any]:
    """
    批次新增多個待買項目至《通用待買清單》（單次 API 請求極速完成 ⚡）
    items 支援字串清單或物件清單：
    [{"item": "收納櫃", "category": "居家生活", "qty": "1組", "channel": "家具店", "note": "寶寶衣物"}]
    """
    if not items:
        return {"success": True, "added_count": 0, "items": []}

    now_str = datetime.now().strftime("%Y-%m-%d")
    status = "待買"
    new_rows = []
    added_list = []

    for it in items:
        if isinstance(it, str):
            it_name = it.strip()
            cat = "日常消耗"
            qty = "1"
            channel = ""
            note = ""
        elif isinstance(it, dict):
            it_name = (it.get("item") or it.get("name") or it.get("品項") or "").strip()
            cat = it.get("category") or it.get("類別") or "日常消耗"
            qty = str(it.get("qty") if it.get("qty") is not None else (it.get("數量") if it.get("數量") is not None else "1"))
            channel = it.get("channel") or it.get("location") or it.get("地點") or it.get("通路") or ""
            note = it.get("note") or it.get("備註") or ""
        else:
            continue

        if not it_name:
            continue

        new_rows.append([status, cat, it_name, qty, channel, note, now_str, ""])
        added_list.append({
            "item": it_name,
            "category": cat,
            "qty": qty,
            "channel": channel,
            "note": note
        })

    if not new_rows:
        return {"success": False, "message": "未提供有效品項名稱"}

    params = {
        "spreadsheetId": SHOPPING_SHEET_ID,
        "range": "'待買清單'!A1:H1",
        "valueInputOption": "USER_ENTERED",
        "insertDataOption": "INSERT_ROWS"
    }
    body = {"values": new_rows}
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "append",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])

    fetch_all_items(force_remote=True)
    return {
        "success": True,
        "added_count": len(added_list),
        "items": added_list,
        "result": res
    }


def add_item(
    item: str,
    category: str = "日常消耗",
    qty: str = "1",
    channel: str = "",
    note: str = ""
) -> Dict[str, Any]:
    """新增單一待買項目至《通用待買清單》（呼叫底層 batch_add_items）"""
    res = batch_add_items([{
        "item": item,
        "category": category,
        "qty": qty,
        "channel": channel,
        "note": note
    }])
    return {
        "success": res.get("success", False),
        "item": item,
        "category": category,
        "qty": qty,
        "channel": channel,
        "note": note,
        "result": res.get("result")
    }


def batch_complete_items(keywords: List[str]) -> Dict[str, Any]:
    """
    批次標記多個項目為已買（極速 ⚡）
    - 一次讀取待買快取
    - 記憶體中批量模糊比對與消歧義
    - 以 batchUpdate 一次性同步 Google Sheets（1 次 API 請求搞定所有項目）
    """
    if not keywords:
        return {
            "success": True,
            "completed_count": 0,
            "completed_shopping": [],
            "not_found": []
        }

    all_shopping = fetch_all_items()
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    shopping_updates = []
    completed_shopping = []
    not_found = []
    matched_shopping_rows = set()

    for kw in keywords:
        kw = str(kw).strip()
        if not kw:
            continue

        shopping_matched = False
        for it in all_shopping:
            if it["row_index"] in matched_shopping_rows:
                continue
            if it["status"] == "待買" and _is_fuzzy_match(kw, it["item"]):
                r_idx = it["row_index"]
                matched_shopping_rows.add(r_idx)
                shopping_updates.append({
                    "range": f"'待買清單'!A{r_idx}",
                    "values": [["已買"]]
                })
                shopping_updates.append({
                    "range": f"'待買清單'!H{r_idx}",
                    "values": [[now_time]]
                })
                completed_shopping.append({
                    "item": it["item"],
                    "category": it["category"],
                    "row": r_idx
                })
                shopping_matched = True
                break

        if not shopping_matched:
            not_found.append(kw)

    if shopping_updates:
        batch_update_values(SHOPPING_SHEET_ID, shopping_updates)

    # 刷新快取
    fetch_all_items(force_remote=True)

    return {
        "success": True,
        "completed_count": len(completed_shopping),
        "completed_shopping": completed_shopping,
        "not_found": not_found
    }


def complete_item(keyword: str) -> Dict[str, Any]:
    """標記單一項目為已買（底層呼叫 batch_complete_items，享極速優勢）"""
    res = batch_complete_items([keyword])
    if res.get("completed_count", 0) > 0:
        c_item = res["completed_shopping"][0]["item"]
        cat = res["completed_shopping"][0]["category"]
        return {
            "success": True,
            "item": c_item,
            "category": cat,
            "completed_details": res
        }
    else:
        return {"success": False, "message": f"在待買清單中找不到未購買的『{keyword}』。"}


def batch_operate(payload: Any) -> Dict[str, Any]:
    """
    全能統一批次操作（單一命令同時執行多項新增與多項勾銷銷帳）
    payload 範例：
    {
      "add": [
        {"item": "收納櫃", "category": "居家生活", "qty": "1組", "note": "收納寶寶備品"}
      ],
      "done": [
        "嬰兒黏著棉花棒", "一般用棉花棒", "stokke 新生兒套件", "麵包超人滾球塔"
      ]
    }
    """
    add_list = []
    done_list = []

    if isinstance(payload, dict):
        add_list = payload.get("add") or payload.get("append") or []
        done_list = payload.get("done") or payload.get("complete") or payload.get("finish") or []
    elif isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                action = entry.get("action", "").lower()
                if action in ("add", "append", "new"):
                    add_list.append(entry)
                elif action in ("done", "complete", "finish", "buy"):
                    kw = entry.get("keyword") or entry.get("item") or entry.get("name")
                    if kw:
                        done_list.append(kw)
                else:
                    add_list.append(entry)
            elif isinstance(entry, str):
                done_list.append(entry)

    res_add = batch_add_items(add_list) if add_list else {"success": True, "added_count": 0, "items": []}
    res_done = batch_complete_items(done_list) if done_list else {
        "success": True,
        "completed_count": 0,
        "completed_shopping": [],
        "baby_synced_count": 0,
        "synced_baby": [],
        "not_found": []
    }

    return {
        "success": res_add.get("success", True) and res_done.get("success", True),
        "added": res_add,
        "done": res_done
    }


def list_items(
    status: str = "待買",
    category: Optional[str] = None,
    channel: Optional[str] = None,
    query_kw: Optional[str] = None
) -> List[Dict[str, Any]]:
    """篩選與查詢待買項目"""
    all_items = fetch_all_items()
    filtered = []

    for it in all_items:
        if status != "all" and it["status"] != status:
            continue
        if category and category.lower() not in it["category"].lower():
            continue
        if channel and channel.lower() not in it["channel"].lower():
            continue
        if query_kw and not _is_fuzzy_match(query_kw, it["item"]):
            continue
        filtered.append(it)

    return filtered


def format_list_markdown(items: List[Dict[str, Any]]) -> str:
    """產生結構化漂亮的清單排版"""
    if not items:
        return "🛒 目前沒有任何待買項目！"

    # 依類別分組
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        cat = it["category"]
        main_cat = cat.split("(")[0].strip()
        groups.setdefault(main_cat, []).append(it)

    icons = {
        "日常消耗": "🥛",
        "食品生鮮": "🍎",
        "居家生活": "📦",
        "3C家電": "💻",
        "藥妝保健": "💊"
    }

    lines = [f"🛒 **家庭待買清單**（共 {len(items)} 項待買）：\n"]
    for cat, it_list in groups.items():
        icon = icons.get(cat, "📦")
        lines.append(f"{icon} **{cat}**（{len(it_list)}項）")
        for i, it in enumerate(it_list, start=1):
            qty_str = f" x{it['qty']}" if it['qty'] and it['qty'] != "1" else ""
            chan_str = f" @ {it['channel']}" if it['channel'] else ""
            note_str = f"（{it['note']}）" if it['note'] else ""
            lines.append(f"  {i}. **{it['item']}**{qty_str}{chan_str}{note_str}")
        lines.append("")

    lines.append(f"👉 試算表連結：https://docs.google.com/spreadsheets/d/{SHOPPING_SHEET_ID}/edit")
    return "\n".join(lines).strip()


def main():
    parser = argparse.ArgumentParser(description="家庭通用待買清單工具 (支援高效批次更新)")
    subparsers = parser.add_subparsers(dest="command", help="子指令")

    # list
    p_list = subparsers.add_parser("list", help="列出待買項目")
    p_list.add_argument("--status", default="待買", choices=["待買", "已買", "all"], help="篩選狀態")
    p_list.add_argument("--category", help="篩選類別")
    p_list.add_argument("--channel", help="篩選地點/通路")
    p_list.add_argument("--kw", help="關鍵字搜尋")
    p_list.add_argument("--json", action="store_true", help="輸出 JSON")

    # add
    p_add = subparsers.add_parser("add", help="新增單一待買項目")
    p_add.add_argument("item", help="品項名稱")
    p_add.add_argument("--category", "-c", default="日常消耗", help="類別 (日常消耗/食品生鮮/居家生活/3C家電/藥妝保健)")
    p_add.add_argument("--qty", "-q", default="1", help="數量或規格")
    p_add.add_argument("--channel", "-l", default="", help="購買通路/地點 (全聯/好市多/屈臣氏/電商)")
    p_add.add_argument("--note", "-n", default="", help="備註說明")

    # batch-add
    p_badd = subparsers.add_parser("batch-add", help="批次新增多個待買項目 (單次 API 請求)")
    p_badd.add_argument("payload", help="JSON 陣列字串、@檔案路徑或 -")

    # complete
    p_done = subparsers.add_parser("done", help="標記單一品項已購買")
    p_done.add_argument("keyword", help="品項關鍵字")

    # batch-done
    p_bdone = subparsers.add_parser("batch-done", help="批次標記多品項已買 (極速 ⚡)")
    p_bdone.add_argument("payload", help="關鍵字 JSON 陣列字串、@檔案路徑或 -")

    # batch
    p_batch = subparsers.add_parser("batch", help="統一全能批次操作 (同時新增多項與勾銷多項)")
    p_batch.add_argument("payload", help="包含 add 與 done 的 JSON 物件字串、@檔案路徑或 -")

    args = parser.parse_args()

    if args.command == "add":
        res = add_item(args.item, args.category, args.qty, args.channel, args.note)
        print(f"✅ 已成功將『{args.item}』加入待買清單！[類別: {args.category}]")

    elif args.command == "batch-add":
        data = _parse_input_json(args.payload)
        res = batch_add_items(data)
        if res.get("success"):
            print(f"✅ 批次新增成功！共加入 {res.get('added_count')} 項待買品項：\n")
            for it in res.get("items", []):
                chan_str = f"@ {it['channel']}" if it.get('channel') else ""
                note_str = f"({it['note']})" if it.get('note') else ""
                print(f"• [{it['category']}] {it['item']} x{it['qty']} {chan_str} {note_str}".strip())
        else:
            print(f"❌ 批次新增失敗: {res.get('message')}")
            sys.exit(1)

    elif args.command == "done":
        res = complete_item(args.keyword)
        if res.get("success"):
            print(f"🎉 已將『{res.get('item')}』標記為已買！")
        else:
            print(f"❌ {res.get('message')}")
            sys.exit(1)

    elif args.command == "batch-done":
        data = _parse_input_json(args.payload)
        res = batch_complete_items(data)
        print(f"🎉 批次採購勾銷完成！共劃掉 {res['completed_count']} 項待買：\n")
        if res["completed_shopping"]:
            for c in res["completed_shopping"]:
                print(f"• [{c['category']}] {c['item']} (第 {c['row']} 列 ➔ 已買)")
            print()
        if res["not_found"]:
            print(f"⚠️ 未在清單中找到的品項 ({len(res['not_found'])} 項): {', '.join(res['not_found'])}")
        print(json.dumps(res, ensure_ascii=False))

    elif args.command == "batch":
        data = _parse_input_json(args.payload)
        res = batch_operate(data)
        add_cnt = res["added"].get("added_count", 0)
        done_cnt = res["done"].get("completed_count", 0)
        print(f"⚡ 統一批次處理完畢！新增 {add_cnt} 項，勾銷 {done_cnt} 項待買：\n")
        if res["added"].get("items"):
            print("📝 【新增待買項目】：")
            for it in res["added"]["items"]:
                print(f"• [{it['category']}] {it['item']} x{it['qty']}")
            print()
        if res["done"].get("completed_shopping"):
            print("🎉 【待買清單銷帳勾銷】：")
            for c in res["done"]["completed_shopping"]:
                print(f"• [{c['category']}] {c['item']} ➔ 已買")
            print()
        print(json.dumps(res, ensure_ascii=False))

    elif args.command == "list" or not args.command:
        status = getattr(args, "status", "待買")
        cat = getattr(args, "category", None)
        chan = getattr(args, "channel", None)
        kw = getattr(args, "kw", None)
        items = list_items(status, cat, chan, kw)
        if getattr(args, "json", False):
            print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            print(format_list_markdown(items))


if __name__ == "__main__":
    main()
