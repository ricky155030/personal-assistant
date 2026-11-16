"""
iOS Share Extension 分享檔案解析輔助模組
支援 PDF、Word (.docx)、Excel (.xlsx)、PowerPoint (.pptx) 與純文字/CSV/程式碼檔案的文字抽取。
使用標準函式庫 zipfile + xml.etree.ElementTree 處理 Office XML 格式，避免額外第三方依賴。
"""
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

MAX_EXTRACTED_CHARS = 12000

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".log", ".xml", ".html", ".htm", ".py", ".swift", ".js", ".ts",
}


def _natural_sort_key(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return ""
        root = ET.fromstring(zf.read("word/document.xml"))
    paragraphs = []
    for p in root.iter():
        if p.tag.endswith("}p"):
            texts = [t.text for t in p.iter() if t.tag.endswith("}t") and t.text]
            if texts:
                paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def _extract_pptx_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        slides = sorted(
            [n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=_natural_sort_key,
        )
        out = []
        for idx, slide_name in enumerate(slides, start=1):
            root = ET.fromstring(zf.read(slide_name))
            texts = [t.text.strip() for t in root.iter() if t.tag.endswith("}t") and t.text and t.text.strip()]
            if texts:
                out.append(f"[投影片 {idx}]\n" + "\n".join(texts))
        return "\n\n".join(out)


def _extract_xlsx_text(path: Path) -> str:
    try:
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() if c is not None else "" for c in row]
                if any(cells):
                    rows.append(" | ".join(cells))
                if len(rows) >= 200:
                    rows.append("...(其餘列略)")
                    break
            if rows:
                out.append(f"[工作表 {sheet_name}]\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(out)
    except ImportError:
        # Fallback: pure-Python XML extraction
        with zipfile.ZipFile(path, "r") as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sroot.iter():
                    if si.tag.endswith("}si"):
                        shared.append("".join(t.text for t in si.iter() if t.tag.endswith("}t") and t.text))
            sheets = sorted(
                [n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)],
                key=_natural_sort_key,
            )
            out = []
            for idx, sname in enumerate(sheets, start=1):
                root = ET.fromstring(zf.read(sname))
                vals = []
                for c in root.iter():
                    if c.tag.endswith("}c"):
                        ctype = c.attrib.get("t", "")
                        v = next((ch.text for ch in c if ch.tag.endswith("}v") and ch.text), "")
                        if ctype == "s" and v.isdigit() and int(v) < len(shared):
                            vals.append(shared[int(v)])
                        elif v:
                            vals.append(v)
                if vals:
                    out.append(f"[工作表 {idx}]\n" + " | ".join(vals[:300]))
            return "\n\n".join(out)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        pages = []
        for idx, page in enumerate(reader.pages[:30], start=1):
            txt = (page.extract_text() or "").strip()
            if txt:
                pages.append(f"[第 {idx} 頁]\n{txt}")
        return "\n\n".join(pages)
    except Exception:
        return ""


def extract_file_preview(path: Path, max_chars: int = MAX_EXTRACTED_CHARS) -> str:
    """從分享檔案抽取可讀文字摘要（支援 PDF / DOCX / XLSX / PPTX / 純文字），截斷至 max_chars。"""
    ext = path.suffix.lower()
    text = ""
    try:
        if ext == ".docx":
            text = _extract_docx_text(path)
        elif ext == ".pptx":
            text = _extract_pptx_text(path)
        elif ext == ".xlsx":
            text = _extract_xlsx_text(path)
        elif ext == ".pdf":
            text = _extract_pdf_text(path)
        elif ext in TEXT_EXTENSIONS:
            raw = path.read_bytes()
            for enc in ("utf-8", "cp950", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
    except Exception as ex:
        return f"（檔案解析失敗：{ex}）"

    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n...(內容過長，已截斷)"
    return text


def sanitize_filename(name: str, fallback: str = "shared_file.bin") -> str:
    """清理檔名中的路徑跳脫與不安全字元"""
    base = Path(name or fallback).name
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", base).strip("._")
    return cleaned or fallback
