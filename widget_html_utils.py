"""HTML Widget 區塊抽取與串流過濾工具。

支援從助理回覆中抽取 `<widget_html>...</widget_html>` 或 ```widget-html ... ``` 區塊，
確保語音 TTS 與對話純文字氣泡不被 HTML 原始碼干擾，並將抽取出的 HTML 放入 metadata.widget_html。
"""
import re
from typing import Optional, Tuple

_WIDGET_HTML_TAG_RE = re.compile(r"<widget_html>\s*(.*?)\s*</widget_html>", re.IGNORECASE | re.DOTALL)
_WIDGET_HTML_FENCE_RE = re.compile(r"```widget-html\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)

_OPEN_TAGS = ("<widget_html>", "```widget-html")
_CLOSE_TAGS = {
    "<widget_html>": "</widget_html>",
    "```widget-html": "```",
}


def extract_widget_html(text: str) -> Tuple[str, Optional[str]]:
    """從完整回覆中抽取 HTML Widget 內容並回傳 (清理後純文字, widget_html 或 None)。"""
    if not text:
        return "", None

    fragments: list[str] = []

    def _collect(match: re.Match) -> str:
        inner = (match.group(1) or "").strip()
        if inner:
            fragments.append(inner)
        return ""

    cleaned = _WIDGET_HTML_TAG_RE.sub(_collect, text)
    cleaned = _WIDGET_HTML_FENCE_RE.sub(_collect, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if not fragments:
        return cleaned, None
    return cleaned, "\n".join(fragments)


def _trailing_prefix_len(buf: str, candidates: Tuple[str, ...]) -> int:
    """回傳 buf 尾端與任一 candidate 開頭部分重疊的最大長度（大小寫不敏感）。"""
    lower_buf = buf.lower()
    max_keep = 0
    for tag in candidates:
        limit = min(len(lower_buf), len(tag) - 1)
        for k in range(1, limit + 1):
            if lower_buf.endswith(tag[:k]):
                if k > max_keep:
                    max_keep = k
    return max_keep


class WidgetHtmlStreamFilter:
    """串流過濾器：在 SSE text_delta 與 TTS 切句前即時隱藏 `<widget_html>...</widget_html>` 區塊。"""

    def __init__(self) -> None:
        self._buf = ""
        self._active_close_tag: Optional[str] = None

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        self._buf += delta
        out_parts: list[str] = []

        while self._buf:
            if self._active_close_tag is None:
                lower_buf = self._buf.lower()
                found_idx = -1
                found_tag = None
                for tag in _OPEN_TAGS:
                    idx = lower_buf.find(tag)
                    if idx != -1 and (found_idx == -1 or idx < found_idx):
                        found_idx = idx
                        found_tag = tag

                if found_idx != -1 and found_tag is not None:
                    if found_idx > 0:
                        out_parts.append(self._buf[:found_idx])
                    self._buf = self._buf[found_idx + len(found_tag):]
                    self._active_close_tag = _CLOSE_TAGS[found_tag]
                    continue

                keep = _trailing_prefix_len(self._buf, _OPEN_TAGS)
                emit_len = len(self._buf) - keep
                if emit_len > 0:
                    out_parts.append(self._buf[:emit_len])
                    self._buf = self._buf[emit_len:]
                break
            else:
                close_tag = self._active_close_tag
                lower_buf = self._buf.lower()
                close_idx = lower_buf.find(close_tag)
                if close_idx != -1:
                    self._buf = self._buf[close_idx + len(close_tag):]
                    self._active_close_tag = None
                    continue

                keep = _trailing_prefix_len(self._buf, (close_tag,))
                self._buf = self._buf[len(self._buf) - keep:] if keep > 0 else ""
                break

        return "".join(out_parts)

    def flush(self) -> str:
        if self._active_close_tag is not None:
            self._buf = ""
            self._active_close_tag = None
            return ""
        rem = self._buf
        self._buf = ""
        return rem
