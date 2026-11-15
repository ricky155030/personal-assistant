import base64
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


def test_share_endpoint_rejects_empty_payload():
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        client = TestClient(main.app)
        resp = client.post("/api/share", json={"prompt": "看圖"})
        assert resp.status_code == 400
    finally:
        main.app.dependency_overrides.clear()


def test_share_endpoint_accepts_multiple_images(tmp_path):
    main.app.dependency_overrides[main.verify_token] = lambda: True
    captured = {}

    def fake_process(prompt, url, text, title, media=None):
        captured["prompt"] = prompt
        captured["media"] = media

    try:
        with patch.object(main.settings, "BASE_DIR", tmp_path), patch.object(
            main, "_process_share_payload_sync", side_effect=fake_process
        ):
            client = TestClient(main.app)
            img1 = base64.b64encode(b"fake-jpg-1").decode("ascii")
            img2 = base64.b64encode(b"fake-jpg-2").decode("ascii")
            resp = client.post(
                "/api/share",
                json={
                    "prompt": "幫我用蝦皮賣家技能上架",
                    "images_base64": [img1, img2],
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["image_count"] == 2
            assert len(captured["media"]) == 2
            for item in captured["media"]:
                assert item["mime_type"] == "image/jpeg"
                assert Path(item["uri"]).exists()
    finally:
        main.app.dependency_overrides.clear()
