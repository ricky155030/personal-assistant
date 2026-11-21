from pathlib import Path
from unittest.mock import patch

import artifacts_store as store


def test_extract_and_register_artifacts_dedup_and_soft_hide(tmp_path):
    store_file = tmp_path / "artifacts.json"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "senao.html").write_text("<html><head><title>神腦餐廳禮券</title></head></html>", encoding="utf-8")

    reply = (
        "已為您整理完成：\n"
        "- [神腦可用餐廳清單](http://example.ts.net:8899/reports/senao.html)\n"
        "- 圖片：https://cdn.example.com/banner.png\n"
        "- 裸連結：/reports/senao.html"
    )

    with patch.object(store, "ARTIFACTS_JSON_PATH", store_file), \
         patch.object(store.settings, "REPORTS_DIR", reports_dir), \
         patch.object(type(store.settings), "get_report_url", side_effect=lambda name: f"http://example.ts.net:8899/reports/{name}"):
        added = store.register_artifacts_from_reply(reply, user_query="整理神腦餐廳")
        assert len(added) == 2

        visible = store.list_artifacts()
        assert len(visible) == 2
        titles = {item["title"] for item in visible}
        assert "神腦可用餐廳清單" in titles

        report_item = next(it for it in visible if it["type"] == "report")
        assert store.hide_artifact(report_item["id"]) is True
        assert store.hide_artifact("nonexistent_id") is False

        visible_after = store.list_artifacts()
        assert len(visible_after) == 1
        assert (reports_dir / "senao.html").exists() is True
