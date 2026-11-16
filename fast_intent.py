import os
import sys
import re
import json
import html
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parent)

HASS_URL = os.getenv("HASS_URL", "").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")

# 快速設備映射表 (支援自訂擴充，由 data/devices.json 載入)
DEVICES_CONFIG = BASE_DIR / "data" / "devices.json"

def _load_device_map() -> Dict[str, Any]:
    if DEVICES_CONFIG.exists():
        try:
            cfg = json.loads(DEVICES_CONFIG.read_text(encoding="utf-8"))
            return cfg.get("devices", cfg)
        except Exception:
            pass
    return {}

DEVICE_MAP = _load_device_map()

def reload_devices():
    global DEVICE_MAP
    DEVICE_MAP = _load_device_map()

def get_current_time_intent(query: str) -> Optional[str]:
    """快速意圖：直接回報目前系統時間與日期 (秒級極速回應)"""
    q = query.strip().lower()
    if q in ["現在幾點", "現在時間", "幾點了", "報時", "time"]:
        now = datetime.now()
        hour_str = f"下午{now.hour - 12}" if now.hour >= 12 else f"上午{now.hour}"
        return f"現在是{hour_str}點{now.minute:02d}分。"
    
    if q in ["今天幾號", "今天日期", "今天星期幾", "date"]:
        now = datetime.now()
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        return f"今天是{now.year}年{now.month}月{now.day}號，星期{weekdays[now.weekday()]}。"
    
    if q in ["ping", "測試連線", "health"]:
        return "Pong! 個人助理系統連線正常，大腦在線。"
        
    return None

def call_ha_service(domain: str, service: str, service_data: dict) -> bool:
    """直接以純 Python urllib 呼叫 Home Assistant REST API (耗時 ~15ms)"""
    if not HASS_URL or not HASS_TOKEN:
        return False
    url = f"{HASS_URL}/api/services/{domain}/{service}"
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json"
    }
    body = json.dumps(service_data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"[FastIntent] HA service call failed: {e}")
        return False

def get_ha_state(entity_id: str) -> Optional[dict]:
    """獲取 HA 實體狀態 (耗時 ~15ms)"""
    if not HASS_URL or not HASS_TOKEN:
        return None
    url = f"{HASS_URL}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {HASS_TOKEN}"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else None
    except Exception as e:
        print(f"[FastIntent] HA get_state failed: {e}")
        return None

_COUPANG_TRIGGERS = {
    "查詢酷澎最新追蹤價格與狀態",
    "查詢酷澎最新價格",
    "酷澎盯價",
    "酷澎價格",
    "酷澎最新狀態",
    "查酷澎最新狀態",
    "酷澎巡檢狀況",
}

_FB_MONITOR_TRIGGERS = {
    "查詢fb社團近期巡檢狀態與貼文",
    "fb巡檢",
    "fb社團巡檢",
    "fb監控狀態",
    "查詢fb巡檢",
    "近期的巡檢有些什麼",
    "近期巡檢有些什麼",
    "社團最近巡檢到了什麼",
}


