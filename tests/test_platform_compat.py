"""
跨平台相容性整合測試 (macOS / Linux / Windows)
驗證 TaskManager 與 Browser-Relay 在當前平台之運作正確性
"""

import sys
import time
import tempfile
import importlib.util
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 動態加載 browser_relay 模組 (避免 hyphen 目錄 import 限制)
BR_PATH = BASE_DIR / "skills" / "browser-relay" / "scripts" / "browser_relay.py"
spec = importlib.util.spec_from_file_location("browser_relay", BR_PATH)
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

detect_chrome_bin = br.detect_chrome_bin
get_default_profile_dir = br.get_default_profile_dir
CDP_LOCK_FILE = br.CDP_LOCK_FILE

from task_manager import task_manager, is_pid_alive
from scripts.setup_wizard import run_doctor_all


def test_browser_relay_cross_platform_config():
    """驗證 Browser-Relay 路徑解析與鎖檔設定具備跨平台彈性"""
    chrome_bin = detect_chrome_bin()
    profile_dir = get_default_profile_dir()

    assert chrome_bin, "Chrome binary 路徑不得為空"
    assert profile_dir, "Profile 目錄路徑不得為空"
    # 驗證鎖檔落在暫存目錄下
    assert Path(tempfile.gettempdir()) in CDP_LOCK_FILE.parents or CDP_LOCK_FILE.parent == Path(tempfile.gettempdir()), "CDP lock file 必須位於系統暫存目錄"


def test_task_manager_lifecycle():
    """驗證 TaskManager 跨平台 Spawn、PID 偵測、日誌輸出與 Process Termination"""
    # 建立臨時背景腳本
    dummy_script = Path(tempfile.gettempdir()) / "test_dummy_worker.py"
    dummy_script.write_text(
        "import time\n"
        "print('Worker started!')\n"
        "for i in range(30):\n"
        "    time.sleep(1)\n"
        "print('Worker finished!')\n",
        encoding="utf-8"
    )

    try:
        # 1. 啟動背景任務
        spawn_res = task_manager.spawn(
            name="test_cross_platform_worker",
            script=str(dummy_script),
            ttl_minutes=1
        )
        assert spawn_res["success"] is True, f"Spawn 失敗: {spawn_res}"
        task_id = spawn_res["task_id"]
        pid = spawn_res["pid"]

        # 等待進程拉起
        time.sleep(0.5)

        # 2. 驗證 PID 活著
        assert is_pid_alive(pid) is True, f"進程 PID {pid} 應該存活"

        # 3. 驗證 list_tasks
        running_tasks = task_manager.list_tasks(include_finished=False)
        found = any(t["task_id"] == task_id for t in running_tasks)
        assert found is True, f"任務 {task_id} 應存在於運行列表中"

        # 4. 驗證 Kill
        kill_res = task_manager.kill_task(task_id)
        assert kill_res["success"] is True, f"Kill 失敗: {kill_res}"

        # 等待進程終止
        time.sleep(0.5)
        assert is_pid_alive(pid) is False, f"進程 PID {pid} 應已被終止"

    finally:
        dummy_script.unlink(missing_ok=True)


def test_skills_doctor():
    """驗證 setup_wizard doctor 模組檢驗能夠在任何平台執行無拋錯"""
    report = run_doctor_all()
    assert isinstance(report, list)
    skill_names = [item["skill"] for item in report]
    assert "browser-relay" in skill_names
    assert "google-calendar" in skill_names
    assert "setup-wizard" in skill_names


def test_claude_singleton_interface():
    """驗證 claude_singleton 模組介面與狀態規格符合規範"""
    from claude_singleton import claude_singleton, format_claude_tool_status
    stat = claude_singleton.get_status()
    assert isinstance(stat, dict)
    assert stat["engine"] == "claude"
    assert "low_brain" in stat
    assert "med_brain" in stat
    assert "high_brain" in stat

    # 驗證工具狀態轉換
    status_text = format_claude_tool_status("Bash", {"command": "python3 skills/browser-relay/scripts/browser_relay.py open https://inline.app"})
    assert "餐廳訂位" in status_text or "瀏覽器" in status_text


