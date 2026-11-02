#!/usr/bin/env python3
"""
Home Assistant 智慧離家巡檢守護者 (scripts/ha_smart_patrol.py)

核心職責：
1. 定期巡檢全屋人員在席狀態（依據 data/devices.json 或環境變數配置）。
2. 當且僅當「全屋無人且持續滿 5 分鐘（防漂移）」時觸發。
3. 精準差量巡檢：一鍵掃描全屋狀態，【只針對當前真正開啟中 (ON) 的目標設備】下達關閉指令。
   已關閉的設備絕不發送多餘指令，零網路負擔。
4. 明確排除冷氣空調、除濕機、新風機，保持生活環境適宜。
5. 關閉後立即發送 Telegram 結構化明細報備。
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Any, Optional
from dotenv import load_dotenv

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[1])
load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"))

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from common.venv_guard import ensure_venv
    ensure_venv()
except Exception:
    pass

HASS_URL = os.getenv("HASS_URL", "http://homeassistant.local:8123").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HA_NOTIFY_DEVICE = os.getenv("HA_NOTIFY_DEVICE", "mobile_app_user_phone")

DEVICES_CONFIG = BASE_DIR / "data" / "devices.json"

# 預設巡檢清冊（若無 data/devices.json 則為空，由使用者於 devices.json 自訂）
DEFAULT_PERSON_ENTITIES = []
DEFAULT_TARGET_LIGHTS = []
DEFAULT_TARGET_MEDIA = []
DEFAULT_TARGET_SAFETY = []

def _load_patrol_config():
    if DEVICES_CONFIG.exists():
        try:
            cfg = json.loads(DEVICES_CONFIG.read_text(encoding="utf-8"))
            patrol_cfg = cfg.get("patrol", {})
            p = [tuple(x) for x in patrol_cfg.get("persons", DEFAULT_PERSON_ENTITIES)]
            l = [tuple(x) for x in patrol_cfg.get("lights", DEFAULT_TARGET_LIGHTS)]
            m = [tuple(x) for x in patrol_cfg.get("media", DEFAULT_TARGET_MEDIA)]
            s = [tuple(x) for x in patrol_cfg.get("safety", DEFAULT_TARGET_SAFETY)]
            return p, l, m, s
        except Exception:
            pass
    return DEFAULT_PERSON_ENTITIES, DEFAULT_TARGET_LIGHTS, DEFAULT_TARGET_MEDIA, DEFAULT_TARGET_SAFETY

PERSON_ENTITIES, TARGET_LIGHTS, TARGET_MEDIA, TARGET_SAFETY = _load_patrol_config()


class HASmartPatrol:
    def __init__(self):
        self.hass_url = HASS_URL
        self.hass_token = HASS_TOKEN

    def _get_all_states(self) -> Dict[str, Any]:
        """0.05 秒單次請求拉取全屋狀態字典"""
        if not self.hass_token:
            raise ValueError("未設定 HASS_TOKEN")
        url = f"{self.hass_url}/api/states"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.hass_token}",
                "Content-Type": "application/json"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.load(resp)
                return {s["entity_id"]: s for s in data}
        except Exception as e:
            print(f"[SmartPatrol] ❌ 讀取 Home Assistant 狀態失敗: {e}", file=sys.stderr)
            return {}

    def _call_service(self, domain: str, service: str, entity_id: str) -> bool:
        """調用 Home Assistant 服務關閉指定單一設備"""
        url = f"{self.hass_url}/api/services/{domain}/{service}"
        payload = json.dumps({"entity_id": entity_id}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.hass_token}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[SmartPatrol] ❌ 關閉 {entity_id} 失敗: {e}", file=sys.stderr)
            return False

    def check_presence(self, states: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        檢查是否全屋無人：
        回傳 (is_nobody_home, detail_list)
        """
        details = []
        is_nobody = True
        for eid, name in PERSON_ENTITIES:
            state_obj = states.get(eid, {})
            current_state = state_obj.get("state", "unknown")
            details.append(f"{name} ({current_state})")
            if current_state == "home":
                is_nobody = False
        return is_nobody, details

    def find_active_devices(self, states: Dict[str, Any]) -> Dict[str, List[Tuple[str, str]]]:
        """
        精準掃描：只找出【目前真正處於開啟中狀態】的設備！
        回傳: {"lights": [...], "media": [...], "safety": [...]}
        """
        active: Dict[str, List[Tuple[str, str]]] = {
            "lights": [],
            "media": [],
            "safety": []
        }

        # 1. 電燈
        for eid, name in TARGET_LIGHTS:
            st = states.get(eid, {}).get("state")
            if st == "on":
                active["lights"].append((eid, name))

        # 2. 電視影音
        for eid, name in TARGET_MEDIA:
            st = states.get(eid, {}).get("state")
            if st not in ("off", "unavailable", "standby", "idle", None):
                active["media"].append((eid, name))

        # 3. 高溫安全設備 (離子夾)
        for eid, name in TARGET_SAFETY:
            st = states.get(eid, {}).get("state")
            if st == "on":
                # 去重：若同名實體已經加入則跳過
                if not any(item[1] == name for item in active["safety"]):
                    active["safety"].append((eid, name))

        return active

    def execute_turn_off_delta(self, active_devices: Dict[str, List[Tuple[str, str]]]) -> Dict[str, List[str]]:
        """
        精準差量關閉：只對真正開啟中的設備發送 turn_off，絕不多關已關閉的設備！
        """
        turned_off: Dict[str, List[str]] = {
            "lights": [],
            "media": [],
            "safety": []
        }

        # 關閉真正開啟的燈
        for eid, name in active_devices["lights"]:
            domain = eid.split(".")[0]
            if self._call_service(domain, "turn_off", eid):
                turned_off["lights"].append(name)

        # 關閉真正開啟的電視
        for eid, name in active_devices["media"]:
            if self._call_service("media_player", "turn_off", eid):
                turned_off["media"].append(name)

        # 關閉真正開啟的離子夾
        for eid, name in active_devices["safety"]:
            domain = eid.split(".")[0]
            if self._call_service(domain, "turn_off", eid):
                turned_off["safety"].append(name)

        return turned_off

    def send_telegram_report(self, turned_off: Dict[str, List[str]], has_hair_iron: bool) -> None:
        """推播離家關閉報備到 Telegram"""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return

        lines = [
            "🏠 <b>【全屋無人・智慧離家巡檢】</b>",
            "偵測到家中已全員離家超過 5 分鐘，已自動為您關閉開著的設備：\n"
        ]

        if turned_off["lights"]:
            lines.append(f"💡 <b>已關閉電燈 ({len(turned_off['lights'])} 處)</b>：")
            for name in turned_off["lights"]:
                lines.append(f"• {name}")
            lines.append("")

        if turned_off["media"]:
            lines.append(f"📺 <b>已關閉電視影音 ({len(turned_off['media'])} 部)</b>：")
            for name in turned_off["media"]:
                lines.append(f"• {name}")
            lines.append("")

        if turned_off["safety"]:
            lines.append(f"🔥 <b>安全高溫家電已斷電 ({len(turned_off['safety'])} 項)</b>：")
            for name in turned_off["safety"]:
                lines.append(f"• ⚠️ {name}（防範火災隱患）")
            lines.append("")

        lines.append("❄️ <i>冷氣空調、除濕機與新風機維持原運轉設定，未受更動。</i>")
        msg_text = "\n".join(lines)

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg_text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                pass
        except Exception as e:
            print(f"[SmartPatrol] ⚠️ Telegram 報備發送失敗: {e}", file=sys.stderr)

        # 若包含離子夾，加發一則 iOS Critical Alert 靜音置頂通知確保第一時間看見
        if has_hair_iron:
            try:
                from scripts.ios_notify import send_critical_alert
                send_critical_alert(
                    title="🔥 離子夾已自動斷電",
                    message="巡檢偵測到出門無人但離子夾仍開著，系統已自動執行安全斷電！",
                    volume=0.0
                )
            except Exception as e:
                pass