def format_fb_monitor_fast_reply(data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """零 LLM Token 組裝 FB 社團巡檢固定格式 HTML 卡片與語音摘要。"""
    if data is None:
        script = BASE_DIR / ".agents" / "skills" / "fb-group-monitor" / "scripts" / "fb_group_monitor.py"
        if not script.exists():
            return None
        try:
            res = subprocess.run(
                [sys.executable, str(script), "--recent", "5", "--json"],
                capture_output=True,
                text=True,
                timeout=6,
                cwd=str(BASE_DIR),
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None
            data = json.loads(res.stdout)
        except Exception:
            return None

    group_title = re.sub(r"^\(\d+\)\s*", "", str(data.get("group_title") or "FB 監控社團")).replace(" | Facebook", "").strip()
    daemon = data.get("daemon") or {}
    is_running = bool(daemon.get("is_running"))
    uptime = str(daemon.get("uptime") or "")
    last_poll_raw = str(data.get("last_poll_at") or "")
    poll_hhmm = last_poll_raw[11:16] if len(last_poll_raw) >= 16 else "近期"
    recent_polls = data.get("recent_polls") or []
    latest_cycle = recent_polls[0].get("cycle") if recent_polls else None
    total_seen = int(data.get("total_seen_posts") or 0)
    posts = data.get("recent_posts") or []

    matched_cnt = sum(1 for p in posts if "MATCH" in str(p.get("status") or "").upper())
    status_word = "運行中" if is_running else "目前未運行"
    spoken = (
        f"FB 社團巡檢守護進程{status_word}（最後巡檢 {poll_hhmm}），"
        f"最近 {len(posts)} 篇新貼文中共有 {matched_cnt} 篇命中通知，詳細清單請看下方卡片。"
    )

    badge_text = f"🟢 運行中 {uptime}".strip() if is_running else "⚪ 已停止"
    badge_bg = "rgba(52,211,153,0.15)" if is_running else "rgba(148,163,184,0.15)"
    badge_fg = "#34d399" if is_running else "#94a3b8"
    cycle_line = f"{poll_hhmm} 巡檢 · 第 {latest_cycle} 輪" if latest_cycle else f"{poll_hhmm} 巡檢"

    post_cards = []
    for p in posts[:5]:
        checked = str(p.get("checked_at") or "")
        hhmm = checked[11:16] if len(checked) >= 16 else str(p.get("posted_ago") or "近期")
        author = html.escape(str(p.get("author") or "成員"))
        img_cnt = int(p.get("image_count") or 0)
        img_str = f" · {img_cnt}圖" if img_cnt > 0 else ""
        is_match = "MATCH" in str(p.get("status") or "").upper()
        tag_text = "✅ 命中通知" if is_match else "已排除"
        tag_fg = "#4ade80" if is_match else "#94a3b8"
        tag_bg = "rgba(34,197,94,0.18)" if is_match else "rgba(148,163,184,0.15)"
        card_bg = "rgba(34,197,94,0.10)" if is_match else "rgba(255,255,255,0.04)"
        card_border = "rgba(34,197,94,0.28)" if is_match else "rgba(255,255,255,0.08)"
        desc = html.escape(str(p.get("summary") or p.get("reason") or p.get("text_preview") or "")[:90])
        permalink = html.escape(str(p.get("permalink") or data.get("group_url") or "#"), quote=True)

        post_cards.append(
            f"<a href=\"{permalink}\" style=\"text-decoration:none;display:block;padding:7px 9px;border-radius:10px;"
            f"background:{card_bg};border:1px solid {card_border};\">"
            "<div style=\"display:flex;justify-content:space-between;align-items:center;\">"
            f"<span style=\"font-size:11px;color:#94a3b8;\">{html.escape(hhmm)} · {author}{img_str}</span>"
            f"<span style=\"font-size:10.5px;font-weight:600;color:{tag_fg};background:{tag_bg};padding:1px 6px;border-radius:6px;\">{tag_text}</span>"
            "</div>"
            f"<div style=\"font-size:12.5px;color:#f1f5f9;margin-top:3px;font-weight:500;\">{desc}</div>"
            "</a>"
        )

    if not post_cards:
        post_cards.append(
            "<div style=\"font-size:12px;color:#94a3b8;padding:8px;text-align:center;\">近期尚無新貼文判讀紀錄</div>"
        )

    widget_html = (
        "<div style=\"display:flex;flex-direction:column;gap:8px;font-family:-apple-system,sans-serif;\">"
        "<div style=\"display:flex;justify-content:space-between;align-items:center;padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,0.1);\">"
        "<div>"
        "<div style=\"font-size:14px;font-weight:700;color:#f8fafc;\">📘 FB 社團巡檢看板</div>"
        f"<div style=\"font-size:11px;color:#94a3b8;margin-top:2px;\">{html.escape(group_title)}</div>"
        "</div>"
        "<div style=\"text-align:right;white-space:nowrap;margin-left:8px;\">"
        f"<div style=\"font-size:11px;color:{badge_fg};background:{badge_bg};padding:2px 8px;border-radius:999px;font-weight:600;\">{html.escape(badge_text)}</div>"
        f"<div style=\"font-size:10.5px;color:#64748b;margin-top:3px;\">{html.escape(cycle_line)}</div>"
        "</div></div>"
        "<div style=\"display:flex;justify-content:space-between;font-size:11px;color:#cbd5e1;background:rgba(255,255,255,0.04);padding:6px 8px;border-radius:8px;\">"
        f"<span>已累計判讀 <b>{total_seen:,}</b> 篇</span>"
        f"<span>近期命中 <b>{matched_cnt}</b> / {len(posts)} 篇</span>"
        "</div>"
        + "".join(post_cards)
        + "</div>"
    )
    return f"{spoken}\n\n<widget_html>\n{widget_html}\n</widget_html>"


def format_coupang_fast_reply(state_path: Optional[Path] = None) -> Optional[str]:
    """零 LLM Token 組裝酷澎購物車固定格式 HTML 卡片與語音摘要。"""
    path = state_path or (BASE_DIR / "data" / "coupang_monitor_state.json")
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    cart_baselines = state.get("cart_baselines") or {}
    if not cart_baselines:
        return "目前酷澎購物車尚未建立追蹤清單。"

    now_dt = datetime.now()
    dropped_items = []
    steady_items = []

    for key, info in cart_baselines.items():
        opt = info.get("option", "")
        if "保留清單" in key or "保留清單" in opt:
            continue
        last_seen_str = info.get("last_seen")
        is_fresh = False
        if last_seen_str:
            try:
                last_seen_dt = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                if (now_dt - last_seen_dt).total_seconds() <= 3600:
                    is_fresh = True
            except Exception:
                pass
        if not is_fresh:
            continue

        price = int(info.get("last_price") or 0)
        wave_high = int(info.get("wave_high_24h") or price)
        diff = price - wave_high
        item = {
            "title": str(info.get("title") or key),
            "option": str(opt),
            "price": price,
            "wave_high": wave_high,
            "diff": diff,
        }
        if diff < 0 and wave_high > 0:
            dropped_items.append(item)
        else:
            steady_items.append(item)

    total_active = len(dropped_items) + len(steady_items)
    if total_active == 0:
        return "目前酷澎購物車沒有一小時內同步的在線商品。"

    dropped_items.sort(key=lambda x: x["diff"])
    sync_raw = str(state.get("last_cart_sync_at") or state.get("last_checked_at") or "")
    sync_hhmm = sync_raw[11:16] if len(sync_raw) >= 16 else "剛剛"
    total_checks = int(state.get("total_checks") or 0)

    if dropped_items:
        highlights = "、".join(
            f"{it['title'][:18]} 降了 {abs(it['diff']):,} 元來到 {it['price']:,} 元"
            for it in dropped_items[:2]
        )
        spoken = f"酷澎目前共有 {total_active} 項商品在線盯價（{sync_hhmm} 同步），其中 {highlights}，詳細清單請看下方卡片。"
    else:
        spoken = f"酷澎目前共有 {total_active} 項商品在線盯價（{sync_hhmm} 同步），目前皆維持平盤，詳細清單請看下方卡片。"

    drop_cards = []
    for it in dropped_items:
        pct = abs(it["diff"]) / it["wave_high"] * 100.0
        title_esc = html.escape(it["title"])
        opt_esc = html.escape(it["option"])
        sub_line = f"{opt_esc} · 24h高 ${it['wave_high']:,}" if opt_esc else f"24h高 ${it['wave_high']:,}"
        drop_cards.append(
            "<div style=\"display:flex;justify-content:space-between;align-items:center;padding:6px 8px;"
            "border-radius:10px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.28);\">"
            "<div>"
            f"<div style=\"font-size:13px;font-weight:600;color:#f8fafc;\">{title_esc}</div>"
            f"<div style=\"font-size:11px;color:#94a3b8;\">{sub_line}</div>"
            "</div>"
            "<div style=\"text-align:right;white-space:nowrap;margin-left:8px;\">"
            f"<div style=\"font-size:14px;font-weight:700;color:#4ade80;\">${it['price']:,}</div>"
            f"<div style=\"font-size:11px;font-weight:600;color:#4ade80;\">📉 -${abs(it['diff']):,} (-{pct:.1f}%)</div>"
            "</div></div>"
        )

    steady_rows = []
    for it in steady_items:
        title_esc = html.escape(it["title"])
        opt_esc = html.escape(it["option"])
        opt_span = f" <span style=\"color:#64748b;font-size:11px;\">{opt_esc}</span>" if opt_esc else ""
        steady_rows.append(
            f"<div style=\"color:#e2e8f0;\">{title_esc}{opt_span}</div>"
            f"<div style=\"color:#f8fafc;font-weight:600;text-align:right;white-space:nowrap;\">${it['price']:,}</div>"
        )

    steady_block = ""
    if steady_rows:
        steady_block = (
            "<div style=\"display:grid;grid-template-columns:1fr auto;row-gap:6px;column-gap:8px;padding:4px 2px;font-size:12.5px;\">"
            + "".join(steady_rows)
            + "</div>"
        )

    widget_html = (
        "<div style=\"display:flex;flex-direction:column;gap:8px;font-family:-apple-system,sans-serif;\">"
        "<div style=\"display:flex;justify-content:space-between;align-items:center;padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,0.1);\">"
        "<div style=\"font-size:14px;font-weight:700;color:#f8fafc;\">🛒 酷澎即時價格監控</div>"
        f"<div style=\"font-size:11px;color:#34d399;background:rgba(52,211,153,0.15);padding:2px 8px;border-radius:999px;font-weight:600;\">{html.escape(sync_hhmm)} 已同步 · {total_checks:,}次</div>"
        "</div>"
        + "".join(drop_cards)
        + steady_block
        + "</div>"
    )
    return f"{spoken}\n\n<widget_html>\n{widget_html}\n</widget_html>"


def try_fast_intent(query: str) -> Optional[str]:
    """
    極速意圖攔截器 (Fast-Path Intent Router)
    - 對於常見的開關家電、溫濕度查詢、時間查詢，直接在 0.05 秒內完成動作並產生自然語音回覆
    - 若非明確單一動作，則回傳 None 交給常駐 AI 大腦深度處理
    """
    stripped = re.sub(r"^\s*【回覆長度約束：[^】]*】[^\n]*\s*", "", query)
    clean_q = re.sub(r"[，。！？\s]+", "", stripped).strip()

    # 0. 酷澎盯價 & FB 社團巡檢 零 Token HTML 卡片快查 (~0.01s)
    if clean_q in _COUPANG_TRIGGERS:
        coupang_reply = format_coupang_fast_reply()
        if coupang_reply:
            return coupang_reply

    if clean_q.lower() in _FB_MONITOR_TRIGGERS:
        fb_reply = format_fb_monitor_fast_reply()
        if fb_reply:
            return fb_reply
    
    # 1. 系統時間/日期秒回 (<0.001s)
    time_reply = get_current_time_intent(clean_q)
    if time_reply:
        return time_reply

    # 2. AI 配額與額度極速查詢 (~0.01s)
    if any(k in clean_q for k in ["目前額度", "剩餘額度", "查詢額度", "還有多少額度", "查看額度", "配額", "模型額度"]) or clean_q in ["quota", "usage", "額度", "用量"]:
        try:
            from quota_service import quota_service
            return quota_service.format_voice_summary()
        except Exception:
            pass

    # 3. 背景長任務極速語音查詢與急停 (~0.01s)
    if clean_q in ["查看背景任務", "背景任務", "有哪些背景任務", "查看任務", "任務清單"]:
        try:
            from task_manager import task_manager
            tasks = task_manager.list_tasks(include_finished=False)
            if not tasks:
                return "目前沒有任何正在運行的背景任務。"
            names = "、".join([t["name"] for t in tasks])
            return f"目前有 {len(tasks)} 個背景任務正在運行中：{names}。"
        except Exception:
            pass

    if clean_q in ["停止所有背景任務", "取消所有背景任務", "停止背景任務", "關閉背景任務"]:
        try:
            from task_manager import task_manager
            killed = task_manager.kill_all()
            if not killed:
                return "目前沒有任何正在運行的背景任務需要停止。"
            names = "、".join([k["name"] for k in killed if k.get("success")])
            return f"已經為你停止所有背景任務了：{names}。"
        except Exception:
            pass

    # 4. 目前即時位置極速查詢 (~0.25s)
    if re.fullmatch(r"(幫我問一下|請幫我查一下|幫我查一下|查一下)?(我現在在哪裡|我現在在哪|我目前在哪裡|我目前在哪|現在位置|目前位置|我現在的位置|我目前的位置|現在在哪裡|現在在哪|我們現在在哪裡|我們現在在哪|我們目前在哪裡|我們目前在哪)[？?嗎啊吧呢\s]*", clean_q):
        try:
            loc_script_path = BASE_DIR / "skills" / "current-location" / "scripts"
            if not loc_script_path.exists():
                loc_script_path = BASE_DIR / ".agents" / "skills" / "current-location" / "scripts"
            if str(loc_script_path) not in sys.path:
                sys.path.append(str(loc_script_path))
            from get_location import resolve_location
            loc = resolve_location()
            if loc.get("status") == "success":
                summary = loc.get("summary", "")
                maps_url = loc.get("maps_url", "")
                if maps_url:
                    return f"{summary}\n\n📍 地圖連結：{maps_url}"
                return summary
        except Exception as e:
            print(f"[FastIntent] Location query failed: {e}")

    # 5. 家裡溫濕度極速查詢 (~0.03s)
    temp_sensor = os.getenv("HA_TEMP_SENSOR", "sensor.0x0ceff6fffe69c1c0_temperature")
    hum_sensor = os.getenv("HA_HUMIDITY_SENSOR", "sensor.0x0ceff6fffe69c1c0_humidity")
    if any(k in clean_q for k in ["客廳溫度", "客廳溫濕度", "家裡溫度", "家裡溫濕度", "室內溫度", "室內溫濕度"]):
        temp_s = get_ha_state(temp_sensor)
        hum_s = get_ha_state(hum_sensor)
        if temp_s and hum_s:
            t = temp_s.get("state", "未知")
            h = hum_s.get("state", "未知")
            return f"客廳目前溫度 {t} 度，濕度 {h}%。"

    # 6. 關閉全部燈光
    if any(k in clean_q for k in ["關掉所有燈", "關閉所有燈", "關掉全部燈", "關閉全部燈", "關所有的燈", "關全部的燈", "關所有燈", "關全部燈"]):
        ok = call_ha_service("automation", "trigger", {"entity_id": "automation.action_turn_lights_off"})
        if ok:
            return "已經為你關閉家裡所有的燈了。"

    # 7. 若包含問句、排錯、原因查詢（例如「為什麼...不會開」、「查一下為什麼打不開」），一律放行給大腦深度處理
    if re.search(r"(為什麼|為何|怎麼|查一下|檢查|排查|不會|不能|沒反應|失效|故障|修復|打不開|關不掉|開不了)", clean_q):
        return None

    # 8. 家電開關動作解析 (需有 DEVICE_MAP 與 HASS 連線)
    if not HASS_TOKEN or not DEVICE_MAP:
        return None

    # 動作識別
    action = None
    if re.search(r"(關掉|關閉|關上|熄掉|熄滅|熄|關)", clean_q):
        action = "turn_off"
    elif re.search(r"(打開|開啟|開了|開)", clean_q):
        action = "turn_on"

    if not action:
        return None

    # 比對目標設備 (由長到短比對，優先匹配長名稱如「主臥廁所電燈」再比「廁所電燈」)
    sorted_aliases = sorted(DEVICE_MAP.keys(), key=lambda x: len(x), reverse=True)
    target_match = None
    for alias in sorted_aliases:
        if alias in clean_q:
            target_match = alias
            break

    if not target_match:
        return None

    dev_info = DEVICE_MAP[target_match]
    dev_type = dev_info[0]
    dev_target = dev_info[1]
    dev_name = dev_info[2] if len(dev_info) > 2 else target_match
    action_text = "開啟" if action == "turn_on" else "關閉"

    # 執行控制
    if dev_type == "multi_switch":
        for eid in dev_target:
            call_ha_service("switch", action, {"entity_id": eid})
        return f"已經為你{action_text}{dev_name}了。"
    elif dev_type == "cover":
        cover_action = "open_cover" if action == "turn_on" else "close_cover"
        cover_text = "打開" if action == "turn_on" else "關閉"
        call_ha_service("cover", cover_action, {"entity_id": dev_target})
        return f"已經為你{cover_text}{dev_name}了。"
    else:
        domain = dev_type
        call_ha_service(domain, action, {"entity_id": dev_target})
        return f"已經為你{action_text}{dev_name}了。"
