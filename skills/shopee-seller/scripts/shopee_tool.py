#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shopee Seller Batch Excel Generator & Staging Tool (shopee-seller)
專為蝦皮賣家中心「批次新增商品 (Basic Template)」打造的自動化工具。

支援「分次傳送累積暫存 (Staging Cart) ➔ 一鍵打包產出單一全新 Excel 並自動清空暫存」工作流：
1. search-category : 從內建 2,088 筆台灣蝦皮官方分類中快速檢索最佳分類 ID
2. stage-add       : 將單筆或多筆商品（含多張本機圖片自動轉直連 URL）加入當前批次暫存區
3. stage-list      : 查看目前暫存區已累積的所有待上架商品
4. stage-update    : 修改暫存區內指定序號（1-based）的商品資訊（如改價格、改標題、改數量）
5. stage-remove    : 移除暫存區內指定序號（1-based）的商品
6. stage-clear     : 手動清空暫存區
7. stage-export    : 將暫存區所有商品一次打包產出為全新的 Shopee_Batch_YYYYMMDD_HHMMSS.xlsx，並自動清空暫存區！
8. generate-batch  : 單次直接產出全新批次 Excel（不經過暫存區）
9. inspect-batch   : 檢視已產出的批次 Excel 檔案內容摘要
"""

import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Union
import warnings
import urllib.parse

warnings.filterwarnings("ignore")
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR.parent.parent.parent
BROWSER_RELAY_DIR = PROJECT_ROOT / ".agents" / "skills" / "browser-relay" / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BROWSER_RELAY_DIR) not in sys.path:
    sys.path.insert(0, str(BROWSER_RELAY_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

import urllib3
import requests
from PIL import Image, ImageOps

urllib3.disable_warnings()

TEMPLATE_PATH = SKILL_DIR / "resources" / "Shopee_Template.xlsx"
CATEGORIES_PATH = SKILL_DIR / "resources" / "categories.json"
STAGING_FILE = PROJECT_ROOT / "data" / "shopee_staging_queue.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop"

# 預設物流設定（依主人偏好：只開 7-ELEVEN、全家、萊爾富、蝦皮店到店、蝦皮店到店-隔日到貨）
DEFAULT_SHIPPING = {
    "AF": "關閉",  # 黑貓宅急便 (channel_id.30001)
    "AG": "開啟",  # 7-ELEVEN (channel_id.30005)
    "AH": "開啟",  # 全家 (channel_id.30006)
    "AI": "開啟",  # 萊爾富 (channel_id.30007)
    "AJ": "關閉",  # 宅配通 (channel_id.30012)
    "AK": "開啟",  # 蝦皮店到店 (channel_id.30015)
    "AL": "開啟",  # 蝦皮店到店 - 隔日到貨 (channel_id.30019)
}

SHIPPING_NAME_MAP = {
    "黑貓宅急便": "AF",
    "黑貓": "AF",
    "7-ELEVEN": "AG",
    "7-11": "AG",
    "711": "AG",
    "全家": "AH",
    "萊爾富": "AI",
    "宅配通": "AJ",
    "蝦皮店到店": "AK",
    "店到店": "AK",
    "蝦皮店到店 - 隔日到貨": "AL",
    "隔日到貨": "AL",
}

# 上傳模板欄位順序 (A ~ AN)
COLUMNS_ORDER = [
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN"
]

IMAGE_COLUMNS = ["S", "T", "U", "V", "W", "X", "Y", "Z", "AA"]  # 主圖 + 圖1~8 (最多9張)


def load_categories():
    if not CATEGORIES_PATH.exists():
        return []
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_staging_queue() -> list:
    if not STAGING_FILE.exists():
        return []
    try:
        with open(STAGING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_staging_queue(items: list):
    STAGING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STAGING_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def search_categories(keywords, limit=15):
    cats = load_categories()
    if not keywords:
        return cats[:limit]

    scored = []
    kw_list = []
    for kw in keywords:
        for token in re.split(r"[\s,/、]+", kw.strip()):
            if token:
                kw_list.append(token.lower())

    for c in cats:
        path_lower = c["path"].lower()
        leaf_lower = path_lower.split("/")[-1]
        score = 0
        for kw in kw_list:
            if kw == leaf_lower:
                score += 50
            elif kw in leaf_lower:
                score += 25
            elif kw in path_lower:
                score += 10
            else:
                common_chars = sum(1 for ch in kw if ch in leaf_lower)
                if len(kw) >= 2 and common_chars >= 2:
                    score += common_chars * 3
        if score > 0:
            # 避坑加權：個人賣家出清優先推薦免繁瑣特殊證號之通用收藏/周邊類別
            if any(preferred in path_lower for preferred in ["動漫周邊", "扭蛋盒玩", "收藏品", "電玩遊戲", "公仔"]):
                score += 20
            # 降權：母嬰用品玩具容易強制要求 BSMI 經濟部檢驗合格證號
            if "母嬰用品" in path_lower and "玩具" in path_lower:
                score -= 20
            scored.append((score, c))

    scored.sort(key=lambda x: (-x[0], len(x[1]["path"])))
    return [item[1] for item in scored[:limit]]


def prepare_jpeg_bytes(image_path: str, max_bytes: int = 1800 * 1024) -> bytes:
    """
    將本機圖片（含 iPhone HEIC/PNG/WebP/JPG）轉為符合蝦皮規範之 RGB JPEG (< 1.8MB)。
    """
    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"找不到圖片檔案: {p}")

    ext = p.suffix.lower()
    temp_jpg = None
    try:
        if ext in (".heic", ".heif"):
            fd, temp_jpg = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            subprocess.run(
                ["/usr/bin/sips", "-s", "format", "jpeg", str(p), "--out", temp_jpg],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            img = Image.open(temp_jpg)
        else:
            img = Image.open(p)

        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        max_dim = 2048
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        for quality in (90, 82, 75, 65):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                return data

        img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        return buf.getvalue()
    finally:
        if temp_jpg and os.path.exists(temp_jpg):
            os.remove(temp_jpg)


def upload_image_to_public_url(image_path_or_url: str) -> str:
    """
    若傳入已經是 http/https URL 則直接回傳；
    若是本機檔案路徑，轉檔壓縮後上傳至臨時圖床取得直連 .jpg URL。
    支援多圖床自動備援容錯：pomf2.lain.la -> uguu.se -> freeimage.host -> Litterbox。
    """
    s = str(image_path_or_url).strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s

    jpg_bytes = prepare_jpeg_bytes(s)

    # 1. 優先使用 pomf2.lain.la (高速穩定，回傳直接 .jpg 圖檔連結)
    try:
        r = requests.post(
            "https://pomf2.lain.la/upload.php",
            files={"files[]": ("product.jpg", io.BytesIO(jpg_bytes), "image/jpeg")},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        if r.status_code == 200:
            res_json = r.json()
            if res_json.get("success") and res_json.get("files"):
                img_url = res_json["files"][0].get("url")
                if img_url and str(img_url).startswith("https://"):
                    return str(img_url).strip()
    except Exception as e:
        sys.stderr.write(f"[警告] pomf2.lain.la 上傳失敗，嘗試備援圖床: {e}\n")

    # 2. 備援圖床: uguu.se (Pomf 結構，回傳直接 .jpg 圖檔連結)
    try:
        r = requests.post(
            "https://uguu.se/upload.php",
            files={"files[]": ("product.jpg", io.BytesIO(jpg_bytes), "image/jpeg")},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        if r.status_code == 200:
            res_json = r.json()
            if res_json.get("success") and res_json.get("files"):
                img_url = res_json["files"][0].get("url")
                if img_url and str(img_url).startswith("https://"):
                    return str(img_url).strip()
    except Exception as e:
        sys.stderr.write(f"[警告] uguu.se 上傳失敗，嘗試備援圖床: {e}\n")

    # 3. 備援圖床: tmpfiles.org (穩定高速，自動解析直連圖檔連結)
    try:
        r = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": ("product.jpg", io.BytesIO(jpg_bytes), "image/jpeg")},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        if r.status_code == 200:
            res_json = r.json()
            page_url = (res_json.get("data") or {}).get("url")
            if page_url and str(page_url).startswith("https://"):
                r_page = requests.get(page_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                m = re.search(r'id=[\"\']img_preview[\"\']\s+src=[\"\']([^\"\']+)[\"\']', r_page.text)
                if m:
                    return m.group(1).strip()
    except Exception as e:
        sys.stderr.write(f"[警告] tmpfiles.org 上傳失敗: {e}\n")

    raise RuntimeError(f"所有圖床上傳皆失敗 ({image_path_or_url})")


def upload_images_parallel(images: list, max_workers: int = 4) -> list:
    """
    使用 ThreadPoolExecutor 併發上傳多張圖片至圖床，保持原始順序回傳直連 URL。
    """
    if not images:
        return []

    clean_images = [str(x).strip() for x in images if str(x).strip()]
    if not clean_images:
        return []

    if all(s.startswith("http://") or s.startswith("https://") for s in clean_images):
        return clean_images

    from concurrent.futures import ThreadPoolExecutor

    results = [None] * len(clean_images)
    workers = min(max_workers, len(clean_images))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {
            executor.submit(upload_image_to_public_url, img): idx
            for idx, img in enumerate(clean_images)
        }
        for future in future_to_idx:
            idx = future_to_idx[future]
            try:
                uploaded_url = future.result()
                if not uploaded_url or not (uploaded_url.startswith("http://") or uploaded_url.startswith("https://")):
                    raise RuntimeError(f"圖床回傳非有效直連網址: {uploaded_url}")
                results[idx] = uploaded_url
            except Exception as e:
                sys.stderr.write(f"[錯誤] 圖片 {clean_images[idx]} 上傳失敗: {e}\n")
                raise RuntimeError(f"商品圖片上傳至圖床失敗，中止寫入 Excel: {clean_images[idx]} (原因: {e})")
    return results


def fetch_best_cover_images(
    query: str,
    limit: int = 2,
    save_dir: Optional[str] = None,
    min_dim: int = 400
) -> List[Dict[str, Any]]:
    """
    依據商品關鍵字自動在網路上搜尋官方或高解析度乾淨圖片（白底/正方形宣傳照），
    自動下載並轉為符合蝦皮規格之標準 RGB JPEG 儲存至本地。
    """
    out_dir = Path(save_dir or (PROJECT_ROOT / "data" / "downloaded_images")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]+', '_', query).strip('_')[:25]
    search_url = f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}&form=HDRSC2&first=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en;q=0.7"
    }
    try:
        r = requests.get(search_url, headers=headers, timeout=10)
        murls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', r.text)
        if not murls:
            murls = re.findall(r'\"murl\":\"(https?://[^\"]+)\"', r.text)
    except Exception as e:
        sys.stderr.write(f"[警告] 首圖搜尋連線失敗: {e}\n")
        return []

    results = []
    seen = set()
    for u in murls[:15]:
        if u in seen:
            continue
        seen.add(u)
        try:
            resp = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if resp.status_code == 200 and len(resp.content) > 10000:
                img = Image.open(io.BytesIO(resp.content))
                img = ImageOps.exif_transpose(img)
                w, h = img.size
                ratio = max(w, h) / min(w, h)
                if w >= min_dim and h >= min_dim and ratio <= 1.8:
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        rgba = img.convert("RGBA")
                        bg.paste(rgba, mask=rgba.split()[3])
                        img = bg
                    elif img.mode != "RGB":
                        img = img.convert("RGB")

                    fname = f"cover_{slug}_{len(results)+1}.jpg"
                    fpath = out_dir / fname
                    img.save(str(fpath), format="JPEG", quality=90, optimize=True)
                    domain = urllib.parse.urlparse(u).netloc
                    results.append({
                        "path": str(fpath),
                        "source_url": u,
                        "domain": domain,
                        "width": w,
                        "height": h,
                        "ratio": round(ratio, 2)
                    })
                    if len(results) >= limit:
                        break
        except Exception:
            continue
    return results


CONDITION_NEW_VALUES = {"全新", "new", "brand new", "unused", "未使用", "未拆封", "全新未拆", "全新現貨"}
CONDITION_USED_VALUES = {"二手", "used", "secondhand", "second-hand", "中古", "出清", "近新", "近全新"}

# 商品狀況 radio 欄位名（模糊匹配）：命中即走 condition 強制分支，絕不經 LLM/規則庫臆測
CONDITION_FIELD_SUBSTR = ("商品狀況", "商品新舊", "新舊", "新旧", "狀況", "condition")

# 標題/描述中的二手語意訊號：命中任一即判定二手（9成新、展示品、僅拆封皆算二手；「未拆封」排除在外）
USED_SEMANTIC_RE = re.compile(
    r"二手|中古|出清|個人收藏|用過|使用過|近新|近全新|9成|9\.5成|95成|僅拆|已拆|(?<!未)拆封|(?<!未)拆過|展示品|盒損|微瑕|使用痕跡",
    re.IGNORECASE,
)

def normalize_condition(raw: object, title: str = "", description: str = "") -> str:
    """正規化商品新舊狀態，一律回傳『全新』或『二手』。語意優先於預設值。"""
    s = str(raw or "").strip().lower()
    if s in CONDITION_NEW_VALUES or "全新" in s:
        # 明確標全新仍需防呆：標題/描述出現二手語意時以語意為準
        hay = f"{title} {description}"
        if USED_SEMANTIC_RE.search(hay):
            return "二手"
        return "全新"
    if s in CONDITION_USED_VALUES or any(k in s for k in ("二手", "used", "中古", "出清")):
        return "二手"
    # 未指定或模糊值 → 從標題+描述語意推斷
    hay = f"{title} {description}"
    if USED_SEMANTIC_RE.search(hay):
        return "二手"
    return "全新"

def is_condition_field(field_name: str) -> bool:
    """是否為商品新舊狀態欄位（商品狀況/新舊 radio）。"""
    return any(k in str(field_name or "") for k in CONDITION_FIELD_SUBSTR)

def normalize_and_prepare_item(item: dict, upload_images: bool = True) -> dict:
    """
    正規化單筆商品結構，並可選在加入暫存時直接把本機圖片上傳為公開 URL。
    """
    norm = dict(item)
    cat_id = str(norm.get("category_id", "") or "").strip()
    if cat_id and "-" in cat_id:
        cat_id = cat_id.split("-", 1)[0].strip()
    norm["category_id"] = cat_id

    title = str(norm.get("title", "") or "").strip()
    if not title:
        raise ValueError("商品缺少必填的『title (商品名稱)』")
    if len(title) > 60:
        title = title[:60]
    norm["title"] = title

    description = str(norm.get("description", "") or "").strip()
    if not description:
        raise ValueError(f"商品『{title}』缺少必填的『description (商品描述)』")
    if len(description) > 3000:
        description = description[:3000]
    norm["description"] = description

    raw_images = norm.get("images", []) or []
    if isinstance(raw_images, str):
        raw_images = [raw_images]

    local_images = []
    for img_entry in raw_images[:9]:
        s = str(img_entry).strip()
        if s:
            local_images.append(s)

    if upload_images and local_images:
        uploaded_urls = upload_images_parallel(local_images)
    else:
        uploaded_urls = list(local_images)

    norm["local_images"] = local_images
    norm["images"] = uploaded_urls

    variations = norm.get("variations")
    if variations and isinstance(variations, list) and len(variations) > 0:
        norm_vars = []
        for var in variations:
            v_copy = dict(var)
            v_copy["price"] = int(float(v_copy.get("price", norm.get("price", 0))))
            v_copy["stock"] = int(float(v_copy.get("stock", norm.get("stock", 1))))
            if v_copy["price"] <= 0:
                raise ValueError(f"商品『{title}』規格價格必須大於 0")
            if v_copy.get("image") and upload_images:
                v_copy["image"] = upload_image_to_public_url(v_copy["image"])
            norm_vars.append(v_copy)
        norm["variations"] = norm_vars
    else:
        price = int(float(norm.get("price", 0)))
        stock = int(float(norm.get("stock", 1)))
        if price <= 0:
            raise ValueError(f"商品『{title}』價格 (price) 必須為大於 0 的整數")
        norm["price"] = price
        norm["stock"] = stock
    norm["weight"] = str(norm.get("weight", "0.5") or "0.5").strip()
    norm["condition"] = normalize_condition(norm.get("condition", ""), title=title, description=description)
    norm["staged_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return norm


def format_staging_summary(queue: list) -> dict:
    cats_map = {c["id"]: c for c in load_categories()}
    items_view = []
    for i, item in enumerate(queue, start=1):
        cat_id = item.get("category_id", "")
        cat_info = cats_map.get(cat_id)
        items_view.append({
            "index": i,
            "title": item.get("title", ""),
            "category_id": cat_id,
            "category_path": cat_info["path"] if cat_info else "",
            "price": item.get("price") if not item.get("variations") else [v.get("price") for v in item["variations"]],
            "stock": item.get("stock") if not item.get("variations") else [v.get("stock") for v in item["variations"]],
            "image_count": len(item.get("images", [])),
            "images": item.get("images", []),
            "staged_at": item.get("staged_at", ""),
        })
    return {
        "staged_count": len(queue),
        "staging_file": str(STAGING_FILE),
        "items": items_view,
    }


def build_excel_rows_from_items(items: list, upload_images: bool = True) -> list:
    """
    將高階商品清單轉換為『上傳模板』的資料列字典 (List[Tuple[item, row_dict, cat_info]])。
    支援單規格與多規格商品。
    """
    excel_rows = []
    cats_map = {c["id"]: c for c in load_categories()}

    for idx, raw_item in enumerate(items, start=1):
        item = normalize_and_prepare_item(raw_item, upload_images=upload_images)
        cat_id = item["category_id"]
        title = item["title"]
        description = item["description"]

        min_qty = str(item.get("min_qty", "") or "").strip()
        parent_sku = str(item.get("parent_sku", "") or "").strip()
        dangerous = str(item.get("dangerous_goods", "No") or "No").strip()
        if dangerous not in ("Yes", "No"):
            dangerous = "No"

        weight = item.get("weight", "0.5")
        length = str(item.get("length", "") or "").strip()
        width = str(item.get("width", "") or "").strip()
        height = str(item.get("height", "") or "").strip()
        dts = str(item.get("dts", "") or "").strip()
        gtin = str(item.get("gtin", "") or "").strip()
        image_urls = item.get("images", [])

        shipping_cfg = dict(DEFAULT_SHIPPING)
        custom_shipping = item.get("shipping")
        if isinstance(custom_shipping, dict):
            for k, v in custom_shipping.items():
                col_key = SHIPPING_NAME_MAP.get(k, k)
                if col_key in shipping_cfg:
                    shipping_cfg[col_key] = "開啟" if v in (True, "開啟", "open", "on", 1, "1") else "關閉"

        base_row = {
            "A": cat_id,
            "B": title,
            "C": description,
            "D": min_qty,
            "E": parent_sku,
            "F": dangerous,
            "R": gtin,
            "AB": weight,
            "AC": length,
            "AD": width,
            "AE": height,
            "AF": shipping_cfg["AF"],
            "AG": shipping_cfg["AG"],
            "AH": shipping_cfg["AH"],
            "AI": shipping_cfg["AI"],
            "AJ": shipping_cfg["AJ"],
            "AK": shipping_cfg["AK"],
            "AL": shipping_cfg["AL"],
            "AM": dts,
        }

        for img_i, img_url in enumerate(image_urls[:9]):
            if img_i < len(IMAGE_COLUMNS) and img_url and (str(img_url).startswith("http://") or str(img_url).startswith("https://")):
                base_row[IMAGE_COLUMNS[img_i]] = img_url

        variations = item.get("variations")
        if variations and isinstance(variations, list) and len(variations) > 0:
            var_group_id = str(item.get("variation_group_id") or f"VAR_{idx:03d}")
            for v_idx, var in enumerate(variations, start=1):
                row_data = dict(base_row)
                row_data["G"] = var_group_id
                row_data["H"] = str(var.get("name1", "規格") or "規格").strip()[:14]
                row_data["I"] = str(var.get("option1", f"選項{v_idx}") or f"選項{v_idx}").strip()[:20]
                if var.get("image"):
                    row_data["J"] = str(var["image"])
                if var.get("name2"):
                    row_data["K"] = str(var.get("name2")).strip()[:14]
                    row_data["L"] = str(var.get("option2", "")).strip()[:20]
                row_data["M"] = str(var["price"])
                row_data["N"] = str(var["stock"])
                if var.get("sku"):
                    row_data["O"] = str(var["sku"]).strip()
                excel_rows.append((item, row_data, cats_map.get(cat_id)))
        else:
            row_data = dict(base_row)
            row_data["M"] = str(item["price"])
            row_data["N"] = str(item["stock"])
            if item.get("sku"):
                row_data["O"] = str(item["sku"]).strip()
            excel_rows.append((item, row_data, cats_map.get(cat_id)))

    return excel_rows


def write_rows_to_new_batch_excel(excel_rows: list, output_path: Path) -> Path:
    """
    以官方 Shopee_Template.xlsx 為母本，在『上傳模板 (xl/worksheets/sheet2.xml)』
    從第 7 列 (Row 7) 開始寫入商品資料，並輸出為全新的 .xlsx 檔案。
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"找不到蝦皮官方範本母檔: {TEMPLATE_PATH}")

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        stem = output_path.stem
        suffix = output_path.suffix
        ts = datetime.datetime.now().strftime("%H%M%S")
        output_path = output_path.parent / f"{stem}_{ts}{suffix}"

    with zipfile.ZipFile(TEMPLATE_PATH, "r") as zin:
        ss_raw = zin.read("xl/sharedStrings.xml").decode("utf-8")
        s2_raw = zin.read("xl/worksheets/sheet2.xml").decode("utf-8")

        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        ss_xml = ET.fromstring(ss_raw.encode("utf-8"))
        existing_si_count = len(ss_xml.findall(".//main:si", ns))

        new_si_xml_chunks = []
        next_si_index = existing_si_count

        def add_shared_string(val: str) -> int:
            nonlocal next_si_index
            idx = next_si_index
            next_si_index += 1
            escaped = xml_escape(val)
            if val.startswith(" ") or val.endswith(" ") or "\n" in val:
                new_si_xml_chunks.append(f'<si><t xml:space="preserve">{escaped}</t></si>')
            else:
                new_si_xml_chunks.append(f"<si><t>{escaped}</t></si>")
            return idx

        row_xml_chunks = []
        for offset, (_, row_dict, _) in enumerate(excel_rows):
            r_num = 7 + offset
            cell_chunks = []
            for col in COLUMNS_ORDER:
                val = row_dict.get(col, "")
                if val is not None and str(val) != "":
                    s_idx = add_shared_string(str(val))
                    cell_chunks.append(f'<c r="{col}{r_num}" t="s"><v>{s_idx}</v></c>')
            row_xml = f'<row customHeight="true" ht="40" r="{r_num}">{"".join(cell_chunks)}</row>'
            row_xml_chunks.append(row_xml)

        if new_si_xml_chunks:
            ss_updated = re.sub(
                r'count="\d+"\s+uniqueCount="\d+"',
                f'count="{next_si_index}" uniqueCount="{next_si_index}"',
                ss_raw,
                count=1,
            )
            ss_updated = ss_updated.replace("</sst>", "".join(new_si_xml_chunks) + "</sst>")
        else:
            ss_updated = ss_raw

        s2_updated = s2_raw.replace("</sheetData>", "".join(row_xml_chunks) + "</sheetData>", 1)

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "xl/sharedStrings.xml":
                    zout.writestr(item, ss_updated.encode("utf-8"))
                elif item.filename == "xl/worksheets/sheet2.xml":
                    zout.writestr(item, s2_updated.encode("utf-8"))
                else:
                    zout.writestr(item, zin.read(item.filename))

    return output_path