def main():
    parser = argparse.ArgumentParser(description="Home Assistant 智慧離家差量巡檢工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. status
    subparsers.add_parser("status", help="即時檢視全屋人員在席與目標設備開啟狀態")

    # 2. scan
    scan_parser = subparsers.add_parser("scan", help="執行單次巡檢")
    scan_parser.add_argument("--execute", action="store_true", help="若全屋無人且有設備開著，真正執行關閉與推播")

    # 3. watch
    watch_parser = subparsers.add_parser("watch", help="啟動背景常駐巡檢守護進程")
    watch_parser.add_argument("--interval", type=int, default=180, help="輪詢檢測間隔 (秒，預設 180)")
    watch_parser.add_argument("--debounce", type=int, default=300, help="無人判定緩衝時間 (秒，預設 300 即 5 分鐘)")

    args = parser.parse_args()
    patrol = HASmartPatrol()

    if args.command == "status":
        states = patrol._get_all_states()
        is_nobody, pres_details = patrol.check_presence(states)
        active = patrol.find_active_devices(states)

        print("\n===============================")
        print("🏠 [Home Assistant 智慧巡檢現況]")
        print("===============================")
        print(f"👥 人員狀態: {', '.join(pres_details)}")
        print(f"📍 全屋判定: {'🔴 全屋無人 (Away)' if is_nobody else '🟢 有人在家 (Home)'}\n")

        total_active = len(active["lights"]) + len(active["media"]) + len(active["safety"])
        if total_active == 0:
            print("✅ 目前所有目標電燈與電視皆處於關閉狀態，完全省電安心！")
        else:
            print(f"⚡ 目前開啟中之設備 (共 {total_active} 項，無人時將精準關閉)：")
            for eid, name in active["lights"]:
                print(f"  • 💡 [電燈] {name} ({eid})")
            for eid, name in active["media"]:
                print(f"  • 📺 [電視] {name} ({eid})")
            for eid, name in active["safety"]:
                print(f"  • 🔥 [安全] {name} ({eid})")
        print("===============================\n")

    elif args.command == "scan":
        states = patrol._get_all_states()
        is_nobody, pres_details = patrol.check_presence(states)
        active = patrol.find_active_devices(states)
        total_active = len(active["lights"]) + len(active["media"]) + len(active["safety"])

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 巡檢執行: 人員={pres_details}, 開啟設備數={total_active}")

        if is_nobody and total_active > 0:
            if args.execute:
                print(f"🚨 全屋無人且有 {total_active} 項設備開啟，開始精準差量關閉...")
                has_safety = len(active["safety"]) > 0
                turned = patrol.execute_turn_off_delta(active)
                patrol.send_telegram_report(turned, has_safety)
                print(f"✅ 關閉完成並已發送報備: {turned}")
            else:
                print("💡 (Dry-run) 偵測到無人且有設備開著，傳入 --execute 即可真正執行關閉。")
        else:
            if not is_nobody:
                print("🟢 有人在家，不更動設備。")
            else:
                print("✅ 全屋無人，但所有目標設備本已關閉，無需動作。")

    elif args.command == "watch":
        print(f"=== 🚀 啟動 HA 智慧離家差量巡檢守護進程 ===")
        print(f"• 檢測週期: 每 {args.interval} 秒")
        print(f"• 無人緩衝: {args.debounce} 秒 (連續無人滿 {args.debounce//60} 分鐘才觸發)")
        print(f"• 巡檢範圍: 14 處電燈、3 台電視 (已排除離子夾)")
        print(f"• 排除保護: 冷氣空調、除濕機、新風機")
        print(f"==========================================")

        not_home_since: Optional[float] = None
        handled_for_this_away_session = False

        while True:
            try:
                states = patrol._get_all_states()
                if states:
                    is_nobody, pres_details = patrol.check_presence(states)
                    now_ts = time.time()

                    if is_nobody:
                        if not_home_since is None:
                            not_home_since = now_ts
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 📍 偵測到全屋無人，開始計時緩衝 5 分鐘... ({', '.join(pres_details)})")

                        elapsed_away = now_ts - not_home_since
                        if elapsed_away >= args.debounce:
                            if not handled_for_this_away_session:
                                # 緩衝滿 5 分鐘，進行精準差量掃描
                                active = patrol.find_active_devices(states)
                                total_active = len(active["lights"]) + len(active["media"]) + len(active["safety"])

                                if total_active > 0:
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚨 連續無人已達 {int(elapsed_away//60)} 分鐘，發現 {total_active} 項設備未關，執行精準差量關閉！")
                                    has_safety = len(active["safety"]) > 0
                                    turned = patrol.execute_turn_off_delta(active)
                                    patrol.send_telegram_report(turned, has_safety)
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 關閉完成並已推播報備: {turned}")
                                else:
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✨ 全屋無人但設備早已全數關閉，安然無恙。")

                                # 標記此段離家期間已處理，避免後續重複打擾
                                handled_for_this_away_session = True
                    else:
                        # 有人回到家，重置離家狀態
                        if not_home_since is not None:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 歡迎回家 ({', '.join(pres_details)})，巡檢重置進入待命狀態。")
                        not_home_since = None
                        handled_for_this_away_session = False

            except Exception as loop_err:
                print(f"[SmartPatrol] ⚠️ 巡檢異常: {loop_err}", file=sys.stderr)

            time.sleep(args.interval)


if __name__ == "__main__":
    main()
