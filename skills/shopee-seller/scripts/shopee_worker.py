#!/usr/bin/env python3
"""
蝦皮賣家自動上架背景工作腳本 (Shopee Worker)
由 TaskManager 以脫鉤獨立進程 (Detached Daemon) 啟動，職責：
1. 讀取 payload JSON（包含 category_id, title, price, images 等）
2. 執行平行圖床上傳與 Excel 批次檔生成
3. 透過 Chrome-Assistant (Playwright CDP) 自動匯入賣家中心
4. 跳轉未刊登草稿箱、核取「非限制級商品」並自動點擊「上架」
5. 寫入任務結果快取至 data/shopee_tasks/<task_id>.json
6. 主動調用 Telegram Bot API 發送推播通知給主人
"""

import os
import sys
import json
import time
import re
import argparse
import datetime
from pathlib import Path

# 確保可以載入專案模組
CURRENT_DIR = Path(__file__).resolve().parent
BASE_DIR = CURRENT_DIR.parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

import shopee_tool
from config import settings

TASKS_DIR = BASE_DIR / "data" / "shopee_tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)


def send_telegram_notification(text: str) -> bool:
    """發送 Telegram 推播通知給主人"""
    try:
        from telegram_service import send_telegram_sync
        # 轉換 Markdown 常用粗體為 HTML 或直接交由 telegram_service 處理
        html_text = text.replace("**", "<b>").replace("__", "<i>")
        # 確保關閉標籤（偶數次替換）
        parts = text.split("**")
        if len(parts) > 1 and len(parts) % 2 == 1:
            rebuilt = []
            for i, p in enumerate(parts):
                if i % 2 == 1:
                    rebuilt.append(f"<b>{p}</b>")
                else:
                    rebuilt.append(p)
            html_text = "".join(rebuilt)
        return send_telegram_sync(html_text, parse_mode="HTML")
    except Exception as e:
        print(f"[ShopeeWorker] 呼叫 telegram_service 失敗，嘗試原生發送: {e}", file=sys.stderr)

    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        print("[ShopeeWorker] 未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過推播。")
        return False

    import urllib.request
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[ShopeeWorker] 原生 Telegram 推播失敗: {e}", file=sys.stderr)
        return False


