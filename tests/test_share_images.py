import base64
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import share_file_parser


def test_share_endpoint_rejects_empty_payload():
    main.app.dependency_overrides[main.verify_token] = lambda: True
    try:
        client = TestClient(main.app)
        resp = client.post("/api/share", json={"prompt": "看圖"})
        assert resp.status_code == 400
    finally:
        main.app.dependency_overrides.clear()


def test_share_file_parser_docx_and_pptx(tmp_path):
    docx_path = tmp_path / "contract.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>合約重點測試</w:t></w:r></w:p></w:body></w:document>',
        )
    assert "合約重點測試" in share_file_parser.extract_file_preview(docx_path)

    pptx_path = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx_path, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>簡報第一頁</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        )
    assert "簡報第一頁" in share_file_parser.extract_file_preview(pptx_path)


def test_share_endpoint_accepts_documents(tmp_path):
    main.app.dependency_overrides[main.verify_token] = lambda: True
    captured = {}

    def fake_process(prompt, url, text, title, media=None, file_contexts=None):
        captured["prompt"] = prompt
        captured["media"] = media
        captured["file_contexts"] = file_contexts

    try:
        with patch.object(main.settings, "BASE_DIR", tmp_path), patch.object(
            main, "_process_share_payload_sync", side_effect=fake_process
        ):
            client = TestClient(main.app)
            txt_b64 = base64.b64encode("這是會議紀錄重點".encode("utf-8")).decode("ascii")
            pdf_b64 = base64.b64encode(b"%PDF-1.4\n%fake").decode("ascii")
            resp = client.post(
                "/api/share",
                json={
                    "prompt": "請幫我摘要",
                    "files": [
                        {"filename": "notes.txt", "mime_type": "text/plain", "data_base64": txt_b64},
                        {"filename": "bill.pdf", "mime_type": "application/pdf", "data_base64": pdf_b64},
                    ],
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["file_count"] == 2
            assert len(captured["file_contexts"]) == 2
            assert "這是會議紀錄重點" in captured["file_contexts"][0]
            assert any(m["mime_type"] == "application/pdf" for m in captured["media"])
    finally:
        main.app.dependency_overrides.clear()


def test_share_endpoint_accepts_multiple_images(tmp_path):
    main.app.dependency_overrides[main.verify_token] = lambda: True
    captured = {}

    def fake_process(prompt, url, text, title, media=None, file_contexts=None):
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


def test_share_file_parser_docx_and_pptx(tmp_path):
    docx_path = tmp_path / "contract.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>合約重點測試</w:t></w:r></w:p></w:body></w:document>',
        )
    assert "合約重點測試" in share_file_parser.extract_file_preview(docx_path)

    pptx_path = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx_path, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>簡報第一頁</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        )
    assert "簡報第一頁" in share_file_parser.extract_file_preview(pptx_path)


def test_share_endpoint_accepts_documents(tmp_path):
    main.app.dependency_overrides[main.verify_token] = lambda: True
    captured = {}

    def fake_process(prompt, url, text, title, media=None, file_contexts=None):
        captured["prompt"] = prompt
        captured["media"] = media
        captured["file_contexts"] = file_contexts

    try:
        with patch.object(main.settings, "BASE_DIR", tmp_path), patch.object(
            main, "_process_share_payload_sync", side_effect=fake_process
        ):
            client = TestClient(main.app)
            txt_b64 = base64.b64encode("這是會議紀錄重點".encode("utf-8")).decode("ascii")
            pdf_b64 = base64.b64encode(b"%PDF-1.4\n%fake").decode("ascii")
            resp = client.post(
                "/api/share",
                json={
                    "prompt": "請幫我摘要",
                    "files": [
                        {"filename": "notes.txt", "mime_type": "text/plain", "data_base64": txt_b64},
                        {"filename": "bill.pdf", "mime_type": "application/pdf", "data_base64": pdf_b64},
                    ],
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["file_count"] == 2
            assert len(captured["file_contexts"]) == 2
            assert "這是會議紀錄重點" in captured["file_contexts"][0]
            assert any(m["mime_type"] == "application/pdf" for m in captured["media"])
    finally:
        main.app.dependency_overrides.clear()