def test_telegram_metadata_dispatch_by_engine():
    """驗證 Telegram 訊息置底指標列依引擎分流（Claude 排除 Gemini 7d 週配額，AGY 保留）"""
    from telegram_service import build_info_line
    from quota_service import quota_service
    from config import settings

    orig_engine = settings.AGENT_ENGINE

    try:
        # 1. 測試 AGY 引擎
        settings.AGENT_ENGINE = "agy"
        line_agy = build_info_line("你好", "好的", 1.2, actions=["thinking: med"])
        assert "7d:" in line_agy, f"AGY 指標列應包含 7d 週配額: {line_agy}"
        assert "Med" in line_agy

        # 2. 測試 Claude 引擎
        settings.AGENT_ENGINE = "claude"
        line_claude = build_info_line("你能存取我的 google sheet 嗎", "可以的", 50.3, actions=["thinking: med"])
        assert "7d:" not in line_claude, f"Claude 指標列絕不應包含 7d 週配額: {line_claude}"
        assert "Med" in line_claude, f"Claude 指標列應於技能旁標註等級: {line_claude}"
        assert "核心大腦" in line_claude

        # 3. 測試 /status 訊息在 Claude 引擎下排除 Gemini 配額與冗餘說明
        status_claude = quota_service.format_telegram_message({"engine": "claude", "is_ready": True})
        assert "Google Gemini 配額" not in status_claude
        assert "Context 上下文" in status_claude
    finally:
        settings.AGENT_ENGINE = orig_engine


def test_google_sheets_tool_generalization():
    """驗證 google-sheets 工具已完全通用化，無特定領域硬編碼 (如 baby) 並支援網址解析"""
    import sys
    from pathlib import Path
    sheets_script_dir = Path(__file__).resolve().parents[1] / "skills" / "google-sheets" / "scripts"
    if str(sheets_script_dir) not in sys.path:
        sys.path.insert(0, str(sheets_script_dir))
    
    from sheets_tool import resolve_sheet_id, _is_fuzzy_match

    # 1. 支援直接傳入完整 Google Sheets 網址並自動擷取 ID
    test_url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?gid=0#gid=0"
    extracted_id = resolve_sheet_id(test_url)
    assert extracted_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms", f"未能正確擷取試算表 ID: {extracted_id}"

    # 2. 支援一般 ID 與別名
    assert resolve_sheet_id("dummy_id_123") == "dummy_id_123"

    # 3. 驗證模糊搜尋為通用比對
    assert _is_fuzzy_match("鮮奶", "全脂鮮牛奶 936ml")
    assert _is_fuzzy_match("apple pie", "Fresh Apple Pie Recipe")
    assert not _is_fuzzy_match("汽水", "全脂鮮奶")


def test_skills_generalization():
    """驗證 shopping-list, coupang-tracker, google-calendar 已全面通用化"""
    import sys
    from pathlib import Path

    # 1. 驗證 coupang_tracker 通用比對
    coupang_dir = Path(__file__).resolve().parents[1] / "skills" / "coupang-tracker" / "scripts"
    if str(coupang_dir) not in sys.path:
        sys.path.insert(0, str(coupang_dir))
    from coupang_tracker import match_item_price, DEFAULT_THRESHOLDS
    assert len(DEFAULT_THRESHOLDS) == 0, "DEFAULT_THRESHOLDS 應為空字典，不應硬編碼特定商品"
    
    mock_snapshot = {
        "cart": {"飛利浦電動牙刷 HX6800": 1999, "UCC 職人濾掛咖啡 50入": 399},
        "search_bests": {"幫寶適超薄乾爽拉拉褲 XL": 1299}
    }
    price, track = match_item_price("UCC 咖啡", mock_snapshot)
    assert price == 399 and "購物車" in track
    price, track = match_item_price("幫寶適 拉拉褲", mock_snapshot)
    assert price == 1299 and "全網搜尋" in track

    # 2. 驗證 google-calendar 通用日曆解析
    cal_dir = Path(__file__).resolve().parents[1] / "skills" / "google-calendar" / "scripts"
    if str(cal_dir) not in sys.path:
        sys.path.insert(0, str(cal_dir))
    from calendar_tool import resolve_calendar_id, _is_date_time_str
    assert resolve_calendar_id("primary") == "primary"
    assert resolve_calendar_id("待辦事項") is not None
    assert _is_date_time_str("2026-11-05")
    assert _is_date_time_str("2026-11-05 14:00")
    assert not _is_date_time_str("買咖啡")


if __name__ == "__main__":
    print("🧪 正在執行跨平台相容性測試...")
    test_browser_relay_cross_platform_config()
    print("  ✓ test_browser_relay_cross_platform_config 通過")
    test_task_manager_lifecycle()
    print("  ✓ test_task_manager_lifecycle 通過")
    test_skills_doctor()
    print("  ✓ test_skills_doctor 通過")
    test_google_sheets_tool_generalization()
    print("  ✓ test_google_sheets_tool_generalization 通過")
    test_skills_generalization()
    print("  ✓ test_skills_generalization 通過")
    print("🎉 所有相容性測試 100% 通過！")
