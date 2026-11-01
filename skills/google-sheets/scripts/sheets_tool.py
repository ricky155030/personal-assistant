#!/usr/bin/env python3
"""
Google Sheets 專用安全查詢與更新工具 (Google Sheets Tool)
嚴格封裝環境變數以防護 gws 憑證靜默刪除問題。
支援別名：baby / newborn 指向《嬰兒用品與待產備品清單》
"""

import sys
import os
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
# ponytail: 腳本直接執行時 shell 不會載入 .env，不補這段 SHEET_ALIASES_JSON 永遠讀不到
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"), override=False)
except Exception:
    pass

SHEET_ALIASES = {
    "shopping": os.getenv("SHOPPING_SHEET_ID", ""),
    "待買": os.getenv("SHOPPING_SHEET_ID", ""),
    "待買清單": os.getenv("SHOPPING_SHEET_ID", ""),
}
try:
    custom_aliases = json.loads(os.getenv("SHEET_ALIASES_JSON", "{}"))
    SHEET_ALIASES.update(custom_aliases)
except Exception:
    pass

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from common.gws_client import (
    GWS_BACKUP_DIR,
    _ensure_credentials_backup,
    _restore_credentials_if_needed,
    run_gws_command,
)

def resolve_sheet_id(name_or_id: str) -> str:
    return SHEET_ALIASES.get(name_or_id.lower(), name_or_id)

def get_first_sheet_title(sheet_id: str) -> str:
    """動態取得試算表第一個工作表的名稱，避免工作表改名導致解析失敗"""
    res = run_gws_command(["sheets", "spreadsheets", "get", "--params", json.dumps({"spreadsheetId": resolve_sheet_id(sheet_id)})])
    sheets = res.get("sheets", [])
    if sheets:
        return sheets[0].get("properties", {}).get("title", "工作表1")
    return "工作表1"

def get_sheet_values(sheet_id: str, range_name: Optional[str] = None) -> List[List[str]]:
    sid = resolve_sheet_id(sheet_id)
    if not range_name:
        title = get_first_sheet_title(sid)
        range_name = f"'{title}'!A1:Z300"
    params = {"spreadsheetId": sid, "range": range_name}
    res = run_gws_command(["sheets", "spreadsheets", "values", "get", "--params", json.dumps(params)])
    if "error" in res:
        print(f"Error: {res['error']}")
        return []
    return res.get("values", [])

def _is_fuzzy_match(kw: str, text: str) -> bool:
    """智慧模糊比對：支援包含、同義詞組（浴盆/澡盆、汽座/安全座椅等）及字元分拆比對"""
    kw = kw.strip().lower()
    text = text.lower()
    if not kw or not text:
        return False
    if kw in text or text in kw:
        return True
    synonyms = [
        {"澡盆", "浴盆", "洗澡盆", "洗澡浴盆"},
        {"汽座", "安全座椅"},
        {"提籃", "嬰兒提籃"},
        {"保護墊", "襯墊", "座墊"},
        {"奶嘴", "安撫奶嘴"},
        {"推車", "手推車", "嬰兒推車"},
        {"消毒鍋", "奶瓶消毒", "消毒機"},
        {"包屁衣", "連身衣", "蝴蝶衣", "兔裝"},
        {"積木", "積木玩具", "益智玩具"}
    ]
    for group in synonyms:
        if any(s in kw for s in group) and any(s in text for s in group):
            return True
    if 2 <= len(kw) <= 4 and all(c in text for c in kw):
        return True
    return False

