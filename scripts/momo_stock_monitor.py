#!/usr/bin/env python3
"""
MOMO 購物網商品補貨即時監控腳本 (scripts/momo_stock_monitor.py)

功能支援：
  status   - 輸出最新監控狀態、庫存狀態、最後成功巡檢時間與健康度
  run-once - 立即執行單次巡檢並輸出結果
  watch    - 背景常駐巡檢循環 (由 TaskManager 調度管理)

特色與防護：
1. 操控 Mac mini 本機真實 Chrome-Assistant 繞過 momo 防爬蟲盾 (403 Forbidden)。
2. 自動長按通關常見驗證盾 (PerimeterX, Cloudflare)。
3. 嚴格的三重到貨判定：出現「直接購買」或「加入購物車」且「售完補貨中」消失。
4. 巡檢完畢立即精準關閉 momo 分頁，維持待機 0% 視窗與資源佔用。
5. 安全機制受阻與 Stale 守護：若連續受阻達門檻，自動發送 Telegram 異常告警，絕不誤報到貨。
"""

import os
import sys
import json
import time
import signal
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[1])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env", override=True)

import importlib.util
BR_PATH = BASE_DIR / "skills" / "browser-relay" / "scripts" / "browser_relay.py"
spec = importlib.util.spec_from_file_location("browser_relay", BR_PATH)
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

from scripts.ios_notify import send_critical_alert

STATE_FILE = BASE_DIR / "data" / "momo_monitor_state.json"
DEFAULT_TARGET_URL = "https://www.momoshop.com.tw/TP/TP0009793/goodsDetail/TP00097930000232"
DEFAULT_PRODUCT_NAME = "【JOLLY】多功能調節床邊床"

RUNNING = True


def sig_handler(signum, frame):
    global RUNNING
    print(f"\n🛑 收到終止訊號 ({signum})，安全結束 MOMO 監控進程...")
    RUNNING = False


signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)


def format_time_ago(ts_str: Optional[str]) -> str:
    """轉換為人類可讀的相對時間"""
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
            return f"{secs // 86400} 天前"
    except Exception:
        return str(ts_str)


def send_telegram_alert(message: str) -> bool:
    """透過專屬機器人通道發送 Telegram 通知"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("[MOMOMonitor] ⚠️ 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過推播", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        # 降級重試：若 Markdown 特殊字元引發 400，轉以純文字發送
        try:
            payload.pop("parse_mode", None)
            retry_data = json.dumps(payload).encode("utf-8")
            retry_req = urllib.request.Request(url, data=retry_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(retry_req, timeout=10.0) as retry_resp:
                return retry_resp.status == 200
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"[MOMOMonitor] ❌ Telegram 發送失敗: {e}", file=sys.stderr)
        return False


def load_state() -> Dict[str, Any]:
    state = {
        "target_url": DEFAULT_TARGET_URL,
        "product_name": DEFAULT_PRODUCT_NAME,
        "last_checked_at": None,
        "last_success_at": None,
        "status": "UNKNOWN",  # IN_STOCK | OUT_OF_STOCK | BLOCKED | ERROR
        "consecutive_failures": 0,
        "total_checks": 0,
        "notified_in_stock": False,
        "last_stale_alert_at": None
    }
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                state.update(saved)
        except Exception as e:
            print(f"[MOMOMonitor] ⚠️ 載入狀態檔失敗: {e}", file=sys.stderr)
    return state


def save_state(state: Dict[str, Any]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


class MomoStockMonitor:
    def __init__(self):
        self.client = br.ChromeAssistantClient()

    def check_stock(self, url: str) -> Tuple[str, str, Dict[str, Any]]:
        """
        巡檢目標頁面庫存狀態
        回傳: (status, reason, details)
        status:
          - IN_STOCK: 可以購買（出現直接購買/加入購物車，且無售完補貨中）
          - OUT_OF_STOCK: 售完補貨中
          - BLOCKED: 被驗證盾/風控阻擋
          - ERROR: 頁面載入異常
        """
        self.client.ensure_chrome()

        with self.client.session_lock("momo_stock_monitor", timeout=30.0):
            with br.sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.client.cdp_url)
                context = browser.contexts[0]

                target_page = None
                for page in context.pages:
                    if "momoshop.com.tw" in (page.url or ""):
                        target_page = page
                        break
                if not target_page:
                    target_page = context.new_page()

                try:
                    target_page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    target_page.wait_for_timeout(2500)

                    # 嘗試自動偵測並自愈常見驗證盾 (PerimeterX, Cloudflare)
                    try:
                        self.client.check_and_bypass_captcha(target_page, max_wait=3.0)
                    except Exception:
                        pass

                    title = target_page.title()
                    body_text = target_page.evaluate("document.body ? document.body.innerText : ''")

                    # 1. 檢查是否遭遇安全機制或封鎖
                    if "Access Denied" in title or "Access Denied" in body_text[:200] or "請稍後再試" in title:
                        return "BLOCKED", f"遭遇安全機制阻擋 (Title: {title})", {"title": title}

                    # 2. 擷取購買相關按鈕
                    button_info = target_page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button, a.btn, input[type=button]'));
                        const res = {
                            has_out_of_stock: false,
                            has_buy_now: false,
                            has_add_to_cart: false,
                            button_texts: []
                        };
                        for (const b of btns) {
                            const txt = (b.innerText || '').trim();
                            const cls = (b.className || '');
                            if (txt) res.button_texts.push(txt);

                            if (txt.includes('售完補貨中') || txt.includes('補貨中') || cls.includes('goods-detail-out-of-stock')) {
                                res.has_out_of_stock = true;
                            }
                            if (txt.includes('直接購買') || cls.includes('goods-detail-buy-now-button')) {
                                res.has_buy_now = true;
                            }
                            if (txt.includes('加入購物車') || cls.includes('goods-detail-add-to-cart-button')) {
                                res.has_add_to_cart = true;
                            }
                        }
                        return res;
                    }""")

                    has_out_of_stock = button_info.get("has_out_of_stock", False)
                    has_buy_now = button_info.get("has_buy_now", False)
                    has_add_to_cart = button_info.get("has_add_to_cart", False)

                    # 嚴格判定：若出現「直接購買」或「加入購物車」且「售完補貨中」消失
                    if (has_buy_now or has_add_to_cart) and not has_out_of_stock:
                        return "IN_STOCK", "商品已到貨！偵測到購買按鈕", button_info

                    if has_out_of_stock:
                        return "OUT_OF_STOCK", "商品目前售完補貨中", button_info

                    # 備援容錯：若按鈕特徵未讀取到，但文字明確包含
                    if "售完補貨中" in body_text:
                        return "OUT_OF_STOCK", "頁面包含售完補貨中標籤", button_info

                    return "ERROR", "無法明確識別庫存狀態", button_info

                except Exception as e:
                    return "ERROR", f"頁面解析異常: {e}", {}
                finally:
                    # 巡檢完畢立即精準關閉 momo 分頁，維持待機 0% 資源佔用
                    try:
                        self.client.cleanup_domain_targets(("momoshop.com.tw",))
                        self.client.gc_tabs(max_tabs=8)
                    except Exception:
                        pass


