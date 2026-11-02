import os
import sys
import re
import json
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

def try_fast_intent(query: str) -> Optional[str]:
    """
    極速意圖攔截器 (Fast-Path Intent Router)
    - 對於常見的開關家電、溫濕度查詢、時間查詢，直接在 0.05 秒內完成動作並產生自然語音回覆
    - 若非明確單一動作，則回傳 None 交給常駐 AI 大腦深度處理
    """
    clean_q = re.sub(r"[，。！？\s]+", "", query).strip()
    
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
