import os
import sys
import re
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

HASS_URL = os.getenv("HASS_URL", "").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")

# 快速設備映射表 (支援自訂擴充，若有 Home Assistant 可自訂或由 data/devices.json 載入)
DEVICE_MAP = {}

def get_current_time_intent(query: str) -> Optional[str]:
    """快速意圖：直接回報目前系統時間與日期 (秒級極速回應)"""
    q = query.strip().lower()
    if q in ["現在幾點", "現在時間", "幾點了", "報時", "time"]:
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"現在時間是 {now.strftime('%H:%M')} ({weekdays[now.weekday()]})"
    
    if q in ["今天幾號", "今天日期", "今天星期幾", "date"]:
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"今天是 {now.strftime('%Y 年 %m 月 %d 日')} {weekdays[now.weekday()]}"
    
    if q in ["ping", "測試連線", "health"]:
        return "Pong! 個人助理系統連線正常，大腦在線。"
        
    return None

def call_hass_service(domain: str, service: str, entity_id: str) -> bool:
    """呼叫 Home Assistant 服務"""
    if not HASS_URL or not HASS_TOKEN:
        return False
        
    url = f"{HASS_URL}/api/services/{domain}/{service}"
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = json.dumps({"entity_id": entity_id}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as response:
            return response.status in (200, 201)
    except Exception:
        return False

def try_fast_intent(query: str) -> Optional[str]:
    """
    極速通道匹配入口 (0.01s 繞過大模型)
    若命中快速意圖或自訂設備控制，直接回傳結果；未命中則回傳 None 交由大腦處理。
    """
    # 1. 系統時間/狀態極速意圖
    time_reply = get_current_time_intent(query)
    if time_reply:
        return time_reply

    # 2. 家電直連控制 (若有設定 DEVICE_MAP 與 HASS)
    if not HASS_TOKEN or not DEVICE_MAP:
        return None

    # 可在此處依據正規表示式比對開關家電意圖
    return None
