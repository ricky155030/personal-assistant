import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import scripts.momo_stock_monitor as msm


def test_format_time_ago():
    assert msm.format_time_ago(None) == "未知"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    assert msm.format_time_ago(now_str) == "剛剛"

    ten_min_ago = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    assert msm.format_time_ago(ten_min_ago) == "10 分鐘前"


def test_cmd_status_json(capsys, monkeypatch):
    fake_state = {
        "target_url": "https://www.momoshop.com.tw/test",
        "product_name": "Test Product",
        "last_checked_at": "2026-11-08 23:00:00",
        "last_success_at": "2026-11-08 23:00:00",
        "status": "OUT_OF_STOCK",
        "consecutive_failures": 0,
        "total_checks": 5,
        "notified_in_stock": False
    }

    monkeypatch.setattr(msm, "load_state", lambda: fake_state)

    args = argparse.Namespace(json=True)
    msm.cmd_status(args)

    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["status"] == "OUT_OF_STOCK"
    assert res["product_name"] == "Test Product"
