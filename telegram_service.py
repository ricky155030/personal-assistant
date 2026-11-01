import os
import json
import time
import asyncio
import mimetypes
import html
import re
import unicodedata
from pathlib import Path
from functools import partial
import httpx
from datetime import datetime
from typing import Optional, List, Tuple

from config import settings
from fast_intent import try_fast_intent
from agy_singleton import agy_singleton
from quota_service import quota_service

TELEGRAM_API_BASE = "https://api.telegram.org"

def infer_skill(query: str, reply: str, actions: Optional[List[str]] = None) -> str:
    """推斷本次對話使用的主要技能或資料來源"""
    act_str = " ".join(actions or []).lower()
    q_lower = query.lower()
    r_lower = reply.lower()

    if "multimodal" in act_str or "image" in act_str:
        for a in actions or []:
            if "multimodal:" in a and "image" in a:
                parts = a.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    num = int(parts[1])
                    if num > 1:
                        return f"👁️ 視覺辨識 ({num}張圖)"
        return "👁️ 視覺辨識"
    if "fast-path" in act_str:
        return "⚡ 極速回應"
    if "sheets_tool" in act_str or any(k in q_lower for k in ["試算表", "表格", "sheets", "待買"]):
        return "📊 雲端試算表"
    if "calendar_tool" in act_str or any(k in q_lower for k in ["行事曆", "待辦", "日程", "繳費", "行程", "提醒"]):
        return "📅 待辦行事曆"
    if "search_web" in act_str or any(k in q_lower for k in ["天氣", "搜尋", "查一下", "最新", "股價", "新聞", "匯率"]):
        return "🌐 即時連網"
    if "fb_group_monitor" in act_str or "fb-group-monitor" in act_str or any(k in q_lower for k in ["社團", "fb社團", "貼文監控"]):
        return "📡 社團雷達"
    return "🤖 核心大腦"

def strip_html_tags(html_text: str) -> str:
    """清除所有 HTML 標籤作為純文字備援，杜絕 blockquote 或 i 等原始標籤外洩"""
    if not html_text:
        return ""
    clean = re.sub(r"<[^>]+>", "", html_text)
    return html.unescape(clean)


# ============================================================================
# Telegram 智慧自適應排版引擎 (Adaptive Rich Formatting Engine - Scheme D)
# ============================================================================

def get_display_width(s: str) -> int:
    """計算字串於等寬字型下的實體顯示寬度 (CJK 全形字元計為 2，半形計為 1)"""
    clean = re.sub(r"[*_`~]", "", s or "")
    w = 0
    for ch in clean:
        status = unicodedata.east_asian_width(ch)
        if status in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def pad_cjk(s: str, target_width: int, align: str = "left") -> str:
    """依據 CJK 寬度進行精確補白對齊"""
    cur_w = get_display_width(s)
    pad = max(0, target_width - cur_w)
    if align == "right":
        return " " * pad + s
    elif align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + s + " " * right
    else:
        return s + " " * pad


def is_numeric_cell(s: str) -> bool:
    """判斷單元格是否為數值、百分比或幣值（以便靠右對齊）"""
    clean = s.strip().replace("$", "").replace("¥", "").replace("€", "").replace(",", "").replace("%", "")
    try:
        float(clean)
        return True
    except ValueError:
        return False


