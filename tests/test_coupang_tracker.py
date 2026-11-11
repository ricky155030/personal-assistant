import sys
import json
import argparse
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CT_PATH = BASE_DIR / "skills" / "coupang-tracker" / "scripts" / "coupang_tracker.py"
spec = importlib.util.spec_from_file_location("coupang_tracker", CT_PATH)
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


def test_format_time_ago():
    assert ct.format_time_ago(None) == "未知"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    assert ct.format_time_ago(now_str) == "剛剛"

    five_min_ago = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    assert ct.format_time_ago(five_min_ago) == "5 分鐘前"

    two_hours_ago = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert ct.format_time_ago(two_hours_ago) == "2 小時前"

    two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert ct.format_time_ago(two_days_ago) == "2 天前"


def test_cmd_status_json(capsys, monkeypatch):
    fake_state = {
        "last_checked_at": "2026-11-08 22:00:00",
        "total_checks": 100,
        "session_status": "LOGGED_OUT",
        "last_cart_sync_at": "2026-11-06 12:00:00",
        "cart_baselines": {
            "Item Active": {
                "title": "Item Active",
                "option": "Red",
                "baseline_price": 1000,
                "last_price": 950,
                "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "Item Stale": {
                "title": "Item Stale",
                "option": "Blue",
                "baseline_price": 2000,
                "last_price": 2100,
                "last_seen": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
            }
        },
        "thresholds": {"Test Cat Food": 500},
        "recent_snapshots": []
    }

    monkeypatch.setattr(ct, "load_state", lambda: fake_state)

    args = argparse.Namespace(json=True)
    ct.cmd_status(args)

    captured = capsys.readouterr()
    res = json.loads(captured.out)

    assert res["session_status"] == "LOGGED_OUT"
    assert "Item Active" in res["cart_active_items"]
    assert "Item Stale" in res["cart_stale_items"]
    assert "Item Stale" not in res["cart_active_items"]


def test_defense_in_depth_abnormal_price_drop(monkeypatch):
    """測試價格異常暴跌（如 99 折被誤判為 $99）時觸發防禦隔離，不發送通知且不破壞基準價"""
    fake_state = {
        "cart_baselines": {
            "Apple iPhone 18 Pro Max (銀色, 512GB)": {
                "title": "Apple iPhone 18 Pro Max",
                "option": "銀色, 512GB",
                "baseline_price": 55600,
                "original_price": 56900,
                "last_price": 55900,
                "last_seen": "2026-11-09 10:00:00"
            }
        },
        "thresholds": {},
        "last_notified": {},
        "recent_snapshots": []
    }

    alerts_dispatched = []
    monkeypatch.setattr(ct, "dispatch_cart_drop_alert", lambda *args: alerts_dispatched.append(args))
    monkeypatch.setattr(ct, "save_state", lambda s: None)

    fake_tracker = ct.CoupangPriceTracker()
    monkeypatch.setattr(fake_tracker, "fetch_cart_items_with_status", lambda: ([{
        "title": "Apple iPhone 18 Pro Max",
        "option": "銀色, 512GB",
        "sale_price": 99,  # 誤判為 99
        "original_price": 56900,
        "url": "https://cart.tw.coupang.com/cart"
    }], True))
    monkeypatch.setattr(fake_tracker, "search_products", lambda q: [])

    # 執行巡檢
    ct.perform_check(fake_tracker, fake_state)

    # 驗證：未發送通報，且 baseline_price 依然維持 55600，未被污染為 99
    assert len(alerts_dispatched) == 0
    assert fake_state["cart_baselines"]["Apple iPhone 18 Pro Max (銀色, 512GB)"]["baseline_price"] == 55600