def run_worker(payload_file: str, task_id: str):
    p_path = Path(payload_file).resolve()
    if not p_path.exists():
        err_msg = f"找不到 payload 檔案: {p_path}"
        print(f"[ShopeeWorker] 錯誤: {err_msg}", file=sys.stderr)
        return

    print(f"=== 蝦皮背景上架 Worker 啟動 (Task ID: {task_id}) ===")
    print(f"時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Payload 檔案: {p_path}")

    with open(p_path, "r", encoding="utf-8") as f:
        payload_data = json.load(f)

    if isinstance(payload_data, list):
        items = payload_data
    elif isinstance(payload_data, dict):
        if "items" in payload_data and isinstance(payload_data["items"], list):
            items = payload_data["items"]
        else:
            items = [payload_data]
    else:
        raise ValueError(f"無效的 payload 結構: {type(payload_data)}")

    item_title = items[0].get("title", "未命名商品")
    item_price = items[0].get("price", 0)

    task_result = {
        "task_id": task_id,
        "created_at": datetime.datetime.now().isoformat(),
        "title": item_title,
        "price": item_price,
        "status": "processing",
        "is_published": False,
        "item_id": None,
        "live_url": None,
        "draft_url": None,
        "error": None
    }

    start_time = time.perf_counter()

    try:
        # 自動保障：若商品尚未配置官方白底宣傳照（cover_ 開頭），自動依標題檢索 1 張高清官方封面照置於 Slot 1
        for it in items:
            cur_images = it.get("images", [])
            has_cover = any("cover_" in str(img) for img in cur_images)
            if not has_cover and it.get("title"):
                clean_title = re.sub(r'[【】\[\]（）()_—\-\s]+', ' ', it["title"]).strip()
                words = [w for w in clean_title.split() if w not in {'全新現貨', '現貨', '全新', '正版', '日本', '熱賣', '免運'}]
                search_query = " ".join(words[:4]) if words else clean_title
                print(f"[CoverAutoFetch] 自動為商品檢索官方清晰首圖: {search_query} ...")
                try:
                    fetched = shopee_tool.fetch_best_cover_images(search_query, limit=1)
                    if fetched:
                        cover_path = fetched[0]["path"]
                        print(f"✅ 成功獲取官方高清首圖: {cover_path}，已置入 Slot 1 首圖！")
                        it["images"] = [cover_path] + [img for img in cur_images if img != cover_path]
                except Exception as cover_err:
                    print(f"[CoverAutoFetch] 官方圖檢索略過或失敗: {cover_err}")

        # 1. 產生 Excel (內部會自動進行平行圖床上傳)
        print("\n[步驟 1/3] 開始處理圖片平行上傳與生成批次 Excel...")
        excel_res = shopee_tool.export_items_to_excel(items, upload_images=True)
        excel_path = excel_res["output_file"]
        print(f"✅ 批次 Excel 已就緒: {excel_path}")

        # 2. 透過 Chrome-Assistant CDP 匯入與發布（附上 title→category 對照，修復草稿頁分類斷鏈）
        all_titles = [it.get("title") for it in items if it.get("title")]
        all_meta = [{"title": it.get("title"), "category_id": str(it.get("category_id", "")), "condition": it.get("condition", ""), "description": it.get("description", "")} for it in items if it.get("title")]
        print(f"\n[步驟 2/3] 透過 Chrome-Assistant 連線蝦皮賣家中心執行匯入與自動發布 ({len(items)} 件商品)...")
        upload_res = shopee_tool.upload_excel_to_shopee(excel_path, timeout_sec=50, expected_title=all_titles, expected_meta=all_meta)
        print(f"✅ 上傳結果: {json.dumps(upload_res, ensure_ascii=False)}")

        # 3. 剖析結果
        task_result["upload_res"] = upload_res
        published_products = upload_res.get("products") or ([upload_res.get("product")] if upload_res.get("product") else [])
        product_info = published_products[0] if published_products else {}
        item_id = upload_res.get("item_id") or product_info.get("item_id")
        live_url = upload_res.get("live_url") or product_info.get("live_url")
        draft_url = upload_res.get("draft_url") or product_info.get("draft_url")
        is_published = product_info.get("is_published", False)

        task_result["products"] = published_products
        task_result["item_id"] = item_id
        task_result["live_url"] = live_url
        task_result["draft_url"] = draft_url
        task_result["is_published"] = is_published
        task_result["status"] = "success" if (upload_res.get("status") == "success") else "warning"

        elapsed = time.perf_counter() - start_time
        task_result["elapsed_seconds"] = round(elapsed, 2)
        task_result["completed_at"] = datetime.datetime.now().isoformat()

        # 4. 寫入任務快取
        cache_file = TASKS_DIR / f"{task_id}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(task_result, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 任務狀態已快取至: {cache_file}")

        learned_rules = product_info.get("learned_rules")
        learned_note = ""
        if learned_rules:
            rules_str = "、".join([f"【{k} ➔ {v}】" for k, v in learned_rules.items()])
            learned_note = f"\n\n───────────────\n🧠 **秘書備忘**：偵測到未知必填欄位，已由 agy 自動填答 {rules_str} 並固化至技能庫！"
        # 卡住欄位明细：讓主人點草稿連結 30 秒手動補完，不用猜
        stuck_note = ""
        _stuck = product_info.get("unresolved_fields") or []
        _ferrs = product_info.get("field_errors") or []
        if _stuck or _ferrs:
            _lines = []
            for f in (_stuck[:5]):
                _lines.append(f"・【{f.get('label')}】（{f.get('widget')}，選項:{str(f.get('options', f.get('value', ''))[:40])}）{f.get('note', '')}")
            for e in (_ferrs[:5]):
                _lines.append(f"・【{e.get('label')}】{e.get('error', '')}")
            stuck_note = "\n* 🧷 **卡住欄位**：\n" + "\n".join(_lines)

        # 5. 發送 Telegram 推播
        print("\n[步驟 3/3] 發送 Telegram 推播通知給主人...")
        if len(items) > 1 and published_products:
            pub_count = sum(1 for p in published_products if p.get("is_published"))
            lines = [f"🎉 **蝦皮批次上架完成（共 {len(items)} 件，已上架 {pub_count} 件）！**\n"]
            for idx, prod in enumerate(published_products, 1):
                p_title = prod.get("title", f"商品 {idx}")
                p_live = prod.get("live_url")
                p_draft = prod.get("draft_url")
                p_status = "✅ 已上架" if prod.get("is_published") else "⚠️ 待草稿"
                lines.append(f"{idx}. {p_status} **{p_title}**")
                if p_live and prod.get("is_published"):
                    lines.append(f"   🌐 {p_live}")
                elif p_draft:
                    lines.append(f"   🔗 草稿：{p_draft}")
                    _ps = prod.get("unresolved_fields") or []
                    _pe = prod.get("field_errors") or []
                    _tags = [f"【{f.get('label')}】" for f in _ps[:3]] + [f"【{e.get('label')}】" for e in _pe[:2]]
                    if _tags:
                        lines.append(f"   🧷 卡住：{'、'.join(_tags)}")
            lines.append(f"\n⏱️ **背景總耗時**：{elapsed:.1f} 秒")
            lines.append(f"⚙️ **賣家中心草稿箱**：\nhttps://seller.shopee.tw/portal/product/list/unpublished/draft")
            if learned_note:
                lines.append(learned_note)
            msg = "\n".join(lines)
        elif is_published and live_url:
            msg = (
                f"🎉 **蝦皮商品已自動上架發布！**\n\n"
                f"* 📦 **商品名稱**：{item_title}\n"
                f"* 💰 **售價**：NT$ {item_price:,}\n"
                f"* ⏱️ **背景總耗時**：{elapsed:.1f} 秒\n\n"
                f"🌐 **前台賣場網址**：\n{live_url}\n\n"
                f"⚙️ **賣家中心頁面**：\n{draft_url or f'https://seller.shopee.tw/portal/product/{item_id}'}"
                f"{learned_note}"
            )
        elif item_id and draft_url:
            pending_reason = product_info.get("pending_reason")
            if not pending_reason:
                pending_reason = "已存入草稿箱，等待後台完成處理或發布"
            msg = (
                f"⚠️ **蝦皮商品已匯入草稿箱（待補填發布）**\n\n"
                f"* 📦 **商品名稱**：{item_title}\n"
                f"* 💰 **售價**：NT$ {item_price:,}\n"
                f"* 📝 **未直接發布原因**：{pending_reason}\n"
                f"{stuck_note}\n"
                f"🔗 **草稿編輯網址 (點擊前往補填)**：\n{draft_url}"
                f"{learned_note}"
            )
        else:
            msg = (
                f"ℹ️ **蝦皮批次上傳已完成匯入**\n\n"
                f"* 📦 **商品名稱**：{item_title}\n"
                f"* 📄 **批次檔案**：`{Path(excel_path).name}`\n"
                f"* 🔗 **賣家中心草稿列表**：\nhttps://seller.shopee.tw/portal/product/list/unpublished/draft"
                f"{learned_note}"
            )

        send_telegram_notification(msg)
        print("✅ Telegram 通知發送完畢。")

    except Exception as e:
        elapsed = time.perf_counter() - start_time
        err_str = str(e)
        print(f"\n❌ 執行過程發生錯誤: {err_str}", file=sys.stderr)
        task_result["status"] = "error"
        task_result["error"] = err_str
        task_result["elapsed_seconds"] = round(elapsed, 2)
        task_result["completed_at"] = datetime.datetime.now().isoformat()

        cache_file = TASKS_DIR / f"{task_id}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(task_result, f, ensure_ascii=False, indent=2)

        err_msg = (
            f"❌ **蝦皮商品背景上架失敗**\n\n"
            f"* 📦 **商品名稱**：{item_title}\n"
            f"* ⚠️ **錯誤訊息**：`{err_str}`\n"
            f"* 📋 **查看日誌**：`data/task_logs/{task_id}.log`"
        )
        send_telegram_notification(err_msg)

    print(f"\n=== 任務 {task_id} 結束 ===")


def main():
    parser = argparse.ArgumentParser(description="Shopee Seller Background Worker")
    parser.add_argument("--payload-file", required=True, help="Payload JSON 檔案路徑")
    parser.add_argument("--task-id", required=True, help="TaskManager 任務識別碼")
    args = parser.parse_args()

    run_worker(args.payload_file, args.task_id)


if __name__ == "__main__":
    main()
