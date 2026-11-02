#!/usr/bin/env python3
"""
即時定位取得工具 (Real-time Location Tool)
從 Home Assistant 的 person 實體 (由 iPhone / Apple Watch 即時更新) 取得 GPS 座標，
並進行逆向地理編碼解析出中/日文具體地址、城市、區域與地標。
"""

import sys
import os
import json
import time
import math
import argparse
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from common.venv_guard import ensure_venv
    ensure_venv()
except Exception:
    pass

from dotenv import load_dotenv

# 載入專案根目錄之 .env
load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"))

HASS_URL = os.getenv("HASS_URL", "").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")
HA_PERSON_ENTITY = os.getenv("HA_PERSON_ENTITY", "person.user")
CACHE_FILE = BASE_DIR / "data" / "loc_cache.json"


def to_tw_traditional(text: str) -> str:
    """轉換常見地名簡化字為標準繁體中文（如 福冈 -> 福岡, 区 -> 區）"""
    if not text:
        return text
    replacements = {
        "福冈": "福岡",
        "冈": "岡",
        "区": "區",
        "县": "縣",
        "东": "東",
        "车站": "車站",
        "铁": "鐵",
        "湾": "灣",
        "涩谷": "澀谷",
        "新宿区": "新宿區",
        "银座": "銀座"
    }
    for s, t in replacements.items():
        text = text.replace(s, t)
    return text


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """計算兩組經緯度之間的公尺距離"""
    R = 6371000  # 地球半徑 (公尺)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_ha_person_state(entity_id: str = None) -> dict:
    """從 Home Assistant 取得使用者即時狀態與 GPS 座標 (耗時 ~15ms)"""
    if not HASS_URL or not HASS_TOKEN:
        return {"error": "未設定 HASS_URL 或 HASS_TOKEN"}
    target_entity = entity_id or HA_PERSON_ENTITY
    url = f"{HASS_URL}/api/states/{target_entity}"
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except Exception as e:
        return {"error": str(e)}


def reverse_geocode(lat: float, lon: float) -> dict:
    """
    透過 OpenStreetMap Nominatim 進行逆向地理編碼，
    具備本機快取保護（5 分鐘內且位移小於 50 公尺直接命中快取）。
    """
    # 1. 檢查快取
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            cached_lat = cache_data.get("latitude")
            cached_lon = cache_data.get("longitude")
            cached_time = cache_data.get("cached_at", 0)

            if cached_lat is not None and cached_lon is not None:
                dist = haversine_distance(lat, lon, cached_lat, cached_lon)
                age = time.time() - cached_time
                if dist < 50.0 and age < 300.0:
                    return cache_data.get("result", {})
    except Exception:
        pass

    # 2. 呼叫 Nominatim API
    url = f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}&accept-language=zh-TW,ja,en"
    headers = {"User-Agent": "MacMiniAssistant/1.0 (personal-assistant)"}
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # 寫入快取
            try:
                CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({
                        "latitude": lat,
                        "longitude": lon,
                        "cached_at": time.time(),
                        "result": data
                    }, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return data
    except Exception as e:
        return {"error": str(e)}


def resolve_location(entity_id: str = None) -> dict:
    """整合 HA 與逆地理編碼，產生完整結構化定位物件"""
    target_entity = entity_id or HA_PERSON_ENTITY
    ha_state = get_ha_person_state(target_entity)
    if "error" in ha_state or not ha_state:
        return {
            "status": "error",
            "message": f"無法從 Home Assistant 取得定位資料: {ha_state.get('error', '未知錯誤')}"
        }

    state = ha_state.get("state", "unknown")
    attrs = ha_state.get("attributes", {})
    lat = attrs.get("latitude")
    lon = attrs.get("longitude")
    accuracy = attrs.get("gps_accuracy")
    source = attrs.get("source", "unknown")
    last_updated = ha_state.get("last_updated")

    # 若狀態為 home，直接秒定
    if state == "home":
        return {
            "status": "success",
            "is_home": True,
            "state": "home",
            "country": "台灣",
            "city": "台北市",
            "poi": "住家",
            "summary": "您目前在家中（台北住家）。",
            "latitude": lat,
            "longitude": lon,
            "accuracy_meters": accuracy,
            "source_device": source,
            "last_updated": last_updated,
            "maps_url": f"https://www.google.com/maps?q={lat},{lon}" if lat and lon else ""
        }

    if lat is None or lon is None:
        return {
            "status": "error",
            "message": f"Home Assistant 上的 {target_entity} 未提供 GPS 經緯度座標。"
        }

    # 逆向地理編碼反查具體地址
    geo_data = reverse_geocode(lat, lon)
    address = geo_data.get("address", {})

    country = to_tw_traditional(address.get("country", ""))
    city = to_tw_traditional(address.get("city") or address.get("town") or address.get("county") or "")
    district = to_tw_traditional(address.get("suburb") or address.get("city_district") or address.get("borough") or "")
    neighbourhood = to_tw_traditional(address.get("neighbourhood") or address.get("quarter") or "")
    road = to_tw_traditional(address.get("road", ""))
    poi = to_tw_traditional(address.get("shop") or address.get("amenity") or address.get("tourism") or address.get("building") or "")

    # 組合自然語音口語描述 (Summary)
    loc_parts = []
    if country and country != "台灣":
        loc_parts.append(country)
    if city:
        loc_parts.append(city)
    if district and district != city:
        loc_parts.append(district)
    
    main_area = "".join(loc_parts)
    detail_parts = [p for p in [neighbourhood, road, poi] if p]
    landmark_str = "、".join(detail_parts) if detail_parts else ""

    if landmark_str:
        summary = f"您目前位於{main_area}（{landmark_str}附近）。"
    elif main_area:
        summary = f"您目前位於{main_area}一帶。"
    else:
        summary = f"您目前座標為緯度 {lat:.4f}、經度 {lon:.4f}。"

    display_name = to_tw_traditional(geo_data.get("display_name", ""))

    return {
        "status": "success",
        "is_home": False,
        "state": state,
        "country": country,
        "city": city,
        "district": district,
        "neighbourhood": neighbourhood,
        "road": road,
        "poi": poi,
        "summary": summary,
        "display_name": display_name,
        "latitude": lat,
        "longitude": lon,
        "accuracy_meters": accuracy,
        "source_device": source,
        "last_updated": last_updated,
        "maps_url": f"https://www.google.com/maps?q={lat},{lon}"
    }


def main():
    parser = argparse.ArgumentParser(description="取得主人即時 GPS 定位與地址資訊")
    parser.add_argument("--json", action="store_true", help="輸出完整結構化 JSON")
    parser.add_argument("--maps", action="store_true", help="僅輸出 Google Maps 連結")
    parser.add_argument("--person", type=str, default=None, help="自訂 HA person 實體 ID")
    args = parser.parse_args()

    loc = resolve_location(args.person)

    if args.json:
        print(json.dumps(loc, ensure_ascii=False, indent=2))
    elif args.maps:
        print(loc.get("maps_url", ""))
    else:
        # 預設純文字輸出口語摘要與 Google Maps 連結
        if loc.get("status") == "success":
            print(loc.get("summary"))
            if loc.get("display_name"):
                print(f"詳細地址: {loc.get('display_name')}")
            if loc.get("maps_url"):
                print(f"地圖連結: {loc.get('maps_url')}")
        else:
            print(f"❌ 定位失敗: {loc.get('message')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
