"""Tests for Shopee seller self-healing publish button & field scan rules."""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOPEE_TOOL_PATH = REPO_ROOT / "skills" / "shopee-seller" / "scripts" / "shopee_tool.py"


def _load_locate_publish_button():
    """Load `_locate_publish_button` without triggering top-level skill dependencies."""
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SHOPEE_TOOL_PATH))
    func_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_locate_publish_button"
    )
    mod_ast = ast.Module(body=[func_node], type_ignores=[])
    ast.fix_missing_locations(mod_ast)
    ns = {"re": re}
    exec(compile(mod_ast, str(SHOPEE_TOOL_PATH), "exec"), ns)
    return ns["_locate_publish_button"]


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
    locate_publish_button = _load_locate_publish_button()
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
    btn = locate_publish_button(page)
    assert btn is not None
    assert btn.count() == 1
    assert btn.inner_text() == "上架"


def test_locate_publish_button_supports_save_and_publish_label():
    locate_publish_button = _load_locate_publish_button()
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
    btn = locate_publish_button(page)
    assert btn is not None
    assert btn.inner_text() == "儲存並上架"


def test_scan_js_filters_optional_date_pickers_and_non_required_fields():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert "const isDatePicker = !!item.querySelector('.eds-date-picker, .eds-date-picker-input');" in source
    assert "const mustCheck = hasAsterisk || isError || isCondition;" in source
    assert "if (!mustCheck) continue;" in source
    assert "if (isDatePicker && !isError) continue;" in source


def _load_login_helpers():
    import sys
    import time
    import urllib.parse

    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SHOPEE_TOOL_PATH))
    wanted = {"_is_shopee_login_url", "_is_shopee_verification_url", "handle_shopee_login"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    mod_ast = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(mod_ast)
    ns = {"urllib": urllib, "time": time, "sys": sys}
    exec(compile(mod_ast, str(SHOPEE_TOOL_PATH), "exec"), ns)
    return ns


def test_shopee_login_and_verification_url_detection():
    ns = _load_login_helpers()
    is_login = ns["_is_shopee_login_url"]
    is_verify = ns["_is_shopee_verification_url"]

    assert is_login("https://accounts.shopee.tw/seller/login?next=foo") is True
    assert is_login("https://seller.shopee.tw/portal/product/list?is_from_login=true") is False
    assert is_verify("https://shopee.tw/verify/ivs?is_from_login=true") is True
    assert is_verify("https://seller.shopee.tw/portal/product/list") is False


def test_handle_shopee_login_waits_for_otp_and_resumes(monkeypatch):
    ns = _load_login_helpers()
    handle_login = ns["handle_shopee_login"]
    monkeypatch.setattr(ns["time"], "sleep", lambda _s: None)

    class _OtpPage:
        def __init__(self):
            self._urls = [
                "https://shopee.tw/verify/ivs?step=1",
                "https://shopee.tw/verify/ivs?step=1",
                "https://seller.shopee.tw/portal/product-mass/import/upload",
            ]
            self._idx = 0

        @property
        def url(self):
            val = self._urls[min(self._idx, len(self._urls) - 1)]
            self._idx += 1
            return val

    alerts = []
    ok = handle_login(
        _OtpPage(),
        otp_wait_timeout=10.0,
        on_otp_required=lambda u: alerts.append(u),
    )
    assert ok is True
    assert len(alerts) == 1
    assert "verify/ivs" in alerts[0]


def test_async_upload_uses_zero_coupling_task_manager_sync():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert 'notify="ios"' in source
    assert "sync_memory=True" in source


def test_live_matching_requires_strict_keyword_ratio():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert "hits >= Math.min(1, keywords.length)" not in source
    assert "Math.max(2, Math.ceil(keywords.length * 0.6))" in source


def test_no_placeholder_shop_id_in_live_url():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert 'os.getenv("SHOPEE_SHOP_ID", "YOUR_SHOP_ID")' not in source


def test_publish_retry_does_not_depend_on_new_learned_rules():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert "for _retry in range(2):" in source
    assert "_dismiss_blocking_modals(draft_page)" in source


def test_is_publish_succeeded_by_url():
    import re as _re
    import time as _time

    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SHOPEE_TOOL_PATH))
    nodes = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name == "_is_publish_succeeded")
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "PUBLISH_SUCCESS_JS" for t in n.targets))
    ]
    mod_ast = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(mod_ast)
    ns = {"re": _re, "time": _time}
    exec(compile(mod_ast, str(SHOPEE_TOOL_PATH), "exec"), ns)
    check = ns["_is_publish_succeeded"]

    class _Page:
        def __init__(self, url, toast=False):
            self.url = url
            self._toast = toast

        def evaluate(self, _js):
            return self._toast

    assert check(_Page("https://seller.shopee.tw/portal/product/list/unpublished/draft")) is True
    assert check(_Page("https://seller.shopee.tw/portal/product/unpublished/123")) is False
    assert check(_Page("https://seller.shopee.tw/portal/product/unpublished/123", toast=True)) is True


def test_image_host_priority_prefers_imgur_and_uguu_over_pomf2():
    source = SHOPEE_TOOL_PATH.read_text(encoding="utf-8")
    assert "IMGUR_CLIENT_ID" in source
    assert source.index("uguu.se/upload.php") < source.index("pomf2.lain.la/upload.php")
