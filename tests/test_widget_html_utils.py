import json
from datetime import datetime

from fast_intent import format_coupang_fast_reply, format_fb_monitor_fast_reply, try_fast_intent
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


def test_format_coupang_fast_reply_builds_fixed_widget(tmp_path):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_file = tmp_path / "coupang_state.json"
    state_file.write_text(
        json.dumps(
            {
                "last_checked_at": now_str,
                "last_cart_sync_at": now_str,
                "total_checks": 42,
                "cart_baselines": {
                    "MacBook Air <M5>": {
                        "title": "MacBook Air <M5>",
                        "option": "午夜色",
                        "last_price": 50000,
                        "wave_high_24h": 51000,
                        "last_seen": now_str,
                    },
                    "濕巾": {
                        "title": "濕巾",
                        "option": "18包",
                        "last_price": 600,
                        "wave_high_24h": 600,
                        "last_seen": now_str,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reply = format_coupang_fast_reply(state_path=state_file)
    assert reply is not None
    spoken, widget = extract_widget_html(reply)
    assert "2 項商品在線盯價" in spoken
    assert "降了 1,000 元來到 50,000 元" in spoken
    assert widget is not None
    assert "MacBook Air &lt;M5&gt;" in widget
    assert "$50,000" in widget
    assert "$600" in widget


def test_try_fast_intent_strips_length_constraint_prefix(monkeypatch):
    monkeypatch.setattr("fast_intent.format_coupang_fast_reply", lambda: "OK<widget_html><div>x</div></widget_html>")
    q = "【回覆長度約束：細節模式】以結構化條理分明方式回覆\n\n查詢酷澎最新追蹤價格與狀態"
    assert try_fast_intent(q) == "OK<widget_html><div>x</div></widget_html>"


def test_format_fb_monitor_fast_reply_builds_fixed_widget():
    sample = {
        "group_title": "(6) 竹北測試社團 <XSS> | Facebook",
        "group_url": "https://www.facebook.com/groups/123/",
        "daemon": {"is_running": True, "uptime": "2 小時 10 分"},
        "last_poll_at": "2026-11-16 11:49:39",
        "total_seen_posts": 120,
        "recent_polls": [{"cycle": 55}],
        "recent_posts": [
            {
                "checked_at": "2026-11-16 11:35:30",
                "author": "王小明",
                "image_count": 2,
                "status": "MATCHED",
                "summary": "免費贈送嬰兒推車",
                "permalink": "https://www.facebook.com/groups/123/posts/1/",
            },
            {
                "checked_at": "2026-11-16 11:20:00",
                "author": "匿名成員",
                "image_count": 0,
                "status": "EXCLUDED",
                "reason": "付費販售",
                "permalink": "https://www.facebook.com/groups/123/posts/2/",
            },
        ],
    }
    reply = format_fb_monitor_fast_reply(data=sample)
    assert reply is not None
    spoken, widget = extract_widget_html(reply)
    assert "運行中" in spoken
    assert "1 篇命中通知" in spoken
    assert widget is not None
    assert "竹北測試社團 &lt;XSS&gt;" in widget
    assert "✅ 命中通知" in widget
    assert "免費贈送嬰兒推車" in widget


def test_try_fast_intent_fb_trigger(monkeypatch):
    monkeypatch.setattr("fast_intent.format_fb_monitor_fast_reply", lambda: "FB_OK<widget_html><div>fb</div></widget_html>")
    q = "【回覆長度約束：細節模式】...\n\n查詢FB社團近期巡檢狀態與貼文"
    assert try_fast_intent(q) == "FB_OK<widget_html><div>fb</div></widget_html>"

