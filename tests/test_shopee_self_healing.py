"""Tests for Shopee seller self-healing publish button & field scan rules."""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOPEE_TOOL_PATH = REPO_ROOT / "skills" / "shopee-seller" / "scripts" / "shopee_tool.py"


def _load_shopee_tool():
    spec = importlib.util.spec_from_file_location("shopee_tool", SHOPEE_TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeLocator:
    def __init__(self, items):
        self._items = list(items)

    def filter(self, has_text=None):
        if has_text is None:
            return self
        matched = []
        for item in self._items:
            text = item["text"]
            if hasattr(has_text, "search"):
                if has_text.search(text):
                    matched.append(item)
            elif str(has_text) in text:
                matched.append(item)
        return _FakeLocator(matched)

    def count(self):
        return len(self._items)

    @property
    def first(self):
        return _FakeLocator(self._items[:1])

    def nth(self, idx):
        return _FakeLocator([self._items[idx]])

    def inner_text(self):
        return self._items[0]["text"] if self._items else ""


class _FakePage:
    def __init__(self, buttons):
        self._buttons = buttons

    def locator(self, selector: str):
        visible_only = ":visible" in selector
        exclude_outline = ":not(.eds-button--outline)" in selector
        require_primary = "eds-button--primary" in selector or "eds-button--xl-large" in selector
        out = []
        for btn in self._buttons:
            if visible_only and not btn.get("visible", True):
                continue
            cls = btn.get("cls", "")
            if exclude_outline and "eds-button--outline" in cls:
                continue
            if require_primary and ("eds-button--primary" not in cls and "eds-button--xl-large" not in cls):
                continue
            out.append(btn)
        return _FakeLocator(out)


def test_locate_publish_button_skips_hidden_outline_bind_buttons():
    mod = _load_shopee_tool()
    page = _FakePage(
        [
            {
                "text": "上架",
                "cls": "eds-button eds-button--primary eds-button--normal eds-button--xl-large",
                "visible": True,
            },
            {
                "text": "上架並綁定",
                "cls": "eds-button eds-button--primary eds-button--normal eds-button--outline upload-button",
                "visible": False,
            },
            {
                "text": "上架並綁定",
                "cls": "eds-button eds-button--primary eds-button--normal eds-button--outline upload-button",
                "visible": False,
            },
        ]
    )
    btn = mod._locate_publish_button(page)
    assert btn is not None
    assert btn.count() == 1
    assert btn.inner_text() == "上架"


def test_locate_publish_button_supports_save_and_publish_label():
    mod = _load_shopee_tool()
    page = _FakePage(
        [
            {
                "text": "上架並綁定",
                "cls": "eds-button eds-button--primary eds-button--outline",
                "visible": True,
            },
            {
                "text": "儲存並上架",
                "cls": "eds-button eds-button--primary eds-button--xl-large",
                "visible": True,
            },
        ]
    )
    btn = mod._locate_publish_button(page)
    assert btn is not None
    assert btn.inner_text() == "儲存並上架"


def test_scan_js_filters_optional_date_pickers_and_non_required_fields():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert "const isDatePicker = !!item.querySelector('.eds-date-picker, .eds-date-picker-input');" in source
    assert "const mustCheck = hasAsterisk || isError || isCondition;" in source
    assert "if (!mustCheck) continue;" in source
    assert "if (isDatePicker && !isError) continue;" in source