def export_items_to_excel(items: list, output_arg: str = None, upload_images: bool = True) -> dict:
    excel_rows = build_excel_rows_from_items(items, upload_images=upload_images)
    if output_arg:
        out_path = Path(output_arg)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = DEFAULT_OUTPUT_DIR / f"Shopee_Batch_{ts}.xlsx"

    final_path = write_rows_to_new_batch_excel(excel_rows, out_path)

    summary = []
    for idx, (orig_item, row_dict, cat_info) in enumerate(excel_rows, start=7):
        imgs = [row_dict[c] for c in IMAGE_COLUMNS if row_dict.get(c)]
        summary.append({
            "excel_row": idx,
            "category_id": row_dict.get("A", ""),
            "category_path": cat_info["path"] if cat_info else "",
            "title": row_dict.get("B", ""),
            "price": row_dict.get("M", ""),
            "stock": row_dict.get("N", ""),
            "image_count": len(imgs),
            "cover_image": row_dict.get("S", ""),
            "all_images": imgs,
            "shipping_enabled": [
                name for name, col in [
                    ("7-ELEVEN", "AG"), ("全家", "AH"), ("萊爾富", "AI"),
                    ("蝦皮店到店", "AK"), ("蝦皮店到店-隔日到貨", "AL"),
                    ("黑貓宅急便", "AF"), ("宅配通", "AJ")
                ] if row_dict.get(col) == "開啟"
            ]
        })

    return {
        "status": "success",
        "output_file": str(final_path),
        "total_rows": len(excel_rows),
        "items": summary
    }


