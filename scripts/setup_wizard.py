#!/usr/bin/env python3
"""
Mac mini Personal Assistant - 模組化設定嚮導與技能調度引擎 (Setup Wizard & Skills Engine)
架構特點：
1. 完全解耦：各技能的前置相依與設定宣告於各自的 `skills/<skill>/manifest.json`。
2. 診斷自愈：支援 `skills doctor` 命令，檢驗系統 OS、CLI 工具、外部授權與環境變數。
3. 雙軌支援：終端全互動式步驟精靈 vs LLM 對話式秒級即時設定。
"""

import os
import sys
import json
import secrets
import shutil
import socket
import subprocess
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SKILLS_SRC_DIR = BASE_DIR / "skills"
SKILLS_ACTIVE_DIR = BASE_DIR / ".agents" / "skills"
ENV_FILE_PATH = BASE_DIR / ".env"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"

# 彩色終端輸出支援
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    ENDC = '\033[0m'

# ==============================================================================
# Manifest 動態載入核心 (Manifest Discovery & Registry)
# ==============================================================================
def load_all_manifests() -> Dict[str, Dict[str, Any]]:
    """動態掃描 skills/*/manifest.json 並回傳所有技能清冊"""
    manifests = {}
    if not SKILLS_SRC_DIR.exists():
        return manifests

    for item in sorted(SKILLS_SRC_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue
        m_file = item / "manifest.json"
        if m_file.exists():
            try:
                data = json.loads(m_file.read_text(encoding="utf-8"))
                s_name = data.get("name", item.name)
                manifests[s_name] = data
            except Exception as e:
                print(f"{Colors.YELLOW}⚠️ 無法解析 {m_file}: {e}{Colors.ENDC}", file=sys.stderr)
        else:
            # 兼容無 manifest 的技能目錄
            manifests[item.name] = {
                "name": item.name,
                "title": item.name,
                "category": "custom",
                "description": "自訂技能",
                "required": False,
                "default": False,
                "prerequisites": {},
                "config": []
            }
    return manifests

def get_manifest(skill_name: str) -> Optional[Dict[str, Any]]:
    """取得指定技能的 Manifest"""
    all_m = load_all_manifests()
    return all_m.get(skill_name)

def get_enabled_skills() -> List[str]:
    """取得當前已啟用的技能清單 (讀取 .agents/skills/ 下的有效項目)"""
    if not SKILLS_ACTIVE_DIR.exists():
        return []
    enabled = []
    for item in sorted(SKILLS_ACTIVE_DIR.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_symlink() or item.is_dir():
            enabled.append(item.name)
    return enabled

def enable_skill(skill_name: str) -> bool:
    """啟用指定技能 (在 .agents/skills/ 建立相對符號連結)"""
    skill_src = SKILLS_SRC_DIR / skill_name
    if not skill_src.exists():
        raise ValueError(f"找不到技能目錄：skills/{skill_name}")

    SKILLS_ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    target_link = SKILLS_ACTIVE_DIR / skill_name

    rel_target = Path(f"../../skills/{skill_name}")
    if target_link.is_symlink() or target_link.exists():
        if target_link.is_symlink():
            try:
                if os.readlink(target_link) == str(rel_target):
                    return True
            except Exception:
                pass
        target_link.unlink()

    target_link.symlink_to(rel_target)
    return True

def disable_skill(skill_name: str) -> bool:
    """停用指定技能 (移除 .agents/skills/ 內的符號連結)"""
    manifest = get_manifest(skill_name) or {}
    if manifest.get("required"):
        raise ValueError(f"技能【{skill_name}】為核心必備技能，不可停用。")

    target_link = SKILLS_ACTIVE_DIR / skill_name
    if target_link.is_symlink() or target_link.exists():
        target_link.unlink()
        return True
    return False

def sync_skills(desired_skills: List[str]) -> Dict[str, Any]:
    """依據指定清單同步啟用技能，其餘可選技能停用"""
    SKILLS_ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    manifests = load_all_manifests()
    desired_set = set(desired_skills)

    # 核心必備技能強制啟用
    for s_id, s_info in manifests.items():
        if s_info.get("required"):
            desired_set.add(s_id)

    enabled_now = set(get_enabled_skills())
    added = []
    removed = []

    for s_id in desired_set:
        if s_id not in enabled_now:
            if (SKILLS_SRC_DIR / s_id).exists():
                enable_skill(s_id)
                added.append(s_id)

    for s_id in enabled_now:
        if s_id not in desired_set:
            info = manifests.get(s_id, {})
            if not info.get("required"):
                disable_skill(s_id)
                removed.append(s_id)

    return {
        "status": "success",
        "added": added,
        "removed": removed,
        "current_enabled": get_enabled_skills()
    }

# ==============================================================================
# 環境變數 (.env) 管理輔助
# ==============================================================================
def read_current_env() -> Dict[str, str]:
    """讀取當前 .env 內容為字典"""
    env_data = {}
    if ENV_FILE_PATH.exists():
        for line in ENV_FILE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_data[k.strip()] = v.strip()
    return env_data

def update_env_file(env_data: Dict[str, str], env_path: Path = ENV_FILE_PATH):
    """更新或建立 .env 檔案"""
    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    elif ENV_EXAMPLE_PATH.exists():
        existing_lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()

    new_lines = []
    handled_keys = set()

    for line in existing_lines:
        line_clean = line.strip()
        if line_clean and not line_clean.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in env_data:
                new_lines.append(f"{key}={env_data[key]}")
                handled_keys.add(key)
                continue
        new_lines.append(line)

    for k, v in env_data.items():
        if k not in handled_keys:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)  # 保護金鑰安全性
    except Exception:
        pass

# ==============================================================================
# 技能前置檢查與診斷引擎 (Skill Doctor Engine)
# ==============================================================================
def check_skill_doctor(skill_name: str, manifest: Optional[Dict[str, Any]] = None, env_data: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """檢驗單一技能之所有前置條件與配置現狀"""
    if manifest is None:
        manifest = get_manifest(skill_name) or {}
    if env_data is None:
        env_data = read_current_env()

    prereqs = manifest.get("prerequisites", {})
    configs = manifest.get("config", [])
    enabled_skills = set(get_enabled_skills())

    report = {
        "skill": skill_name,
        "title": manifest.get("title", skill_name),
        "category": manifest.get("category", "custom"),
        "description": manifest.get("description", ""),
        "required": manifest.get("required", False),
        "enabled": skill_name in enabled_skills,
        "ready": True,
        "issues": [],
        "checks": {
            "os": {"ok": True, "detail": sys.platform},
            "binaries": [],
            "auth": [],
            "dependencies": [],
            "services": [],
            "browser_session": None,
            "configs": []
        }
    }

    # 1. 作業系統檢驗
    allowed_os = prereqs.get("os")
    if allowed_os and sys.platform not in allowed_os:
        report["checks"]["os"]["ok"] = False
        msg = f"僅支援作業系統：{', '.join(allowed_os)} (當前: {sys.platform})"
        report["checks"]["os"]["detail"] = msg
        report["issues"].append({"type": "os", "message": msg})
        report["ready"] = False

    # 2. 二進位工具檢驗 (Binaries / CLI tools)
    for b in prereqs.get("binaries", []):
        cmd = b.get("cmd")
        path_check = b.get("path_check")
        install_hint = b.get("install_hint", f"需安裝 {cmd}")
        found = False
        if path_check:
            found = Path(path_check).exists()
        else:
            found = bool(shutil.which(cmd))

        report["checks"]["binaries"].append({
            "cmd": cmd,
            "ok": found,
            "hint": install_hint
        })
        if not found:
            report["issues"].append({"type": "binary", "cmd": cmd, "message": install_hint})
            report["ready"] = False

    # 3. 外部授權與憑證健檢 (Auth Checks)
    for a in prereqs.get("auth", []):
        name = a.get("name", "auth")
        check_cmd = a.get("check_cmd")
        setup_cmd = a.get("setup_cmd", "")
        hint = a.get("hint", "")
        auth_ok = False

        if check_cmd:
            try:
                res = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, timeout=3)
                auth_ok = (res.returncode == 0)
            except Exception:
                auth_ok = False

        report["checks"]["auth"].append({
            "name": name,
            "ok": auth_ok,
            "setup_cmd": setup_cmd,
            "hint": hint
        })
        if not auth_ok:
            report["issues"].append({
                "type": "auth",
                "name": name,
                "setup_cmd": setup_cmd,
                "message": hint
            })
            report["ready"] = False

    # 4. 技能間依賴檢驗 (Skill Dependencies)
    for dep in prereqs.get("skills", []):
        dep_enabled = dep in enabled_skills
        report["checks"]["dependencies"].append({
            "skill": dep,
            "ok": dep_enabled
        })
        if not dep_enabled:
            msg = f"需同時啟用相依技能：【{dep}】"
            report["issues"].append({"type": "dependency", "skill": dep, "message": msg})
            report["ready"] = False

    # 5. 服務端口檢驗 (Services / Ports)
    for srv in prereqs.get("services", []):
        name = srv.get("name", "service")
        port = srv.get("port")
        srv_ok = False
        if port:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    srv_ok = True
            except Exception:
                pass
        report["checks"]["services"].append({
            "name": name,
            "port": port,
            "ok": srv_ok,
            "hint": srv.get("hint", "")
        })

    # 6. 瀏覽器 Session 狀態提示 (Browser Sessions)
    bs = prereqs.get("browser_session")
    if bs:
        report["checks"]["browser_session"] = {
            "url": bs.get("url"),
            "hint": bs.get("hint", "需登入狀態")
        }

    # 7. 環境變數配置檢驗 (Configs)
    for cfg in configs:
        k = cfg.get("key")
        label = cfg.get("label", k)
        req = cfg.get("required", False)
        val = env_data.get(k, "")
        is_set = bool(val)

        report["checks"]["configs"].append({
            "key": k,
            "label": label,
            "required": req,
            "set": is_set,
            "value": val if not ("KEY" in k or "TOKEN" in k) else ("******" if val else ""),
            "hint": cfg.get("hint", "")
        })

        if req and not is_set:
            msg = f"缺少必要環境變數：{k} ({label})"
            report["issues"].append({"type": "config", "key": k, "message": msg})
            report["ready"] = False

    return report

def run_doctor_all() -> List[Dict[str, Any]]:
    """執行全域技能健檢"""
    manifests = load_all_manifests()
    env_data = read_current_env()
    reports = []
    for s_name, manifest in manifests.items():
        reports.append(check_skill_doctor(s_name, manifest, env_data))
    return reports

# ==============================================================================
# 個人檔案與 Markdown 同步
# ==============================================================================
def detect_tailscale_domain() -> str:
    """自動偵測 Tailscale MagicDNS 網域名稱"""
    try:
        res = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            dns_name = data.get("Self", {}).get("DNSName", "").rstrip(".")
            if dns_name:
                return dns_name
    except Exception:
        pass
    return "your-mac-mini.ts.net"

def detect_default_user() -> str:
    return os.getenv("USER") or "主人"

def prompt_input(prompt_text: str, default: str = "", secret: bool = False) -> str:
    if default:
        display_prompt = f"{Colors.BOLD}{prompt_text}{Colors.ENDC} [{Colors.YELLOW}{default}{Colors.ENDC}]: "
    else:
        display_prompt = f"{Colors.BOLD}{prompt_text}{Colors.ENDC}: "

    try:
        val = input(display_prompt).strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        print(f"\n{Colors.YELLOW}已中斷設定。{Colors.ENDC}")
        sys.exit(0)

def test_telegram_connection(bot_token: str, chat_id: str) -> bool:
    if not bot_token or not chat_id or "123456" in bot_token:
        return False
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": "🎉 **恭喜！**\n您的 Mac mini Personal Assistant 已成功連線至 Telegram！",
            "parse_mode": "Markdown"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False

def update_profile_markdown(user_info: Dict[str, str], profile_path: Path):
    content = f"""# 主人個人檔案與核心身分 (`memory/profile.md`)

## 1. 核心身分 (Agent Identity)
- **身分**：住在 Mac mini 上的專屬個人 AI 秘書與助理。
- **主人**：**{user_info.get('owner_name', '主人')}**
- **溝通端點**：主要透過 Telegram Bot、Apple Watch（語音互動）、iPhone 與 Mac mini。

## 2. 關於主人 (User Profile)
- **稱呼**：{user_info.get('owner_name', '主人')}
- **姓名**：{user_info.get('full_name', user_info.get('owner_name', '主人'))}
- **稱謂**：先生 / 女士
- **電子信箱**：{user_info.get('email', '')}
- **所在地**：{user_info.get('location', '台灣 / 台北')} (時區: Asia/Taipei)
- **重要通訊聯絡人 (iMessage 白名單 / 聯絡電話)**：
  - 主人本人：{user_info.get('owner_phone', '')}（別名：我、自己、主人）
  - 家人/伴侶：{user_info.get('family_phone', '')}（別名：家人、老婆、先生）
- **硬體設備**：
  - 核心主機：Mac mini (以 Tailscale 連網常駐)
  - 隨身裝置：{user_info.get('devices', 'Apple Watch, iPhone')}
  - 智慧音箱：HomePod mini (語音入口)
- **開發與工作環境**：
  - macOS、Zsh、Python (uv)、Node.js、FastAPI
  - 習慣使用 Tailscale 進行內網穿透與裝置互聯 (網域: `{user_info.get('tailscale_domain', 'your-mac-mini.ts.net')}:8899`)

## 3. 溝通與回覆準則
- **終端機開發與架構討論 (agy CLI)**：不受長度限制，充分運用 Markdown、程式碼區塊與詳細分析。
- **手錶與音箱日常互動 (Apple Watch & HomePod)**：日常任務多為記錄待辦（Google Calendar「待辦事項」）或生活快速查詢。回覆內容與 Telegram **1:1 完全鏡像對齊**。
  - **預設簡短模式（未提詳細說明）**：控制在 200 字以內，結構化條列格式。HomePod 100% 嚴禁任何 Emoji 與 Markdown；Apple Watch 支援適量視覺 Emoji。
  - **指定詳細模式（要求詳細說明）**：解除字數限制，展開深入結構化詳細解答。HomePod 輸出完整長篇口語段落（無 Emoji），Apple Watch 輸出完整結構化長文；Telegram 1:1 鏡像同步。
- **Telegram 機器人**：清晰條理，支援完整 Markdown 格式（粗體、清單、代碼等）。
- **反問確認優先原則（嚴禁未經確認的過度調研）**：遇到任何關鍵資訊欠缺（如未指明連鎖分店、未指定時段、有多種採買規格或多種架構選項）時，**嚴禁在背景窮舉式過度調研浪費時間與 Token！必須第一時間秒級反問主人**，確認具體偏好後才精準深入執行。
"""
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(content, encoding="utf-8")

def update_agents_md(owner_name: str, agents_path: Path):
    if not agents_path.exists():
        return
    text = agents_path.read_text(encoding="utf-8")
    if "<您的稱呼>" in text:
        text = text.replace("<您的稱呼>", owner_name)
        agents_path.write_text(text, encoding="utf-8")

# ==============================================================================
# 全互動式設定嚮導 (Interactive Wizard)
# ==============================================================================
def run_interactive_wizard():
    print(f"""
{Colors.CYAN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════════════════════╗
║             🏛️  Mac mini Personal Assistant - 初始化設定嚮導               ║
╚══════════════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
歡迎使用個人 AI 助理！本精靈將引導您完成【身分檔案】、【安全金鑰】與【專屬技能選配】。
按 Enter 可直接採用預設值 [中括號內容]。
""")

    env_path = ENV_FILE_PATH
    profile_path = BASE_DIR / "memory" / "profile.md"
    agents_path = BASE_DIR / "AGENTS.md"

    existing_env = read_current_env()
    all_manifests = load_all_manifests()

    # --------------------------------------------------------------------------
    # 第一階段：主人個人資訊 (Profile & Identity)
    # --------------------------------------------------------------------------
    print(f"\n{Colors.GREEN}{Colors.BOLD}【步驟 1/4】設定主人個人資訊與身分識別{Colors.ENDC}")
    print("──────────────────────────────────────────────────────────────────────────────")
    default_owner = existing_env.get("OWNER_NAME") or detect_default_user()
    owner_name = prompt_input("1. 主人的稱呼 / 暱稱 (例如: Alex, David, 主人)", default=default_owner)
    full_name = prompt_input("2. 主人全名 (若與稱呼相同可直接 Enter)", default=owner_name)
    email = prompt_input("3. 常用電子信箱 (選填，用於重要郵件提醒)", default=existing_env.get("OWNER_EMAIL", ""))
    location = prompt_input("4. 所在城市與地區", default="台灣 / 台北")
    devices = prompt_input("5. 您日常搭配的隨身穿戴裝置", default="Apple Watch Ultra, iPhone, HomePod mini")

    # --------------------------------------------------------------------------
    # 第二階段：安全金鑰與通訊認證 (Credentials & Security)
    # --------------------------------------------------------------------------
    print(f"\n{Colors.GREEN}{Colors.BOLD}【步驟 2/4】設定通訊管道與安全連線密鑰{Colors.ENDC}")
    print("──────────────────────────────────────────────────────────────────────────────")

    default_key = existing_env.get("ASSISTANT_API_KEY")
    if not default_key or default_key in ["your-secret-api-key-here", "your-watch-secret"]:
        default_key = secrets.token_urlsafe(20)
    api_key = prompt_input("1. 捷徑認證安全金鑰 (X-Agent-Key，直接 Enter 可自動生成隨機高強度金鑰)", default=default_key)

    default_ts = existing_env.get("TAILSCALE_DOMAIN") or detect_tailscale_domain()
    tailscale_domain = prompt_input("2. Tailscale 網域名稱 (用於安全存取與 HTML 報告直連)", default=default_ts)

    print("\nTelegram Bot 雙向通訊 (選填，可直接接收卡片與語音回覆，向 @BotFather 申請)：")
    tg_token = prompt_input("3. Telegram Bot Token (向 @BotFather 申請取得)", default=existing_env.get("TELEGRAM_BOT_TOKEN", ""))
    tg_chat_id = prompt_input("4. Telegram 您的個人 Chat ID (向 @userinfobot 取得數字 ID)", default=existing_env.get("TELEGRAM_CHAT_ID", ""))

    if tg_token and tg_chat_id:
        print(f"正在驗證 Telegram 連線狀態...", end=" ")
        if test_telegram_connection(tg_token, tg_chat_id):
            print(f"{Colors.GREEN}✅ 驗證成功！測試訊息已發送至您的 Telegram。{Colors.ENDC}")
        else:
            print(f"{Colors.YELLOW}⚠️ 連線失敗或未輸入正確 Token，稍後仍可在 .env 手動調整。{Colors.ENDC}")

    # --------------------------------------------------------------------------
    # 第三階段：技能模組選配 (Skill Modules Selection)
    # --------------------------------------------------------------------------
    print(f"\n{Colors.GREEN}{Colors.BOLD}【步驟 3/4】選擇啟用之專屬技能模組 (Skill Modules){Colors.ENDC}")
    print("──────────────────────────────────────────────────────────────────────────────")
    print("系統採模組化軟連結設計，僅載入啟用的技能，大幅節省上下文 Token 並避免意圖誤判。\n")

    current_enabled = set(get_enabled_skills())
    if not current_enabled:
        current_enabled = {k for k, v in all_manifests.items() if v.get("default")}

    optional_keys = [k for k, v in all_manifests.items() if not v.get("required")]

    print(f"{Colors.CYAN}{Colors.BOLD}【核心必備技能 (常駐啟用)】{Colors.ENDC}")
    for k, v in all_manifests.items():
        if v.get("required"):
            print(f"  🔒 [必備] {Colors.BOLD}{k}{Colors.ENDC} ({v.get('title', k)}) - {v.get('description', '')}")

    print(f"\n{Colors.CYAN}{Colors.BOLD}【可選擴充技能模組】{Colors.ENDC}")
    for idx, k in enumerate(optional_keys, start=1):
        is_chk = k in current_enabled
        mark = f"{Colors.GREEN}[✓]{Colors.ENDC}" if is_chk else f"{Colors.YELLOW}[ ]{Colors.ENDC}"
        print(f"  {mark} {Colors.BOLD}{idx}. {k}{Colors.ENDC} ({all_manifests[k].get('title', k)})\n      說明: {all_manifests[k].get('description', '')}")

    print("\n說明：直接輸入欲切換的序號（如「1,3」），或輸入「all」全選、「none」只保留核心，直接 Enter 採用目前勾選。")
    skill_choice = prompt_input("請選擇", default="").strip().lower()

    selected_skills = set(current_enabled)
    if skill_choice == "all":
        selected_skills = set(all_manifests.keys())
    elif skill_choice == "none":
        selected_skills = {k for k, v in all_manifests.items() if v.get("required")}
    elif skill_choice:
        try:
            indexes = [int(x.strip()) for x in skill_choice.replace(" ", ",").split(",") if x.strip().isdigit()]
            for idx in indexes:
                if 1 <= idx <= len(optional_keys):
                    target_k = optional_keys[idx - 1]
                    if target_k in selected_skills:
                        selected_skills.remove(target_k)
                    else:
                        selected_skills.add(target_k)
        except Exception:
            print(f"{Colors.YELLOW}輸入格式無效，維持目前勾選。{Colors.ENDC}")

    sync_res = sync_skills(list(selected_skills))

    # --------------------------------------------------------------------------
    # 第四階段：各選用技能專屬前置檢驗與參數配置 (Manifest-Driven Config)
    # --------------------------------------------------------------------------
    print(f"\n{Colors.GREEN}{Colors.BOLD}【步驟 4/4】選定技能前置相依檢驗與專屬參數設定{Colors.ENDC}")
    print("──────────────────────────────────────────────────────────────────────────────")

    collected_env = dict(existing_env)
    collected_env.update({
        "ASSISTANT_API_KEY": api_key,
        "TAILSCALE_DOMAIN": tailscale_domain,
        "TELEGRAM_BOT_TOKEN": tg_token,
        "TELEGRAM_CHAT_ID": tg_chat_id,
        "OWNER_NAME": owner_name,
        "OWNER_EMAIL": email,
    })

    for s_name in sync_res["current_enabled"]:
        manifest = all_manifests.get(s_name, {})
        title = manifest.get("title", s_name)
        configs = manifest.get("config", [])
        prereqs = manifest.get("prerequisites", {})

        # 執行健檢
        diag = check_skill_doctor(s_name, manifest, collected_env)

        has_interaction = bool(configs or (diag["issues"] and not manifest.get("required")))
        if has_interaction:
            print(f"\n{Colors.BOLD}👉 設定技能：{Colors.CYAN}{title} ({s_name}){Colors.ENDC}")

        # 若有缺失相依性，友善提示
        for issue in diag["issues"]:
            if issue["type"] == "binary":
                print(f"  {Colors.YELLOW}⚠️ 缺少相依工具：{issue['cmd']}{Colors.ENDC} ({issue['message']})")
            elif issue["type"] == "auth":
                print(f"  {Colors.YELLOW}⚠️ 外部帳號尚未授權：{issue['message']}{Colors.ENDC}")
                if issue.get("setup_cmd"):
                    print(f"     請於終端機執行：{Colors.BOLD}{issue['setup_cmd']}{Colors.ENDC}")
            elif issue["type"] == "dependency":
                print(f"  {Colors.YELLOW}⚠️ {issue['message']}{Colors.ENDC}")

        # 逐一詢問該技能所宣告的 config 參數
        for cfg in configs:
            k = cfg["key"]
            label = cfg.get("label", k)
            default_val = collected_env.get(k) or cfg.get("default", "")
            hint = cfg.get("hint", "")

            prompt_label = f"  • {label}"
            if hint:
                prompt_label += f" ({Colors.BLUE}{hint}{Colors.ENDC})"

            val = prompt_input(prompt_label, default=default_val)
            if val:
                collected_env[k] = val

    # 寫入 .env
    update_env_file(collected_env, env_path)
    print(f"\n✅ 已安全更新環境變數設定檔：{Colors.BOLD}{env_path}{Colors.ENDC} (權限: 600)")

    # 寫入 profile.md & AGENTS.md
    user_info = {
        "owner_name": owner_name,
        "full_name": full_name,
        "email": email,
        "location": location,
        "devices": devices,
        "owner_phone": collected_env.get("OWNER_PHONE", ""),
        "family_phone": collected_env.get("FAMILY_PHONE", ""),
        "tailscale_domain": tailscale_domain
    }
    update_profile_markdown(user_info, profile_path)
    print(f"✅ 已更新個人模組檔案：{Colors.BOLD}{profile_path}{Colors.ENDC}")

    update_agents_md(owner_name, agents_path)
    print(f"✅ 已同步更新指令指引：{Colors.BOLD}{agents_path}{Colors.ENDC}")

    try:
        from memory_manager import MemoryManager
        MemoryManager().sync_to_memory_md()
        print(f"✅ 已自動編譯全域記憶庫聚合檔：{Colors.BOLD}{BASE_DIR / 'MEMORY.md'}{Colors.ENDC}")
    except Exception as e:
        print(f"{Colors.YELLOW}⚠️ 自動編譯 MEMORY.md 略過 ({e}){Colors.ENDC}")

    print(f"\n{Colors.CYAN}{Colors.BOLD}══════════════════════════════════════════════════════════════════════════════")
    print(f"🎉 恭喜！Mac mini Personal Assistant 初始化配置完成！")
    print(f"══════════════════════════════════════════════════════════════════════════════{Colors.ENDC}")
    print(f"• 主人大腦稱呼：{Colors.BOLD}{owner_name}{Colors.ENDC}")
    print(f"• 外部連網網域：{Colors.BOLD}http://{tailscale_domain}:8899{Colors.ENDC}")
    print(f"• 捷徑 API 金鑰：{Colors.BOLD}{api_key}{Colors.ENDC}")
    print(f"• 已啟用技能數：{Colors.BOLD}{len(sync_res['current_enabled'])} 個技能{Colors.ENDC} ({', '.join(sync_res['current_enabled'])})")

    print(f"\n{Colors.BOLD}🚀 接下來啟動您的助理：{Colors.ENDC}")
    print(f"  1. 在終端機執行：{Colors.GREEN}{Colors.BOLD}./start.sh{Colors.ENDC}")
    print(f"  2. 隨時以 LLM 或 CLI 調整技能：{Colors.BOLD}python3 setup.py skills doctor{Colors.ENDC}")
    print(f"──────────────────────────────────────────────────────────────────────────────\n")

# ==============================================================================
# 非互動模式 / LLM 專用模式 (Non-Interactive / LLM-Driven Mode)
# ==============================================================================
def run_non_interactive(args):
    """支援命令列直接傳參快速設定 (供 LLM 或自動化腳本調用)"""
    env_path = ENV_FILE_PATH
    profile_path = BASE_DIR / "memory" / "profile.md"
    agents_path = BASE_DIR / "AGENTS.md"

    env_data = {}
    if args.api_key:
        env_data["ASSISTANT_API_KEY"] = args.api_key
    if args.tailscale:
        env_data["TAILSCALE_DOMAIN"] = args.tailscale
    if args.tg_token:
        env_data["TELEGRAM_BOT_TOKEN"] = args.tg_token
    if args.tg_chat_id:
        env_data["TELEGRAM_CHAT_ID"] = args.tg_chat_id
    if args.owner_phone:
        env_data["OWNER_PHONE"] = args.owner_phone
    if args.family_phone:
        env_data["FAMILY_PHONE"] = args.family_phone

    # 支援透過 --config KEY=VALUE 自訂各技能參數
    if args.config:
        for pair in args.config:
            if "=" in pair:
                k, v = pair.split("=", 1)
                env_data[k.strip()] = v.strip()

    if env_data:
        update_env_file(env_data, env_path)

    owner_name = args.owner
    if any([args.owner, args.full_name, args.email, args.location, args.devices, args.owner_phone, args.family_phone]):
        user_info = {
            "owner_name": owner_name or "主人",
            "full_name": args.full_name or owner_name or "主人",
            "email": args.email or "",
            "location": args.location or "台灣 / 台北",
            "devices": args.devices or "Apple Watch, iPhone",
            "owner_phone": args.owner_phone or env_data.get("OWNER_PHONE", ""),
            "family_phone": args.family_phone or env_data.get("FAMILY_PHONE", ""),
            "tailscale_domain": args.tailscale or "your-mac-mini.ts.net"
        }
        update_profile_markdown(user_info, profile_path)
        if owner_name:
            update_agents_md(owner_name, agents_path)

    # 技能配置處理
    all_manifests = load_all_manifests()
    skills_changed = None

    if args.enable_skill:
        for s in args.enable_skill:
            enable_skill(s)
        skills_changed = get_enabled_skills()
    if args.disable_skill:
        for s in args.disable_skill:
            disable_skill(s)
        skills_changed = get_enabled_skills()
    if args.skills:
        skill_list = [s.strip() for s in args.skills.split(",") if s.strip()]
        res = sync_skills(skill_list)
        skills_changed = res["current_enabled"]
    elif args.all_skills:
        res = sync_skills(list(all_manifests.keys()))
        skills_changed = res["current_enabled"]
    elif args.default_skills:
        default_list = [k for k, v in all_manifests.items() if v.get("default")]
        res = sync_skills(default_list)
        skills_changed = res["current_enabled"]

    try:
        from memory_manager import MemoryManager
        MemoryManager().sync_to_memory_md()
    except Exception:
        pass

    result = {
        "status": "success",
        "message": f"設定更新完成！",
        "env_path": str(env_path),
        "profile_path": str(profile_path),
        "enabled_skills": skills_changed or get_enabled_skills()
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

# ==============================================================================
# 技能專屬子命令 CLI (skills list / doctor / enable / disable / set / config)
# ==============================================================================
def handle_skills_cli(sub_args):
    action = sub_args.skill_action
    all_manifests = load_all_manifests()
    enabled_set = set(get_enabled_skills())

    if action == "list":
        data = []
        for s_id, meta in all_manifests.items():
            data.append({
                "id": s_id,
                "title": meta.get("title", s_id),
                "category": meta.get("category", "custom"),
                "description": meta.get("description", ""),
                "required": meta.get("required", False),
                "default": meta.get("default", False),
                "enabled": s_id in enabled_set,
                "configs": [c.get("key") for c in meta.get("config", [])]
            })

        if sub_args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"\n{Colors.CYAN}{Colors.BOLD}Mac mini Personal Assistant - 技能模組清單：{Colors.ENDC}")
            print("─" * 78)
            for item in data:
                status_badge = f"{Colors.GREEN}[已啟用]{Colors.ENDC}" if item["enabled"] else f"{Colors.YELLOW}[未啟用]{Colors.ENDC}"
                req_badge = f" {Colors.BLUE}(核心必備){Colors.ENDC}" if item["required"] else ""
                print(f"• {status_badge} {Colors.BOLD}{item['id']}{Colors.ENDC}{req_badge}：{item['title']}")
                print(f"    說明: {item['description']}")
                if item["configs"]:
                    print(f"    配置: {', '.join(item['configs'])}")
            print("─" * 78)
            enabled_cnt = sum(1 for x in data if x["enabled"])
            print(f"總計 {len(data)} 個技能，目前已啟用 {enabled_cnt} 個。\n")

    elif action == "doctor":
        target = sub_args.target
        env_data = read_current_env()
        reports = []

        if target:
            if target not in all_manifests:
                print(f"{Colors.RED}錯誤：找不到技能【{target}】{Colors.ENDC}", file=sys.stderr)
                sys.exit(1)
            reports.append(check_skill_doctor(target, all_manifests[target], env_data))
        else:
            for s_id, meta in all_manifests.items():
                reports.append(check_skill_doctor(s_id, meta, env_data))

        if sub_args.json:
            print(json.dumps(reports, ensure_ascii=False, indent=2))
        else:
            print(f"\n{Colors.CYAN}{Colors.BOLD}🩺 Mac mini Personal Assistant - 技能前置診斷報告 (Skill Doctor)：{Colors.ENDC}")
            print("═" * 78)
            for r in reports:
                status = f"{Colors.GREEN}✅ 就緒{Colors.ENDC}" if r["ready"] else f"{Colors.RED}❌ 缺失相依{Colors.ENDC}"
                en_status = f"{Colors.BLUE}[已啟用]{Colors.ENDC}" if r["enabled"] else f"{Colors.YELLOW}[未啟用]{Colors.ENDC}"
                print(f"\n• {Colors.BOLD}{r['title']} ({r['skill']}){Colors.ENDC} {en_status} ➔ {status}")

                for b in r["checks"]["binaries"]:
                    b_mark = f"{Colors.GREEN}✓{Colors.ENDC}" if b["ok"] else f"{Colors.RED}✗{Colors.ENDC}"
                    print(f"    - CLI 工具 [{b_mark}] {b['cmd']}" + (f" ({b['hint']})" if not b["ok"] else ""))

                for a in r["checks"]["auth"]:
                    a_mark = f"{Colors.GREEN}✓{Colors.ENDC}" if a["ok"] else f"{Colors.RED}✗{Colors.ENDC}"
                    print(f"    - 外部授權 [{a_mark}] {a['name']}" + (f" (需執行: {a['setup_cmd']})" if not a["ok"] else ""))

                for c in r["checks"]["configs"]:
                    c_mark = f"{Colors.GREEN}✓{Colors.ENDC}" if c["set"] else (f"{Colors.RED}✗{Colors.ENDC}" if c["required"] else f"{Colors.YELLOW}-{Colors.ENDC}")
                    print(f"    - 環境設定 [{c_mark}] {c['key']} ({c['label']}) = {c['value'] or '(未設定)'}")

                if r["checks"]["browser_session"]:
                    bs = r["checks"]["browser_session"]
                    print(f"    - 網頁狀態 [ℹ] {bs['url']} ({bs['hint']})")

            print("\n" + "═" * 78 + "\n")

    elif action == "enable":
        if not sub_args.targets:
            print(f"{Colors.RED}錯誤：請指定欲啟用的技能名稱 (例如: setup.py skills enable imessage){Colors.ENDC}")
            sys.exit(1)
        enabled_list = []
        for name in sub_args.targets:
            try:
                enable_skill(name)
                enabled_list.append(name)
            except Exception as e:
                print(f"{Colors.RED}啟用失敗 ({name}): {e}{Colors.ENDC}")

        # 執行診斷回報缺失
        env_data = read_current_env()
        diagnostics = [check_skill_doctor(n, all_manifests.get(n), env_data) for n in enabled_list if n in all_manifests]

        if sub_args.json:
            print(json.dumps({
                "status": "success",
                "enabled": enabled_list,
                "current_skills": get_enabled_skills(),
                "diagnostics": diagnostics
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{Colors.GREEN}✅ 成功啟用技能：{', '.join(enabled_list)}{Colors.ENDC}")
            for d in diagnostics:
                if not d["ready"]:
                    print(f"\n{Colors.YELLOW}⚠️ 注意：【{d['title']}】尚未完全就緒：{Colors.ENDC}")
                    for issue in d["issues"]:
                        print(f"  • {issue['message']}")

    elif action == "disable":
        if not sub_args.targets:
            print(f"{Colors.RED}錯誤：請指定欲停用的技能名稱 (例如: setup.py skills disable coupang-tracker){Colors.ENDC}")
            sys.exit(1)
        disabled_list = []
        for name in sub_args.targets:
            try:
                disable_skill(name)
                disabled_list.append(name)
            except Exception as e:
                print(f"{Colors.RED}停用失敗 ({name}): {e}{Colors.ENDC}")
        if sub_args.json:
            print(json.dumps({"status": "success", "disabled": disabled_list, "current_skills": get_enabled_skills()}))
        else:
            print(f"{Colors.YELLOW}✅ 成功停用技能：{', '.join(disabled_list)}{Colors.ENDC}")

    elif action == "set":
        if not sub_args.skill_list:
            print(f"{Colors.RED}錯誤：請提供逗號分隔的技能名稱清單 (例如: setup.py skills set google-calendar,google-sheets){Colors.ENDC}")
            sys.exit(1)
        targets = [x.strip() for x in sub_args.skill_list.split(",") if x.strip()]
        res = sync_skills(targets)
        if sub_args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"{Colors.GREEN}✅ 已重新設定啟用技能清單：{', '.join(res['current_enabled'])}{Colors.ENDC}")

    elif action == "config":
        skill = sub_args.target_skill
        if skill not in all_manifests:
            print(f"{Colors.RED}錯誤：找不到技能【{skill}】{Colors.ENDC}")
            sys.exit(1)

        manifest = all_manifests[skill]
        env_data = read_current_env()
        updated = {}

        if sub_args.set_pairs:
            for pair in sub_args.set_pairs:
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k, v = k.strip(), v.strip()
                    updated[k] = v
                    env_data[k] = v
            update_env_file(updated, ENV_FILE_PATH)

        if sub_args.json:
            print(json.dumps({
                "skill": skill,
                "title": manifest.get("title", skill),
                "updated": updated,
                "configs": [
                    {
                        "key": c["key"],
                        "label": c.get("label", c["key"]),
                        "current_value": env_data.get(c["key"], ""),
                        "default": c.get("default", "")
                    }
                    for c in manifest.get("config", [])
                ]
            }, ensure_ascii=False, indent=2))
        else:
            if updated:
                print(f"{Colors.GREEN}✅ 已更新【{skill}】設定：{', '.join(f'{k}={v}' for k, v in updated.items())}{Colors.ENDC}")
            else:
                print(f"\n{Colors.BOLD}【{manifest.get('title', skill)}】目前設定項目：{Colors.ENDC}")
                for c in manifest.get("config", []):
                    k = c["key"]
                    print(f"  • {c.get('label', k)} ({k}) = {env_data.get(k, '(未設定)')}")
                print()

def main():
    parser = argparse.ArgumentParser(description="Mac mini Personal Assistant Setup Wizard & Skills Engine")

    # 頂層子命令：skills
    subparsers = parser.add_subparsers(dest="subcommand", help="子命令 (skills / setup)")

    skills_parser = subparsers.add_parser("skills", help="技能模組管理與健檢 (Skills Management & Doctor)")
    skills_sub = skills_parser.add_subparsers(dest="skill_action", help="技能動作 (list/doctor/enable/disable/set/config)")

    p_list = skills_sub.add_parser("list", help="列出所有技能與啟用狀態")
    p_list.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    p_doc = skills_sub.add_parser("doctor", help="檢驗技能之系統相依性與外部授權 (Doctor)")
    p_doc.add_argument("target", nargs="?", help="特定技能名稱 (選填，留空則健檢全部)")
    p_doc.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    p_enable = skills_sub.add_parser("enable", help="啟用指定技能")
    p_enable.add_argument("targets", nargs="+", help="技能名稱")
    p_enable.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    p_disable = skills_sub.add_parser("disable", help="停用指定技能")
    p_disable.add_argument("targets", nargs="+", help="技能名稱")
    p_disable.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    p_set = skills_sub.add_parser("set", help="批量設定啟用的技能")
    p_set.add_argument("skill_list", help="逗號分隔之技能名稱")
    p_set.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    p_cfg = skills_sub.add_parser("config", help="檢視或更新特定技能設定參數")
    p_cfg.add_argument("target_skill", help="技能名稱")
    p_cfg.add_argument("--set", dest="set_pairs", nargs="+", help="設定鍵值對 (KEY=VALUE)")
    p_cfg.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    # 頂層一般參數 (供 setup-wizard 或非互動模式)
    parser.add_argument("--non-interactive", action="store_true", help="非互動模式 (以參數直接寫入)")
    parser.add_argument("--owner", help="主人稱呼")
    parser.add_argument("--full-name", help="主人全名")
    parser.add_argument("--email", help="聯絡信箱")
    parser.add_argument("--location", help="所在地區")
    parser.add_argument("--devices", help="隨身設備清單")
    parser.add_argument("--api-key", help="捷徑 API 金鑰")
    parser.add_argument("--tailscale", help="Tailscale 網域名稱")
    parser.add_argument("--tg-token", help="Telegram Bot Token")
    parser.add_argument("--tg-chat-id", help="Telegram Chat ID")
    parser.add_argument("--owner-phone", help="主人手機號碼")
    parser.add_argument("--family-phone", help="家人手機號碼")

    # 技能配置參數
    parser.add_argument("--enable-skill", nargs="+", help="啟用指定技能 (可多選)")
    parser.add_argument("--disable-skill", nargs="+", help="停用指定技能 (可多選)")
    parser.add_argument("--skills", help="批量指定啟用技能清單 (逗號分隔)")
    parser.add_argument("--all-skills", action="store_true", help="啟用全部技能")
    parser.add_argument("--default-skills", action="store_true", help="還原為預設啟用技能")
    parser.add_argument("--config", nargs="+", help="自訂環境變數鍵值對 (KEY=VALUE，可多個)")

    args = parser.parse_args()

    if args.subcommand == "skills":
        handle_skills_cli(args)
        return

    # 若傳入任何設定參數或明確指定 --non-interactive
    if args.non_interactive or any([
        args.owner, args.api_key, args.tg_token, args.enable_skill,
        args.disable_skill, args.skills, args.all_skills, args.default_skills, args.config
    ]):
        run_non_interactive(args)
    else:
        run_interactive_wizard()

if __name__ == "__main__":
    main()