def dispatch_in_stock_alert(state: Dict[str, Any]):
    """觸發商品到貨即時通報 (iOS Critical Alert + Telegram)"""
    url = state["target_url"]
    prod_name = state["product_name"]

    print(f"\n🎉🎉🎉 【MOMO 補貨警報！】{prod_name} 已恢復供貨可下單！")

    # 1. iOS Critical Alert (純靜音置頂)
    ios_title = f"🚨 MOMO 補貨快報！"
    ios_msg = f"{prod_name} 目前已到貨可下單！點此立即搶購。"
    try:
        send_critical_alert(
            title=ios_title,
            message=ios_msg,
            url=url,
            volume=0.0,
            tag="momo_stock_alert"
        )
        print("[MOMOMonitor] 📱 iOS Critical Alert 發送成功！")
    except Exception as e:
        print(f"[MOMOMonitor] ❌ iOS Critical Alert 失敗: {e}", file=sys.stderr)

    # 2. Telegram 通知卡片
    tg_lines = [
        f"🚨 **【MOMO 商品補貨到貨通報】**\n",
        f"📦 **商品名稱**：`{prod_name}`",
        f"🔥 **當前狀態**：**已到貨開賣，可直接購買 / 加入購物車！**",
        f"🕒 **發現時間**：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n",
        f"🛒 [點此立即前往 MOMO 搶購]({url})"
    ]
    ok_tg = send_telegram_alert("\n".join(tg_lines))
    if ok_tg:
        print("[MOMOMonitor] ✈️ Telegram 補貨通知推播成功！")
    else:
        print("[MOMOMonitor] ⚠️ Telegram 推播未成功。", file=sys.stderr)


