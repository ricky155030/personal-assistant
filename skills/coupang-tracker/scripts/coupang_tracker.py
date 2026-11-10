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
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from coupang_price_tracker import CoupangPriceTracker, send_telegram_alert
except ImportError:
    from .coupang_price_tracker import CoupangPriceTracker, send_telegram_alert
from scripts.ios_notify import send_critical_alert

STATE_FILE = BASE_DIR / "data" / "coupang_monitor_state.json"
LEGACY_STATE_FILE = BASE_DIR / "data" / "iphone18_monitor_state.json"

DEFAULT_THRESHOLDS: Dict[str, int] = {}

RUNNING = True


def sig_handler(signum, frame):
    global RUNNING
    print(f"\n🛑 收到終止訊號 ({signum})，安全結束監控進程...")
    RUNNING = False


signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)


def format_time_ago(ts_str: Optional[str]) -> str:
    """將時間字串轉換為人類可讀的相對時間 (如: 5分鐘前、2天前)"""
    if not ts_str:
        return "未知"
    try:
        t = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        diff = datetime.now() - t
        secs = int(diff.total_seconds())
        if secs < 0:
            return "剛剛"
        if secs < 60:
            return "剛剛"
        elif secs < 3600:
            return f"{secs // 60} 分鐘前"
        elif secs < 86400:
            return f"{secs // 3600} 小時前"
        else:
            days = secs // 86400
            return f"{days} 天前"
    except Exception:
        return str(ts_str)


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
    """從快照中尋找特定品項的現價與來源軌道（支援購物車與全網搜尋多詞通用匹配）"""
    if not snapshot or not name:
        return None, "無資料"
    
    cart = snapshot.get("cart", {})
    bests = snapshot.get("search_bests", {})
    name_norm = name.strip().lower()
    tokens = [t.lower() for t in name.split() if len(t) > 1]

    # 1. 優先精準比對 (購物車 / 搜尋)
    for k, p in cart.items():
        if k.strip().lower() == name_norm:
            return p, "購物車直連"
    for k, p in bests.items():
        if k.strip().lower() == name_norm:
            return p, "全網搜尋"

    # 2. 多詞完全包含比對
    if tokens:
        for k, p in cart.items():
            if all(t in k.lower() for t in tokens):
                return p, "購物車直連"
        for k, p in bests.items():
            if all(t in k.lower() for t in tokens):
                return p, "全網搜尋"

    # 3. 子字串包含比對
    for k, p in cart.items():
        if name_norm in k.lower() or k.lower() in name_norm:
            return p, "購物車直連"
    for k, p in bests.items():
        if name_norm in k.lower() or k.lower() in name_norm:
            return p, "全網搜尋"

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
    cart_items, is_logged_in = tracker.fetch_cart_items_with_status()
    print(f"   購物車讀取到 {len(cart_items)} 項商品 (登入狀態: {'🟢 已登入' if is_logged_in else '🔴 未登入/過期'})：")
    for ci in cart_items:
        print(f"   • {ci['title'][:35]}.. ({ci['option']}) -> 現價: ${ci['sale_price']:,}")

    state["session_status"] = "LOGGED_IN" if is_logged_in else "LOGGED_OUT"

    if is_logged_in:
        state["last_cart_sync_at"] = now_str
        # 維護購物車自動追蹤基準價
        cart_baselines = state.setdefault("cart_baselines", {})
        current_item_keys = set()

        # 1. 自動追蹤購物車所有品項降價 (Auto-Track)
        for ci in cart_items:
            item_key = f"{ci['title']} ({ci['option']})".strip()
            current_item_keys.add(item_key)
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
                    # 異常價格防禦：若暴跌超過 80% (例如 $55,900 跌至 $99)，高機率為 DOM 折數標籤誤解析，隔離保護
                    if base_p >= 500 and current_p < base_p * 0.2:
                        print(f"⚠️ [異常價格保護] {item_key}: 現價 ${current_p:,} 較基準價 ${base_p:,} 暴跌逾 80%，疑似折數標籤誤判，暫停更新基準與通報。", file=sys.stderr)
                        continue

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
                tokens = [t.lower() for t in k.split() if len(t) > 1]
                is_matched = (k.lower() in full_text.lower()) or (tokens and all(t in full_text.lower() for t in tokens))

                if is_matched:
                    if should_notify(k, p, th, state):
                        dispatch_alerts(k, ci.get("option", "主商品"), p, th, url)
                        state.setdefault("last_notified", {})[k] = {"price": p, "timestamp": time.time(), "time": now_str}
                    else:
                        print(f"   ℹ️ [{k}] 購物車售價 ${p:,}，目標門檻 <${th:,}。")

        # 3. 自動歸檔：若已確認登入，且品項連續超過 24 小時未再出現於購物車
        archived_baselines = state.setdefault("archived_cart_baselines", {})
        keys_to_archive = []
        for k, info in list(cart_baselines.items()):
            if k not in current_item_keys:
                last_seen_str = info.get("last_seen")
                if last_seen_str:
                    try:
                        last_seen_dt = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - last_seen_dt).total_seconds() > 86400:
                            keys_to_archive.append(k)
                    except Exception:
                        pass
        for k in keys_to_archive:
            print(f"   📦 [歸檔] 品項已超過 24 小時未在購物車出現，自動歸檔：【{k}】")
            archived_baselines[k] = cart_baselines.pop(k)
            archived_baselines[k]["archived_at"] = now_str
    else:
        print("   ⚠️ 酷澎未登入或 Session 已失效，暫停更新購物車基準，避免誤判。")

    # 記錄快照（專注購物車項目巡檢）
    snapshot = {
        "check_num": current_check,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cart": {f"{ci['title']} {ci.get('option', '')}".strip()[:60]: ci["sale_price"] for ci in cart_items}
    }

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
    session_status = state.get("session_status", "UNKNOWN")
    last_cart_sync = state.get("last_cart_sync_at")

    # 精準以 last_seen 區分「在線新鮮 (Active)」與「過期待同步 (Stale)」
    active_items = []
    stale_items = []

    now_dt = datetime.now()
    for k, info in cart_baselines.items():
        if "保留清單" in k or "保留清單" in info.get("option", ""):
            continue
        last_seen_str = info.get("last_seen")
        is_fresh = False
        diff_secs = 999999
        if last_seen_str:
            try:
                last_seen_dt = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                diff_secs = (now_dt - last_seen_dt).total_seconds()
                # 巡檢週期為 6 分鐘，以 1 小時 (3600 秒) 內見過視為在線有效
                if diff_secs <= 3600:
                    is_fresh = True
            except Exception:
                pass

        if is_fresh:
            active_items.append((k, info, diff_secs))
        else:
            stale_items.append((k, info, diff_secs))

    if args.json:
        data = {
            "last_checked_at": last_time,
            "total_checks": total_checks,
            "session_status": session_status,
            "last_cart_sync_at": last_cart_sync,
            "cart_active_items": {k: info for k, info, _ in active_items},
            "cart_stale_items": {k: info for k, info, _ in stale_items},
            "manual_thresholds": thresholds,
            "latest_snapshot": latest_snapshot,
            "recent_snapshots_count": len(snapshots)
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print("══════════════════════════════════════════════════════════════════════════════")
    print("📊 酷澎購物車守護神巡檢日誌 (最新狀態報表)")
    print(f"🕒 守護進程時間: {last_time}  |  🔄 累計巡檢次數: {total_checks} 次")
    if session_status == "LOGGED_IN":
        print("🔐 會員登入狀態: 🟢 已登入")
    elif session_status == "LOGGED_OUT":
        print("🔐 會員登入狀態: 🔴 Session 已過期 (請於 Chrome-Assistant 登入)")
    else:
        print("🔐 會員登入狀態: ⚪ 待確認")

    if last_cart_sync:
        print(f"🛒 購物車最後同步: {last_cart_sync} ({format_time_ago(last_cart_sync)})")
    else:
        print("🛒 購物車最後同步: 尚未成功同步")
    print("══════════════════════════════════════════════════════════════════════════════")

    print("\n🛒 【購物車主區品項 (即時在線追蹤中)】")
    if not active_items:
        if session_status == "LOGGED_OUT":
            print("（⚠️ 目前未偵測到在線品項。原因：酷澎登入已過期，請重新登入以恢復同步）")
        else:
            print("（目前購物車無在線品項，新商品放入購物車後將自動同步）")
    else:
        print(f"{'商品品名與規格':<42} | {'目前現價':<10} | {'基準最低價':<10} | {'差額':<9} | {'狀態'}")
        print("------------------------------------------------------------------------------")
        for k, info, _ in active_items:
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

    # 若有過期品項，移入歷史暫存區塊，並顯式標註最後同步時間與警告
    if stale_items:
        print("\n📦 【歷史追蹤快照 (非即時現價 / 待重新同步)】")
        print(f"{'商品品名與規格':<40} | {'歷史記錄價':<10} | {'最後同步時間':<22} | {'狀態說明'}")
        print("------------------------------------------------------------------------------")
        for k, info, _ in stale_items:
            cur_p = info.get("last_price", info.get("baseline_price", 0))
            last_seen_str = info.get("last_seen", "未知")
            time_ago = format_time_ago(last_seen_str)
            sync_col = f"{last_seen_str[:16]} ({time_ago})"
            name_display = f"{info.get('title', k)[:22]} ({info.get('option', '')[:12]})"
            print(f"{name_display:<38} | ${cur_p:<9,} | {sync_col:<20} | ⚠️ 過期快照 (非現價)")

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
    p_set.add_argument("--name", required=True, help="商品名稱或型號 (例如 'Apple iPhone 17' 或 'AirPods Pro 2')")
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
