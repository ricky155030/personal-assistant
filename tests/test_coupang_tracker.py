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
