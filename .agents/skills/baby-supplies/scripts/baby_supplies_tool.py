#!/usr/bin/env python3
"""
待產與嬰幼兒用品備品清單專屬管理工具 (Baby Supplies Checklist Tool)
支援：
1. add: 新增備品列，若提供 --photo 則自動複製至專案內暫存區、上傳至 Google Drive 專屬資料夾，並組裝 =HYPERLINK 公式寫入試算表。
2. search: 關鍵字搜尋備品清單。
3. list: 依類別或收納位置篩選備品。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional

BASE_DIR = next(
    (p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()),
    Path(__file__).resolve().parents[2],
)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"), override=False)
except Exception:
    pass

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()


def _get_env_config():
    sheet_id = os.getenv("BABY_SUPPLIES_SHEET_ID", "").strip()
    folder_id = os.getenv("BABY_SUPPLIES_DRIVE_FOLDER_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("環境變數 BABY_SUPPLIES_SHEET_ID 未設定，請於 .env 中配置。")
    return sheet_id, folder_id


def _upload_photo_to_drive(photo_path: str, item: str, spec: str, index: int, label: str, folder_id: str) -> str:
    src = Path(photo_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"找不到照片檔案: {src}")

    temp_dir = BASE_DIR / "data" / "temp_images"
    temp_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix or ".jpg"
    rel_temp = Path("data") / "temp_images" / f"baby_upload_{uuid.uuid4().hex[:8]}{ext}"
    abs_temp = BASE_DIR / rel_temp
    shutil.copy2(src, abs_temp)

    safe_label = label.strip() or f"{item}_{spec}"
    drive_filename = f"{item}_{spec}_#{index}_{safe_label}{ext}"
    meta = {"name": drive_filename}
    if folder_id:
        meta["parents"] = [folder_id]

    try:
        res = subprocess.run(
            ["gws", "drive", "files", "create", "--json", json.dumps(meta, ensure_ascii=False), "--upload", str(rel_temp)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        if res.returncode != 0:
            raise RuntimeError(f"上傳 Google Drive 失敗: {res.stderr or res.stdout}")
        data = json.loads(res.stdout)
        file_id = data.get("id")
        if not file_id:
            raise RuntimeError(f"Google Drive 未回傳檔案 ID: {res.stdout}")
        return f'=HYPERLINK("https://drive.google.com/file/d/{file_id}/view", "📸 #{index} {safe_label}")'
    finally:
        if abs_temp.exists():
            abs_temp.unlink()


def cmd_add(args: argparse.Namespace):
    sheet_id, folder_id = _get_env_config()
    photo1_cell = ""
    photo2_cell = ""

    if args.photo:
        label1 = args.photo_label or f"{args.item} ({args.spec})"
        photo1_cell = _upload_photo_to_drive(args.photo, args.item, args.spec, 1, label1, folder_id)

    if args.photo2:
        label2 = args.photo2_label or f"{args.item} ({args.spec}) #2"
        photo2_cell = _upload_photo_to_drive(args.photo2, args.item, args.spec, 2, label2, folder_id)

    row: List[str] = [
        args.category,
        args.item,
        args.spec,
        str(args.qty),
        args.note or "",
        args.location or "待整理",
        args.purchase_tip or "",
        photo1_cell,
    ]
    if photo2_cell:
        row.append(photo2_cell)

    params = {
        "spreadsheetId": sheet_id,
        "range": "備品清單!A:I",
        "valueInputOption": "USER_ENTERED",
    }
    body = {"values": [row]}
    res = subprocess.run(
        ["gws", "sheets", "spreadsheets", "values", "append", "--params", json.dumps(params, ensure_ascii=False), "--json", json.dumps(body, ensure_ascii=False)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if res.returncode != 0:
        raise RuntimeError(f"寫入試算表失敗: {res.stderr or res.stdout}")
    print(res.stdout)


def cmd_search(args: argparse.Namespace):
    sheet_id, _ = _get_env_config()
    sheets_script = BASE_DIR / ".agents" / "skills" / "google-sheets" / "scripts" / "sheets_tool.py"
    subprocess.run([sys.executable, str(sheets_script), "search", sheet_id, args.keyword], check=True)


def cmd_list(args: argparse.Namespace):
    sheet_id, _ = _get_env_config()
    params = {"spreadsheetId": sheet_id, "range": "備品清單!A1:I300"}
    res = subprocess.run(
        ["gws", "sheets", "spreadsheets", "values", "get", "--params", json.dumps(params, ensure_ascii=False)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    data = json.loads(res.stdout)
    rows = data.get("values", [])
    if not rows:
        print("試算表無資料")
        return
    headers = rows[0]
    matched = []
    for idx, r in enumerate(rows[1:], start=2):
        cat = r[0] if len(r) > 0 else ""
        loc = r[5] if len(r) > 5 else ""
        if args.category and args.category not in cat:
            continue
        if args.location and args.location.lower() not in loc.lower():
            continue
        pairs = [f"{headers[i]}: {r[i]}" for i in range(min(len(headers), len(r))) if r[i]]
        matched.append(f"• [第 {idx} 列] " + " ｜ ".join(pairs))
    print(f"共找到 {len(matched)} 筆備品：")
    for line in matched:
        print(line)


def main():
    parser = argparse.ArgumentParser(description="嬰幼兒備品清單專屬管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="新增備品（支援自動上傳實品照片並寫入超連結）")
    p_add.add_argument("--category", required=True, help="類別（如：哺育餵養、沐浴與護理、嬰幼衣物、尿布衛生）")
    p_add.add_argument("--item", required=True, help="品項名稱（如：奶瓶奶嘴）")
    p_add.add_argument("--spec", required=True, help="規格與品牌描述")
    p_add.add_argument("--qty", default="1", help="數量")
    p_add.add_argument("--note", default="已有", help="備註")
    p_add.add_argument("--location", default="待整理", help="收納位置")
    p_add.add_argument("--purchase-tip", default="", help="日本採購建議 / 購買地點")
    p_add.add_argument("--photo", default="", help="實品照片 1 本機路徑")
    p_add.add_argument("--photo-label", default="", help="實品照片 1 顯示短標籤")
    p_add.add_argument("--photo2", default="", help="實品照片 2 本機路徑")
    p_add.add_argument("--photo2-label", default="", help="實品照片 2 顯示短標籤")
    p_add.set_defaults(func=cmd_add)

    p_search = sub.add_parser("search", help="搜尋備品關鍵字")
    p_search.add_argument("keyword", help="搜尋關鍵字")
    p_search.set_defaults(func=cmd_search)

    p_list = sub.add_parser("list", help="依分類或收納位置列出備品")
    p_list.add_argument("--category", default="", help="篩選類別")
    p_list.add_argument("--location", default="", help="篩選收納位置")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