def parse_markdown_table(raw_table: str) -> Tuple[Optional[List[str]], Optional[List[List[str]]]]:
    """解析標準 Markdown 表格，回傳 (headers, rows)"""
    lines = [l.strip() for l in raw_table.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return None, None

    def split_row(row_str: str) -> List[str]:
        s = row_str.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    headers = split_row(lines[0])
    sep = split_row(lines[1])
    if not all(re.match(r"^:?-+:?$", col.strip()) for col in sep if col.strip()):
        return None, None

    rows = []
    for line in lines[2:]:
        cols = split_row(line)
        if len(cols) < len(headers):
            cols += [""] * (len(headers) - len(cols))
        rows.append(cols[:len(headers)])

    return headers, rows


def render_adaptive_table(headers: List[str], rows: List[List[str]]) -> str:
    """
    方案 D (自適應混搭)：
    1. 緊湊型表格 (欄位 <= 3 且 總寬度 <= 34 字元) ➔ CJK 等寬 ASCII 邊框表格 (<pre>)
    2. 寬型或多欄表格 (欄位 > 3 或 總寬度 > 34 字元) ➔ 行動端結構化卡片流 (Mobile Cards Flow)
    3. 資料列超過門檻時自動包覆 <blockquote expandable>（一鍵收折防洗版）
    """
    cols = len(headers)
    col_widths = []
    for i in range(cols):
        max_w = get_display_width(headers[i])
        for r in rows:
            max_w = max(max_w, get_display_width(r[i]))
        col_widths.append(max(max_w, 2))

    total_width = sum(col_widths) + (cols - 1) * 3

    # 1. 緊湊型表格：欄位 <= 3 且 總寬度 <= 34 字元
    if cols <= 3 and total_width <= 34:
        lines = []
        header_line = " │ ".join(pad_cjk(headers[i], col_widths[i]) for i in range(cols))
        sep_line = "─┼─".join("─" * col_widths[i] for i in range(cols))
        lines.append(header_line)
        lines.append(sep_line)
        for r in rows:
            r_line = " │ ".join(
                pad_cjk(r[i], col_widths[i], align="right" if is_numeric_cell(r[i]) else "left")
                for i in range(cols)
            )
            lines.append(r_line)
        output = "<pre>" + html.escape("\n".join(lines)) + "</pre>"
        if len(rows) > 5:
            output = f"<blockquote expandable>\n{output}\n</blockquote>"
        return output

    # 2. 多欄型或寬型表格：行動端結構化卡片流 (Mobile Cards Flow)
    cards = []
    for r in rows:
        title = r[0].strip("*_`")
        card_lines = [f"📌 <b>{html.escape(title)}</b>"]
        details = []

        idx = 1
        # 若第 2 與第 3 欄皆為簡短數值（例如「數量」與「金額」），合併同一行以節省手機垂直高度
        if cols >= 3 and get_display_width(r[1]) <= 8 and get_display_width(r[2]) <= 10:
            h1, v1 = headers[1], r[1]
            h2, v2 = headers[2], r[2]
            v1_fmt = f"<code>{html.escape(v1)}</code>"
            v2_fmt = f"<code>{html.escape(v2)}</code>"
            details.append(f"• {html.escape(h1)}：{v1_fmt} ｜ {html.escape(h2)}：{v2_fmt}")
            idx = 3

        while idx < cols:
            h = headers[idx]
            v = r[idx]
            if v:
                if re.match(r"^[$¥€£]?\s*[\d,.]+%?$", v) or v in ("是", "否", "完成", "未完成", "正常", "異常", "已買", "待買"):
                    v_fmt = f"<code>{html.escape(v)}</code>"
                else:
                    v_fmt = html.escape(v)
                details.append(f"• {html.escape(h)}：{v_fmt}")
            idx += 1

        card_lines.extend(details)
        cards.append("\n".join(card_lines))

    card_output = "\n\n".join(cards)
    if len(rows) > 3:
        return f"<blockquote expandable>\n{card_output}\n</blockquote>"
    return card_output


def markdown_to_telegram_html(text: str, allow_quotes: bool = True) -> str:
    """
    將 AI 產出的 Markdown 標記精確轉換為 Telegram 支援的 HTML 標籤（方案 D 自適應混搭）：
    - 代碼塊 (```lang\n...```) ➔ <pre><code class="language-lang">...</code></pre>（保留語法高亮）
    - 行內代碼 (`...`) ➔ <code>...</code>
    - 表格 (| ... |) ➔ 智慧自適應混搭（緊湊走 <pre>，寬表走行動卡片，長表 expandable 折疊）
    - 引用塊 (> ...) ➔ <blockquote>...</blockquote> 或 <blockquote expandable>
    - 連結 [text](url) ➔ http/https/tg 保留超連結，其餘轉為 <code>
    - 標題 (###, ##, #) ➔ <b>標題</b>
    - 清單 (- 或 *) ➔ 圓點 •
    - 粗體 (** 或 __) ➔ <b>...</b>
    - 斜體 (* 或 _) ➔ <i>...</i>
    - 刪除線 (~~...~~) ➔ <s>...</s>
    - 所有原始 <, >, & 符號均安全逸出，杜絕實體解析失敗與標籤洩漏
    """
    if not text:
        return ""

    # 1. 抽離並保護 code blocks (```lang\n...```)
    code_blocks = []
    def save_code_block(m):
        lang = (m.group(1) or "").strip().lower()
        code_body = m.group(2)
        code_blocks.append((lang, code_body))
        return f"@@@CODE_BLOCK_{len(code_blocks)-1}@@@"
    text = re.sub(r"```([a-zA-Z0-9_-]*)\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    # 2. 抽離並保護已存在的 Telegram 原生 blockquote 區塊
    raw_blockquotes = []
    def save_raw_blockquote(m):
        raw_blockquotes.append(m.group(0))
        return f"@@@RAW_BQ_{len(raw_blockquotes)-1}@@@"
    text = re.sub(r"<(?:blockquote|blockquote expandable)>.*?</blockquote\s*>", save_raw_blockquote, text, flags=re.DOTALL | re.IGNORECASE)

    # 3. 抽離並保護 Markdown 表格（方案 D 智慧分流）
    tables = []
    table_pattern = re.compile(
        r"(?:^[ \t]*\|?[^\n|]+(?:\|[^\n|]+)+\|?[ \t]*\n)"
        r"(?:^[ \t]*\|?(?:[ \t]*:?-+:?[ \t]*\|)+[ \t]*:?-+:?[ \t]*\|?[ \t]*\n)"
        r"(?:^[ \t]*\|?[^\n|]+(?:\|[^\n|]+)+\|?[ \t]*(?:\n|$))+",
        re.MULTILINE
    )
    def save_table(m):
        raw_tbl = m.group(0)
        h, r = parse_markdown_table(raw_tbl)
        if h and r:
            rendered = render_adaptive_table(h, r)
            tables.append(rendered)
            return f"\n@@@TABLE_{len(tables)-1}@@@\n"
        return raw_tbl

    text = table_pattern.sub(save_table, text)

    # 4. 抽離並處理 Markdown 引用 (> quote，支援 >> 或 >! 轉為 expandable)
    quotes = []
    if allow_quotes:
        quote_pattern = re.compile(r"(?:^[ \t]*>[ \t]?(?:[^\n]*)(?:\n|$))+", re.MULTILINE)
        def save_quote(m):
            raw_q = m.group(0)
            lines = []
            is_expandable = False
            for l in raw_q.split("\n"):
                l_str = l.strip()
                if not l_str:
                    continue
                if l_str.startswith(">>") or l_str.startswith(">!"):
                    is_expandable = True
                    lines.append(l_str[2:].strip())
                elif l_str.startswith(">"):
                    lines.append(l_str[1:].strip())
                else:
                    lines.append(l_str)
            inner_content = "\n".join(lines)
            quotes.append((is_expandable, inner_content))
            return f"\n@@@QUOTE_{len(quotes)-1}@@@"

        text = quote_pattern.sub(save_quote, text)

    # 5. 處理 Markdown 連結 [text](url)
    links = []
    def save_link(m):
        label, url = m.group(1).strip(), m.group(2).strip()
        is_code = label.startswith("`") and label.endswith("`")
        clean_label = label.strip("`")
        if url.startswith(("http://", "https://", "tg://")):
            content = f"<code>{html.escape(clean_label)}</code>" if is_code else html.escape(clean_label)
            links.append(f'<a href="{html.escape(url)}">{content}</a>')
        else:
            links.append(f"<code>{html.escape(clean_label)}</code>" if is_code else html.escape(clean_label))
        return f"@@@LINK_{len(links)-1}@@@"
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", save_link, text)

    # 6. 抽離並保護 inline code (`...`)
    inline_codes = []
    def save_inline_code(m):
        inline_codes.append(m.group(1))
        return f"@@@INLINE_CODE_{len(inline_codes)-1}@@@"
    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)

    # 7. 安全逸出一般文字中的 HTML 特殊符號 (&, <, >)
    text = html.escape(text)

    # 8. 轉換 Markdown 標題 (#, ##, ###) 為粗體 <b>...</b>
    text = re.sub(r"^(#{1,6})\s+(.+)$", r"<b>\2</b>", text, flags=re.MULTILINE)

    # 9. 轉換 Markdown 清單符號 (- 或 *) 為乾淨圓點 •
    text = re.sub(r"^[ \t]*[-*][ \t]+", "• ", text, flags=re.MULTILINE)

    # 10. 轉換粗體 **text** 或 __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 11. 轉換斜體 *text* 或 _text_（避免誤傷）
    text = re.sub(r"(?<!\w)\*([^* \n][^*\n]*?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_ \n][^_\n]*?)_(?!\w)", r"<i>\1</i>", text)

    # 12. 轉換刪除線 ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 13. 轉換常見 LaTeX 符號 (如 $\rightarrow$, $\to$) 為乾淨 Unicode 箭頭 →
    text = re.sub(r"\\?\$?\\rightarrow\$?|\\?\$?\\to\$?", "→", text)

    # 14. 還原連結
    for i, link_html in enumerate(links):
        text = text.replace(f"@@@LINK_{i}@@@", link_html)

    # 15. 還原 inline code
    for i, code in enumerate(inline_codes):
        text = text.replace(f"@@@INLINE_CODE_{i}@@@", f"<code>{html.escape(code)}</code>")

    # 16. 還原 quotes
    for i, (is_exp, q_body) in enumerate(quotes):
        q_fmt = markdown_to_telegram_html(q_body, allow_quotes=False)
        tag_start = "<blockquote expandable>" if is_exp else "<blockquote>"
        text = text.replace(f"@@@QUOTE_{i}@@@", f"{tag_start}\n{q_fmt}\n</blockquote>")

    # 17. 還原 tables
    for i, tbl_html in enumerate(tables):
        text = text.replace(f"@@@TABLE_{i}@@@", tbl_html)

    # 18. 還原原生 blockquotes
    for i, bq_html in enumerate(raw_blockquotes):
        text = text.replace(f"@@@RAW_BQ_{i}@@@", bq_html)

    # 19. 還原 code blocks (支援語法高亮 class="language-...")
    for i, (lang, code) in enumerate(code_blocks):
        escaped_code = html.escape(code)
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            tag = f'<pre><code>{escaped_code}</code></pre>'
        text = text.replace(f"@@@CODE_BLOCK_{i}@@@", tag)

    # 清理多餘連續空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def format_telegram_response(
    reply: str,
    user_query: str,
    latency: float,
    actions: Optional[List[str]] = None,
    report_link_html: str = "",
    thinking_level: Optional[str] = None
) -> str:
    """
    依照方案 1（置底一體化 Quote）格式化 Telegram 訊息：
    第一行：使用者問題
    第二行：⏱️ 耗時 ｜ 🛠️ 技能 ｜ ⚡ 7d: 週配額% (Thinking Level)
    """
    try:
        q_summary = quota_service.get_quota_summary()
        g_w_pct = q_summary.get("gemini", {}).get("weekly_7d", {}).get("remaining_pct", 100.0)
    except Exception:
        g_w_pct = 100.0

    # 1. 耗時格式化 (極速家電通道以兩位小數呈現，一般查詢以一位小數呈現)
    if latency < 0.1:
        latency_str = f"⚡ {latency:.2f}s"
    else:
        latency_str = f"⏱️ {latency:.1f}s"

    # 2. 技能推斷
    skill = infer_skill(user_query, reply, actions)

    # 3. 解析思考等級 (thinking_level: low/med/high)
    level_candidate = thinking_level
    if not level_candidate and actions:
        for a in actions:
            a_lower = a.lower()
            if "thinking:" in a_lower or "level:" in a_lower or "(low)" in a_lower or "(med)" in a_lower or "(high)" in a_lower or "(medium)" in a_lower:
                if "low" in a_lower:
                    level_candidate = "low"
                elif "high" in a_lower:
                    level_candidate = "high"
                elif "med" in a_lower or "medium" in a_lower:
                    level_candidate = "med"
                break

    level_tag = ""
    if level_candidate:
        lvl_map = {"low": "Low", "medium": "Med", "med": "Med", "high": "High"}
        level_tag = f" ({lvl_map.get(level_candidate.lower(), level_candidate.capitalize())})"

    # 4. 週配額格式化 (在配額旁邊標記思考等級)
    quota_str = f"⚡ 7d: {g_w_pct:.1f}%{level_tag}"

    # 5. 第二行指標列
    info_line = f"{latency_str} ｜ {skill} ｜ {quota_str}"

    clean_reply = markdown_to_telegram_html(reply.strip())
    safe_user_query = html.escape(user_query)
    return f"🤖 {clean_reply}{report_link_html}\n\n<blockquote>👤 <i>{safe_user_query}</i>\n{info_line}</blockquote>"

