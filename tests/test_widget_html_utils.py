from widget_html_utils import WidgetHtmlStreamFilter, extract_widget_html


def test_extract_widget_html_no_widget():
    text = "目前台積電股價為 1050 元，上漲 2.5%。"
    cleaned, widget = extract_widget_html(text)
    assert cleaned == text
    assert widget is None


def test_extract_widget_html_tag_and_preserve_hex():
    raw = (
        "台積電目前表現強勢，漲幅達 2.5%。\n\n"
        "<widget_html>\n"
        "<div style='color:#0ea5e9'>TSMC 2330</div>\n"
        "</widget_html>"
    )
    cleaned, widget = extract_widget_html(raw)
    assert cleaned == "台積電目前表現強勢，漲幅達 2.5%。"
    assert widget == "<div style='color:#0ea5e9'>TSMC 2330</div>"


def test_extract_widget_html_fence_case_insensitive():
    raw = "摘要如下：\n```WIDGET-HTML\n<p>OK</p>\n```\n以上。"
    cleaned, widget = extract_widget_html(raw)
    assert cleaned == "摘要如下：\n\n以上。"
    assert widget == "<p>OK</p>"


def test_stream_filter_hides_widget_across_chunk_boundaries():
    full = "你好，<widget_html><div>秘密卡片</div></widget_html>完成了！"
    flt = WidgetHtmlStreamFilter()
    emitted = []
    # 故意以 2 字元切塊測試跨 chunk 邊界標籤切割
    for i in range(0, len(full), 2):
        chunk = flt.feed(full[i : i + 2])
        if chunk:
            emitted.append(chunk)
    tail = flt.flush()
    if tail:
        emitted.append(tail)

    assert "".join(emitted) == "你好，完成了！"


def test_stream_filter_preserves_normal_angle_brackets():
    full = "條件是 3 < 5 且 6 > 4。"
    flt = WidgetHtmlStreamFilter()
    out = "".join(flt.feed(ch) for ch in full) + flt.flush()
    assert out == full