def query_sheet(sheet_id: str, keyword: str):
    """在試算表中模糊搜尋關鍵字"""
    rows = get_sheet_values(sheet_id)
    if not rows:
        return
    
    headers = rows[0]
    matched = []
    kw = keyword.lower()
    
    for r in rows[1:]:
        row_str = " ".join(r).lower()
        item_name = r[1] if len(r) > 1 else ""
        if kw in row_str or _is_fuzzy_match(kw, item_name) or _is_fuzzy_match(kw, row_str):
            matched.append(r)
            
    print(f"🔍 搜尋「{keyword}」共找到 {len(matched)} 筆結果：\n")
    for row in matched:
        # 依照常見欄位輸出
        cat = row[0] if len(row) > 0 else ""
        item = row[1] if len(row) > 1 else ""
        spec = f" ({row[2]})" if len(row) > 2 and row[2] and row[2] != "—" else ""
        qty = f" x{row[3]}" if len(row) > 3 and row[3] else ""
        loc = f" [位置: {row[5]}]" if len(row) > 5 and row[5] else ""
        note = f" (備註: {row[4]})" if len(row) > 4 and row[4] else ""
        print(f"• [{cat}] {item}{spec}{qty}{loc}{note}")

def list_sheet(sheet_id: str, max_rows: int = 40):
    rows = get_sheet_values(sheet_id)
    if not rows:
        return
    headers = rows[0]
    print(f"📋 試算表內容 (共 {len(rows)-1} 筆項目，顯示前 {max_rows} 筆)：\n")
    for row in rows[1:max_rows+1]:
        cat = row[0] if len(row) > 0 else ""
        item = row[1] if len(row) > 1 else ""
        spec = f" ({row[2]})" if len(row) > 2 and row[2] and row[2] != "—" else ""
        qty = f" x{row[3]}" if len(row) > 3 and row[3] else ""
        loc = f" @ {row[5]}" if len(row) > 5 and row[5] else ""
        print(f"• [{cat}] {item}{spec}{qty}{loc}")

def append_sheet_rows(sheet_id: str, rows: List[List[str]], range_name: Optional[str] = None) -> dict:
    sid = resolve_sheet_id(sheet_id)
    if not range_name:
        title = get_first_sheet_title(sid)
        range_name = f"'{title}'!A1"
    params = {
        "spreadsheetId": sid,
        "range": range_name,
        "valueInputOption": "USER_ENTERED"
    }
    body = {
        "values": rows
    }
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "append",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])
    return res

def update_sheet_values(sheet_id: str, range_name: str, values: List[List[Any]]) -> dict:
    sid = resolve_sheet_id(sheet_id)
    params = {
        "spreadsheetId": sid,
        "range": range_name,
        "valueInputOption": "USER_ENTERED"
    }
    body = {
        "range": range_name,
        "majorDimension": "ROWS",
        "values": values
    }
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])
    return res

def batch_update_values(sheet_id: str, data: List[Dict[str, Any]]) -> dict:
    """以 Google Sheets values:batchUpdate 批次更新多個不同區域的資料"""
    sid = resolve_sheet_id(sheet_id)
    params = {
        "spreadsheetId": sid
    }
    body = {
        "valueInputOption": "USER_ENTERED",
        "data": data
    }
    res = run_gws_command([
        "sheets", "spreadsheets", "values", "batchUpdate",
        "--params", json.dumps(params),
        "--json", json.dumps(body)
    ])
    return res

def _col_idx_to_letter(col_idx: int) -> str:
    """0-indexed 欄位轉 A1 字母 (0->A, 7->H, 25->Z, 26->AA)"""
    res = ""
    n = col_idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res = chr(65 + rem) + res
    return res

def _ensure_baby_photo_folder() -> str:
    """確保 Google Drive『嬰兒備品照片庫』資料夾存在且具備 anyone reader 權限"""
    return BABY_PHOTO_FOLDER_ID