def test_wave_high_calculation_24h():
    """測試 24 小時滑動窗口最高價 (Rolling 24h High) 計算與過期紀錄修剪"""
    now_ts = 1700000000.0

    # 1. 空歷史
    hist, wave_high = ct.update_price_history_24h([], 1000, now_ts=now_ts)
    assert wave_high == 1000
    assert len(hist) == 1

    # 2. 24 小時內之多筆紀錄，應取最大值
    hist = [
        {"timestamp": now_ts - 7200, "price": 950},
        {"timestamp": now_ts - 3600, "price": 1050},
        {"timestamp": now_ts - 1800, "price": 980},
    ]
    updated_hist, wave_high = ct.update_price_history_24h(hist, 920, now_ts=now_ts)
    assert wave_high == 1050
    assert len(updated_hist) == 4

    # 3. 超過 24 小時 (86,400 秒) 的紀錄應被剔除
    hist_with_expired = [
        {"timestamp": now_ts - 90000, "price": 2000},  # 超過 24h，應被剔除
        {"timestamp": now_ts - 50000, "price": 1200},  # 24h 內
        {"timestamp": now_ts - 10000, "price": 1150},  # 24h 內
    ]
    pruned_hist, wave_high = ct.update_price_history_24h(hist_with_expired, 1100, now_ts=now_ts)
    assert len(pruned_hist) == 3
    assert wave_high == 1200
    assert all(p["price"] != 2000 for p in pruned_hist)


def test_should_notify_cart_drop_threshold_5_percent():
    """測試降幅滿 5% (且跌幅金額 >= 10 元) 觸發通報"""
    state = {"last_notified": {}}

    # 剛好滿 5% (1000 -> 950, drop 50)
    assert ct.should_notify_cart_drop("test_item", 950, 1000, 0.05, 50, state) is True

    # 超過 5% (1000 -> 940, drop 60)
    assert ct.should_notify_cart_drop("test_item", 940, 1000, 0.06, 60, state) is True

    # 降幅達 5% 但跌幅金額未達 10 元 (100 -> 95, drop 5)，不通報避免幾毛錢誤差
    assert ct.should_notify_cart_drop("cheap_item", 95, 100, 0.05, 5, state) is False


def test_should_notify_cart_drop_under_5_percent():
    """測試降幅未滿 5% 不通報"""
    state = {"last_notified": {}}

    # 降幅 4% (< 5%)
    assert ct.should_notify_cart_drop("test_item", 960, 1000, 0.04, 40, state) is False

    # 降幅 4.9% (< 5%)
    assert ct.should_notify_cart_drop("test_item", 951, 1000, 0.049, 49, state) is False

    # 無降幅或漲價
    assert ct.should_notify_cart_drop("test_item", 1000, 1000, 0.0, 0, state) is False
    assert ct.should_notify_cart_drop("test_item", 1050, 1000, -0.05, -50, state) is False


def test_cart_drop_cooldown_and_ratchet():
    """測試通報防頻率轟炸 (Cooldown / Ratchet)：破更低價即刻通報，同價冷卻 6 小時"""
    now_ts = 1700000000.0
    state = {
        "last_notified": {
            "cart_drop:Item A": {
                "price": 940,
                "timestamp": now_ts - 3600,  # 1 小時前通報過 940
            }
        }
    }

    # 1. 1 小時後價格仍為 940（未破更低，未滿 6 小時）-> 不通報
    assert ct.should_notify_cart_drop("cart_drop:Item A", 940, 1000, 0.06, 60, state, rebounded=False, now_ts=now_ts) is False

    # 2. 1 小時後價格進一步下殺至 920 (< 940 破更低價 Ratchet) -> 觸發通報
    assert ct.should_notify_cart_drop("cart_drop:Item A", 920, 1000, 0.08, 80, state, rebounded=False, now_ts=now_ts) is True

    # 3. 超過 6 小時 (21,600 秒) 後價格仍為 940 -> 觸發提醒
    assert ct.should_notify_cart_drop("cart_drop:Item A", 940, 1000, 0.06, 60, state, rebounded=False, now_ts=now_ts + 22000) is True