ATTRIBUTE_RULES_PATH = SKILL_DIR / "resources" / "attribute_rules.json"

GLOBAL_ATTRIBUTE_FIELDS = {
    "限制級商品", "限制級", "禁運品", "禁運", "是否有BSMI字號", "BSMI字號", "保固期限"
}


def load_attribute_rules(category_id: Optional[str] = None) -> dict:
    """
    載入商品屬性預設值字典（支援全域與特定分類命名空間）。
    優先載入 _global 通用規則，若有指定 category_id 則以該分類專屬規則覆蓋/擴充。
    """
    raw_rules = {}
    if ATTRIBUTE_RULES_PATH.exists():
        try:
            with open(ATTRIBUTE_RULES_PATH, "r", encoding="utf-8") as f:
                raw_rules = json.load(f)
        except Exception:
            pass

    merged = {
        "限制級商品": "否",
        "禁運品": "否",
        "是否有BSMI字號": "否",
        "保固期限": "無保固"
    }

    if "_global" in raw_rules and isinstance(raw_rules["_global"], dict):
        merged.update(raw_rules["_global"])
    else:
        # 相容舊版扁平格式
        for k, v in raw_rules.items():
            if isinstance(v, str):
                merged[k] = v

    if category_id:
        cat_str = str(category_id)
        if cat_str in raw_rules and isinstance(raw_rules[cat_str], dict):
            for k, v in raw_rules[cat_str].items():
                if not k.startswith("_") and isinstance(v, str):
                    merged[k] = v

    return merged


def save_attribute_rule(field_name: str, chosen_option: str, category_id: Optional[str] = None):
    """
    將新學習到的屬性規則寫回 attribute_rules.json 固化技能。
    全域欄位寫入 _global，分類特定欄位寫入對應的 category_id 命名空間，徹底杜絕跨分類錯套。
    """
    raw_rules = {}
    if ATTRIBUTE_RULES_PATH.exists():
        try:
            with open(ATTRIBUTE_RULES_PATH, "r", encoding="utf-8") as f:
                raw_rules = json.load(f)
        except Exception:
            pass

    if "_global" not in raw_rules or not isinstance(raw_rules["_global"], dict):
        raw_rules["_global"] = {
            "限制級商品": "否",
            "禁運品": "否",
            "是否有BSMI字號": "否",
            "保固期限": "無保固"
        }

    is_global = any(g in field_name for g in GLOBAL_ATTRIBUTE_FIELDS)
    if is_global or not category_id:
        raw_rules["_global"][field_name] = chosen_option
    else:
        cat_str = str(category_id)
        if cat_str not in raw_rules or not isinstance(raw_rules[cat_str], dict):
            raw_rules[cat_str] = {}
        raw_rules[cat_str][field_name] = chosen_option

    try:
        with open(ATTRIBUTE_RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(raw_rules, f, ensure_ascii=False, indent=2)
        sys.stderr.write(f"[SelfHealing] 成功將【{field_name}: {chosen_option}】固化至分類 {category_id or '_global'}\n")
    except Exception as e:
        sys.stderr.write(f"[SelfHealing] 儲存 attribute_rules.json 失敗: {e}\n")


def _agy_bin() -> str:
    """本機 agy 執行檔路徑，優先走 PATH，找不到才用硬編碼後備。"""
    import shutil
    return shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")

RISKY_OPTION_SUBSTR = ("新增選項", "自填選項", "AI 推薦", "載入中...", "請選擇")

def safe_default_option(options: list) -> str:
    """不呼叫 LLM 時的安全後備：排除會彈窗/高風險選項，偏好『其他/否/無』類通用值。"""
    cands = [o for o in options if not any(r in o for r in RISKY_OPTION_SUBSTR)]
    pool = cands or list(options)
    for prefer in ("其他", "否", "無", "一般", "通用", "不適用", "非應施檢驗", "非醫療"):
        for o in pool:
            if prefer in o:
                return o
    return pool[0] if pool else "否"

def ask_agy_attribute_decision(field_name: str, options: list, product_title: str) -> str:
    """
    未知必填欄位 → 本機 agy 決策。選項編號化後回傳索引，完全避開文字模糊匹配。
    任何異常一律走 safe_default_option，絕不回傳『新增選項』這類會彈窗的值。
    """
    clean_options = [
        str(o).strip() for o in options
        if str(o).strip() and str(o).strip() not in ["請選擇", "載入中...", "AI 推薦選項", "自填選項"]
    ]
    if not clean_options:
        return "否"

    numbered = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(clean_options))
    prompt = (
        f"你正在協助在蝦皮賣家中心上架二手出清/個人收藏商品。\n"
        f"【商品名稱】：{product_title}\n"
        f"【未知的必填欄位】：{field_name}\n"
        f"【候選選項】：\n{numbered}\n\n"
        f"以個人賣家出清且符合法規、能最快順利通過上架為原則。\n"
        f"防呆策略：優先規避高風險法規驗證盾（如選非應施檢驗器材可避開BSMI強制字號、避免醫療器材）；"
        f"偏好『其他/通用/否』類最寬鬆的選項；絕不選『新增選項』類會彈出視窗的值。\n"
        f"請只回傳你選中的選項編號（一個阿拉伯數字），不要回傳任何其他文字。"
    )

    try:
        res = subprocess.run(
            [_agy_bin(), "-p", prompt, "--model", "gemini-3.8-flash-low"],
            capture_output=True,
            text=True,
            timeout=25,
            cwd=str(PROJECT_ROOT)
        )
        if res.returncode == 0 and res.stdout.strip():
            raw = res.stdout.strip().replace('"', '').replace("'", "")
            m = re.search(r"\d+", raw)
            if m:
                idx = int(m.group(0)) - 1
                if 0 <= idx < len(clean_options):
                    return clean_options[idx]
            # 後備：LLM 回了文字而非編號 → 精確匹配優先
            for opt in clean_options:
                if raw == opt:
                    return opt
            for opt in clean_options:
                if raw and (raw in opt or opt in raw):
                    return opt
    except Exception as e:
        sys.stderr.write(f"[SelfHealing] agy 決策執行失敗: {e}\n")

    return safe_default_option(clean_options)