def split_markdown_into_chunks(text: str, max_chunk_len: int = 3000) -> List[str]:
    """
    將超長 Markdown 文本依自然段落安全切分為符合 Telegram 單則上限 (<4000字) 的區塊。
    切分優先序：
    1. 雙換行段落 (\n\n)
    2. 單換行 (\n)
    3. 硬切長行
    """
    if not text or len(text) <= max_chunk_len:
        return [text] if text else [""]

    paragraphs = text.split("\n\n")
    chunks = []
    curr = []
    curr_len = 0

    for p in paragraphs:
        p_len = len(p) + 2
        if curr_len + p_len <= max_chunk_len:
            curr.append(p)
            curr_len += p_len
        else:
            if curr:
                chunks.append("\n\n".join(curr).strip())
                curr = []
                curr_len = 0
            if len(p) > max_chunk_len:
                lines = p.split("\n")
                line_curr = []
                line_len = 0
                for l in lines:
                    if line_len + len(l) + 1 <= max_chunk_len:
                        line_curr.append(l)
                        line_len += len(l) + 1
                    else:
                        if line_curr:
                            chunks.append("\n".join(line_curr).strip())
                            line_curr = []
                            line_len = 0
                        while len(l) > max_chunk_len:
                            chunks.append(l[:max_chunk_len])
                            l = l[max_chunk_len:]
                        if l:
                            line_curr.append(l)
                            line_len = len(l)
                if line_curr:
                    curr.append("\n".join(line_curr))
                    curr_len = line_len
            else:
                curr.append(p)
                curr_len = len(p) + 2

    if curr:
        chunks.append("\n\n".join(curr).strip())
    return [c for c in chunks if c.strip()]

def send_formatted_telegram_reply(
    reply: str,
    user_query: str,
    latency: float,
    actions: Optional[List[str]] = None,
    report_link_html: str = "",
    thinking_level: Optional[str] = None,
    msg_id: Optional[int] = None,
    chat_id: Optional[str] = None,
    delete_progress_card: bool = True
) -> bool:
    """
    發送 Telegram 回覆，完整支援：
    1. 超長文本自動智能分段切分 (徹底杜絕 400 Bad Request: message is too long)
    2. 新訊息有感推播（有聲音/震動通知，確保使用者即時收到完成提示）
    3. 自動清理臨時進度卡片 (deleteMessage)，維持聊天室乾淨俐落
    4. 末段附帶置底 Quote 效能卡片與報告連結
    5. 各段落獨立 Markdown->Telegram HTML 渲染與 Fallback 標籤淨化
    """
    try:
        q_summary = quota_service.get_quota_summary()
        g_w_pct = q_summary.get("gemini", {}).get("weekly_7d", {}).get("remaining_pct", 100.0)
    except Exception:
        g_w_pct = 100.0

    latency_str = f"⚡ {latency:.2f}s" if latency < 0.1 else f"⏱️ {latency:.1f}s"
    skill = infer_skill(user_query, reply, actions)

    level_candidate = thinking_level
    if not level_candidate and actions:
        for a in actions:
            a_lower = a.lower()
            if any(k in a_lower for k in ["thinking:", "level:", "(low)", "(med)", "(high)", "(medium)"]):
                if "low" in a_lower:
                    level_candidate = "low"
                elif "high" in a_lower:
                    level_candidate = "high"
                elif "med" in a_lower or "medium" in a_lower:
                    level_candidate = "med"
                break

    level_tag = ""
    ctx_str = ""
    if level_candidate:
        lvl_map = {"low": "Low", "medium": "Med", "med": "Med", "high": "High"}
        lvl_name = lvl_map.get(level_candidate.lower(), level_candidate.capitalize())
        level_tag = f" ({lvl_name})"
        
        # 讀取當前大腦的 Context 用量百分比
        try:
            lvl_norm = "med" if ("med" in level_candidate.lower() or "medium" in level_candidate.lower()) else ("high" if "high" in level_candidate.lower() else "low")
            brain_key = f"{lvl_norm}_brain"
            stat = agy_singleton.get_status()
            target_brain = stat.get(brain_key, {})
            ctx_pct = target_brain.get("percent")
            if ctx_pct is None or ctx_pct == 0:
                # 若當前進程池未記錄，向本機 API 服務查詢即時狀態
                import urllib.request, json
                req = urllib.request.Request(f"http://127.0.0.1:{settings.PORT}/api/quota")
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    api_data = json.loads(resp.read().decode("utf-8"))
                    ctx_pct = api_data.get("context", {}).get(brain_key, {}).get("percent", 0.0)
            if ctx_pct is not None:
                ctx_str = f" ｜ 🧠 Context: {ctx_pct:.1f}%"
        except Exception:
            pass

    quota_str = f"⚡ 7d: {g_w_pct:.1f}%{level_tag}"
    info_line = f"{latency_str} ｜ {skill} ｜ {quota_str}{ctx_str}"
    safe_user_query = html.escape(user_query)
    quote_card = f"\n\n<blockquote>👤 <i>{safe_user_query}</i>\n{info_line}</blockquote>"

    chunks = split_markdown_into_chunks(reply.strip(), max_chunk_len=3000)
    total_chunks = len(chunks)

    all_ok = True
    for idx, chunk in enumerate(chunks):
        is_first = (idx == 0)
        is_last = (idx == total_chunks - 1)

        chunk_html = markdown_to_telegram_html(chunk)
        if total_chunks > 1:
            header_prefix = f"🤖 <b>({idx + 1}/{total_chunks})</b>\n\n" if is_first else f"📄 <b>({idx + 1}/{total_chunks})</b>\n\n"
        else:
            header_prefix = "🤖 "

        msg_body = f"{header_prefix}{chunk_html}"
        if is_last:
            msg_body += f"{report_link_html}{quote_card}"

        ok = send_telegram_sync(msg_body, chat_id=chat_id, parse_mode="HTML")
        all_ok = all_ok and ok

    # 任務完成新訊息已發送後，自動刪除舊有的臨時進度卡片，還原乾淨聊天室
    if msg_id and delete_progress_card:
        delete_telegram_message_sync(msg_id, chat_id=chat_id)

    return all_ok