def _compose_16x9_hover_card(
    image_path: str,
    slot_num: int,
    label: str,
    item_name: str,
    spec: str = "",
    location: str = "",
    note: str = ""
) -> Path:
    """將實拍照片合成為 Google Sheets Hover 預覽最佳化之 16:9 橫式滿版資訊卡 (1200x675)"""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"找不到圖片檔案: {p}")

    temp_heic_jpg = None
    try:
        if p.suffix.lower() in (".heic", ".heif"):
            fd, temp_heic_jpg = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            subprocess.run(
                ["/usr/bin/sips", "-s", "format", "jpeg", str(p), "--out", temp_heic_jpg],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            raw_img = Image.open(temp_heic_jpg)
        else:
            raw_img = Image.open(p)

        raw_img = ImageOps.exif_transpose(raw_img)
        if raw_img.mode != "RGB":
            raw_img = raw_img.convert("RGB")
    finally:
        if temp_heic_jpg and os.path.exists(temp_heic_jpg):
            os.remove(temp_heic_jpg)

    # 16:9 滿版畫布 (1200x675)
    canvas = Image.new("RGB", (1200, 675), (250, 245, 238))
    draw = ImageDraw.Draw(canvas)
    accent = (210, 160, 110)

    # 外框白底卡片
    draw.rounded_rectangle([20, 20, 1180, 655], radius=30, fill=(255, 255, 255), outline=accent, width=7)

    # 左側實拍照底框 (x: 42..732, y: 42..633 -> 690x591)
    frame_x1, frame_y1, frame_x2, frame_y2 = 42, 42, 732, 633
    draw.rounded_rectangle([frame_x1, frame_y1, frame_x2, frame_y2], radius=20, fill=(246, 244, 240), outline=(230, 220, 210), width=3)

    max_w, max_h = (frame_x2 - frame_x1 - 20), (frame_y2 - frame_y1 - 20)
    raw_img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    paste_x = frame_x1 + (frame_x2 - frame_x1 - raw_img.width) // 2
    paste_y = frame_y1 + (frame_y2 - frame_y1 - raw_img.height) // 2
    canvas.paste(raw_img, (paste_x, paste_y))

    # 右側大字資訊區 (x: 760..1155)
    font_badge = font_title = font_info = None
    for font_candidate in (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ):
        if os.path.exists(font_candidate):
            try:
                font_badge = ImageFont.truetype(font_candidate, 40)
                font_title = ImageFont.truetype(font_candidate, 38)
                font_info = ImageFont.truetype(font_candidate, 26)
                break
            except Exception:
                continue
    if font_badge is None:
        font_badge = font_title = font_info = ImageFont.load_default()

    # #N 編號標籤
    draw.rounded_rectangle([760, 65, 950, 138], radius=18, fill=accent)
    draw.text((788, 74), f"#{slot_num} 實品", fill=(255, 255, 255), font=font_badge)

    # 款式主標題（自動換行，最多 2 行）
    display_title = (label or item_name).strip()
    line1 = display_title[:10]
    line2 = display_title[10:20]
    draw.text((760, 175), line1, fill=(40, 40, 40), font=font_title)
    y_cursor = 235
    if line2:
        draw.text((760, 230), line2, fill=(40, 40, 40), font=font_title)
        y_cursor = 290

    # 分隔細線
    draw.line([(760, y_cursor + 10), (1145, y_cursor + 10)], fill=(230, 220, 210), width=3)
    y_cursor += 35

    # 品項顯示去除重複括號說明，避免超過右邊界
    clean_base_item = item_name.split("(")[0].split("（")[0].strip() or item_name
    info_lines = [
        f"品項：{clean_base_item}",
        f"規格：{spec[:16] if spec else '—'}",
        f"位置：{location[:12] if location else '待歸位'}",
    ]
    if note:
        info_lines.append(f"狀態：{note[:12]}")

    for line in info_lines:
        draw.text((760, y_cursor), line, fill=(95, 95, 95), font=font_info)
        y_cursor += 52

    out_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "temp_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, out_path_str = tempfile.mkstemp(prefix="baby_card_", suffix=".jpg", dir=str(out_dir))
    os.close(fd)
    out_path = Path(out_path_str)
    canvas.save(out_path, "JPEG", quality=90)
    return out_path

def upload_baby_photo_card(
    image_path: str,
    slot_num: int,
    label: str,
    item_name: str,
    spec: str = "",
    location: str = "",
    note: str = ""
) -> Dict[str, str]:
    """合成 16:9 Hover 資訊卡並上傳至 Google Drive，回傳 file_id、view_url 與 HYPERLINK 公式"""
    card_path = _compose_16x9_hover_card(
        image_path=image_path,
        slot_num=slot_num,
        label=label,
        item_name=item_name,
        spec=spec,
        location=location,
        note=note
    )
    try:
        folder_id = _ensure_baby_photo_folder()
        safe_spec = f"_{spec}" if spec else ""
        safe_label = f"_{label}" if label else ""
        drive_filename = f"{item_name}{safe_spec}_#{slot_num}{safe_label}.jpg".replace("/", "-")
        res = run_gws_command([
            "drive", "files", "create",
            "--params", json.dumps({"fields": "id, name, webViewLink"}),
            "--json", json.dumps({"name": drive_filename, "parents": [folder_id]}),
            "--upload", str(card_path)
        ])
        fid = res.get("id", "")
        if not fid:
            raise RuntimeError(f"Google Drive 上傳失敗: {res}")
        view_url = f"https://drive.google.com/file/d/{fid}/view"
        short_tag = (label or item_name).strip()
        link_text = f"📸 #{slot_num} {short_tag}".replace('"', "'")
        formula = f'=HYPERLINK("{view_url}", "{link_text}")'
        return {
            "file_id": fid,
            "view_url": view_url,
            "formula": formula,
            "link_text": link_text
        }
    finally:
        if card_path.exists():
            try:
                card_path.unlink()
            except Exception:
                pass

def _spec_matches(target_spec: str, row_spec: str) -> bool:
    """比對尺寸/規格是否吻合（例如 '60cm' 吻合 'NB～60 cm'，但不吻合 '70 cm'）"""
    import re
    ts = target_spec.strip().lower().replace(" ", "")
    rs = row_spec.strip().lower().replace(" ", "")
    if not ts:
        return True
    if not rs:
        return False
    if ts in rs or rs in ts:
        return True
    # 擷取數字比對 (例如 60 vs NB~60cm)
    t_nums = set(re.findall(r"\d+", ts))
    r_nums = set(re.findall(r"\d+", rs))
    if t_nums and r_nums:
        return bool(t_nums & r_nums)
    return False

def batch_record_items(sheet_id: str, items: List[Dict[str, Any]]) -> dict:
    """智慧批次記錄（支援隨拍隨補圖片）：一次查詢試算表，自動比對現有品項（含規格尺寸區分）。
    - 若已存在品項：自動產生 range update（更新狀態、數量、位置、備註），若有傳入 photo/photos 則自動往該列右方第一個空白照片格 (H, I, J...) 追加 16:9 Hover 超連結！
    - 若為新品項：自動組裝成新列（含照片超連結），並透過 append 一次全數新增。
    """
    sid = resolve_sheet_id(sheet_id)
    title = get_first_sheet_title(sid)
    full_range = f"'{title}'!A1:Z500"
    rows = get_sheet_values(sid, full_range)
    if not rows:
        return {"error": "無法讀取試算表資料"}

    headers = rows[0]
    existing_rows = rows[1:]

    updates_data = []
    to_append = []
    updated_items = []
    appended_items = []
    max_photo_col_idx = max(len(headers) - 1, 6)

    for item in items:
        base_name = (item.get("item") or item.get("name") or item.get("品項") or "").strip()
        if not base_name:
            continue

        style_label = str(item.get("label") or item.get("style") or item.get("款式") or "").strip()
        raw_spec = str(item.get("spec") or item.get("規格") or "").strip()

        # B 欄 (品項) 保持乾淨的標準大類名稱（如：包屁衣、褲子、蝴蝶衣、奶嘴），方便篩選器分類！
        # 詳細花色/品牌/款式統一收納至 C 欄 (規格)，格式：「尺寸｜款式特徵」
        target_name = base_name
        if style_label and style_label.lower() not in raw_spec.lower():
            target_spec = f"{raw_spec}｜{style_label}" if raw_spec else style_label
        else:
            target_spec = raw_spec

        has_specific_style = bool(style_label or "｜" in target_spec or "/" in target_spec)
        force_new_row = bool(item.get("new_row", False))

        target_norm = target_name.lower()
        matched_row_idx = None
        matched_row = None

        if not force_new_row:
            # 收集所有名稱吻合的候選列
            candidates = []
            for r_i, r in enumerate(existing_rows):
                r_item = r[1].strip() if len(r) > 1 else ""
                r_spec = r[2].strip() if len(r) > 2 else ""
                r_norm = r_item.lower()
                if not r_norm:
                    continue
                if has_specific_style:
                    # 有特定花色/款式時：只有當既有列的「品項相同」且「規格/花色也包含同款名稱」才合併！
                    # （若既有列品項名稱完全相同且規格尚為空白，視為待補齊之佔位列，亦予吻合）
                    # 絕不會誤吸進籠統的「還沒點完」舊總數列，也確保不同花色各自獨立一列
                    if (target_norm == r_norm or _is_fuzzy_match(target_norm, r_norm)) and (
                        (style_label and len(style_label) >= 2 and style_label.lower() in r_spec.lower())
                        or (target_spec and target_spec.lower() == r_spec.lower())
                        or (not r_spec.strip() and target_norm == r_norm)
                    ):
                        candidates.append((1, r_i + 2, r))
                else:
                    if target_norm == r_norm or (len(target_norm) >= 2 and target_norm in r_norm) or (len(r_norm) >= 2 and r_norm in target_norm):
                        candidates.append((1, r_i + 2, r))
                    elif _is_fuzzy_match(target_norm, r_norm):
                        candidates.append((2, r_i + 2, r))

            # 若有指定規格（例如 60cm vs 70cm），優先挑選規格吻合的列
            if candidates:
                if target_spec:
                    spec_matched = [c for c in candidates if _spec_matches(target_spec, c[2][2] if len(c[2]) > 2 else "")]
                    if spec_matched:
                        spec_matched.sort(key=lambda x: x[0])
                        _, matched_row_idx, matched_row = spec_matched[0]
                    else:
                        candidates.sort(key=lambda x: x[0])
                        _, matched_row_idx, matched_row = candidates[0]
                else:
                    candidates.sort(key=lambda x: x[0])
                    _, matched_row_idx, matched_row = candidates[0]

        # 解析圖片清單 (支援單張 photo/image 或多張 photos/images)
        raw_photos = []
        if item.get("photo") or item.get("image"):
            raw_photos.append({
                "path": item.get("photo") or item.get("image"),
                "label": item.get("label") or item.get("style") or item.get("款式") or ""
            })
        for p_entry in (item.get("photos") or item.get("images") or []):
            if isinstance(p_entry, str):
                raw_photos.append({"path": p_entry, "label": item.get("label") or ""})
            elif isinstance(p_entry, dict) and (p_entry.get("path") or p_entry.get("photo")):
                raw_photos.append({
                    "path": p_entry.get("path") or p_entry.get("photo"),
                    "label": p_entry.get("label") or p_entry.get("style") or item.get("label") or ""
                })

        if matched_row_idx and matched_row:
            # 找到現有項目，進行更新
            cat = matched_row[0] if len(matched_row) > 0 else ""
            curr_item = matched_row[1] if len(matched_row) > 1 else target_name
            curr_spec = matched_row[2] if len(matched_row) > 2 else ""
            curr_qty = matched_row[3] if len(matched_row) > 3 else ""
            curr_note = matched_row[4] if len(matched_row) > 4 else ""
            curr_loc = matched_row[5] if len(matched_row) > 5 else ""

            new_spec = target_spec or curr_spec
            # 若設定 qty_add (例如拍一件衣服自動 +1)
            if item.get("qty_add") is not None and str(curr_qty).isdigit():
                new_qty = str(int(curr_qty) + int(item["qty_add"]))
            else:
                new_qty = str(item.get("qty") if item.get("qty") is not None else (item.get("數量") if item.get("數量") is not None else curr_qty))
            new_note = item.get("note") or item.get("備註") or item.get("status") or item.get("狀態") or curr_note
            new_loc = item.get("location") or item.get("位置") or curr_loc

            # 更新範圍 C{row}:F{row}
            update_range = f"'{title}'!C{matched_row_idx}:F{matched_row_idx}"
            updates_data.append({
                "range": update_range,
                "values": [[new_spec, new_qty, new_note, new_loc]]
            })

            uploaded_links = []
            if raw_photos:
                # 尋找該列從 H 欄 (index 7) 開始第一個空白格
                next_col_idx = 7
                while next_col_idx < len(matched_row) and str(matched_row[next_col_idx]).strip() != "":
                    next_col_idx += 1

                for p_info in raw_photos:
                    slot_num = (next_col_idx - 7) + 1
                    up_res = upload_baby_photo_card(
                        image_path=p_info["path"],
                        slot_num=slot_num,
                        label=p_info["label"] or curr_item,
                        item_name=curr_item,
                        spec=new_spec,
                        location=new_loc,
                        note=new_note
                    )
                    col_letter = _col_idx_to_letter(next_col_idx)
                    updates_data.append({
                        "range": f"'{title}'!{col_letter}{matched_row_idx}",
                        "values": [[up_res["formula"]]]
                    })
                    # 同步更新記憶體中的 matched_row，避免同批次多張圖覆蓋同一格
                    while len(matched_row) <= next_col_idx:
                        matched_row.append("")
                    matched_row[next_col_idx] = up_res["link_text"]
                    max_photo_col_idx = max(max_photo_col_idx, next_col_idx)
                    uploaded_links.append({
                        "col": f"{col_letter}{matched_row_idx}",
                        "slot": slot_num,
                        "link_text": up_res["link_text"],
                        "view_url": up_res["view_url"]
                    })
                    next_col_idx += 1

            updated_items.append({
                "row": matched_row_idx,
                "item": curr_item,
                "category": cat,
                "spec": new_spec,
                "qty": new_qty,
                "note": new_note,
                "location": new_loc,
                "photos_added": uploaded_links
            })
        else:
            # 新項目，新增至 append 清單
            new_cat = item.get("category") or item.get("類別") or "其他"
            new_spec = target_spec
            new_qty = str(item.get("qty") if item.get("qty") is not None else (item.get("數量") if item.get("數量") is not None else "1"))
            new_note = item.get("note") or item.get("備註") or item.get("status") or item.get("狀態") or "已有"
            new_loc = item.get("location") or item.get("位置") or ""
            new_advice = item.get("advice") or item.get("採購建議") or ""

            row_vals = [new_cat, target_name, new_spec, new_qty, new_note, new_loc, new_advice]
            uploaded_links = []
            for idx_p, p_info in enumerate(raw_photos):
                slot_num = idx_p + 1
                up_res = upload_baby_photo_card(
                    image_path=p_info["path"],
                    slot_num=slot_num,
                    label=p_info["label"] or target_name,
                    item_name=target_name,
                    spec=new_spec,
                    location=new_loc,
                    note=new_note
                )
                row_vals.append(up_res["formula"])
                max_photo_col_idx = max(max_photo_col_idx, 7 + idx_p)
                uploaded_links.append({
                    "slot": slot_num,
                    "link_text": up_res["link_text"],
                    "view_url": up_res["view_url"]
                })

            to_append.append(row_vals)
            appended_items.append({
                "category": new_cat,
                "item": target_name,
                "spec": new_spec,
                "qty": new_qty,
                "note": new_note,
                "location": new_loc,
                "photos_added": uploaded_links
            })

    # 自動補齊第 1 列照片欄標題 (實品照片 1, 實品照片 2...)
    if max_photo_col_idx >= 7:
        header_updates = []
        for c_i in range(7, max_photo_col_idx + 1):
            curr_h = headers[c_i].strip() if c_i < len(headers) else ""
            if not curr_h:
                col_letter = _col_idx_to_letter(c_i)
                updates_data.append({
                    "range": f"'{title}'!{col_letter}1",
                    "values": [[f"實品照片 {c_i - 6}"]]
                })

    result = {
        "status": "success",
        "updated_count": len(updated_items),
        "appended_count": len(appended_items),
        "updated": updated_items,
        "appended": appended_items
    }

    if updates_data:
        update_res = batch_update_values(sid, updates_data)
        result["update_api_result"] = update_res

    if to_append:
        append_res = append_sheet_rows(sid, to_append)
        result["append_api_result"] = append_res

    return result

def _parse_input_json(raw: str):
    """支援直接傳 JSON 字串、@檔案路徑、或 - (stdin)"""
    raw = raw.strip()
    if raw.startswith("@"):
        with open(raw[1:], "r", encoding="utf-8") as f:
            return json.load(f)
    elif raw == "-":
        return json.load(sys.stdin)
    return json.loads(raw)

def main():
    if len(sys.argv) < 2:
        print("Usage: sheets_tool.py <query|list|get|append|update|batch_append|batch_update|batch_record> [sheet_alias_or_id] [params...]")
        sys.exit(1)
        
    cmd = sys.argv[1]
    sheet_id = sys.argv[2] if len(sys.argv) > 2 else "baby"
    
    if cmd == "query":
        kw = sys.argv[3] if len(sys.argv) > 3 else ""
        if not kw:
            print("請提供搜尋關鍵字")
            sys.exit(1)
        query_sheet(sheet_id, kw)
    elif cmd == "list":
        list_sheet(sheet_id)
    elif cmd == "get":
        rng = sys.argv[3] if len(sys.argv) > 3 else None
        vals = get_sheet_values(sheet_id, rng)
        print(json.dumps(vals, ensure_ascii=False, indent=2))
    elif cmd in ("append", "batch_append"):
        if len(sys.argv) < 4:
            print("Usage: sheets_tool.py append|batch_append <sheet_alias_or_id> '<json_array_of_rows|@file>'")
            sys.exit(1)
        rows = _parse_input_json(sys.argv[3])
        if rows and isinstance(rows, list) and not isinstance(rows[0], list):
            rows = [rows]  # 單列轉二維陣列
        res = append_sheet_rows(sheet_id, rows)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "update":
        if len(sys.argv) < 5:
            print("Usage: sheets_tool.py update <sheet_alias_or_id> <range> '<json_array_of_rows|@file>'")
            sys.exit(1)
        rng = sys.argv[3]
        rows = _parse_input_json(sys.argv[4])
        res = update_sheet_values(sheet_id, rng, rows)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "batch_update":
        if len(sys.argv) < 4:
            print("Usage: sheets_tool.py batch_update <sheet_alias_or_id> '<json_data_array|@file>'")
            sys.exit(1)
        data = _parse_input_json(sys.argv[3])
        res = batch_update_values(sheet_id, data)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd == "batch_record":
        if len(sys.argv) < 4:
            print("Usage: sheets_tool.py batch_record <sheet_alias_or_id> '<json_items_array|@file>'")
            sys.exit(1)
        items = _parse_input_json(sys.argv[3])
        if isinstance(items, dict):
            items = [items]
        res = batch_record_items(sheet_id, items)
        
        # 友善終端機/口語輸出
        print(f"✅ 智慧批次記錄完成！共更新 {res['updated_count']} 筆，新增 {res['appended_count']} 筆：\n")
        if res["updated"]:
            print("📌 更新現有項目：")
            for u in res["updated"]:
                photo_info = ""
                if u.get("photos_added"):
                    tags = ", ".join([f"{p['col']}:{p['link_text']}" for p in u["photos_added"]])
                    photo_info = f" | 圖片追加: [{tags}]"
                print(f"• [{u['category']}] {u['item']} (第 {u['row']} 列) ➔ 規格: {u['spec']} | 數量: {u['qty']} | 狀態/備註: {u['note']} | 位置: {u['location']}{photo_info}")
            print()
        if res["appended"]:
            print("📌 新增項目：")
            for a in res["appended"]:
                photo_info = ""
                if a.get("photos_added"):
                    tags = ", ".join([p["link_text"] for p in a["photos_added"]])
                    photo_info = f" | 圖片: [{tags}]"
                print(f"• [{a['category']}] {a['item']} (規格: {a['spec']}) x{a['qty']} [{a['note']} @ {a['location']}]{photo_info}")
            print()
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()


