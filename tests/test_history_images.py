from io import BytesIO
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import main
from memory_manager import MemoryManager


def test_clean_history_query_preserves_multiline_user_query():
    raw = (
        "【語音輸出嚴格規範】\n1. 長度限制\n\n"
        "【回覆長度約束：標準模式】重點突出。\n\n"
        "請詳細說明眼前這張照片的物品名稱。\n\n"
        "（主人補充語音：這是要怎麼用？）"
    )
    cleaned = main._clean_history_query(raw)
    assert cleaned == "請詳細說明眼前這張照片的物品名稱。\n\n（主人補充語音：這是要怎麼用？）"


def test_clean_history_query_strips_expanded_file_preview():
    raw = (
        "【回覆長度約束：細節模式】重點。\n\n"
        "整理這份投資報告並告訴我值得投資的產業\n\n"
        "📎 檔案：Report_2026Q4.pdf（本機路徑：/tmp/ios_doc_123_Report_2026Q4.pdf）\n"
        "[第 1 頁]\n這是第一頁的大量內文...\n\n[第 2 頁]\n這是第二頁的大量內文..."
    )
    cleaned = main._clean_history_query(raw)
    assert cleaned == "整理這份投資報告並告訴我值得投資的產業\n\n📎 Report_2026Q4.pdf"


def test_save_compressed_history_images_and_log_interaction(tmp_path):
    src_img = tmp_path / "big.png"
    Image.new("RGBA", (1600, 1200), color=(255, 0, 0, 255)).save(src_img)

    history_file = tmp_path / "history.jsonl"
    mm = MemoryManager()
    with patch("memory_manager.settings.DATA_DIR", tmp_path), \
         patch("memory_manager.settings.HISTORY_LOG_PATH", history_file):
        saved = mm.save_compressed_history_images([{"mime_type": "image/png", "uri": str(src_img)}])
        assert len(saved) == 1
        out_path = tmp_path / "history_images" / saved[0]
        assert out_path.exists()
        with Image.open(out_path) as out_img:
            assert max(out_img.size) <= 720

        mm.log_interaction("看這張圖", "這是一張紅圖", images=saved)

    lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["images"] == saved


def test_get_history_image_endpoint_security_and_serving(tmp_path):
    img_dir = tmp_path / "history_images"
    img_dir.mkdir(parents=True)
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=(0, 128, 255)).save(buf, format="JPEG")
    (img_dir / "sample.jpg").write_bytes(buf.getvalue())

    client = TestClient(main.app)
    with patch("main.settings.DATA_DIR", tmp_path), \
         patch("main.settings.API_KEY", ""):
        ok_resp = client.get("/api/history/images/sample.jpg")
        assert ok_resp.status_code == 200
        assert ok_resp.headers["content-type"].startswith("image/jpeg")

        missing_resp = client.get("/api/history/images/missing.jpg")
        assert missing_resp.status_code == 404