def send_telegram_sync(
    text: str, 
    chat_id: Optional[str] = None, 
    parse_mode: str = "HTML",
    disable_notification: bool = False
) -> bool:
    """同步發送 Telegram 訊息 (可用於非同步/同步場景)"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat:
        return False

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode
    }
    if disable_notification:
        payload["disable_notification"] = True
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            print(f"[TelegramService] ⚠️ sendMessage 失敗 (HTTP {resp.status_code}): {resp.text}")
            # 若 HTML 解析失敗或長度溢出，降級以過濾標籤後的純文字重新發送
            fallback_text = strip_html_tags(text)
            if len(fallback_text) > 4000:
                fallback_text = fallback_text[:3950] + "..."
            fallback_payload = {"chat_id": target_chat, "text": fallback_text}
            if disable_notification:
                fallback_payload["disable_notification"] = True
            fallback_resp = client.post(url, json=fallback_payload)
            return fallback_resp.status_code == 200
    except Exception as e:
        print(f"[TelegramService] ❌ 同步發送失敗: {e}")
        return False

def send_telegram_message_return_id(
    text: str, 
    chat_id: Optional[str] = None, 
    parse_mode: Optional[str] = None,
    disable_notification: bool = False
) -> Optional[int]:
    """同步發送 Telegram 訊息並回傳 message_id，供後續串流打字機編輯"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat:
        return None
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {"chat_id": target_chat, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if disable_notification:
        payload["disable_notification"] = True
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", {}).get("message_id")
            print(f"[TelegramService] ⚠️ sendMessage_return_id 失敗 (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[TelegramService] ❌ 發送初始串流訊息失敗: {e}")
    return None

def delete_telegram_message_sync(message_id: int, chat_id: Optional[str] = None) -> bool:
    """同步刪除 Telegram 訊息 (用於清理臨時動態進度卡片)"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat or not message_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{token}/deleteMessage"
    payload = {"chat_id": target_chat, "message_id": message_id}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 400 and "message to delete not found" in resp.text:
                return True
            print(f"[TelegramService] ⚠️ deleteMessage 失敗 (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[TelegramService] ❌ deleteMessage 例外: {e}")
    return False

def edit_telegram_message_sync(message_id: int, text: str, chat_id: Optional[str] = None, parse_mode: Optional[str] = None) -> bool:
    """同步編輯 Telegram 訊息 (打字機串流效果)"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat or not message_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{token}/editMessageText"
    payload = {"chat_id": target_chat, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            print(f"[TelegramService] ⚠️ editMessageText 失敗 (HTTP {resp.status_code}): {resp.text}")
            if parse_mode:
                # 若 HTML 解析失敗，降級純文字並徹底過濾標籤避免 blockquote 洩漏
                payload.pop("parse_mode", None)
                fallback = strip_html_tags(text)
                if len(fallback) > 4000:
                    fallback = fallback[:3950] + "..."
                payload["text"] = fallback
                resp2 = client.post(url, json=payload)
                return resp2.status_code == 200
    except Exception as e:
        print(f"[TelegramService] ❌ editMessageText 例外: {e}")
    return False

class TelegramLiveCard:
    """
    Telegram 原地動態進度卡片控制器 (Option 1: Ephemeral Live Progress Card)
    - 啟動瞬間 (<0.5s) 建立 Telegram 靜音訊息 (disable_notification=True)，杜絕初期惱人干擾
    - 步驟與打字機動態更新，末尾標註「🕒 最後更新：HH:MM:SS」，讓主人掌握最新進度時間
    - 嚴格落實 Telegram editMessageText 節流防護 (>= 1.2s)，杜絕 429 Too Many Requests
    - 任務完成後由 send_formatted_telegram_reply 發送帶響鈴之全新訊息，並自動刪除本卡片清理聊天室
    """

    def __init__(self, chat_id: Optional[str] = None, initial_step: str = "⏳ 正在分析需求..."):
        self.chat_id = chat_id
        self.msg_id: Optional[int] = None
        self.collected_text: List[str] = []
        self.status_steps: List[str] = [initial_step]
        self.last_updated_time = datetime.now().strftime("%H:%M:%S")
        self.last_edit_time = time.perf_counter()
        self.last_card_content = ""
        self._initial_render()

    def _initial_render(self):
        content = self._build_content()
        self.msg_id = send_telegram_message_return_id(
            content, 
            chat_id=self.chat_id, 
            disable_notification=True
        )
        self.last_card_content = content
        self.last_edit_time = time.perf_counter()

    def _build_content(self) -> str:
        time_tag = f"🕒 最後更新：{self.last_updated_time}"

        # 1. 若已有 LLM 生成文字，進入即時文字打字機模式
        if self.collected_text:
            full_text = "".join(self.collected_text).strip()
            preview = full_text if len(full_text) <= 3800 else full_text[:3800] + "...\n\n⏳ [內容長篇深研中...] ▍"
            return f"🤖 {preview} ▍\n\n{time_tag}"

        # 2. 若尚未產生文字（執行工具中），展示結構化即時步驟卡片
        steps_display = []
        if self.status_steps:
            for s in self.status_steps[:-1]:
                clean_s = s.replace("✓ ", "").lstrip("⏳🔍🌐📋⚡🖱️⌨️🏠🛒💬📊✉️📍⚙️ ")
                steps_display.append(f"✓ {clean_s}")
            curr = self.status_steps[-1].replace("✓ ", "")
            steps_display.append(f"{curr} ▍")
        else:
            steps_display.append("⏳ 正在分析需求... ▍")

        return "🤖 正在處理中...\n\n" + "\n".join(steps_display) + f"\n\n{time_tag}"

    def on_status(self, status_text: str):
        """當新工具調用啟動時記錄步驟"""
        self.last_updated_time = datetime.now().strftime("%H:%M:%S")
        if not self.status_steps or self.status_steps[-1] != status_text:
            self.status_steps.append(status_text)
            if len(self.status_steps) > 3:
                self.status_steps.pop(0)
        self._maybe_edit()

    def on_tool_done(self, tool_name: str):
        """當工具執行完畢時將最後一步標記為完成"""
        self.last_updated_time = datetime.now().strftime("%H:%M:%S")
        if self.status_steps and not self.status_steps[-1].startswith("✓ "):
            clean_s = self.status_steps[-1].lstrip("⏳🔍🌐📋⚡🖱️⌨️🏠🛒💬📊✉️📍⚙️ ")
            self.status_steps[-1] = f"✓ {clean_s}"
        self._maybe_edit()

    def on_text(self, delta: str):
        """當收到文字 delta 時記錄並更新"""
        self.last_updated_time = datetime.now().strftime("%H:%M:%S")
        self.collected_text.append(delta)
        self._maybe_edit()

    def _maybe_edit(self):
        """節流編輯 Telegram 訊息"""
        now = time.perf_counter()
        content = self._build_content()
        if self.msg_id is None:
            self.msg_id = send_telegram_message_return_id(
                content, 
                chat_id=self.chat_id, 
                disable_notification=True
            )
            self.last_edit_time = now
            self.last_card_content = content
        elif (now - self.last_edit_time >= 1.2) and (content != self.last_card_content):
            edit_telegram_message_sync(self.msg_id, content, chat_id=self.chat_id)
            self.last_edit_time = now
            self.last_card_content = content

    def cleanup(self):
        """手動主動清除進度卡片"""
        if self.msg_id:
            delete_telegram_message_sync(self.msg_id, chat_id=self.chat_id)
            self.msg_id = None

    def finalize(self):
        """防抖確保距離最後一次編輯至少 0.5s，確保在刪除或覆蓋時操作順暢"""
        elapsed = time.perf_counter() - self.last_edit_time
        if elapsed < 0.5:
            time.sleep(max(0.0, 0.5 - elapsed))

async def delete_telegram_message(message_id: int, chat_id: Optional[str] = None) -> bool:
    """非同步刪除 Telegram 訊息"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat or not message_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{token}/deleteMessage"
    payload = {"chat_id": target_chat, "message_id": message_id}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            if resp.status_code == 400 and "message to delete not found" in resp.text:
                return True
            print(f"[TelegramService] ⚠️ deleteMessage 失敗 (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[TelegramService] ❌ deleteMessage 例外: {e}")
    return False

async def send_telegram_message(
    text: str, 
    chat_id: Optional[str] = None, 
    parse_mode: str = "HTML",
    disable_notification: bool = False
) -> bool:
    """非同步發送 Telegram 訊息"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat:
        return False

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode
    }
    if disable_notification:
        payload["disable_notification"] = True
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200 and parse_mode:
                fallback_payload = {"chat_id": target_chat, "text": strip_html_tags(text)}
                if disable_notification:
                    fallback_payload["disable_notification"] = True
                fallback_resp = await client.post(url, json=fallback_payload)
                return fallback_resp.status_code == 200
            return resp.status_code == 200
    except Exception as e:
        print(f"[TelegramService] ❌ 發送失敗: {e}")
        return False

async def send_chat_action(action: str = "typing", chat_id: Optional[str] = None):
    """傳送正在輸入狀態"""
    token = settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not target_chat:
        return
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendChatAction"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(url, json={"chat_id": target_chat, "action": action})
    except Exception:
        pass

async def download_telegram_file(
    client: httpx.AsyncClient, 
    token: str, 
    file_id: str, 
    fallback_mime: Optional[str] = None
) -> Optional[dict]:
    """
    透過 Telegram Bot API 下載檔案至本地 telegram_uploads 目錄，
    並回傳 dict: {"uri": abs_path, "mime_type": mime_type}
    """
    try:
        # 1. 取得檔案路徑
        info_resp = await client.get(f"{TELEGRAM_API_BASE}/bot{token}/getFile?file_id={file_id}")
        if info_resp.status_code != 200:
            print(f"[TelegramService] ❌ getFile 失敗 (狀態碼: {info_resp.status_code})")
            return None
        info_data = info_resp.json()
        if not info_data.get("ok"):
            print(f"[TelegramService] ❌ getFile 回傳錯誤: {info_data}")
            return None

        remote_path = info_data["result"].get("file_path", "")
        if not remote_path:
            return None

        # 2. 下載檔案內容
        dl_url = f"{TELEGRAM_API_BASE}/file/bot{token}/{remote_path}"
        dl_resp = await client.get(dl_url)
        if dl_resp.status_code != 200:
            print(f"[TelegramService] ❌ 下載檔案失敗 (狀態碼: {dl_resp.status_code})")
            return None

        # 3. 確定副檔名與 MIME 類型
        ext = Path(remote_path).suffix
        if not ext:
            if fallback_mime and "png" in fallback_mime:
                ext = ".png"
            else:
                ext = ".jpg"

        mime_type, _ = mimetypes.guess_type(remote_path)
        if not mime_type:
            mime_type = fallback_mime or "image/jpeg"

        # 4. 儲存至本地 settings.TELEGRAM_UPLOADS_DIR
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{timestamp_str}_{Path(remote_path).name}"
        if not safe_name.endswith(ext):
            safe_name += ext
        local_path = settings.TELEGRAM_UPLOADS_DIR / safe_name
        local_path.write_bytes(dl_resp.content)

        print(f"[TelegramService] 📥 成功下載圖片: {local_path} ({len(dl_resp.content)} bytes, {mime_type})")
        return {
            "mime_type": mime_type,
            "uri": str(local_path.resolve())
        }
    except Exception as e:
        print(f"[TelegramService] ❌ 下載 Telegram 檔案發生例外: {e}")
        return None

class MediaGroupAggregator:
    def __init__(self):
        # 依 media_group_id 聚合同一相簿內的所有圖片
        self.groups: dict = {}
        # 依 chat_id 聚合短時間內連續發送的單張圖片與文字
        self.chat_pending: dict = {}

    def _get_or_create_entry(
        self,
        chat_id: str,
        media_group_id: Optional[str],
        caption: str,
        from_user: str
    ) -> dict:
        if media_group_id:
            key = str(media_group_id)
            store = self.groups
        else:
            key = str(chat_id)
            store = self.chat_pending

        if key not in store:
            store[key] = {
                "key": key,
                "is_group": bool(media_group_id),
                "chat_id": str(chat_id),
                "from_user": from_user,
                "media_slots": [],
                "in_flight": 0,
                "caption": caption.strip(),
                "task": None,
            }
        else:
            entry = store[key]
            if caption.strip():
                if entry["caption"]:
                    entry["caption"] = f"{entry['caption']}\n{caption.strip()}"
                else:
                    entry["caption"] = caption.strip()
        return store[key]

    def begin_media_download(
        self,
        chat_id: str,
        media_group_id: Optional[str],
        caption: str,
        from_user: str
    ) -> int:
        """
        在開始下載圖片『之前』同步呼叫：
        1. 立即凍結（取消）正在倒數的派發計時器，防止後面圖片還在下載時前面圖片先偷跑發車
        2. 預留順序槽位 (slot_idx)，確保併發下載 10 張圖時仍 100% 維持原始傳圖順序
        """
        entry = self._get_or_create_entry(chat_id, media_group_id, caption, from_user)
        slot_idx = len(entry["media_slots"])
        entry["media_slots"].append(None)
        entry["in_flight"] += 1

        if entry["task"] and not entry["task"].done():
            entry["task"].cancel()
            entry["task"] = None

        return slot_idx

    def finish_media_download(
        self,
        chat_id: str,
        media_group_id: Optional[str],
        slot_idx: int,
        media_item: Optional[dict],
        dispatch_func
    ):
        """
        當單張圖片下載完成（或失敗）時呼叫：
        只有當該批次所有下載中的圖片都完成 (in_flight == 0) 時，才啟動防抖倒數計時器。
        """
        store = self.groups if media_group_id else self.chat_pending
        key = str(media_group_id) if media_group_id else str(chat_id)
        entry = store.get(key)
        if not entry:
            return

        if 0 <= slot_idx < len(entry["media_slots"]):
            entry["media_slots"][slot_idx] = media_item

        entry["in_flight"] = max(0, entry["in_flight"] - 1)

        if entry["task"] and not entry["task"].done():
            entry["task"].cancel()
            entry["task"] = None

        # 只要還有任何一張圖片正在下載中，就絕對不啟動倒數計時！
        if entry["in_flight"] > 0:
            return

        delay = 2.5 if media_group_id else 3.0
        entry["task"] = asyncio.create_task(
            self._delayed_dispatch(store, key, delay, dispatch_func)
        )

    async def _delayed_dispatch(self, store: dict, key: str, delay: float, dispatch_func):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        entry = store.get(key)
        if not entry or entry["in_flight"] > 0:
            return

        store.pop(key, None)
        media_list = [m for m in entry["media_slots"] if m is not None]
        if not media_list:
            return

        await dispatch_func(
            chat_id=entry["chat_id"],
            user_text=entry["caption"],
            media_list=media_list,
            from_user=entry["from_user"]
        )

    def attach_text_to_pending(self, chat_id: str, text: str, dispatch_func) -> bool:
        """
        若該對話剛好有等待中（或正在下載中）的單圖或相簿，
        將文字附加為圖片說明；若圖片皆已下載完畢則立即派發，否則等圖片下載完自動派發。
        """
        chat_id_str = str(chat_id)
        target_store = None
        target_key = None

        if chat_id_str in self.chat_pending:
            target_store = self.chat_pending
            target_key = chat_id_str
        else:
            for g_key, g_entry in self.groups.items():
                if g_entry.get("chat_id") == chat_id_str:
                    target_store = self.groups
                    target_key = g_key
                    break

        if target_store and target_key:
            entry = target_store[target_key]
            if entry["caption"]:
                entry["caption"] = f"{entry['caption']}\n{text.strip()}"
            else:
                entry["caption"] = text.strip()

            if entry["task"] and not entry["task"].done():
                entry["task"].cancel()
                entry["task"] = None

            if entry["in_flight"] == 0:
                entry["task"] = asyncio.create_task(
                    self._delayed_dispatch(target_store, target_key, 0.3, dispatch_func)
                )
            return True
        return False

async def process_telegram_request(
    chat_id: str,
    user_text: str,
    media_list: Optional[List[dict]] = None,
    from_user: str = "unknown"
):
    """
    非同步處理 Telegram 請求並回傳結果：
    - 支援單張或多張圖片綜合分析
    - Fast-Path 家電秒回（無圖片時）
    - agy 常駐大腦深度理解與多模態分析
    - 生成報告連結對接
    """
    media_list = media_list or []
    
    # 格式化多模態 Prompt（並附上每張圖片的本機檔案路徑，供 shopee-seller 等技能直接取用）
    if media_list:
        paths_note = "\n".join(
            f"- 圖片 {i}: {m.get('uri')}" for i, m in enumerate(media_list, start=1) if m.get("uri")
        )
        if len(media_list) > 1:
            base_q = user_text or f"請幫我分析這 {len(media_list)} 張圖片。"
            effective_query = f"{base_q}\n\n（主人一次傳送了 {len(media_list)} 張圖片，本機檔案路徑依序為：\n{paths_note}\n請綜合檢視並回應）"
        else:
            base_q = user_text or "請幫我分析這張圖片"
            effective_query = f"{base_q}\n\n（附圖本機檔案路徑：\n{paths_note}）"
    else:
        effective_query = user_text

    img_log = f" (含 {len(media_list)} 張圖片)" if media_list else ""
    print(f"\n[TelegramService] 📩 處理 Telegram 指令: \"{effective_query}\"{img_log} (來自: {from_user})")

    # 1. 優先嘗試 Fast-Path 極速家電通道 (<0.02s，無圖片時)
    start_t = datetime.now()
    fast_reply = try_fast_intent(effective_query) if not media_list else None

    if fast_reply:
        diff = (datetime.now() - start_t).total_seconds()
        print(f"[TelegramService] ⚡ Fast-Path 秒回 (耗時: {diff:.3f}s): \"{fast_reply}\"")
        tg_text = format_telegram_response(
            reply=fast_reply,
            user_query=effective_query,
            latency=diff,
            actions=["engine: fast-path"]
        )
        await send_telegram_message(tg_text, chat_id=chat_id, parse_mode="HTML")
        return

    # 2. 轉交 agy 常駐 AI 大腦並啟用 Telegram 即時打字機串流
    await send_chat_action("typing", chat_id=chat_id)
    loop = asyncio.get_running_loop()
    existing_reports = {f.name: f.stat().st_mtime for f in settings.REPORTS_DIR.glob("*.html")}

    def _stream_runner():
        card = TelegramLiveCard(chat_id=chat_id)
        turn_result = {}
        try:
            stream_gen = agy_singleton.send_query_stream(effective_query, media=media_list if media_list else None)
            while True:
                item = next(stream_gen)
                if isinstance(item, tuple) and len(item) == 2:
                    ev_type, ev_data = item
                else:
                    ev_type, ev_data = "text", item

                if ev_type == "status":
                    card.on_status(str(ev_data))
                elif ev_type == "tool_done":
                    card.on_tool_done(str(ev_data))
                elif ev_type == "text":
                    card.on_text(str(ev_data))
        except StopIteration as e:
            turn_result = e.value or {}
        except Exception as e:
            if card.msg_id:
                delete_telegram_message_sync(card.msg_id, chat_id=chat_id)
            raise e

        card.finalize()
        clean_out = "".join(card.collected_text).strip() or turn_result.get("clean_reply", "")
        return clean_out, turn_result, card.msg_id

    try:
        reply, turn_result, msg_id = await loop.run_in_executor(None, _stream_runner)
    except Exception as e:
        print(f"[TelegramService] ❌ 串流處理異常: {e}")
        await send_telegram_message(f"⚠️ 處理訊息時發生錯誤：{e}", chat_id=chat_id)
        return
    total_lat = turn_result.get("total_latency", (datetime.now() - start_t).total_seconds())
    ttft = turn_result.get("time_to_first_token", 0)
    thinking_level = turn_result.get("thinking_level", "low")
    actions = [
        f"engine: agy-singleton ({thinking_level})",
        f"ttft: {ttft:.2f}s",
        f"total: {total_lat:.2f}s"
    ]
    if media_list:
        actions.append(f"multimodal: {len(media_list)} images" if len(media_list) > 1 else "multimodal: image")

    print(f"[TelegramService] 🤖 agy 回覆 (等級: {thinking_level.upper()} | 耗時: {total_lat:.2f}s): \"{reply[:60]}...\"")

    new_reports = [
        f for f in settings.REPORTS_DIR.glob("*.html")
        if f.name not in existing_reports or f.stat().st_mtime > existing_reports.get(f.name, 0)
    ]
    report_link_html = ""
    if new_reports:
        latest_report = max(new_reports, key=lambda f: f.stat().st_mtime)
        report_url = settings.get_report_url(latest_report.name)
        portal_url = f"http://{settings.TAILSCALE_DOMAIN}:{settings.PORT}/reports"
        report_link_html = f"\n\n🌐 <b>線上報告已生成</b>：\n👉 <a href=\"{report_url}\">點此開啟報告</a> ｜ 📂 <a href=\"{portal_url}\">報告總覽中心</a>"

    # 底部 Quote 顯示原始使用者輸入或圖片說明，調用支援長篇切分之發送引擎
    display_query = user_text if user_text else (f"傳送了 {len(media_list)} 張圖片" if len(media_list) > 1 else "請幫我分析這張圖片")
    if not reply or not reply.strip():
        reply = "⚠️ 抱歉主人，剛才處理此請求時底層程序超時或遇到中斷。請直接再試一次，或告知我為您處理！"
    send_formatted_telegram_reply(
        reply=reply,
        user_query=display_query,
        latency=total_lat,
        actions=actions,
        report_link_html=report_link_html,
        thinking_level=thinking_level,
        msg_id=msg_id,
        chat_id=chat_id
    )

async def start_telegram_bot():
    """常駐 Telegram Long-Polling 監聽循環"""
    token = settings.TELEGRAM_BOT_TOKEN
    allowed_chat_id = str(settings.TELEGRAM_CHAT_ID)

    if not token:
        print("[TelegramService] ⚠️ 未設定 TELEGRAM_BOT_TOKEN，略過 Telegram 監聽。")
        return

    offset = 0
    async with httpx.AsyncClient(timeout=35.0) as client:
        try:
            me_resp = await client.get(f"{TELEGRAM_API_BASE}/bot{token}/getMe")
            me_data = me_resp.json().get("result", {})
            bot_name = me_data.get("username", "UnknownBot")
            print(f"[TelegramService] 🚀 正在啟動 Telegram 監聽服務 (Bot: @{bot_name}, 授權 Chat ID: {allowed_chat_id})...")

            # 自動向 Telegram 伺服器同步註冊快捷指令選單
            commands = [
                {"command": "status", "description": "查看系統狀態、上下文用量與 AI 配額"},
                {"command": "perf", "description": "審查近期慢問答並產出優化改進提案"},
                {"command": "tasks", "description": "查看目前背景運行的監控與長任務"},
                {"command": "memory", "description": "檢視個人秘書模組化記憶庫概況"},
                {"command": "sync_memory", "description": "審查記憶庫並自動 Git 歸檔與推送"},
                {"command": "restart", "description": "重啟常駐代理進程並清空 Context"},
            ]
            cmd_resp = await client.post(f"{TELEGRAM_API_BASE}/bot{token}/setMyCommands", json={"commands": commands})
            if cmd_resp.status_code == 200 and cmd_resp.json().get("ok"):
                print("[TelegramService] ✅ Telegram 快捷指令選單已自動同步註冊 (status, tasks, memory, sync_memory, restart)")
            else:
                print(f"[TelegramService] ⚠️ 快捷指令選單註冊異常: {cmd_resp.text}")
        except Exception as e:
            print(f"[TelegramService] ⚠️ 初始化檢查失敗: {e}")

        aggregator = MediaGroupAggregator()
        while True:
            try:
                url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates?offset={offset}&timeout=25"
                resp = await client.get(url)
                if resp.status_code != 200:
                    print(f"[TelegramService] ⚠️ getUpdates 狀態碼: {resp.status_code}")
                    await asyncio.sleep(3)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    print(f"[TelegramService] ⚠️ getUpdates 回傳錯誤: {data}")
                    await asyncio.sleep(3)
                    continue

                updates = data.get("result", [])
                for update in updates:
                    offset = max(offset, update["update_id"] + 1)
                    message = update.get("message")
                    if not message:
                        continue

                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id"))
                    from_user = message.get("from", {}).get("username", "unknown")

                    # 安全過濾：僅處理主人的訊息
                    if allowed_chat_id and chat_id != allowed_chat_id:
                        print(f"[TelegramService] 🚫 忽略未授權使用者的訊息 (Chat ID: {chat_id}, User: {from_user})")
                        continue

                    # 提取文字或圖片說明文字 (Caption)
                    raw_text = message.get("text") or message.get("caption") or ""
                    user_text = raw_text.strip()

                    # 1. 偵測是否有照片或圖片文件
                    photo_items = message.get("photo")
                    doc_item = message.get("document")
                    media_group_id = message.get("media_group_id")

                    target_file_id = None
                    target_fallback_mime = None
                    action_type = "upload_photo"

                    if photo_items and isinstance(photo_items, list) and photo_items:
                        best_photo = photo_items[-1]
                        target_file_id = best_photo.get("file_id")
                        action_type = "upload_photo"
                    elif doc_item and isinstance(doc_item, dict):
                        doc_mime = doc_item.get("mime_type", "")
                        file_name = doc_item.get("file_name", "").lower()
                        is_img = doc_mime.startswith("image/") or any(
                            file_name.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"]
                        )
                        if is_img:
                            target_file_id = doc_item.get("file_id")
                            target_fallback_mime = doc_mime
                            action_type = "upload_document"

                    # 若此訊息包含圖片：先同步凍結計時器並預留順序槽位，再非同步併發下載（支援一次傳 10 張圖不搶跑）
                    if target_file_id:
                        slot_idx = aggregator.begin_media_download(
                            chat_id=chat_id,
                            media_group_id=str(media_group_id) if media_group_id else None,
                            caption=user_text,
                            from_user=from_user
                        )

                        async def _download_and_commit(
                            _cid=chat_id,
                            _mgid=str(media_group_id) if media_group_id else None,
                            _slot=slot_idx,
                            _fid=target_file_id,
                            _mime=target_fallback_mime,
                            _act=action_type
                        ):
                            downloaded = None
                            try:
                                if _slot == 0:
                                    await send_chat_action(_act, chat_id=_cid)
                                downloaded = await download_telegram_file(client, token, _fid, fallback_mime=_mime)
                            finally:
                                aggregator.finish_media_download(
                                    chat_id=_cid,
                                    media_group_id=_mgid,
                                    slot_idx=_slot,
                                    media_item=downloaded,
                                    dispatch_func=process_telegram_request
                                )

                        asyncio.create_task(_download_and_commit())
                        continue

                    # 若純文字訊息，檢查是否為附加在上一張等待中圖片的說明文字：
                    if user_text and aggregator.attach_text_to_pending(chat_id, user_text, process_telegram_request):
                        print(f"[TelegramService] 🔗 成功將文字補充附加至等待中的圖片訊息: \"{user_text}\"")
                        continue

                    # 若既無文字也無圖片：
                    if not user_text:
                        if "voice" in message:
                            await send_telegram_message("收到語音訊息！目前建議點擊鍵盤麥克風使用 iOS 聽寫發送文字，秒控更精確喔！", chat_id=chat_id)
                        continue

                    # 系統狀態與配額整合指令：/status, /usage, /quota, /context, 狀態, 用量, 額度, 配額
                    if user_text.lower() in ("/status", "/usage", "/quota", "/context", "狀態", "用量", "額度", "配額"):
                        stat = agy_singleton.get_status()
                        status_msg = quota_service.format_telegram_message(context_status=stat)
                        await send_telegram_message(status_msg, chat_id=chat_id)
                        continue

                    if user_text.lower() in ("/restart", "/reset", "重啟", "重置"):
                        agy_singleton.trigger_restart_with_notification("主人手動指令觸發")
                        continue

                    if user_text.lower() in ("/memory", "記憶", "記憶庫"):
                        memory_msg = (
                            f"🧠 <b>[個人秘書記憶庫概況]</b>\n\n"
                            f"📁 <b>模組化結構 (<code>memory/</code>)</b>：\n"
                            f"• <code>profile.md</code>：身分定位與溝通模式分流\n"
                            f"• <code>preferences.md</code>：生活與系統工具偏好\n"
                            f"• <code>projects.md</code>：核心專案目標與排程原則\n"
                            f"• <code>notes.md</code>：重要里程碑與備忘\n\n"
                            f"📅 <b>排程管理</b>：所有待辦日程一律委派至 Google Calendar「待辦事項」\n"
                            f"🌐 架構說明：<a href=\"{settings.get_report_url('secretary_memory_architecture.html')}\">點此查看視覺化圖表</a>"
                        )
                        await send_telegram_message(memory_msg, chat_id=chat_id)
                        continue

                    if user_text.lower() in ("/sync_memory", "/sync", "同步記憶", "歸檔記憶"):
                        await send_telegram_message("⏳ 正在檢查記憶庫異動並調用 LLM 審查...", chat_id=chat_id)
                        from memory_scheduler import sync_and_commit_memory
                        loop = asyncio.get_running_loop()
                        res = await loop.run_in_executor(None, sync_and_commit_memory, False)
                        if res.get("status") == "no_changes":
                            await send_telegram_message("✅ 今日記憶庫無未提交之異動，狀態乾淨！", chat_id=chat_id)
                        elif res.get("status") == "error":
                            await send_telegram_message(f"❌ 記憶庫歸檔失敗：{res.get('message')}", chat_id=chat_id)
                        continue

                    # 慢問答效能審查與改進提案指令：/perf, /review_perf, 審查慢問答, 效能審查
                    if user_text.lower() in ("/perf", "/review_perf", "審查慢問答", "效能審查", "慢問答", "檢查長問答"):
                        await send_telegram_message("⏳ 正在分析最近 24 小時之問答延遲並產出診斷提案...", chat_id=chat_id)
                        from performance_reviewer import performance_reviewer
                        loop = asyncio.get_running_loop()
                        res = await loop.run_in_executor(None, performance_reviewer.run_review, 24, 15.0, False)
                        if res.get("status") == "clean":
                            await send_telegram_message("✅ 過去 24 小時問答皆維持在 15 秒內，系統極為順暢，無偏長紀錄！", chat_id=chat_id)
                        else:
                            report_url = res.get("report_url")
                            portal_url = f"http://{settings.TAILSCALE_DOMAIN}:{settings.PORT}/reports"
                            top_text = ""
                            for idx, it in enumerate(res.get("top_proposals", []), 1):
                                top_text += (
                                    f"\n<b>{idx}️⃣ 【{it.get('category')}】</b>\n"
                                    f"• 原因：{it.get('root_cause')[:60]}...\n"
                                    f"• 提案：{it.get('proposal')}\n"
                                    f"• 預期：{it.get('estimated_saving')}\n"
                                )
                            msg = (
                                f"📊 <b>【助理效能審查與改進提案】</b>\n"
                                f"過去 24 小時共有 <b>{res.get('count')} 筆</b> 回應較長之問答：\n"
                                f"{top_text}\n"
                                f"🌐 <b>線上完整分析報告</b>：\n"
                                f"👉 <a href=\"{report_url}\">點此查看詳情報告</a> ｜ 📂 <a href=\"{portal_url}\">報告總覽中心</a>\n\n"
                                f"💡 <b>由主人決定是否採納</b>：若想優化哪一項，直接跟我說「幫我優化第 1 項」或「同意改動」即可！"
                            )
                            await send_telegram_message(msg, chat_id=chat_id, parse_mode="HTML")
                        continue

                    # 背景長任務管理指令：/tasks, /kill, /task_log
                    if user_text.lower() in ("/tasks", "任務", "背景任務", "查看任務", "查看背景任務", "task"):
                        from task_manager import task_manager
                        tasks = task_manager.list_tasks(include_finished=False)
                        if not tasks:
                            await send_telegram_message("📌 目前沒有任何運行中的背景長任務。", chat_id=chat_id)
                        else:
                            now = datetime.now()
                            lines = ["📋 <b>【運行中背景長任務清單】</b>\n"]
                            for i, t in enumerate(tasks, 1):
                                try:
                                    started = datetime.strptime(t["started_at"], "%Y-%m-%d %H:%M:%S")
                                    elapsed_mins = int((now - started).total_seconds() / 60)
                                except Exception:
                                    elapsed_mins = 0
                                lines.append(
                                    f"<b>{i}. {t['name']}</b>\n"
                                    f"   • 任務 ID: <code>{t['task_id']}</code> (PID: {t['pid']})\n"
                                    f"   • 啟動時間: {t['started_at']} (已運行 {elapsed_mins} 分鐘 / TTL {t.get('ttl_minutes', 120)}m)\n"
                                    f"   • 查 Log: <code>/task_log {t['task_id']}</code>\n"
                                    f"   • 停止指令: <code>/kill {t['task_id']}</code>\n"
                                )
                            await send_telegram_message("\n".join(lines), chat_id=chat_id)
                        continue

                    if user_text.startswith("/kill") or user_text.startswith("/stop"):
                        parts = user_text.split(maxsplit=1)
                        if len(parts) < 2:
                            await send_telegram_message("⚠️ 請提供要終止的 task_id, pid 或任務名稱。\n範例：<code>/kill task_xxx</code> 或 <code>/kill 78912</code>", chat_id=chat_id)
                        else:
                            target = parts[1].strip()
                            from task_manager import task_manager
                            res = task_manager.kill_task(target)
                            if res.get("success"):
                                await send_telegram_message(f"🛑 {res.get('message')}", chat_id=chat_id)
                            else:
                                await send_telegram_message(f"❌ 終止失敗：{res.get('error', '未知錯誤')}", chat_id=chat_id)
                        continue

                    if user_text.startswith("/task_log") or user_text.startswith("/tlog"):
                        parts = user_text.split(maxsplit=1)
                        if len(parts) < 2:
                            await send_telegram_message("⚠️ 請提供要查看 Log 的 task_id, pid 或任務名稱。\n範例：<code>/task_log task_xxx</code>", chat_id=chat_id)
                        else:
                            target = parts[1].strip()
                            from task_manager import task_manager
                            res = task_manager.get_log(target, lines=20)
                            if res.get("success"):
                                content = res.get("log_content", "").strip() or "(日誌內容為空)"
                                if len(content) > 3000:
                                    content = content[-3000:]
                                msg = f"📄 <b>【{res['name']} ({res['task_id']}) 最新日誌】</b>\n<pre>{content}</pre>"
                                await send_telegram_message(msg, chat_id=chat_id)
                            else:
                                await send_telegram_message(f"❌ 讀取失敗：{res.get('error', '未知錯誤')}", chat_id=chat_id)
                        continue

                    # 一般對話請求：非阻塞派發 process_telegram_request，絕不阻塞輪詢迴圈
                    asyncio.create_task(process_telegram_request(
                        chat_id=chat_id,
                        user_text=user_text,
                        media_list=[],
                        from_user=from_user
                    ))

            except asyncio.CancelledError:
                print("[TelegramService] 🛑 Telegram 監聽服務已停止。")
                break
            except Exception as e:
                print(f"[TelegramService] ⚠️ 輪詢異常: {e}")
                await asyncio.sleep(3)
