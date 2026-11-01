#!/usr/bin/env python3
"""
酷澎價格守護神 CLI 與核心引擎 (Coupang Tracker)
路徑: .agents/skills/coupang-tracker/scripts/coupang_tracker.py

功能支援:
  status   - 輸出最新巡檢狀態、即時價格、目標門檻、差額與健康指標
  list     - 列出所有正在監控的品項與門檻價
  set      - 新增或調整品項之目標通報門檻價
  remove   - 移除特定監控品項
  run-once - 立即執行單次雙軌巡檢 (不阻塞待機)
  watch    - 背景常駐巡檢循環 (由 TaskManager 調度管理)
"""

import os
import sys
import json
import time
import random
import signal
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from scripts.coupang_price_tracker import CoupangPriceTracker, send_telegram_alert
from scripts.ios_notify import send_critical_alert

STATE_FILE = BASE_DIR / "data" / "coupang_monitor_state.json"
LEGACY_STATE_FILE = BASE_DIR / "data" / "iphone18_monitor_state.json"

DEFAULT_THRESHOLDS = {
    "Farmina 法米納 GC6 1.5kg 貓飼料": 900,
    "Apple iPhone 17": 29000,
    "Apple iPhone 17 Pro": 35000,
    "Apple iPhone 18 Pro": 39500,
    "Apple iPhone 18 Pro Max": 43000
}

RUNNING = True


def sig_handler(signum, frame):
    global RUNNING
    print(f"\n🛑 收到終止訊號 ({signum})，安全結束監控進程...")
    RUNNING = False


signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)


def load_state() -> Dict[str, Any]:
    state = {
        "thresholds": DEFAULT_THRESHOLDS.copy(),
        "last_notified": {},
        "last_checked_at": None,
        "total_checks": 0,
        "recent_snapshots": []
    }
    
    target_path = STATE_FILE if STATE_FILE.exists() else LEGACY_STATE_FILE
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                state.update(saved)
                if "thresholds" not in state or not state["thresholds"]:
                    state["thresholds"] = DEFAULT_THRESHOLDS.copy()
        except Exception as e:
            print(f"⚠️ 載入狀態檔失敗，使用預設值: {e}", file=sys.stderr)

    return state