def test_rebound_and_second_drop_triggers_notification(monkeypatch):
    """測試價格回彈後再次下殺 5% 成功觸發通報 (解決舊邏輯 baseline 鎖死歷史低點問題)"""
    fake_state = {
        "cart_baselines": {},
        "thresholds": {},
        "last_notified": {},
        "recent_snapshots": []
    }

    dispatched = []
    monkeypatch.setattr(ct, "dispatch_cart_drop_alert", lambda *args: dispatched.append(args))
    monkeypatch.setattr(ct, "save_state", lambda s: None)

    fake_tracker = ct.CoupangPriceTracker()

    def make_cart_item(price: int):
        return [{
            "title": "測試藍牙耳機",
            "option": "曜石黑",
            "sale_price": price,
            "original_price": 1000,
            "url": "https://cart.tw.coupang.com/cart"
        }]

    # 第 1 輪：初始以 1000 入購物車
    monkeypatch.setattr(fake_tracker, "fetch_cart_items_with_status", lambda: (make_cart_item(1000), True))
    ct.perform_check(fake_tracker, fake_state)
    assert len(dispatched) == 0
    assert fake_state["cart_baselines"]["測試藍牙耳機 (曜石黑)"]["wave_high_24h"] == 1000

    # 第 2 輪：下殺至 900 (降 10% >= 5%) -> 觸發第 1 次通報
    monkeypatch.setattr(fake_tracker, "fetch_cart_items_with_status", lambda: (make_cart_item(900), True))
    ct.perform_check(fake_tracker, fake_state)
    assert len(dispatched) == 1
    assert fake_state["last_notified"]["cart_drop:測試藍牙耳機 (曜石黑)"]["price"] == 900

    # 第 3 輪：價格仍維持 900 -> 防轟炸冷卻中，不通報
    monkeypatch.setattr(fake_tracker, "fetch_cart_items_with_status", lambda: (make_cart_item(900), True))
    ct.perform_check(fake_tracker, fake_state)
    assert len(dispatched) == 1

    # 第 4 輪：價格回彈至 1000 -> 回彈偵測生效 (rebounded = True)
    monkeypatch.setattr(fake_tracker, "fetch_cart_items_with_status", lambda: (make_cart_item(1000), True))
    ct.perform_check(fake_tracker, fake_state)
    assert len(dispatched) == 1
    assert fake_state["cart_baselines"]["測試藍牙耳機 (曜石黑)"]["rebounded"] is True

    # 第 5 輪：價格再度下殺至 940 (自 24h 波段高點 1000 降 6% >= 5%)
    # 即使 940 高於上次通報價 900，因回彈成功重置波段，應成功觸發第 2 次通報！
    monkeypatch.setattr(fake_tracker, "fetch_cart_items_with_status", lambda: (make_cart_item(940), True))
    ct.perform_check(fake_tracker, fake_state)
    assert len(dispatched) == 2
    # 驗證傳給通報卡片的參數 (title, option, current_p, wave_high, drop_amount, drop_rate, url)
    last_alert = dispatched[-1]
    assert last_alert[0] == "測試藍牙耳機"
    assert last_alert[2] == 940   # 最新特價
    assert last_alert[3] == 1000  # 24h 波段高點
    assert last_alert[4] == 60    # 降幅金額
    assert round(last_alert[5], 2) == 0.06  # 降幅比例 6%


def test_dispatch_cart_drop_alert_card_content(monkeypatch):
    """驗證 Telegram 與 iOS 通報卡片中包含波段高點、最新特價與降幅金額比例"""
    sent_ios = []
    sent_tg = []

    monkeypatch.setattr(ct, "send_critical_alert", lambda **kwargs: sent_ios.append(kwargs) or True)
    monkeypatch.setattr(ct, "send_telegram_alert", lambda msg: sent_tg.append(msg) or True)

    ct.dispatch_cart_drop_alert(
        title="日本花王洗髮精",
        option="薄荷清爽 500ml",
        current_price=285,
        wave_high=300,
        drop_amount=15,
        drop_rate_or_url=0.05,
        url="https://cart.tw.coupang.com/cart"
    )

    # 1. 驗證 iOS Critical Alert 內容
    assert len(sent_ios) == 1
    ios = sent_ios[0]
    assert "波段高點 $300" in ios["message"]
    assert "最新特價 $285" in ios["message"]
    assert "降幅 -$15 / -5.0%" in ios["message"]

    # 2. 驗證 Telegram 訊息內容
    assert len(sent_tg) == 1
    tg = sent_tg[0]
    assert "波段高點**：`$300`" in tg
    assert "最新特價**：`$285`" in tg
    assert "降幅金額與比例**：`-$15 (-5.0%)`" in tg