def dispatch_stale_warning(state: Dict[str, Any], reason: str):
    """巡檢連續受阻達門檻時發送溫馨提醒"""
    now_ts = time.time()
    last_stale_ts = state.get("last_stale_alert_ts", 0)

    # 至少間隔 4 小時 (14400 秒) 才再次發送異常提醒，杜絕洗版
    if now_ts - last_stale_ts < 14400:
        return

    prod_name = state["product_name"]
    last_succ = state.get("last_success_at", "未知")
    time_ago = format_time_ago(last_succ)

    tg_lines = [
        f"⚠️ **【MOMO 監控警示：巡檢受阻】**\n",
        f"📦 **監控品項**：`{prod_name}`",
        f"🛑 **受阻原因**：`{reason}`",
        f"⏳ **最後成功巡檢**：`{last_succ}` ({time_ago})",
        f"💡 **說明**：為防止漏失補貨通知，已暫停誤報；若持續受阻可於 Mac mini 檢查 Chrome 視窗。"
    ]
    ok_tg = send_telegram_alert("\n".join(tg_lines))
    if ok_tg:
        state["last_stale_alert_ts"] = now_ts
        state["last_stale_alert_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("[MOMOMonitor] 📢 已向 Telegram 發送巡檢受阻溫馨警報。")


def perform_check(monitor: MomoStockMonitor, state: Dict[str, Any]):
    current_check = state.get("total_checks", 0) + 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["total_checks"] = current_check
    state["last_checked_at"] = now_str

    url = state["target_url"]
    status, reason, details = monitor.check_stock(url)
    print(f"[{now_str}] 第 {current_check} 次巡檢 -> 狀態: {status} ({reason})")

    if status in ("IN_STOCK", "OUT_OF_STOCK"):
        state["last_success_at"] = now_str
        state["consecutive_failures"] = 0
        state["status"] = status

        if status == "IN_STOCK":
            # 首次偵測到有貨或狀態改變時發送通知
            if not state.get("notified_in_stock", False):
                dispatch_in_stock_alert(state)
                state["notified_in_stock"] = True
        else:
            # 狀態回到缺貨時重設通知旗標
            state["notified_in_stock"] = False

    elif status in ("BLOCKED", "ERROR"):
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["status"] = status
        print(f"   ⚠️ 巡檢異常/受阻 (連續 {state['consecutive_failures']} 次): {reason}")

        # 若連續失敗達到 3 次，觸發 Stale 警示
        if state["consecutive_failures"] >= 3:
            dispatch_stale_warning(state, reason)

    save_state(state)


def cmd_status(args):
    state = load_state()
    last_check = state.get("last_checked_at", "尚未巡檢")
    last_succ = state.get("last_success_at")
    succ_display = f"{last_succ} ({format_time_ago(last_succ)})" if last_succ else "尚無成功記錄"
    status = state.get("status", "UNKNOWN")

    status_map = {
        "IN_STOCK": "🟢 已補貨 (可購買下單！)",
        "OUT_OF_STOCK": "🟡 售完補貨中 (持續監控中)",
        "BLOCKED": "🔴 巡檢受阻 (遭遇安全驗證/風控)",
        "ERROR": "⚪ 頁面異常",
        "UNKNOWN": "⚪ 待檢測"
    }
    badge = status_map.get(status, status)

    if getattr(args, "json", False):
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return

    print("══════════════════════════════════════════════════════════════════════════════")
    print("📊 MOMO 購物網補貨即時監控日誌 (狀態報表)")
    print(f"📦 監控品項: {state.get('product_name')}")
    print(f"🔗 商品網址: {state.get('target_url')}")
    print(f"🕒 最後巡檢時間: {last_check}  |  🔄 累計巡檢次數: {state.get('total_checks', 0)} 次")
    print(f"🛒 庫存狀態: {badge}")
    print(f"⏱️ 最後成功確認時間: {succ_display}")
    if state.get("consecutive_failures", 0) > 0:
        print(f"⚠️ 連續受阻/異常次數: {state.get('consecutive_failures')} 次")
    print("══════════════════════════════════════════════════════════════════════════════")
    print("💡 說明：一旦頁面出現「直接購買」或「加入購物車」，將立即發送 iOS Critical Alert 與 Telegram 通知。")


def cmd_run_once(args):
    state = load_state()
    monitor = MomoStockMonitor()
    print("🔍 正在執行 MOMO 單次即時庫存巡檢...")
    perform_check(monitor, state)


def cmd_watch(args):
    interval = getattr(args, "interval", 120)
    print(f"🚀 啟動 MOMO 補貨監控守護者 (每 {interval} 秒巡檢一次)...")
    state = load_state()
    monitor = MomoStockMonitor()

    while RUNNING:
        try:
            perform_check(monitor, state)
        except Exception as e:
            print(f"[MOMOMonitor] ❌ 巡檢迴圈未捕獲異常: {e}", file=sys.stderr)

        # 隨機抖動休息，避免固定頻率特徵
        jitter = time.time() % 5
        sleep_time = interval + jitter
        print(f"⏳ 休息 {int(sleep_time)} 秒後進行下一次檢查...")
        for _ in range(int(sleep_time)):
            if not RUNNING:
                break
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="MOMO 購物網商品補貨監控器")
    subparsers = parser.add_subparsers(dest="command")

    p_status = subparsers.add_parser("status", help="檢視最新監控狀態報表")
    p_status.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    p_run = subparsers.add_parser("run-once", help="立即執行單次巡檢")

    p_watch = subparsers.add_parser("watch", help="常駐循環巡檢 (由 TaskManager 調度)")
    p_watch.add_argument("--interval", type=int, default=120, help="巡檢週期秒數 (預設 120 秒)")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "run-once":
        cmd_run_once(args)
    elif args.command == "watch":
        cmd_watch(args)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()
