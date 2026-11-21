"""助理交付產物櫃 (Artifacts Store).

僅記錄助理正式貼給主人的報告網址與圖片產物，支援軟移除（隱藏清單但不刪本機原檔）。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from config import settings

ARTIFACTS_JSON_PATH = settings.DATA_DIR / "artifacts.json"
_LOCK = threading.Lock()

_MD_LINK_RE = re.compile(r"\[([^\]]{1,120})\]\((https?://[^\s)]+|/reports/[^\s)]+|/api/history-images/[^\s)]+)\)")
_BARE_REPORT_RE = re.compile(r"(https?://[^\s)>\]\"']+/reports/[^\s)>\]\"']+|/reports/[A-Za-z0-9._-]+\.(?:html?|png|jpe?g|webp|pdf))")
_BARE_IMAGE_RE = re.compile(r"(https?://[^\s)>\]\"']+\.(?:png|jpe?g|webp|gif)(?:\?[^\s)>\]\"']*)?)", re.IGNORECASE)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_GENERIC_LABELS = {"連結", "點此開啟", "點擊開啟", "開啟報告", "查看報告", "報告", "圖片", "網址", "link", "url"}


def _read_all() -> List[Dict[str, Any]]:
    if not ARTIFACTS_JSON_PATH.exists():
        return []
    try:
        data = json.loads(ARTIFACTS_JSON_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_all(items: List[Dict[str, Any]]) -> None:
    ARTIFACTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARTIFACTS_JSON_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ARTIFACTS_JSON_PATH)


def _normalize_url(url: str) -> str:
    clean = url.strip().rstrip(".,;!?")
    if clean.startswith("/reports/"):
        filename = clean[len("/reports/"):]
        return settings.get_report_url(filename)
    return clean


def _classify_artifact(url: str) -> Optional[str]:
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if "/reports/" in path_lower:
        ext = Path(path_lower).suffix
        return "image" if ext in _IMAGE_EXTS else "report"
    if "/api/history-images/" in path_lower:
        return "image"
    ext = Path(path_lower).suffix
    if ext in _IMAGE_EXTS:
        return "image"
    return None


def _resolve_title(label: str, url: str, user_query: str) -> str:
    clean_label = (label or "").strip()
    if clean_label and clean_label.lower() not in _GENERIC_LABELS and not clean_label.startswith("http"):
        return clean_label[:60]

    parsed = urlparse(url)
    if "/reports/" in parsed.path:
        filename = Path(parsed.path).name
        local_file = settings.REPORTS_DIR / filename
        if local_file.exists() and local_file.suffix.lower() in {".html", ".htm"}:
            try:
                head = local_file.read_text(encoding="utf-8", errors="ignore")[:4096]
                m = re.search(r"<title>(.*?)</title>", head, re.IGNORECASE | re.DOTALL)
                if m and m.group(1).strip():
                    return m.group(1).strip()[:60]
            except Exception:
                pass
        return Path(filename).stem[:60]

    q = (user_query or "").strip().splitlines()[0] if user_query else ""
    if q:
        return q[:40]
    return Path(parsed.path).name or "交付產物"


def extract_artifacts_from_reply(reply_text: str, user_query: str = "") -> List[Dict[str, str]]:
    """從助理回覆中萃取正式交付給主人的報告網址與圖片產物。"""
    if not reply_text:
        return []

    found: Dict[str, Dict[str, str]] = {}

    for label, raw_url in _MD_LINK_RE.findall(reply_text):
        norm_url = _normalize_url(raw_url)
        kind = _classify_artifact(norm_url)
        if not kind:
            continue
        found[norm_url] = {
            "url": norm_url,
            "type": kind,
            "title": _resolve_title(label, norm_url, user_query),
        }

    for raw_url in _BARE_REPORT_RE.findall(reply_text) + _BARE_IMAGE_RE.findall(reply_text):
        norm_url = _normalize_url(raw_url)
        if norm_url in found:
            continue
        kind = _classify_artifact(norm_url)
        if not kind:
            continue
        found[norm_url] = {
            "url": norm_url,
            "type": kind,
            "title": _resolve_title("", norm_url, user_query),
        }

    return list(found.values())


def register_artifacts_from_reply(reply_text: str, user_query: str = "") -> List[Dict[str, Any]]:
    """將助理回覆中的產物自動登記至 artifacts.json（同網址去重更新）。"""
    extracted = extract_artifacts_from_reply(reply_text, user_query=user_query)
    if not extracted:
        return []

    now = datetime.now().isoformat()
    added: List[Dict[str, Any]] = []
    with _LOCK:
        items = _read_all()
        by_url = {item.get("url"): item for item in items if item.get("url")}
        for entry in extracted:
            existing = by_url.get(entry["url"])
            if existing:
                existing["title"] = entry["title"] or existing.get("title", "")
                existing["updated_at"] = now
                existing["hidden"] = False
                added.append(existing)
            else:
                new_item = {
                    "id": f"art_{uuid.uuid4().hex[:10]}",
                    "title": entry["title"],
                    "url": entry["url"],
                    "type": entry["type"],
                    "created_at": now,
                    "updated_at": now,
                    "hidden": False,
                }
                items.append(new_item)
                by_url[entry["url"]] = new_item
                added.append(new_item)
        _write_all(items)
    return added


def list_artifacts(include_hidden: bool = False) -> List[Dict[str, Any]]:
    """取得產物清單（預設僅回傳未隱藏項目，依更新時間由新到舊排序）。"""
    with _LOCK:
        items = _read_all()
    visible = [it for it in items if include_hidden or not it.get("hidden", False)]
    visible.sort(key=lambda it: it.get("updated_at") or it.get("created_at") or "", reverse=True)
    return visible


def hide_artifact(artifact_id: str) -> bool:
    """從 App 清單軟移除產物（僅標記 hidden=True，絕不刪除硬碟原檔）。"""
    with _LOCK:
        items = _read_all()
        for it in items:
            if it.get("id") == artifact_id:
                it["hidden"] = True
                it["updated_at"] = datetime.now().isoformat()
                _write_all(items)
                return True
    return False