def _scope_row_for_label(draft_page, label_text: str):
    """用 label 精確文字定位所屬表單容器，避免 has_text 子字串誤命中多列。"""
    containers = draft_page.locator(".edit-row, .eds-form-item, .product-edit-form-item")
    n = containers.count()
    for i in range(n):
        c = containers.nth(i)
        try:
            lab = c.locator("label, .item-title-text, .eds-form-item__label, .edit-label, .edit-title").first
            if lab.count() == 0:
                continue
            txt = (lab.inner_text() or "").replace("*", "").strip().split("\n")[0].strip()
            if txt == label_text:
                return c
        except Exception:
            continue
    # 降級：子字串匹配
    try:
        fb = containers.filter(has_text=label_text)
        if fb.count() > 0:
            return fb.first
    except Exception:
        pass
    return None

def _read_selector_value(row_loc) -> str:
    try:
        sel = row_loc.locator(".eds-selector").first
        if sel.count() == 0:
            return ""
        return (sel.inner_text() or "").strip()
    except Exception:
        return ""

def _click_option_exact(draft_page, target_option: str) -> bool:
    """精確文字優先、包含匹配降級的選項點擊。"""
    try:
        pop = draft_page.locator(".eds-popper:not([style*='display: none'])")
        scope = pop if pop.count() > 0 else draft_page
        opts = scope.locator(".eds-option, .eds-dropdown-menu-item, [role='option']")
        if opts.count() == 0:
            return False
        for i in range(opts.count()):
            try:
                if (opts.nth(i).inner_text() or "").strip() == target_option:
                    opts.nth(i).scroll_into_view_if_needed()
                    opts.nth(i).click()
                    return True
            except Exception:
                continue
        fb = opts.filter(has_text=target_option)
        if fb.count() > 0:
            try:
                fb.first.scroll_into_view_if_needed()
                fb.first.click()
                return True
            except Exception:
                pass
    except Exception:
        pass
    try:
        return bool(draft_page.evaluate("""(optText) => {
            const container = document.querySelector('.eds-popper-container') || document.body;
            const els = Array.from(container.querySelectorAll('.eds-option, .eds-dropdown-menu-item, li, div, span'));
            let target = els.find(el => el.innerText && el.innerText.trim() === optText);
            if (!target) target = els.find(el => el.innerText && el.innerText.trim().includes(optText));
            if (target) { target.click(); return true; }
            return false;
        }""", target_option))
    except Exception:
        return False

def _fill_radio_group(row_loc, target_option: str) -> bool:
    """處理 radio/checkbox 型必填：按選項文字精確勾選。"""
    try:
        labels = row_loc.locator("label")
        for i in range(labels.count()):
            lab = labels.nth(i)
            try:
                if (lab.inner_text() or "").strip() == target_option:
                    box = lab.locator("input[type='radio'], input[type='checkbox']").first
                    if box.count() > 0:
                        if not box.is_checked():
                            box.check(force=True)
                    else:
                        lab.click()
                    return True
            except Exception:
                continue
        # 降級：文字包含
        for i in range(labels.count()):
            try:
                if target_option and target_option in (labels.nth(i).inner_text() or ""):
                    labels.nth(i).click()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def auto_fill_unresolved_attributes(draft_page, product_title: str, category_id: Optional[str] = None, condition: Optional[str] = None, product_description: str = "") -> tuple:
    """
    自動巡檢草稿頁所有未填/報錯的必填欄位（下拉、單選、文字輸入皆涵蓋）。
    回傳 (learned_rules, unresolved_fields)：
      learned_rules: {欄位名: 選取值}（僅「驗證已選上」才會固化寫檔）
      unresolved_fields: [{label, widget, options, error}]（給 task JSON + Telegram 顯示）
    流程：dirty-flag 多輪直到穩定（上限 5 輪），每輪只處理仍未填的欄位，
    規則庫命中→直接填；未命中→LLM 編號決策→驗證→固化。
    商品新舊（商品狀況/新舊 radio）走 condition 強制分支：語意判定優先，
    絕不經 LLM 臆測、絕不固化進 attribute_rules（同分類新舊混賣會污染）。
    """
    learned_rules: dict = {}
    unresolved: list = []

    # 1. 確保頁面元件已完全掛載 (SPA 渲染保護)
    try:
        draft_page.wait_for_selector(".eds-selector, .edit-row", timeout=12000)
    except Exception:
        pass
    time.sleep(1.0)

    # 2. 重量防護：為空或 0 則補齊預設值，確保物流開關可正確展開
    try:
        weight_input = draft_page.locator(".edit-row, .eds-form-item").filter(has_text="重量").locator("input").first
        if weight_input.count() > 0:
            val = (weight_input.input_value() or "").strip()
            try:
                is_zero = float(val or "0") == 0
            except Exception:
                is_zero = True
            if not val or is_zero:
                weight_input.fill("0.4")
                time.sleep(0.5)
    except Exception:
        pass

    # 3. 物流防護：依主人白名單開啟超商/店到店，關閉黑貓/宅配通
    try:
        allowed_shipping = ["7-ELEVEN", "全家", "萊爾富", "蝦皮店到店"]
        forbidden_shipping = ["黑貓", "宅配通"]
        shipping_rows = draft_page.locator("tr").filter(has_text="蝦皮支援物流")
        s_count = shipping_rows.count()
        for s_idx in range(s_count):
            s_row = shipping_rows.nth(s_idx)
            s_text = s_row.inner_text()
            s_name = s_text.splitlines()[0]
            sw = s_row.locator(".eds-switch").first
            if sw.count() > 0:
                cls = sw.get_attribute("class") or ""
                is_open = "open" in cls
                should_open = any(a in s_name for a in allowed_shipping) and not any(f in s_name for f in forbidden_shipping)
                if should_open and not is_open:
                    sw.click()
                    time.sleep(0.3)
                elif not should_open and is_open:
                    sw.click()
                    time.sleep(0.3)
    except Exception:
        pass

    # 4. 屬性多輪自愈巡檢（dirty-flag：有進展就再掃，直到穩定或 5 輪）
    SCAN_JS = """() => {
        const formItems = Array.from(document.querySelectorAll('.eds-form-item, .edit-row, .product-edit-form-item'));
        const list = [];
        for (const item of formItems) {
            const labelEl = item.querySelector('label, .item-title-text, .eds-form-item__label, .edit-label, .edit-title');
            const rawLabel = labelEl ? labelEl.innerText.trim() : '';
            const hasAsterisk = rawLabel.includes('*') || item.classList.contains('is-required') || !!item.querySelector('.eds-form-item__required');
            const labelText = rawLabel.replace(/\\*/g, '').trim().split('\\n')[0].trim();
            if (!labelText) continue;
            const selectorEl = item.querySelector('.eds-selector');
            const radios = item.querySelectorAll("input[type='radio'], input[type='checkbox']");
            const textInput = item.querySelector("input[type='text'], input:not([type]), textarea");
            const errEl = item.querySelector('.eds-form-item__error, .product-edit-form-item-error, .eds-form-item__explain');
            const isError = item.classList.contains('is-error') || item.classList.contains('error') || !!errEl;
            const errText = errEl ? errEl.innerText.trim().slice(0, 120) : '';
            let widget = '', value = '', filled = true;
            if (selectorEl) {
                widget = 'selector';
                value = selectorEl.innerText.trim().slice(0, 60);
                filled = !(value.includes('請選擇') || value === '');
            } else if (radios.length > 0) {
                widget = 'radio';
                const checked = Array.from(radios).find(r => r.checked);
                filled = !!checked;
                value = checked ? (checked.value || '已選') : '';
            } else if (textInput && (hasAsterisk || isError)) {
                widget = 'input';
                value = (textInput.value || '').slice(0, 60);
                filled = value.trim() !== '';
            } else {
                continue;
            }
            if (!filled || isError) {
                if (!list.some(e => e.label === labelText)) {
                    list.push({label: labelText, widget, value, error: errText});
                }
            }
        }
        return list;
    }"""

    OPTIONS_JS = """() => {
        const out = [];
        const poppers = Array.from(document.querySelectorAll('.eds-popper:not([style*="display: none"])'));
        const scope = poppers.length ? poppers : [document.querySelector('.eds-popper-container') || document.body];
        // virtual-scroll / lazy-load：把可滾容器捲到底再讀
        for (const sc of scope) {
            const roller = sc.querySelector('.eds-scrollbar__wrap, .eds-select-dropdown__wrap, [class*="scroll"]');
            if (roller) { roller.scrollTop = roller.scrollHeight; }
        }
        for (const sc of scope) {
            const els = Array.from(sc.querySelectorAll('.eds-option, .eds-dropdown-menu-item, .eds-select-option, [role="option"]'));
            for (const el of els) {
                const t = el.innerText ? el.innerText.trim() : '';
                if (t && t.length > 0 && t.length < 40 && !t.includes('\\n') && !['請選擇', '載入中...', 'AI 推薦選項', '自填選項'].includes(t)) {
                    if (!out.includes(t)) out.push(t);
                }
            }
        }
        return out;
    }"""

    for attempt in range(5):
        rules = load_attribute_rules(category_id=category_id)
        try:
            fields = draft_page.evaluate(SCAN_JS) or []
        except Exception:
            fields = []

        if not fields:
            unresolved = []
            break

        made_progress = False
        still_open = []
        for f in fields:
            field_name = f.get("label", "")
            widget = f.get("widget", "")
            err_text = f.get("error", "")
            if not field_name:
                continue
            row_loc = _scope_row_for_label(draft_page, field_name)
            if row_loc is None:
                still_open.append({**f, "note": "定位不到該列"})
                continue

            # 商品新舊強制分支：語意判定優先，不查規則庫、不問 LLM、不固化
            if is_condition_field(field_name):
                want = normalize_condition(condition or "", title=product_title, description=product_description)
                sys.stderr.write(f"[Condition] 商品新舊欄位【{field_name}】強制指定➔【{want}】\n")
                if widget == "radio":
                    filled_ok = _fill_radio_group(row_loc, want)
                    time.sleep(0.4)
                    if filled_ok:
                        try:
                            recheck = draft_page.evaluate(SCAN_JS) or []
                            filled_ok = not any(r.get("label") == field_name for r in recheck)
                        except Exception:
                            pass
                elif widget == "selector":
                    pass  # 下方共用 selector 流程會用 want 作為 target_option
                    target_option = want
                else:
                    target_option = want
                if widget == "radio":
                    if filled_ok:
                        made_progress = True
                    else:
                        still_open.append({**f, "note": f"商品新舊點選失敗（目標:{want}）"})
                    continue

            target_option = rules.get(field_name)
            is_new_learning = False
            filled_ok = False

            if widget == "radio":
                # radio：規則命中直接勾；無規則 → LLM 從頁面選項文字決策
                if not target_option:
                    try:
                        seen = row_loc.locator("label").all_inner_texts()
                        seen = [s.strip() for s in seen if s.strip() and len(s.strip()) < 40]
                    except Exception:
                        seen = []
                    if seen:
                        sys.stderr.write(f"[SelfHealing] 未知單選必填【{field_name}】(分類: {category_id})，選項: {seen}\n")
                        target_option = ask_agy_attribute_decision(field_name, seen, product_title)
                        is_new_learning = True
                if target_option:
                    filled_ok = _fill_radio_group(row_loc, target_option)
                    time.sleep(0.4)
                    # 驗證：重掃該列是否仍未填
                    if filled_ok:
                        try:
                            recheck = draft_page.evaluate(SCAN_JS) or []
                            filled_ok = not any(r.get("label") == field_name for r in recheck)
                        except Exception:
                            pass
            elif widget == "input":
                # 文字輸入型：只填規則庫有的值，不臆測；無規則則留待人工
                if target_option:
                    try:
                        inp = row_loc.locator("input[type='text'], input:not([type]), textarea").first
                        if inp.count() > 0:
                            inp.fill(str(target_option))
                            time.sleep(0.4)
                            filled_ok = True
                    except Exception:
                        pass
                if not filled_ok:
                    still_open.append({**f, "note": "文字輸入型無規則可填，需人工補"})
                    continue
            else:  # selector 下拉
                try:
                    sel_loc = row_loc.locator(".eds-selector").first
                    if sel_loc.count() == 0:
                        still_open.append({**f, "note": "找不到下拉元件"})
                        continue
                    sel_loc.scroll_into_view_if_needed()
                    sel_loc.click()
                    time.sleep(0.6)
                except Exception:
                    still_open.append({**f, "note": "下拉點不開"})
                    continue

                try:
                    options = draft_page.evaluate(OPTIONS_JS) or []
                except Exception:
                    options = []
                if not options:
                    # lazy-load 再試一次：滾動後重讀
                    time.sleep(0.8)
                    try:
                        options = draft_page.evaluate(OPTIONS_JS) or []
                    except Exception:
                        options = []
                if not options:
                    still_open.append({**f, "note": "選項讀不到（可能 lazy-load），已跳過不硬點"})
                    try:
                        draft_page.keyboard.press("Escape")
                    except Exception:
                        pass
                    continue

                valid_target = False
                if target_option:
                    for opt in options:
                        if target_option == opt:
                            valid_target = True
                            break
                    if not valid_target:
                        for opt in options:
                            if target_option in opt or opt in target_option:
                                target_option = opt
                                valid_target = True
                                break

                # 商品新舊 selector 若文字對不上：用包含匹配兜底，絕不丟給通用 LLM（會偏向選全新）
                if (not valid_target or not target_option) and is_condition_field(field_name):
                    want = normalize_condition(condition or "", title=product_title, description=product_description)
                    for opt in options:
                        if want in opt or opt in want:
                            target_option = opt
                            valid_target = True
                            break
                    if not valid_target:
                        # 最後兜底：二手找含「二手/2手」選項，全新找含「全新/全新品」選項
                        for opt in options:
                            if want == "二手" and ("二手" in opt or "2手" in opt or "used" in opt.lower()):
                                target_option = opt
                                valid_target = True
                                break
                            if want == "全新" and ("全新" in opt or "new" in opt.lower()):
                                target_option = opt
                                valid_target = True
                                break
                if not valid_target or not target_option:
                    # 商品新舊走不到這裡（上方已兜底）；真走不到就留待人工，絕不 LLM 亂選全新
                    if is_condition_field(field_name):
                        still_open.append({**f, "note": f"商品新舊選項對不上（目標:{target_option or '無'}，候選:{options[:6]}），需人工確認"})
                        try:
                            draft_page.keyboard.press("Escape")
                        except Exception:
                            pass
                        continue
                    sys.stderr.write(f"[SelfHealing] 未知必填【{field_name}】(分類: {category_id})，選項: {options}\n")
                    target_option = ask_agy_attribute_decision(field_name, options, product_title)
                    sys.stderr.write(f"[SelfHealing] agy 決策：【{field_name}】➔【{target_option}】\n")
                    is_new_learning = True

                clicked = _click_option_exact(draft_page, target_option)
                time.sleep(0.6)
                # 驗證閉環：重讀該下拉文字，確認真的選上（排除「新增選項」彈窗等假成功）
                if clicked:
                    cur = _read_selector_value(row_loc)
                    filled_ok = bool(cur) and ("請選擇" not in cur) and (
                        cur == target_option or target_option in cur or cur in target_option)
                    if not filled_ok:
                        sys.stderr.write(f"[SelfHealing] 驗證失敗【{field_name}】：點選後仍為『{cur}』，不固化規則\n")

            if filled_ok:
                made_progress = True
                # 商品新舊絕不固化：同分類新舊混賣，寫死會污染後續商品
                if is_new_learning and target_option and not is_condition_field(field_name):
                    learned_rules[field_name] = target_option
                    save_attribute_rule(field_name, target_option, category_id=category_id)
            else:
                still_open.append({**f, "note": f"已嘗試仍未填（目標:{target_option or '無'}）"})

        unresolved = still_open
        if not unresolved:
            break
        if not made_progress:
            # 本輪零進展：再掃無意義，直接收工保留證據
            break

    # 點擊頁面空白處關閉任何殘留之下拉浮層
    try:
        draft_page.locator(".route-portal-product, h2, .item-title-text").first.click()
        time.sleep(0.3)
    except Exception:
        pass

    return learned_rules, unresolved


