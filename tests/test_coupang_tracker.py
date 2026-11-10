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
    monkeypatch.setattr(fake_tracker, "fetch_cart_items", lambda: ([{
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

