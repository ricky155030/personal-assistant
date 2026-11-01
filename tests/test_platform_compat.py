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


if __name__ == "__main__":
    print("🧪 正在執行跨平台相容性測試...")
    test_browser_relay_cross_platform_config()
    print("  ✓ test_browser_relay_cross_platform_config 通過")
    test_task_manager_lifecycle()
    print("  ✓ test_task_manager_lifecycle 通過")
    test_skills_doctor()
    print("  ✓ test_skills_doctor 通過")
    print("🎉 所有相容性測試 100% 通過！")