def ensure_chrome_assistant(port: int = 9223, timeout: float = 10.0) -> None:
    """確保 Chrome-Assistant 正在運行，若未啟動或 CDP 無回應則自動修復重啟"""
    import urllib.request
    import subprocess
    cdp_url = f"http://127.0.0.1:{port}"
    profile_dir = Path.home() / "Library" / "Application Support" / "Google" / "Chrome-Assistant"
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    def is_healthy():
        try:
            req = urllib.request.Request(f"{cdp_url}/json/version")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    if is_healthy():
        return

    # 若 port 卡住或未啟動，強制清理舊行程以防 socket 殭屍
    subprocess.run(["pkill", "-9", "-f", "Chrome-Assistant"], capture_output=True)
    time.sleep(0.5)

    cmd = [
        chrome_bin,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,AutoDeElevate",
        "--window-size=1280,900"
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    start_t = time.perf_counter()
    while time.perf_counter() - start_t < timeout:
        if is_healthy():
            return
        time.sleep(0.4)
    raise TimeoutError(f"Chrome-Assistant 啟動超時 ({timeout}s)，未能於 {cdp_url} 回應。")


def handle_shopee_login(page, timeout: float = 15.0) -> bool:
    """若頁面處於蝦皮賣家登入頁，使用環境變數帳密自動登入"""
    import os
    username = os.getenv("SHOPEE_USERNAME")
    password = os.getenv("SHOPEE_PASSWORD")
    if not username or not password:
        return False
    try:
        user_input = page.locator('input[placeholder*="Email"], input[type="text"]').first
        pwd_input = page.locator('input[placeholder*="密碼"], input[type="password"]').first
        if user_input.count() > 0 and pwd_input.count() > 0:
            user_input.fill(username)
            pwd_input.fill(password)
            time.sleep(0.5)
            btn = page.locator('button').filter(has_text="登入").first
            if btn.count() > 0:
                btn.click()
                time.sleep(2.5)
                return True
    except Exception as e:
        print(f"[handle_shopee_login] 自動登入嘗試失敗: {e}")
    return False


def _screenshot_evidence(draft_page, task_tag: str, item_id: str) -> Optional[str]:
    """失敗時存檔截圖，回傳路徑（供 task JSON + 除錯）。"""
    try:
        from pathlib import Path as _P
        log_dir = _P(__file__).resolve().parent.parent.parent.parent / "data" / "task_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        shot = log_dir / f"{task_tag}_{item_id}.png"
        draft_page.screenshot(path=str(shot), full_page=False)
        sys.stderr.write(f"[Evidence] 失敗截圖已存: {shot}\n")
        return str(shot)
    except Exception as e:
        sys.stderr.write(f"[Evidence] 截圖失敗: {e}\n")
        return None

def _collect_field_errors(draft_page) -> list:
    """抓取每個欄位旁的紅字錯誤，Publish 被擋時知道是『哪幾個』欄位。"""
    try:
        return draft_page.evaluate("""() => {
            const out = [];
            const items = Array.from(document.querySelectorAll('.eds-form-item, .edit-row, .product-edit-form-item'));
            for (const item of items) {
                const errEl = item.querySelector('.eds-form-item__error, .product-edit-form-item-error, .eds-form-item__explain');
                if (!errEl || !errEl.innerText.trim()) continue;
                const labelEl = item.querySelector('label, .item-title-text, .eds-form-item__label, .edit-label, .edit-title');
                const label = labelEl ? labelEl.innerText.trim().replace(/\\*/g, '').split('\\n')[0].trim() : '(未知欄位)';
                out.push({label, error: errEl.innerText.trim().slice(0, 150)});
            }
            const tip = document.querySelector(".product-edit__submit-bottom-tip, .eds-message");
            if (tip && tip.innerText.trim()) out.push({label: "(頁面提示)", error: tip.innerText.trim().slice(0, 200)});
            return out;
        }""") or []
    except Exception:
        return []

def upload_excel_to_shopee(excel_path: str, timeout_sec: int = 40, expected_title: Optional[Union[str, List[str]]] = None, expected_meta: Optional[List[dict]] = None) -> dict:
    """
    透過 Chrome-Assistant (Playwright CDP) 自動將批次 Excel 檔案上傳至蝦皮賣家中心，
    並即時等待上傳狀態檢核與逐一自動發布。
    expected_meta: [{title, category_id, condition, description}]，用於把分類 ID 與
    新舊狀態帶進草稿頁的屬性自愈（修復斷鏈）。condition 由主對話 LLM 依主人
    prompt+照片語意判定（沒二手訊號即全新），草稿頁只強制執行不再臆測。
    """
    from playwright.sync_api import sync_playwright

    excel_p = Path(excel_path).expanduser().resolve()
    if not excel_p.exists():
        raise FileNotFoundError(f"找不到要上傳的 Excel 檔案: {excel_p}")

    cdp_url = "http://127.0.0.1:9223"
    ensure_chrome_assistant(port=9223)

    target_titles: List[str] = []
    if isinstance(expected_title, list):
        target_titles = [str(t) for t in expected_title if t]
    elif expected_title:
        target_titles = [str(expected_title)]

    # 分類 ID 對照表（title 前 12 字正規化 key）：草稿列表頁 DOM 沒有 category，必須從 payload 回查
    # condition/description 同理回查：主 LLM 的語意判定經 expected_meta 帶進草稿頁自愈
    cat_map: dict = {}
    cond_map: dict = {}
    desc_map: dict = {}
    for m in (expected_meta or []):
        try:
            t = str(m.get("title", "")).strip()
            c = str(m.get("category_id", "")).strip()
            if t and c:
                cat_map[t[:12]] = c
                cat_map[t] = c
            if t:
                _cond = normalize_condition(m.get("condition", ""), title=t, description=str(m.get("description", "") or ""))
                cond_map[t] = _cond
                cond_map[t[:12]] = _cond
                _desc = str(m.get("description", "") or "")
                if _desc:
                    desc_map[t] = _desc
                    desc_map[t[:12]] = _desc
        except Exception:
            continue

    from browser_relay import ChromeAssistantClient
    cdp_client = ChromeAssistantClient(port=9223)

    with cdp_client.session_lock("shopee-uploader", timeout=60.0), sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            ensure_chrome_assistant(port=9223)
            browser = p.chromium.connect_over_cdp(cdp_url)

        context = browser.contexts[0]
        # 一律建立全新獨立的分頁執行上傳，避免沿用背景陳舊/休眠分頁導致 DOM 找不到 input[type="file"]
        upload_page = context.new_page()
        upload_page.goto("https://seller.shopee.tw/portal/product-mass/import/upload", wait_until="domcontentloaded", timeout=30000)
        upload_page.wait_for_timeout(2000)

        # 精確比對路徑是否為登入頁（避免 URL query 帶有 is_from_login=true 造成誤判）
        parsed_path = urllib.parse.urlparse(upload_page.url).path
        if "/seller/login" in parsed_path or parsed_path.rstrip("/").endswith("/login"):
            handle_shopee_login(upload_page)
            upload_page.goto("https://seller.shopee.tw/portal/product-mass/import/upload", wait_until="domcontentloaded", timeout=30000)
            upload_page.wait_for_timeout(2000)

        # 確保處於「上傳檔案」頁籤
        upload_tab = upload_page.get_by_text("上傳檔案", exact=True)
        if upload_tab.count() > 0:
            try:
                upload_tab.first.click()
                upload_page.wait_for_timeout(1000)
            except Exception:
                pass

        file_input = upload_page.locator('input[type="file"]').first
        file_input.wait_for(state="attached", timeout=15000)
        file_input.set_input_files(str(excel_p))

        start_t = time.perf_counter()
        target_name = excel_p.name
        latest_record = None
        success = False

        while time.perf_counter() - start_t < timeout_sec:
            time.sleep(0.6)
            try:
                records = upload_page.evaluate("""() => {
                    const rows = Array.from(document.querySelectorAll('table tbody tr, .eds-table__body-row, .eds-table-body tr'));
                    return rows.map(r => Array.from(r.querySelectorAll('td, .eds-table__cell')).map(c => c.innerText.trim()).filter(Boolean));
                }""")
            except Exception:
                records = []

            for rec in records:
                if any(target_name in col for col in rec):
                    latest_record = rec
                    if any("成功" in col for col in rec):
                        success = True
                    break

            if success or (latest_record and any("失敗" in col for col in latest_record)):
                break

        published_products = []
        if success:
            try:
                draft_page = None
                for pg in context.pages:
                    if "list/unpublished" in pg.url:
                        draft_page = pg
                        break
                if not draft_page:
                    draft_page = context.new_page()
                    draft_page.goto("https://seller.shopee.tw/portal/product/list/unpublished/draft", wait_until="commit")
                else:
                    draft_page.goto("https://seller.shopee.tw/portal/product/list/unpublished/draft", wait_until="commit")

                # 等待草稿清單載入並擷取最新建立之商品
                try:
                    draft_page.wait_for_selector(".draft-product-list-table-row, .eds-table__row, tr", timeout=8000)
                except Exception:
                    pass

                extracted = []
                for _ in range(6):
                    time.sleep(0.8)
                    extracted = draft_page.evaluate("""() => {
                        const rows = Array.from(document.querySelectorAll(".draft-product-list-table-row, .eds-table__row, tr"));
                        const items = [];
                        for (const r of rows) {
                            const linkEl = r.querySelector("a[href*='/portal/product/unpublished/'], a[href*='unpublished']");
                            const titleEl = linkEl || r.querySelector(".product-name, [class*='title'], a");
                            if (linkEl && linkEl.href) {
                                items.push({
                                    title: titleEl ? titleEl.innerText.trim() : "",
                                    draft_url: linkEl.href,
                                    item_id: linkEl.href.split("/").pop()
                                });
                            }
                        }
                        return items;
                    }""")
                    if extracted:
                        break

                targets = []
                if target_titles and extracted:
                    used_indices = set()
                    for tt in target_titles:
                        clean_words = [w for w in tt[:20].replace('【', ' ').replace('】', ' ').split() if len(w) >= 2]
                        matched_idx = None
                        for idx, it in enumerate(extracted):
                            if idx not in used_indices and any(kw in it.get("title", "") for kw in clean_words):
                                matched_idx = idx
                                break
                        if matched_idx is not None:
                            used_indices.add(matched_idx)
                            targets.append(extracted[matched_idx])
                    if not targets:
                        targets = extracted[:len(target_titles)]
                elif extracted:
                    targets = [extracted[0]]

                for prod in targets:
                    product_info = dict(prod)
                    shop_id = os.getenv("SHOPEE_SHOP_ID", "YOUR_SHOP_ID")
                    item_id = product_info["item_id"]
                    product_info["shop_id"] = shop_id
                    product_info["live_url"] = f"https://shopee.tw/product/{shop_id}/{item_id}"
                    product_info["draft_url"] = f"https://seller.shopee.tw/portal/product/unpublished/{item_id}"
                    product_info["product_url"] = product_info["draft_url"]

                    try:
                        draft_page.goto(product_info["draft_url"], wait_until="load")
                        
                        for _ in range(25):
                            is_ready = draft_page.evaluate("""() => {
                                const text = document.body.innerText || '';
                                if (text.includes('讀取中...')) return false;
                                const inputs = Array.from(document.querySelectorAll('input, textarea'));
                                const hasFilledTitle = inputs.some(i => i.value && i.value.length >= 5 && (i.placeholder.includes('品牌名稱') || i.className.includes('eds-input') || i.name.includes('name')));
                                const hasPublishBtn = Array.from(document.querySelectorAll('button')).some(b => b.innerText.trim() === '上架');
                                return Boolean(hasFilledTitle && hasPublishBtn);
                            }""")
                            if is_ready:
                                break
                            time.sleep(0.8)
                        else:
                            time.sleep(2.0)

                        item_title = product_info.get("title", "")
                        # 分類 ID 回查：草稿頁 DOM 沒有 category，先用完整標題、再用前 12 字對 expected_meta
                        cat_id = product_info.get("category_id") or cat_map.get(item_title) or cat_map.get(item_title[:12])
                        if cat_id:
                            product_info["category_id"] = cat_id
                        # 新舊狀態回查：主 LLM 語意判定（沒二手訊號即全新），缺席則 normalize 從標題/描述推斷
                        item_cond = product_info.get("condition") or cond_map.get(item_title) or cond_map.get(item_title[:12])
                        item_desc = product_info.get("description", "") or desc_map.get(item_title) or desc_map.get(item_title[:12]) or ""
                        learned_rules, unresolved = auto_fill_unresolved_attributes(draft_page, item_title, category_id=cat_id, condition=item_cond, product_description=item_desc)
                        if learned_rules:
                            product_info["learned_rules"] = learned_rules
                        if unresolved:
                            product_info["unresolved_fields"] = unresolved

                        time.sleep(1.5)

                        publish_btn = draft_page.locator("button.eds-button--xl-large, button.eds-button--primary").filter(has_text="上架").last
                        if publish_btn.count() > 0:
                            publish_btn.click()
                            for _ in range(18):
                                time.sleep(0.8)
                                current_url = draft_page.url
                                if ("/portal/product/list" in current_url) or ("portal/product/" in current_url and "unpublished" not in current_url):
                                    product_info["is_published"] = True
                                    product_info["status"] = "published"
                                    break
                                confirm_btn = draft_page.locator(".eds-modal button.eds-button--primary, .eds-modal__footer button").filter(has_text=re.compile(r"確認|Confirm|確定|我知道了"))
                                if confirm_btn.count() > 0:
                                    try:
                                        if confirm_btn.first.is_visible():
                                            confirm_btn.first.click()
                                    except Exception:
                                        pass
                                tip = draft_page.evaluate("""() => {
                                    const tip = document.querySelector(".product-edit__submit-bottom-tip, .eds-message");
                                    return tip ? tip.innerText.trim() : null;
                                }""")
                                if tip and ("成功" in tip or "發布" in tip):
                                    product_info["is_published"] = True
                                    product_info["status"] = "published"
                                    break
                            else:
                                # 上架被擋：再自愈一輪（處理第一輪填完才長出來的連動欄位），重試一次
                                second_learned, second_open = auto_fill_unresolved_attributes(draft_page, item_title, category_id=cat_id, condition=item_cond, product_description=item_desc)
                                if second_learned:
                                    learned_rules.update(second_learned)
                                    product_info["learned_rules"] = learned_rules
                                if second_open:
                                    product_info["unresolved_fields"] = second_open
                                if second_learned:
                                    publish_btn.click()
                                    for _ in range(12):
                                        time.sleep(0.8)
                                        current_url = draft_page.url
                                        if ("/portal/product/list" in current_url) or ("portal/product/" in current_url and "unpublished" not in current_url):
                                            product_info["is_published"] = True
                                            product_info["status"] = "published"
                                            break

                                if not product_info.get("is_published"):
                                    field_errs = _collect_field_errors(draft_page)
                                    if field_errs:
                                        product_info["field_errors"] = field_errs
                                        product_info["pending_reason"] = "；".join(
                                            f"【{e.get('label')}】{e.get('error')}" for e in field_errs[:5]
                                        )
                                        if second_open:
                                            labels = "、".join(f"【{f.get('label')}】" for f in second_open[:5])
                                            product_info["pending_reason"] += f"（未填欄位：{labels}）"
                                    else:
                                        err_tip = draft_page.evaluate("""() => {
                                            const tip = document.querySelector(".product-edit__submit-bottom-tip, .eds-message");
                                            return tip ? tip.innerText.trim() : null;
                                        }""")
                                        product_info["pending_reason"] = err_tip
                                    shot = _screenshot_evidence(draft_page, excel_p.stem, str(item_id))
                                    if shot:
                                        product_info["evidence_screenshot"] = shot
                                    product_info["is_published"] = False

                        if product_info.get("is_published") and learned_rules:
                            sys.stderr.write(f"[SelfHealing] 技能庫已自主演進！新增規則已固化至分類 {cat_id or '_global'}: {learned_rules}\n")
                    except Exception as pub_err:
                        product_info["auto_publish_error"] = str(pub_err)

                    published_products.append(product_info)

                for product_info in published_products:
                    check_title = product_info.get("title", "")
                    if not check_title:
                        continue
                    GENERIC_WORDS = {
                        '全新現貨', '現貨', '全新', '正版', '日本', '熱賣', '免運',
                        '萬代', 'BANDAI', 'TAKARA', 'TOMY', 'SEGA', 'FuRyu', 'T-ARTS',
                        '扭蛋', '盒玩', '公仔', '吊飾', '附蛋紙', '附蛋殼'
                    }
                    clean_words = [w for w in re.sub(r'[【】\[\]（）()_—\-\s]+', ' ', check_title).split() if len(w) >= 2 and w not in GENERIC_WORDS]
                    distinct_words = [w for w in clean_words if len(w) >= 3]
                    search_kw = distinct_words[0] if distinct_words else (clean_words[0] if clean_words else "")
                    
                    search_urls = [
                        f"https://seller.shopee.tw/portal/product/list/live/all?operationSortBy=ctime_desc&searchKey={urllib.parse.quote(search_kw)}" if search_kw else None,
                        "https://seller.shopee.tw/portal/product/list/live/all?operationSortBy=ctime_desc"
                    ]
                    search_urls = [u for u in search_urls if u]

                    matched_live_id = None
                    for surl in search_urls:
                        if matched_live_id:
                            break
                        for _ in range(3):
                            time.sleep(1.0)
                            try:
                                draft_page.goto(surl, wait_until="load")
                                time.sleep(1.2)
                                live_id = draft_page.evaluate("""(keywords) => {
                                    const rows = Array.from(document.querySelectorAll('tbody tr, .product-item, .eds-table__body-row, .shopee-table-row, [class*="product-row"]'));
                                    for (const r of rows) {
                                        const text = r.innerText || '';
                                        if (text.includes('商品診斷') && text.includes('成效')) continue;
                                        let hits = 0;
                                        for (const kw of keywords) {
                                            if (text.includes(kw)) hits++;
                                        }
                                        if (hits >= Math.min(1, keywords.length)) {
                                            const m = text.match(/商品 ID[:：\\s]+(\\d+)/);
                                            if (m) return m[1];
                                        }
                                    }
                                    return null;
                                }""", clean_words[:5])
                                if live_id:
                                    matched_live_id = live_id
                                    break
                            except Exception:
                                pass

                    if matched_live_id:
                        product_info["item_id"] = matched_live_id
                        product_info["live_url"] = f"https://shopee.tw/product/{shop_id}/{matched_live_id}"
                        product_info["is_published"] = True
                        product_info["status"] = "published"
                        product_info["pending_reason"] = None
                    else:
                        # 嚴格防呆門檻：未在架上清單匹配到商品，絕不能認定為已上架，必須扣在草稿狀態，避免 Telegram 誤報
                        product_info["is_published"] = False
                        product_info["status"] = "draft_pending"
                        if not product_info.get("pending_reason"):
                            product_info["pending_reason"] = "商品尚未在架上商品清單出現，可能仍在草稿審核或待手動處理"
            except Exception as e:
                published_products.append({"error": str(e)})

        res = {
            "status": "success" if success else ("failed" if latest_record and any("失敗" in col for col in latest_record) else "uploaded_pending"),
            "file": str(excel_p),
            "record": latest_record,
            "unpublished_url": "https://seller.shopee.tw/portal/product/list/unpublished/draft"
        }
        res["products"] = published_products
        if published_products:
            res["product"] = published_products[0]
            first_p = published_products[0]
            if "draft_url" in first_p:
                res["product_url"] = first_p.get("draft_url")
                res["draft_url"] = first_p.get("draft_url")
                res["item_id"] = first_p.get("item_id")
                res["live_url"] = first_p.get("live_url")
        try:
            upload_page.close()
        except Exception:
            pass

    # 上架作業結束後，確保留 1 個空白頁保活視窗，並強制關閉所有蝦皮分頁與殘留之 shared_worker (如 chateasy/worker.js)，避免背景孤兒行程狂吃 CPU
    _cleanup_shopee_cdp_targets(cdp_url)
    return res


def _cleanup_shopee_cdp_targets(cdp_url: str = "http://127.0.0.1:9223") -> None:
    """關閉所有 shopee.tw 相關之 page / shared_worker / iframe，僅留空白頁保活，並執行分頁 GC"""
    try:
        from browser_relay import ChromeAssistantClient
        client = ChromeAssistantClient(port=9223)
        client.cleanup_domain_targets(("shopee.tw", "shopeemobile.com"))
        client.gc_tabs(max_tabs=8)
    except Exception:
        pass


def inspect_batch_excel(excel_path: str):
    p = Path(excel_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"找不到檔案: {p}")

    with zipfile.ZipFile(p, "r") as z:
        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        ss_xml = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared_strings = [
            "".join([t.text or "" for t in si.findall(".//main:t", ns)])
            for si in ss_xml.findall(".//main:si", ns)
        ]
        ws_xml = ET.fromstring(z.read("xl/worksheets/sheet2.xml"))
        rows_out = []
        for r in ws_xml.findall(".//main:row", ns):
            r_num = int(r.attrib.get("r", "0"))
            if r_num < 7:
                continue
            vals = {}
            for c in r.findall("main:c", ns):
                col = "".join([ch for ch in c.attrib.get("r", "") if ch.isalpha()])
                t = c.attrib.get("t")
                v_el = c.find("main:v", ns)
                if v_el is not None and v_el.text is not None:
                    vals[col] = shared_strings[int(v_el.text)] if t == "s" else v_el.text
            if vals:
                rows_out.append({"row": r_num, "cells": vals})
    return rows_out


def main():
    parser = argparse.ArgumentParser(description="Shopee Seller Batch Excel & Staging Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. search-category
    p_cat = subparsers.add_parser("search-category", help="搜尋台灣蝦皮官方分類 ID")
    p_cat.add_argument("keywords", nargs="+", help="商品關鍵字（可多個）")
    p_cat.add_argument("--limit", type=int, default=10, help="回傳筆數上限")

    # 2. stage-add (加入待上架暫存區)
    p_sadd = subparsers.add_parser("stage-add", help="將單筆或多筆商品加入本批次暫存區（支援分多次對話累積）")
    p_sadd.add_argument("payload", help="JSON 格式的單筆商品物件或商品陣列")
    p_sadd.add_argument("--no-upload-images", action="store_true", help="暫存時不上傳本機圖片")

    # 3. stage-list (查看暫存區)
    subparsers.add_parser("stage-list", help="查看目前暫存區已累積的所有待上架商品")

    # 4. stage-update (更新暫存區指定商品)
    p_supd = subparsers.add_parser("stage-update", help="修改暫存區第 N 筆商品（1-based 序號）")
    p_supd.add_argument("index", type=int, help="要修改的商品序號 (從 1 開始)")
    p_supd.add_argument("patch", help="要更新的欄位 JSON 物件 (例如 '{\"price\": 400}')")

    # 5. stage-remove (移除暫存區指定商品)
    p_srem = subparsers.add_parser("stage-remove", help="移除暫存區第 N 筆商品（1-based 序號）")
    p_srem.add_argument("index", type=int, help="要移除的商品序號 (從 1 開始)")

    # 6. stage-clear (清空暫存區)
    subparsers.add_parser("stage-clear", help="清空目前暫存區的所有商品")

    # 7. stage-export (將暫存區一鍵打包成全新 Excel 並自動清空暫存)
    p_sexp = subparsers.add_parser("stage-export", help="將暫存區所有商品打包產出為單一全新 Excel 檔，並自動清空暫存區")
    p_sexp.add_argument("--output", "-o", help="指定輸出 .xlsx 路徑（預設 ~/Desktop/Shopee_Batch_YYYYMMDD_HHMMSS.xlsx）")
    p_sexp.add_argument("--keep", action="store_true", help="產檔後保留暫存區不清空（預設會自動清空以確保下次為新批次）")
    p_sexp.add_argument("--upload", action="store_true", help="產檔後自動透過 Chrome-Assistant 上傳至蝦皮賣家中心")

    # 8. upload-image
    p_img = subparsers.add_parser("upload-image", help="壓縮本機圖片並上傳至臨時圖床取得直連 URL")
    p_img.add_argument("images", nargs="+", help="本機圖片路徑")

    # 9. upload-batch (直接上傳現有 Excel)
    p_upb = subparsers.add_parser("upload-batch", help="將現有批次 Excel 檔案上傳至蝦皮賣家中心（需先在 Chrome-Assistant 登入）")
    p_upb.add_argument("excel_path", help="Excel 檔案路徑")
    p_upb.add_argument("--timeout", type=int, default=40, help="等待上傳檢核超時秒數 (預設 40)")

    # 10. upload-direct (一步到位：單次直接打包上傳並取得商品連結)
    p_upd = subparsers.add_parser("upload-direct", help="單次直接生成 Excel、自動上傳蝦皮賣家中心並擷取商品連結（一步到位，單對話即上傳）")
    p_upd.add_argument("payload", nargs="?", help="JSON 格式的單筆商品物件或包含 items 的陣列")
    p_upd.add_argument("--input-file", "-i", help="從 JSON 檔案讀取商品資料")
    p_upd.add_argument("--output", "-o", help="指定輸出 .xlsx 路徑")
    p_upd.add_argument("--timeout", type=int, default=40, help="等待上傳檢核超時秒數 (預設 40)")

    # 11. generate-batch (單次直接產出 Excel)
    p_gen = subparsers.add_parser("generate-batch", help="單次直接生成全新批次的蝦皮上架 Excel 檔案")
    p_gen.add_argument("payload", nargs="?", help="JSON 格式的商品陣列或包含 items 的物件")
    p_gen.add_argument("--input-file", "-i", help="從 JSON 檔案讀取商品資料")
    p_gen.add_argument("--output", "-o", help="指定輸出 .xlsx 路徑")
    p_gen.add_argument("--no-upload-images", action="store_true", help="不上傳本機圖片")
    p_gen.add_argument("--upload", action="store_true", help="產檔後自動透過 Chrome-Assistant 上傳至蝦皮賣家中心")

    # 12. inspect-batch
    p_ins = subparsers.add_parser("inspect-batch", help="檢視產出的蝦皮批次 Excel 檔案內容")
    p_ins.add_argument("excel_path", help="Excel 檔案路徑")

    # 13. async-upload (非同步秒級委派背景上架)
    p_async = subparsers.add_parser("async-upload", help="非同步委派背景上架任務 (Fast Handoff，秒級釋放，透過 Telegram 推播完成通知)")
    p_async.add_argument("--payload", help="JSON 格式的單筆商品物件或包含 items 的陣列")
    p_async.add_argument("--payload-file", "--input-file", "-i", dest="payload_file", help="從 JSON 檔案讀取商品資料")

    # 14. task-status (查詢上架任務狀態)
    p_stat = subparsers.add_parser("task-status", help="查詢背景上架任務狀態與快取結果")
    p_stat.add_argument("task_id", help="TaskManager 任務識別碼 (task_YYYYMMDD_HHMMSS)")

    # 15. fetch-cover (自動搜尋官方/高畫質宣傳首圖)
    p_cover = subparsers.add_parser("fetch-cover", help="自動搜尋並下載官方/高畫質商品宣傳首圖")
    p_cover.add_argument("query", help="搜尋關鍵字（品牌、系列、型號、品名）")
    p_cover.add_argument("--limit", type=int, default=2, help="最多下載張數（預設 2）")
    p_cover.add_argument("--out-dir", default=str(PROJECT_ROOT / "data" / "downloaded_images"), help="圖片儲存目錄")
    p_cover.add_argument("--min-dim", type=int, default=400, help="最小寬高像素（預設 400）")

    args = parser.parse_args()

    if args.command == "search-category":
        matches = search_categories(args.keywords, limit=args.limit)
        print(json.dumps({"count": len(matches), "categories": matches}, ensure_ascii=False, indent=2))

    elif args.command == "stage-add":
        data = json.loads(args.payload)
        if isinstance(data, dict):
            new_items = data.get("items", [data])
        elif isinstance(data, list):
            new_items = data
        else:
            raise ValueError("payload 必須為 JSON 物件或陣列")

        queue = load_staging_queue()
        added = []
        for raw_item in new_items:
            prepared = normalize_and_prepare_item(raw_item, upload_images=not args.no_upload_images)
            queue.append(prepared)
            added.append(prepared["title"])

        save_staging_queue(queue)
        res = format_staging_summary(queue)
        res["status"] = "staged"
        res["just_added"] = added
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "stage-list":
        queue = load_staging_queue()
        res = format_staging_summary(queue)
        res["status"] = "ok"
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "stage-update":
        queue = load_staging_queue()
        idx = args.index - 1
        if idx < 0 or idx >= len(queue):
            raise IndexError(f"序號 {args.index} 超出目前暫存區範圍 (共 {len(queue)} 筆)")
        patch = json.loads(args.patch)
        merged = dict(queue[idx])
        merged.update(patch)
        queue[idx] = normalize_and_prepare_item(merged, upload_images=True)
        save_staging_queue(queue)
        res = format_staging_summary(queue)
        res["status"] = "updated"
        res["updated_index"] = args.index
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "stage-remove":
        queue = load_staging_queue()
        idx = args.index - 1
        if idx < 0 or idx >= len(queue):
            raise IndexError(f"序號 {args.index} 超出目前暫存區範圍 (共 {len(queue)} 筆)")
        removed = queue.pop(idx)
        save_staging_queue(queue)
        res = format_staging_summary(queue)
        res["status"] = "removed"
        res["removed_title"] = removed.get("title", "")
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "stage-clear":
        save_staging_queue([])
        print(json.dumps({"status": "cleared", "staged_count": 0}, ensure_ascii=False, indent=2))

    elif args.command == "stage-export":
        queue = load_staging_queue()
        if not queue:
            raise ValueError("目前暫存區是空的，沒有可匯出的待上架商品！請先透過 stage-add 加入商品。")
        res = export_items_to_excel(queue, output_arg=args.output, upload_images=True)
        if not args.keep:
            save_staging_queue([])
            res["staging_cleared"] = True
        else:
            res["staging_cleared"] = False

        if args.upload:
            upload_res = upload_excel_to_shopee(res["output_file"])
            res["upload_result"] = upload_res
            if "product_url" in upload_res:
                res["product_url"] = upload_res["product_url"]
                res["draft_url"] = upload_res.get("draft_url")
                res["live_url"] = upload_res.get("live_url")
                res["item_id"] = upload_res.get("item_id")

        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "upload-image":
        results = []
        for img_path in args.images:
            url = upload_image_to_public_url(img_path)
            results.append({"path": img_path, "url": url})
        print(json.dumps({"uploaded": results}, ensure_ascii=False, indent=2))

    elif args.command == "upload-batch":
        res = upload_excel_to_shopee(args.excel_path, timeout_sec=args.timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "upload-direct":
        if args.input_file:
            with open(Path(args.input_file).expanduser(), "r", encoding="utf-8") as f:
                data = json.load(f)
        elif args.payload:
            data = json.loads(args.payload)
        else:
            raise ValueError("請提供 JSON payload 或 --input-file")

        items = data.get("items", data) if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or len(items) == 0:
            raise ValueError("商品資料 (items) 必須為非空物件或陣列")

        excel_res = export_items_to_excel(items, output_arg=args.output, upload_images=True)
        upload_res = upload_excel_to_shopee(excel_res["output_file"], timeout_sec=args.timeout)

        out = {
            "status": upload_res.get("status"),
            "is_published": upload_res.get("product", {}).get("is_published", False),
            "pending_reason": upload_res.get("product", {}).get("pending_reason"),
            "excel_file": excel_res["output_file"],
            "total_items": len(items),
            "items_summary": excel_res.get("items", []),
            "product_url": upload_res.get("product_url"),
            "draft_url": upload_res.get("draft_url"),
            "live_url": upload_res.get("live_url"),
            "item_id": upload_res.get("item_id"),
            "upload_record": upload_res.get("record"),
            "unpublished_url": upload_res.get("unpublished_url")
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))

    elif args.command == "generate-batch":
        if args.input_file:
            with open(Path(args.input_file).expanduser(), "r", encoding="utf-8") as f:
                data = json.load(f)
        elif args.payload:
            data = json.loads(args.payload)
        else:
            raise ValueError("請提供 JSON payload 或 --input-file")

        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list) or len(items) == 0:
            raise ValueError("商品清單 (items) 必須為非空陣列")

        res = export_items_to_excel(items, output_arg=args.output, upload_images=not args.no_upload_images)
        if args.upload:
            upload_res = upload_excel_to_shopee(res["output_file"])
            res["upload_result"] = upload_res
            if "product_url" in upload_res:
                res["product_url"] = upload_res["product_url"]
                res["draft_url"] = upload_res.get("draft_url")
                res["live_url"] = upload_res.get("live_url")
                res["item_id"] = upload_res.get("item_id")

        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "inspect-batch":
        rows = inspect_batch_excel(args.excel_path)
        print(json.dumps({"file": args.excel_path, "rows": rows}, ensure_ascii=False, indent=2))

    elif args.command == "async-upload":
        if args.payload_file:
            with open(Path(args.payload_file).expanduser(), "r", encoding="utf-8") as f:
                data = json.load(f)
        elif args.payload:
            data = json.loads(args.payload)
        else:
            raise ValueError("請提供 JSON payload 或 --payload-file")

        items = data.get("items", data) if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or len(items) == 0:
            raise ValueError("商品資料 (items) 必須為非空物件或陣列")

        tasks_dir = PROJECT_ROOT / "data" / "shopee_tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        task_id = f"task_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        payload_path = tasks_dir / f"{task_id}_payload.json"
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

        from task_manager import TaskManager
        tm = TaskManager()
        worker_script = Path(__file__).resolve().parent / "shopee_worker.py"
        spawn_res = tm.spawn(
            name=f"shopee_upload_{task_id}",
            script=str(worker_script),
            args=["--payload-file", str(payload_path), "--task-id", task_id],
            ttl_minutes=15
        )

        first_item = items[0]
        out = {
            "status": "spawned",
            "task_id": task_id,
            "pid": spawn_res.get("pid"),
            "log_file": spawn_res.get("log_file"),
            "title": first_item.get("title", ""),
            "price": first_item.get("price", 0),
            "estimated_completion_sec": 20
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))

    elif args.command == "task-status":
        tasks_dir = PROJECT_ROOT / "data" / "shopee_tasks"
        cache_file = tasks_dir / f"{args.task_id}.json"
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                res = json.load(f)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            from task_manager import TaskManager, is_pid_alive
            tm = TaskManager()
            tasks = tm._load_tasks()
            matched = next((t for t in tasks if t["task_id"] == args.task_id), None)
            if matched and is_pid_alive(matched.get("pid", -1)):
                log_path = Path(matched.get("log_file", ""))
                recent_log = ""
                if log_path.exists():
                    with open(log_path, "r", encoding="utf-8") as lf:
                        recent_log = "".join(lf.readlines()[-5:])
                print(json.dumps({
                    "task_id": args.task_id,
                    "status": "running",
                    "pid": matched.get("pid"),
                    "recent_log": recent_log.strip()
                }, ensure_ascii=False, indent=2))
            else:
                print(json.dumps({
                    "task_id": args.task_id,
                    "status": "not_found",
                    "message": "查無此任務快取檔，可能未曾啟動或已清除"
                }, ensure_ascii=False, indent=2))

    elif args.command == "fetch-cover":
        covers = fetch_best_cover_images(args.query, limit=args.limit, save_dir=args.out_dir, min_dim=args.min_dim)
        print(json.dumps({"count": len(covers), "covers": covers}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