def save_state(state: Dict[str, Any]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state, ensure_ascii=False, indent=2)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    # 保持向下相容性同步
    try:
        with open(LEGACY_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


def match_item_price(name: str, snapshot: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """從快照中尋找特定品項的現價與來源軌道"""
    if not snapshot:
        return None, "無資料"
    
    cart = snapshot.get("cart", {})
    bests = snapshot.get("search_bests", {})

    # 1. 飼料類 / Farmina
    if "法米納" in name or "Farmina" in name:
        for k, p in cart.items():
            if "Farmina" in k or "法米納" in k:
                return p, "購物車直連"
        for k, p in bests.items():
            if "Farmina" in k or "法米納" in k:
                return p, "搜尋頁"

    # 2. iPhone 系列
    if "iPhone" in name:
        name_lower = name.lower()
        # 先精確比對 search_bests
        for k, p in bests.items():
            if k.strip().lower() == name_lower:
                return p, "全網搜尋"
        
        # 比對購物車與保留清單
        for k, p in cart.items():
            k_lower = k.lower()
            if "512" in name_lower and ("512" not in k_lower and "512" not in name_lower):
                continue
            # 針對 512GB 特殊處理（若舊快照 key 僅為 "Apple iPhone 18 Pro Max" 且價格為 56700 等）
            if "512" in name_lower:
                if "512" in k_lower or (p > 52000 and "max" in k_lower):
                    return p, "購物車直連"
                continue
            
            # 一般 256GB / 規格
            if "512" in k_lower or (p > 52000 and "max" in k_lower and "512" not in name_lower):
                continue
            if "pro" not in name_lower and "pro" in k_lower:
                continue
            if "max" not in name_lower and "max" in k_lower:
                continue
            if "e" not in name_lower and any(tag in k_lower for tag in [" 17e", "17e", " 16e", "16e"]):
                continue

            if "iphone 18 pro max" in name_lower and "iphone 18 pro max" in k_lower:
                return p, "購物車保留"
            if "iphone 18 pro" in name_lower and "iphone 18 pro" in k_lower and "max" not in k_lower:
                return p, "購物車保留"
            if "iphone 17 pro" in name_lower and "iphone 17 pro" in k_lower:
                return p, "購物車保留"
            if "iphone 17" in name_lower and "iphone 17" in k_lower and "pro" not in k_lower:
                return p, "購物車保留"

        # 模糊包含比對搜尋結果
        for k, p in bests.items():
            if all(part.lower() in k.lower() for part in name.split() if len(part) > 1):
                return p, "全網搜尋"

        return None, "待採樣"

    # 3. 通用比對
    for k, p in bests.items():
        if name.lower() in k.lower():
            return p, "全網搜尋"
    for k, p in cart.items():
        if name.lower() in k.lower():
            return p, "購物車"

    return None, "待採樣"


def should_notify(model_key: str, price: int, threshold: int, state: Dict[str, Any]) -> bool:
    """判斷是否應發送通知：價格達標且距上次通知已超過 6 小時 (或價格破更低)"""
    if price >= threshold:
        return False

    now_ts = time.time()
    last_info = state.get("last_notified", {}).get(model_key)
    if not last_info:
        return True

    last_time = last_info.get("timestamp", 0)
    last_price = last_info.get("price", 999999)

    # 破更低價即刻通知
    if price < last_price:
        return True

    # 維持門檻以下，冷卻超過 6 小時 (21600 秒) 再次溫馨提醒
    if (now_ts - last_time) > 21600:
        return True

    return False


def dispatch_alerts(name: str, spec_text: str, current_price: int, threshold: int, url: str):
    print(f"🚨 【特價達標警報！】{name} ({spec_text})：現價 ${current_price:,} (低於門檻 ${threshold:,})！")

    # 1. iOS Critical Alert (純靜音置頂)
    ios_title = f"🚨 酷澎特價達標！{name}"
    ios_msg = f"【{spec_text}】現正特價 ${current_price:,} (門檻 ${threshold:,})！點此立即下單。"
    try:
        ok_ios = send_critical_alert(
            title=ios_title,
            message=ios_msg,
            url=url,
            volume=0.0,
            tag="coupang_deal_alert"
        )
        if ok_ios:
            print("📱 iOS Critical Alert 發送成功！")
        else:
            print("⚠️ iOS Critical Alert 發送失敗。")
    except Exception as e:
        print(f"❌ iOS 推播異常: {e}", file=sys.stderr)

    # 2. Telegram 通知卡片
    tg_lines = [
        f"🚨 **【酷澎特價達標警報：{name}】**\n",
        f"📦 **商品規格**：`{spec_text}`",
        f"🎯 **設定門檻**：`${threshold:,}`",
        f"🔥 **目前特價**：`${current_price:,}` (**已低於心目中理想價格！**)\n",
        f"🛒 [點此立即前往結帳/購物車]({url})"
    ]
    try:
        ok_tg = send_telegram_alert("\n".join(tg_lines))
        if ok_tg:
            print("✈️ Telegram 降價訊息推播成功！")
    except Exception as e:
        print(f"❌ Telegram 推播異常: {e}", file=sys.stderr)


def dispatch_cart_drop_alert(title: str, option: str, current_price: int, baseline_price: int, drop_amount: int, url: str):
    spec_text = option or "主商品"
    print(f"📉 【購物車自動降價警報！】{title} ({spec_text})：現價 ${current_price:,} (自基準 ${baseline_price:,} 降了 ${drop_amount:,})！")

    # 1. iOS Critical Alert (純靜音置頂)
    ios_title = f"📉 購物車降價！{title}"
    ios_msg = f"【{spec_text}】現正降價至 ${current_price:,} (現省 ${drop_amount:,})！點此結帳。"
    try:
        ok_ios = send_critical_alert(
            title=ios_title,
            message=ios_msg,
            url=url,
            volume=0.0,
            tag="coupang_cart_drop"
        )
        if ok_ios:
            print("📱 iOS Critical Alert 發送成功！")
        else:
            print("⚠️ iOS Critical Alert 發送失敗。")
    except Exception as e:
        print(f"❌ iOS 推播異常: {e}", file=sys.stderr)

    # 2. Telegram 通知卡片
    tg_lines = [
        f"📉 **【酷澎購物車降價警報】**\n",
        f"📦 **商品**：`{title}`",
        f"🎨 **規格**：`{spec_text}`",
        f"💰 **加入時現價**：`${baseline_price:,}`",
        f"🔥 **最新特價**：`${current_price:,}`",
        f"🎉 **現省金額**：`-${drop_amount:,}`\n",
        f"🛒 [點此立即結帳]({url})"
    ]
    try:
        ok_tg = send_telegram_alert("\n".join(tg_lines))
        if ok_tg:
            print("✈️ Telegram 降價訊息推播成功！")
    except Exception as e:
        print(f"❌ Telegram 推播異常: {e}", file=sys.stderr)


def perform_check(tracker: CoupangPriceTracker, state: Dict[str, Any]):
    current_check = state.get("total_checks", 0) + 1
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n🔍 [{now_str}] 開始執行第 {current_check} 次酷澎雙軌巡檢...")

    # 軌道一：購物車直連 (全品項自動降價監控)
    print("🛒 [軌道 1: 購物車直連] 正在讀取購物車主區商品...")
    cart_items = tracker.fetch_cart_items()
    print(f"   購物車讀取到 {len(cart_items)} 項商品：")
    for ci in cart_items:
        print(f"   • {ci['title'][:35]}.. ({ci['option']}) -> 現價: ${ci['sale_price']:,}")

    # 維護購物車自動追蹤基準價
    cart_baselines = state.setdefault("cart_baselines", {})

    # 1. 自動追蹤購物車所有品項降價 (Auto-Track)
    for ci in cart_items:
        item_key = f"{ci['title']} ({ci['option']})".strip()
        current_p = ci["sale_price"]
        orig_p = ci.get("original_price") or current_p
        url = ci.get("url") or "https://cart.tw.coupang.com/cart"

        if item_key not in cart_baselines:
            cart_baselines[item_key] = {
                "title": ci["title"],
                "option": ci["option"],
                "baseline_price": current_p,
                "original_price": orig_p,
                "first_seen": now_str,
                "last_price": current_p,
                "last_seen": now_str
            }
            print(f"   ✨ [自動追蹤] 新入購物車品項：【{item_key}】(初始基準: ${current_p:,})")
        else:
            base_info = cart_baselines[item_key]
            base_p = base_info.get("baseline_price", current_p)
            base_info["last_price"] = current_p
            base_info["last_seen"] = now_str
            base_info["original_price"] = orig_p

            # 若現價低於初始基準價，立即推播通報！
            if current_p < base_p:
                drop_amount = base_p - current_p
                notify_key = f"cart_drop:{item_key}"
                if should_notify(notify_key, current_p, base_p, state):
                    dispatch_cart_drop_alert(ci["title"], ci["option"], current_p, base_p, drop_amount, url)
                    state.setdefault("last_notified", {})[notify_key] = {
                        "price": current_p,
                        "timestamp": time.time(),
                        "time": now_str
                    }
                    base_info["baseline_price"] = current_p  # 更新基準

    # 2. 同步檢查手動門檻 (Thresholds)
    thresholds = state.get("thresholds", {})
    for ci in cart_items:
        full_text = f"{ci['title']} {ci.get('option', '')}".strip()
        p = ci["sale_price"]
        url = ci.get("url") or "https://cart.tw.coupang.com/cart"

        for k, th in thresholds.items():
            # 支援貓飼料或特定指定型號
            is_matched = False
            if ("Farmina" in k or "法米納" in k) and ("Farmina" in full_text or "法米納" in full_text):
                is_matched = True
            elif k.lower() in full_text.lower():
                is_matched = True

            if is_matched:
                if should_notify(k, p, th, state):
                    dispatch_alerts(k, ci.get("option", "主商品"), p, th, url)
                    state.setdefault("last_notified", {})[k] = {"price": p, "timestamp": time.time(), "time": now_str}
                else:
                    print(f"   ℹ️ [{k}] 購物車售價 ${p:,}，目標門檻 <${th:,}。")

    # 軌道二：搜尋頁
    print("🌐 [軌道 2: 搜尋全網比價] 正在巡檢全網特價...")
    search_queries = set()
    for k in state["thresholds"].keys():
        if "iPhone 18" in k:
            search_queries.add("iPhone 18 Pro")
        elif "iPhone 17" in k:
            search_queries.add("iPhone 17")

    raw_search = []
    for q in search_queries:
        res = tracker.search_products(q)
        raw_search.extend(res)

    print(f"   搜尋頁抓取到 {len(raw_search)} 筆規格：")
    for it in raw_search:
        if "256GB" in it.get("capacity", "").upper():
            print(f"   • {it['model']} | {it['color']} | 256GB -> ${it['sale_price']:,}")

    # 比對搜尋結果是否低於目標價
    for it in raw_search:
        m = it.get("model")
        cap = it.get("capacity", "")
        if "256GB" not in cap.upper() or m not in state["thresholds"]:
            continue
        p = it["sale_price"]
        th = state["thresholds"][m]
        if should_notify(m, p, th, state):
            dispatch_alerts(m, f"{it['color']} / 256GB", p, th, it["url"])
            state.setdefault("last_notified", {})[m] = {"price": p, "timestamp": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    # 記錄快照
    snapshot = {
        "check_num": current_check,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cart": {f"{ci['title']} {ci.get('option', '')}".strip()[:60]: ci["sale_price"] for ci in cart_items},
        "search_bests": {}
    }
    for it in raw_search:
        m = it.get("model")
        cap = it.get("capacity", "")
        if "256GB" in cap.upper():
            p = it["sale_price"]
            if m not in snapshot["search_bests"] or p < snapshot["search_bests"][m]:
                snapshot["search_bests"][m] = p

    state.setdefault("recent_snapshots", []).append(snapshot)
    if len(state["recent_snapshots"]) > 100:
        state["recent_snapshots"] = state["recent_snapshots"][-100:]

    state["total_checks"] = current_check
    state["last_checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)


def cmd_status(args):
    state = load_state()
    snapshots = state.get("recent_snapshots", [])
    latest_snapshot = snapshots[-1] if snapshots else {}
    last_time = state.get("last_checked_at") or (latest_snapshot.get("time") if latest_snapshot else "尚未巡檢")
    total_checks = state.get("total_checks", len(snapshots))
    cart_baselines = state.get("cart_baselines", {})
    thresholds = state.get("thresholds", {})

    if args.json:
        data = {
            "last_checked_at": last_time,
            "total_checks": total_checks,
            "cart_items_auto_tracked": cart_baselines,
            "manual_thresholds": thresholds,
            "latest_snapshot": latest_snapshot,
            "recent_snapshots_count": len(snapshots)
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print("══════════════════════════════════════════════════════════════════════════════")
    print(f"📊 酷澎購物車守護神巡檢日誌 (最新狀態報表)")
    print(f"🕒 最後巡檢時間: {last_time}  |  🔄 累計巡檢次數: {total_checks} 次")
    print("══════════════════════════════════════════════════════════════════════════════")

    print("\n🛒 【購物車主區品項 (全自動降價追蹤中)】")
    print(f"{'商品品名與規格':<42} | {'目前現價':<10} | {'基準現價':<10} | {'差額':<9} | {'狀態'}")
    print("------------------------------------------------------------------------------")
    if not cart_baselines:
        print("（目前購物車無記錄，執行巡檢後將自動登記購物車現有品項）")
    else:
        for k, info in cart_baselines.items():
            # 排除保留清單
            if "保留清單" in k or "保留清單" in info.get("option", ""):
                continue
            cur_p = info.get("last_price", info.get("baseline_price", 0))
            base_p = info.get("baseline_price", cur_p)
            diff = cur_p - base_p

            name_display = f"{info.get('title', k)[:24]} ({info.get('option', '')[:14]})"
            price_str = f"${cur_p:,}"
            base_str = f"${base_p:,}"

            if diff < 0:
                diff_str = f"-${abs(diff):,}"
                status = "🚨 降價下殺！"
            elif diff == 0:
                diff_str = "$0"
                status = "🟢 盯價中"
            else:
                diff_str = f"+${diff:,}"
                status = "📈 價格回彈"

            print(f"{name_display:<40} | {price_str:<10} | {base_str:<10} | {diff_str:<9} | {status}")

    # 手動目標門檻項目 (例如外部或特別指定的理想價)
    if thresholds:
        print("\n🎯 【手動指定目標門檻項目】")
        print(f"{'品項名稱':<32} | {'目前現價':<10} | {'目標門檻':<10} | {'差額':<9} | {'狀態與來源'}")
        print("------------------------------------------------------------------------------")
        for name, th in thresholds.items():
            price, source = match_item_price(name, latest_snapshot)
            if price is not None:
                diff = price - th
                price_str = f"${price:,}"
                th_str = f"${th:,}"
                if diff <= 0:
                    diff_str = f"-${abs(diff):,}"
                    status = f"🚨 特價達標 ({source})"
                else:
                    diff_str = f"+${diff:,}"
                    status = f"👀 觀察中 ({source})"
            else:
                price_str = "採樣中"
                th_str = f"${th:,}"
                diff_str = "--"
                status = f"⏳ 等待巡檢 ({source})"

            print(f"{name:<30} | {price_str:<10} | {th_str:<10} | {diff_str:<9} | {status}")

    print("\n══════════════════════════════════════════════════════════════════════════════")
    if state.get("last_notified"):
        print("📢 最近觸發之通知紀錄：")
        for k, v in state["last_notified"].items():
            print(f"  • {k}: ${v.get('price', 0):,} ({v.get('time', '未知時間')})")
        print("──────────────────────────────────────────────────────────────────────────────")
    print("💡 說明：放入酷澎購物車的商品一律全自動主動追蹤，有降價或新折扣即刻推播。")


def cmd_list(args):
    state = load_state()
    cart_baselines = state.get("cart_baselines", {})
    thresholds = state.get("thresholds", {})

    print(f"📋 目前酷澎自動監控品項：")
    print(f"\n🛒 購物車自動追蹤商品 (共 {len(cart_baselines)} 項)：")
    for idx, (k, info) in enumerate(cart_baselines.items(), 1):
        if "保留清單" in k or "保留清單" in info.get("option", ""):
            continue
        print(f"  {idx}. {info.get('title')} ({info.get('option')}) -> 基準現價: ${info.get('baseline_price'):,}")

    if thresholds:
        print(f"\n🎯 手動指定門檻品項 (共 {len(thresholds)} 項)：")
        for idx, (name, th) in enumerate(thresholds.items(), 1):
            print(f"  {idx}. {name} -> 目標門檻: ${th:,}")


def cmd_set(args):
    name = args.name.strip()
    th = int(args.threshold)
    state = load_state()
    state.setdefault("thresholds", {})[name] = th
    save_state(state)
    print(f"✅ 成功設定監控項目：【{name}】")
    print(f"🎯 目標通報門檻價設為: ${th:,}")
    print(f"💡 當價格低於 ${th:,} 時，將自動透過 iOS Critical Alert (純靜音) 與 Telegram 推播通知。")


def cmd_remove(args):
    name = args.name.strip()
    state = load_state()
    thresholds = state.get("thresholds", {})
    
    # 支援精確或部分比對
    matched_key = None
    if name in thresholds:
        matched_key = name
    else:
        for k in list(thresholds.keys()):
            if name.lower() in k.lower():
                matched_key = k
                break

    if matched_key:
        old_val = thresholds.pop(matched_key)
        save_state(state)
        print(f"🗑️ 成功移除監控項目：【{matched_key}】 (原門檻價: ${old_val:,})")
    else:
        print(f"⚠️ 找不到名稱包含 '{name}' 的監控項目。")
        print(f"目前清單中的品項有: {list(thresholds.keys())}")


def cmd_run_once(args):
    state = load_state()
    tracker = CoupangPriceTracker()
    perform_check(tracker, state)
    print("\n✅ 單次巡檢完成！可執行 status 指令檢視最新狀態。")


def cmd_watch(args):
    interval = args.interval or 360
    print(f"🚀 啟動酷澎價格守護神常駐監控 (間隔基準: {interval} 秒)")
    tracker = CoupangPriceTracker()

    while RUNNING:
        try:
            state = load_state()
            perform_check(tracker, state)
        except Exception as e:
            print(f"⚠️ 巡檢時發生非致命例外: {e}", file=sys.stderr)

        if not RUNNING:
            break

        jitter = random.randint(-15, 15)
        sleep_sec = max(60, interval + jitter)
        print(f"⏳ 雙軌巡檢完畢，休息 {sleep_sec} 秒後進行下一次檢查...")
        for _ in range(sleep_sec):
            if not RUNNING:
                break
            time.sleep(1)

    print("🏁 酷澎常駐守護神已安全終止。")


def main():
    parser = argparse.ArgumentParser(description="酷澎雙軌價格追蹤器 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="檢視最新巡檢狀態與價格報表")
    p_status.add_argument("--json", action="store_true", help="輸出 JSON 格式")
    p_status.set_defaults(func=cmd_status)

    # list
    p_list = subparsers.add_parser("list", help="列出目前監控項目清單")
    p_list.set_defaults(func=cmd_list)

    # set / add
    p_set = subparsers.add_parser("set", aliases=["add"], help="新增或修改目標門檻價")
    p_set.add_argument("--name", required=True, help="商品名稱或型號 (例如 'Apple iPhone 17' 或 'Farmina 法米納 GC6 1.5kg 貓飼料')")
    p_set.add_argument("--threshold", required=True, type=int, help="目標通知門檻金額 (例如 29000)")
    p_set.set_defaults(func=cmd_set)

    # remove / rm
    p_rm = subparsers.add_parser("remove", aliases=["rm"], help="移除監控項目")
    p_rm.add_argument("--name", required=True, help="商品名稱或型號關鍵字")
    p_rm.set_defaults(func=cmd_remove)

    # run-once
    p_run = subparsers.add_parser("run-once", help="立即執行單次雙軌巡檢")
    p_run.set_defaults(func=cmd_run_once)

    # watch
    p_watch = subparsers.add_parser("watch", help="啟動背景常駐巡檢循環")
    p_watch.add_argument("--interval", type=int, default=360, help="巡檢週期秒數 (預設 360 秒 = 6 分鐘)")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
