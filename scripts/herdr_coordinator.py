#!/usr/bin/env python3
"""
Herdr 終端 Agent 協調與守護模組 (scripts/herdr_coordinator.py)

職責：
1. 任務派發與 Prompt 鐵律約束注入 (Dispatch)
   - 自動在 Prompt 注入【協調指令規範】，約束 Agent 在有疑問時條列問題 (Q1:, 選項 1: 等) 並結束 turn，
     嚴禁使用彈窗式的互動對話框 (如 ask_question)。
   - 透過 `herdr agent prompt <name> "<prompt>"` 發送。
   - 自動透過 TaskManager 託管背景守護進程 (Watch Daemon)，讓主大腦免於阻塞等待。
2. 背景守護者 (Watch Daemon)
   - 輪詢 `herdr agent get <name>` 與 `herdr agent read <name> --source visible --lines 100`。
   - 提問辨識：當偵測到問句/選項 (如 `Q1:`, `選項 1:`, 等待確認模式等) 且處於 idle/blocked，
     觸發 Telegram 通知請示主人，標記為等待輸入狀態 (不重複轟炸)。
   - 完工辨識：當 Agent 由 working 轉為 idle/done 且無提問時，擷取最後成果文字，
     發送 Telegram 完工卡片並結束守護進程。
3. 答案回寫 (Relay)
   - 透過 `herdr agent send-keys <name> "<text>\\n"` 將主人的答覆寫回終端解鎖 Agent。
4. CLI 支援子指令：
   - list
   - dispatch --agent <name> --prompt "<prompt>" [--no-watch]
   - watch --agent <name> [--interval <sec>]
   - relay --agent <name> --answer "<text>"
"""

import os
import sys
import json
import re
import time
import html
import shutil
import signal
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# 跨平台 UTF-8 輸出防護
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = next(
    (p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()),
    Path(__file__).resolve().parents[1],
)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

# 協調指令注入鐵律 (Prompt Injection Rule)
COORDINATION_RULE = (
    "【協調指令規範】若執行過程中需要確認或有疑問，嚴禁使用 ask_question 等互動對話框工具；"
    "請一律在文字輸出中條列問題（格式：Q1: ... / 選項 1: ... 選項 2: ...）並結束 turn 等待輸入。"
)

# 提問與選項偵測正規表示式清單
QUESTION_PATTERNS = [
    r"Q\d*[:：]",                                    # Q1:, Q2:, Q:
    r"Question\s*\d*[:：]",                           # Question 1:
    r"問題\s*\d*[:：]",                               # 問題 1:, 問題:
    r"選項\s*[1-9１-９一二三四A-Za-z][:：、. )\]]",     # 選項 1:, 選項 A., 選項 1)
    r"選項\s*\d+",                                   # 選項 1
    r"等待確認",
    r"請確認",
    r"請選擇",
    r"請輸入",
    r"請問",
    r"是否繼續",
    r"確認請",
    r"請回覆",
    r"等待輸入",
    r"\[Y/n\]",
    r"\[y/N\]",
    r"\(y/n\)",
    r"Select an option:",
    r"Choose one:",
]

# 狀態列與終端雜訊過濾模式
STATUSLINE_MARKERS = [
    "模型:",
    "上下文:",
    "權杖數:",
    "每週額度:",
    "VCS類型:",
    "重置倒數:",
    "訂閱方案:",
]


def get_herdr_bin() -> str:
    """取得 herdr 執行檔路徑"""
    bin_path = shutil.which("herdr")
    if bin_path:
        return bin_path
    for candidate in [
        Path.home() / ".local" / "bin" / "herdr",
        Path("/usr/local/bin/herdr"),
        Path("/opt/homebrew/bin/herdr"),
    ]:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "herdr"


def run_herdr_cmd(cmd_args: List[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    """執行 herdr CLI 指令封裝"""
    herdr_bin = get_herdr_bin()
    full_cmd = [herdr_bin] + cmd_args
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def inject_coordination_rule(prompt: str) -> str:
    """在派發的 Prompt 中注入協調指令規範鐵律"""
    trimmed = prompt.strip()
    if COORDINATION_RULE in trimmed:
        return trimmed
    return f"{trimmed}\n\n{COORDINATION_RULE}"


def list_herdr_agents() -> List[Dict[str, Any]]:
    """列出 Herdr 中的所有 Agent"""
    res = run_herdr_cmd(["agent", "list"])
    if res.returncode != 0:
        return []
    try:
        data = json.loads(res.stdout)
        return data.get("result", {}).get("agents", [])
    except Exception:
        return []


def get_herdr_agent(target: str) -> Optional[Dict[str, Any]]:
    """取得特定 Herdr Agent 的狀態資訊"""
    res = run_herdr_cmd(["agent", "get", target])
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
        return data.get("result", {}).get("agent")
    except Exception:
        return None


def read_herdr_agent_visible(target: str, lines: int = 100) -> str:
    """讀取 Agent 終端可視區域文字 (TUI 如 agy 必須使用 --source visible)"""
    res = run_herdr_cmd(["agent", "read", target, "--source", "visible", "--lines", str(lines)])
    if res.returncode != 0:
        return ""
    return res.stdout or ""


def send_herdr_keys(target: str, keys: str) -> Dict[str, Any]:
    """向 Agent 終端傳送按鍵或文字"""
    res = run_herdr_cmd(["agent", "send-keys", target, keys])
    if res.returncode != 0:
        try:
            err_data = json.loads(res.stdout or res.stderr)
            return {"success": False, "error": err_data.get("error", {}).get("message", res.stderr.strip())}
        except Exception:
            return {"success": False, "error": res.stderr.strip() or f"Exit code {res.returncode}"}
    return {"success": True}


def submit_herdr_prompt(target: str, prompt_text: str) -> Dict[str, Any]:
    """向 Agent 提交 Prompt"""
    res = run_herdr_cmd(["agent", "prompt", target, prompt_text])
    if res.returncode != 0:
        try:
            err_data = json.loads(res.stdout or res.stderr)
            return {"success": False, "error": err_data.get("error", {}).get("message", res.stderr.strip())}
        except Exception:
            return {"success": False, "error": res.stderr.strip() or f"Exit code {res.returncode}"}
    return {"success": True}


def clean_terminal_text(raw_text: str) -> str:
    """
    清理終端可視輸出：
    1. 去除 ANSI 顏色控制碼
    2. 去除注入之協調鐵律本身 (防止自身比對為問題)
    3. 過濾底部分隔線、提示符號及 statusline 狀態列
    """
    if not raw_text:
        return ""

    # 去除 ANSI escape sequences
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_text)

    # 去除協調鐵律規範本文 (無論是否有換行)，避免規則內的 Q1: 誤判為 Agent 提問
    text = re.sub(r"【協調指令規範】.*?(等待輸入。|\n\n|$)", "", text, flags=re.DOTALL)

    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 過濾水平分隔線 (例如 ────, ===, ---)
        if re.match(r"^[─━=\-_#*]{4,}$", stripped):
            continue
        # 過濾 statusline 欄位
        if any(marker in stripped for marker in STATUSLINE_MARKERS):
            continue
        # 過濾孤立的提示符號或用戶輸入回顯 (例如 > 請確認..., > git diff)
        if stripped in (">", "❯", "➜", "$", "#", "%") or stripped.startswith("> "):
            continue
        cleaned_lines.append(line)

    # 去除首尾多餘空行
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    return "\n".join(cleaned_lines)


def detect_question_or_confirmation(raw_text: str, status: str = "") -> Optional[str]:
    """
    辨識 Agent 是否輸出提問、選項或等待確認：
    - 若有命中 Q1: / 選項 1: / 請問 / 等待確認等模式，擷取提問區塊
    - 若狀態為 blocked，亦視為需要互動確認
    """
    cleaned = clean_terminal_text(raw_text)
    if not cleaned and status != "blocked":
        return None

    lines = cleaned.splitlines()
    first_match_idx = -1

    for idx, line in enumerate(lines):
        for pattern in QUESTION_PATTERNS:
            if re.search(pattern, line):
                first_match_idx = idx
                break
        if first_match_idx != -1:
            break

    if first_match_idx != -1:
        # 從提問開始位置往上保留最多 2 行上下文，一直擷取到文字結尾
        start_idx = max(0, first_match_idx - 2)
        question_block = "\n".join(lines[start_idx:]).strip()
        return question_block

    if status == "blocked":
        # 若 Herdr 標註為 blocked 但未精準命中 regex，提取最後 10 行作為問題描述
        return "\n".join(lines[-10:]).strip() if lines else "Agent 目前處於 blocked 等待確認狀態。"

    return None


def get_agent_session_transcript(agent: str, agent_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """從 Agent 底層 session 日誌中提取最後一輪完整回覆內容 (避免終端視窗截斷)"""
    info = agent_info if agent_info is not None else get_herdr_agent(agent)
    if not info:
        return None
    session = info.get("agent_session", {})
    agent_kind = session.get("agent", "")
    session_val = session.get("value", "")

    if agent_kind == "agy" and session_val:
        candidate_paths = [
            Path.home() / ".gemini" / "antigravity-cli" / "brain" / session_val / ".system_generated" / "logs" / "transcript_full.jsonl",
            Path.home() / ".gemini" / "antigravity-cli" / "brain" / session_val / ".system_generated" / "logs" / "transcript.jsonl",
        ]
        for p in candidate_paths:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in reversed(lines):
                        data = json.loads(line)
                        if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                            return data["content"].strip()
                except Exception:
                    pass
    return None


def extract_completion_summary(
    agent_or_text: str,
    raw_text: str = "",
    agent_info: Optional[Dict[str, Any]] = None,
    max_chars: int = 3500
) -> str:
    """擷取 Agent 任務完工時的最後成果文字摘要，優先從對話日誌讀取完整乾淨文字"""
    agent = ""
    text_to_clean = raw_text
    if raw_text:
        agent = agent_or_text
    else:
        # 單一參數呼叫：若為多行長字串視為純終端文字
        if len(agent_or_text.splitlines()) > 1 or len(agent_or_text) > 80:
            text_to_clean = agent_or_text
        else:
            agent = agent_or_text

    if agent:
        log_content = get_agent_session_transcript(agent, agent_info=agent_info)
        if log_content:
            if len(log_content) > max_chars:
                return log_content[:max_chars] + "\n\n...(全文過長，已為您摘要截斷)..."
            return log_content

    # 回退到終端可視區域文字解析
    cleaned = clean_terminal_text(text_to_clean)
    if not cleaned:
        return "任務已執行完成（無輸出文字）。"

    lines = cleaned.splitlines()
    summary = "\n".join(lines[-25:]).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars]
    return summary


def send_telegram_alert(text: str, parse_mode: str = "HTML") -> bool:
    """發送 Telegram 通知 (透過專案現有介面)"""
    try:
        from telegram_service import send_telegram_sync
        return send_telegram_sync(text, parse_mode=parse_mode)
    except Exception as e:
        print(f"[HerdrCoordinator] ⚠️ Telegram 呼叫失敗: {e}", file=sys.stderr)
        return False


def notify_question(agent: str, question_text: str) -> bool:
    """發送提問/等待確認卡片給主人 (使用自適應渲染引擎轉換為精美 HTML)"""
    raw_msg = (
        f"❓ **【Herdr Agent [{agent}] 請示主人】**\n\n"
        f"{question_text.strip()}\n\n"
        f"💡 _請透過 Telegram 回覆或使用指令回寫：_\n"
        f"`python3 scripts/herdr_coordinator.py relay --agent {agent} --answer \"<答覆>\"`"
    )
    try:
        from telegram_service import markdown_to_telegram_html
        html_msg = markdown_to_telegram_html(raw_msg)
        ok = send_telegram_alert(html_msg, parse_mode="HTML")
    except Exception as e:
        print(f"[HerdrCoordinator] ⚠️ 提問渲染失敗，回退轉義: {e}", file=sys.stderr)
        escaped_q = html.escape(question_text.strip())
        fallback_html = (
            f"❓ <b>Herdr Agent [{agent}] 請示主人</b>\n\n"
            f"<pre>{escaped_q}</pre>\n\n"
            f"💡 <i>請透過 Telegram 回覆或使用指令回寫：</i>\n"
            f"<code>python3 scripts/herdr_coordinator.py relay --agent {agent} --answer \"&lt;答覆&gt;\"</code>"
        )
        ok = send_telegram_alert(fallback_html, parse_mode="HTML")

    print(f"\n[HerdrCoordinator] 📢 請示主人 (Agent: {agent}):\n{question_text}\n")
    return ok


def notify_completion(agent: str, summary_text: str) -> bool:
    """發送完工卡片給主人 (使用 telegram_service.markdown_to_telegram_html 消除 # 字號，轉為精美粗體與圓點)"""
    content = summary_text.strip()
    if len(content) > 3500:
        content = content[:3500] + "\n\n...(部分長輸出已為您截斷)..."

    raw_msg = f"🎉 **【Herdr Agent [{agent}] 任務完成】**\n\n{content}"
    try:
        from telegram_service import markdown_to_telegram_html
        formatted_html = markdown_to_telegram_html(raw_msg)
        ok = send_telegram_alert(formatted_html, parse_mode="HTML")
    except Exception as e:
        print(f"[HerdrCoordinator] ⚠️ markdown_to_telegram_html 轉換失敗: {e}", file=sys.stderr)
        ok = send_telegram_alert(html.escape(raw_msg), parse_mode="HTML")

    if not ok:
        send_telegram_alert(f"🎉 Herdr Agent [{agent}] 任務完成\n\n{content}", parse_mode="")

    print(f"\n[HerdrCoordinator] 🎉 任務完工 (Agent: {agent}):\n{summary_text}\n")
    return ok


def dispatch_task(
    agent: str,
    prompt: str,
    no_watch: bool = False,
    interval: float = 3.0,
    ttl_minutes: int = 120,
) -> Dict[str, Any]:
    """
    派發任務給 Herdr Agent：
    1. 注入協調指令規範鐵律
    2. 透過 herdr agent prompt 發送
    3. 若無指定 --no-watch，則透過 TaskManager 託管啟動背景 watch 守護進程
    """
    injected_prompt = inject_coordination_rule(prompt)
    prompt_res = submit_herdr_prompt(agent, injected_prompt)
    if not prompt_res.get("success"):
        print(f"❌ 派發任務至 Herdr Agent [{agent}] 失敗: {prompt_res.get('error')}", file=sys.stderr)
        return prompt_res

    res = {
        "success": True,
        "agent": agent,
        "injected_prompt": injected_prompt,
    }

    if not no_watch:
        try:
            from task_manager import TaskManager
            tm = TaskManager()
            watch_task = tm.spawn(
                name=f"herdr-watch-{agent}",
                script="scripts/herdr_coordinator.py",
                args=["watch", "--agent", agent, "--interval", str(interval)],
                ttl_minutes=ttl_minutes,
            )
            res["watch_task"] = watch_task
            print(f"🚀 任務已成功派發給 Herdr Agent [{agent}]！")
            print(f"👀 背景守護進程已啟動 (Task ID: {watch_task.get('task_id')})，將於需要確認或完工時推播 Telegram。")
        except Exception as e:
            print(f"[HerdrCoordinator] ⚠️ 啟動 TaskManager 守護進程失敗: {e}", file=sys.stderr)
            res["watch_task_error"] = str(e)
    else:
        print(f"🚀 任務已成功派發給 Herdr Agent [{agent}] (略過背景守護)。")

    return res


def relay_answer(agent: str, answer: str) -> Dict[str, Any]:
    """回寫答案解鎖等待輸入的 Agent"""
    res = send_herdr_keys(agent, f"{answer}\n")
    if res.get("success"):
        print(f"✅ 已成功將答覆回寫至 Herdr Agent [{agent}]：{answer}")
    else:
        print(f"❌ 回寫答覆至 Herdr Agent [{agent}] 失敗: {res.get('error')}", file=sys.stderr)
    return res


def watch_agent(
    agent: str,
    interval: float = 3.0,
    max_iterations: Optional[int] = None,
    timeout: Optional[float] = None,
    start_grace_seconds: float = 12.0,
) -> Dict[str, Any]:
    """
    背景守護主循環：
    1. 輪詢 agent get 與 read visible lines
    2. 提問辨識：偵測到提問且處於 idle/blocked，推播 Telegram 請示主人
    3. 完工辨識：由 working 轉為 idle/done 且無提問，推播完工卡片並結束進程
    """
    start_time = time.time()
    has_seen_working = False
    waiting_input = False
    last_question_content: Optional[str] = None
    iteration = 0

    print(f"[HerdrCoordinator] 👀 開始守護 Herdr Agent [{agent}] (輪詢間隔: {interval}s)...")

    while True:
        iteration += 1
        if max_iterations is not None and iteration > max_iterations:
            return {"status": "max_iterations_reached", "agent": agent}

        if timeout is not None and (time.time() - start_time) > timeout:
            print(f"[HerdrCoordinator] ⚠️ 守護超時 ({timeout}s)，自動退出。")
            return {"status": "timeout", "agent": agent}

        agent_info = get_herdr_agent(agent)
        if not agent_info:
            print(f"[HerdrCoordinator] ⚠️ 找不到 Agent [{agent}]，等待重試...")
            time.sleep(interval)
            continue

        agent_status = agent_info.get("agent_status", "unknown")
        visible_text = read_herdr_agent_visible(agent, lines=100)

        if agent_status == "working":
            has_seen_working = True
            waiting_input = False
            # Agent 正在處理中，繼續等待
            time.sleep(interval)
            continue

        # 狀態為 idle, blocked, done 或 unknown
        question_text = detect_question_or_confirmation(visible_text, status=agent_status)

        if question_text:
            # 偵測到提問或需要確認
            if not waiting_input or question_text != last_question_content:
                waiting_input = True
                last_question_content = question_text
                notify_question(agent, question_text)
            # 保持在守護狀態，等待主人 relay 答覆
            time.sleep(interval)
            continue

        # 無提問
        if has_seen_working:
            # 曾進入 working 狀態且現在回到 idle/done，且無提問 -> 完工！
            summary = extract_completion_summary(agent, visible_text, agent_info=agent_info)
            notify_completion(agent, summary)
            return {"status": "completed", "agent": agent, "summary": summary}

        # 尚未觀察到 working 狀態：檢查是否超過剛派發的初始啟動緩衝期
        elapsed = time.time() - start_time
        if elapsed < start_grace_seconds:
            # 緩衝期內，繼續等待 Agent 啟動
            time.sleep(interval)
            continue
        else:
            # 超過緩衝期且依然為 idle/done，視為快速指令完工
            summary = extract_completion_summary(agent, visible_text, agent_info=agent_info)
            notify_completion(agent, summary)
            return {"status": "completed", "agent": agent, "summary": summary}


def print_agent_table(agents: List[Dict[str, Any]]) -> None:
    """印出格式化 Agent 清單"""
    if not agents:
        print("目前沒有運行的 Herdr Agent。")
        return

    header = f"{'AGENT / TARGET':<18} {'STATUS':<10} {'PANE ID':<10} {'WORKSPACE':<12} {'TITLE / CWD'}"
    print(header)
    print("─" * 78)
    for a in agents:
        agent_name = a.get("agent", "unknown")
        status = a.get("agent_status", "unknown")
        pane_id = a.get("pane_id", "-")
        ws = a.get("workspace_id", "-")
        title = a.get("terminal_title_stripped") or a.get("cwd") or "-"
        if len(title) > 35:
            title = title[:32] + "..."
        print(f"{agent_name:<18} {status:<10} {pane_id:<10} {ws:<12} {title}")


def main():
    def handle_exit(signum, frame):
        print(f"\n[HerdrCoordinator] 收到訊號 {signum}，守護進程終止。")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    parser = argparse.ArgumentParser(
        description="Herdr 終端 Agent 協調與背景守護者 (scripts/herdr_coordinator.py)"
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="子指令")

    # list
    sub_list = subparsers.add_parser("list", help="列出所有 Herdr Agent")
    sub_list.add_argument("--json", action="store_true", help="以 JSON 格式輸出")

    # dispatch
    sub_dispatch = subparsers.add_parser("dispatch", help="派發任務並啟動背景守護")
    sub_dispatch.add_argument("--agent", "-a", required=True, help="目標 Agent 名稱或 Pane ID")
    sub_dispatch.add_argument("--prompt", "-p", required=True, help="要派發的任務 Prompt")
    sub_dispatch.add_argument("--no-watch", action="store_true", help="僅派發任務，不啟動背景守護進程")
    sub_dispatch.add_argument("--interval", type=float, default=3.0, help="守護者輪詢間隔 (秒)")
    sub_dispatch.add_argument("--ttl", type=int, default=120, help="守護進程生命週期上限 (分鐘)")

    # watch
    sub_watch = subparsers.add_parser("watch", help="啟動背景守護監聽特定 Agent")
    sub_watch.add_argument("--agent", "-a", required=True, help="目標 Agent 名稱或 Pane ID")
    sub_watch.add_argument("--interval", "-i", type=float, default=3.0, help="輪詢間隔 (秒)")
    sub_watch.add_argument("--timeout", type=float, default=None, help="最大監聽逾時 (秒)")

    # relay
    sub_relay = subparsers.add_parser("relay", help="回寫答案解鎖等待輸入的 Agent")
    sub_relay.add_argument("--agent", "-a", required=True, help="目標 Agent 名稱或 Pane ID")
    sub_relay.add_argument("--answer", "-ans", required=True, help="主人的答覆內容")

    args = parser.parse_args()

    if args.subcommand == "list":
        agents = list_herdr_agents()
        if args.json:
            print(json.dumps(agents, ensure_ascii=False, indent=2))
        else:
            print_agent_table(agents)

    elif args.subcommand == "dispatch":
        res = dispatch_task(
            agent=args.agent,
            prompt=args.prompt,
            no_watch=args.no_watch,
            interval=args.interval,
            ttl_minutes=args.ttl,
        )
        if not res.get("success"):
            sys.exit(1)

    elif args.subcommand == "watch":
        res = watch_agent(
            agent=args.agent,
            interval=args.interval,
            timeout=args.timeout,
        )
        print(f"[HerdrCoordinator] 守護結束: {res.get('status')}")

    elif args.subcommand == "relay":
        res = relay_answer(
            agent=args.agent,
            answer=args.answer,
        )
        if not res.get("success"):
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
