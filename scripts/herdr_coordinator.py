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


def resolve_session_log_path(agent_info: Optional[Dict[str, Any]]) -> Tuple[str, Optional[Path]]:
    """解析不同類型 Agent (agy / omp) 的底層 JSONL 對話日誌路徑"""
    if not agent_info:
        return ("", None)
    session = agent_info.get("agent_session") or {}
    agent_kind = (session.get("agent") or agent_info.get("agent") or "").lower()
    kind = (session.get("kind") or "").lower()
    val = str(session.get("value") or "").strip()
    if not val:
        return (agent_kind, None)

    if agent_kind == "agy" or kind == "id":
        candidate_paths = [
            Path.home() / ".gemini" / "antigravity-cli" / "brain" / val / ".system_generated" / "logs" / "transcript_full.jsonl",
            Path.home() / ".gemini" / "antigravity-cli" / "brain" / val / ".system_generated" / "logs" / "transcript.jsonl",
        ]
        for p in candidate_paths:
            if p.exists():
                return ("agy", p)
        return ("agy", None)

    if kind == "path" or agent_kind == "omp":
        p = Path(val).expanduser()
        return (agent_kind or "omp", p if p.exists() else None)

    return (agent_kind, None)


def count_session_log_lines(agent: str, agent_info: Optional[Dict[str, Any]] = None) -> int:
    """取得派發當下該 Agent JSONL 日誌的總行數，作為增量擷取基準線"""
    info = agent_info if agent_info is not None else get_herdr_agent(agent)
    _, log_path = resolve_session_log_path(info)
    if not log_path or not log_path.exists():
        return 0
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _extract_omp_text_blocks(content: Any) -> str:
    """將 omp message.content（字串或區塊陣列）正規化為純文字"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text"):
                parts.append(str(blk["text"]).strip())
            elif isinstance(blk, str) and blk.strip():
                parts.append(blk.strip())
        return "\n".join(parts).strip()
    return ""


def read_session_turns(
    agent: str,
    agent_info: Optional[Dict[str, Any]] = None,
    baseline_lines: int = 0,
) -> Dict[str, Any]:
    """
    從 agy 或 omp 的 JSONL 日誌中，精準擷取基準線之後的新增輸入、工具呼叫與回覆文字。
    """
    info = agent_info if agent_info is not None else get_herdr_agent(agent)
    agent_kind, log_path = resolve_session_log_path(info)
    result: Dict[str, Any] = {
        "agent_kind": agent_kind,
        "log_path": str(log_path) if log_path else "",
        "total_lines": 0,
        "user_inputs": [],
        "assistant_outputs": [],
        "tool_calls": [],
        "latest_reply": "",
    }
    if not log_path or not log_path.exists():
        return result

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return result

    result["total_lines"] = len(lines)
    start_idx = max(0, min(baseline_lines, len(lines)))
    delta_lines = lines[start_idx:]

    for raw in delta_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        if agent_kind == "agy":
            step_type = data.get("type")
            if step_type == "USER_INPUT" and data.get("content"):
                result["user_inputs"].append(str(data["content"]).strip())
            elif step_type == "PLANNER_RESPONSE":
                for tc in data.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        t_name = tc.get("name") or tc.get("function", {}).get("name") or "tool"
                        result["tool_calls"].append(str(t_name))
                if data.get("content"):
                    result["assistant_outputs"].append(str(data["content"]).strip())
            elif step_type == "GENERIC" and data.get("content"):
                result["assistant_outputs"].append(str(data["content"]).strip())

        elif agent_kind == "omp":
            if data.get("type") != "message":
                continue
            msg = data.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "user":
                txt = _extract_omp_text_blocks(msg.get("content"))
                if txt:
                    result["user_inputs"].append(txt)
            elif role == "assistant":
                txt = _extract_omp_text_blocks(msg.get("content"))
                if txt:
                    result["assistant_outputs"].append(txt)
            elif role == "toolResult":
                t_name = str(msg.get("toolName") or "tool")
                is_err = bool(msg.get("isError"))
                result["tool_calls"].append(f"{t_name}{' (error)' if is_err else ''}")

    if result["assistant_outputs"]:
        result["latest_reply"] = result["assistant_outputs"][-1]
    return result


def get_agent_session_transcript(agent: str, agent_info: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """從 Agent 底層 session 日誌中提取最後一輪完整回覆內容 (支援 agy 與 omp)"""
    turns = read_session_turns(agent, agent_info=agent_info, baseline_lines=0)
    latest = (turns.get("latest_reply") or "").strip()
    return latest or None


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


def resolve_agent_display_name(
    agent: str,
    custom_name: Optional[str] = None,
    agent_info: Optional[Dict[str, Any]] = None
) -> str:
    """解析出更具語意、易讀的 Agent 顯示名稱 (例如: 個人助理 · agy 或 自訂名稱)"""
    if custom_name and custom_name.strip():
        return custom_name.strip()

    info = agent_info if agent_info is not None else get_herdr_agent(agent)
    if not info:
        return f"Herdr Agent [{agent}]"

    ws_name = ""
    cwd = info.get("cwd") or info.get("foreground_cwd")
    if cwd:
        ws_name = Path(cwd).name
    if not ws_name:
        ws_name = info.get("tokens", {}).get("group") or info.get("workspace_id", "")

    agent_kind = info.get("agent") or "agent"
    pane_id = info.get("pane_id") or agent

    if ws_name:
        return f"{ws_name} · {agent_kind} ({pane_id})"
    return f"Herdr Agent [{agent}]"


def notify_question(
    agent: str,
    question_text: str,
    name: Optional[str] = None,
    agent_info: Optional[Dict[str, Any]] = None
) -> bool:
    """發送提問/等待確認卡片給主人 (使用自適應渲染引擎轉換為精美 HTML)"""
    display_title = resolve_agent_display_name(agent, custom_name=name, agent_info=agent_info)
    raw_msg = (
        f"❓ **【{display_title} 請示主人】**\n\n"
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
            f"❓ <b>{display_title} 請示主人</b>\n\n"
            f"<pre>{escaped_q}</pre>\n\n"
            f"💡 <i>請透過 Telegram 回覆或使用指令回寫：</i>\n"
            f"<code>python3 scripts/herdr_coordinator.py relay --agent {agent} --answer \"&lt;答覆&gt;\"</code>"
        )
        ok = send_telegram_alert(fallback_html, parse_mode="HTML")

    print(f"\n[HerdrCoordinator] 📢 請示主人 (Agent: {display_title}):\n{question_text}\n")
    return ok


def notify_completion(
    agent: str,
    summary_text: str,
    name: Optional[str] = None,
    agent_info: Optional[Dict[str, Any]] = None
) -> bool:
    """發送完工卡片給主人 (使用 telegram_service.markdown_to_telegram_html 消除 # 字號，轉為精美粗體與圓點)"""
    display_title = resolve_agent_display_name(agent, custom_name=name, agent_info=agent_info)
    content = summary_text.strip()
    if len(content) > 3500:
        content = content[:3500] + "\n\n...(部分長輸出已為您截斷)..."

    raw_msg = f"🎉 **【{display_title} 任務完成】**\n\n{content}"
    try:
        from telegram_service import markdown_to_telegram_html
        formatted_html = markdown_to_telegram_html(raw_msg)
        ok = send_telegram_alert(formatted_html, parse_mode="HTML")
    except Exception as e:
        print(f"[HerdrCoordinator] ⚠️ markdown_to_telegram_html 轉換失敗: {e}", file=sys.stderr)
        ok = send_telegram_alert(html.escape(raw_msg), parse_mode="HTML")

    if not ok:
        send_telegram_alert(f"🎉 {display_title} 任務完成\n\n{content}", parse_mode="")

    print(f"\n[HerdrCoordinator] 🎉 任務完工 (Agent: {display_title}):\n{summary_text}\n")
    return ok


def collect_workspace_evidence(cwd: Optional[str]) -> str:
    """在目標 Agent 工作目錄下收集 git status 與 git diff --stat 供品管驗收參考"""
    if not cwd or not Path(cwd).is_dir():
        return ""
    parts: List[str] = []
    for label, git_args in [("git status", ["git", "status", "--short"]), ("git diff --stat", ["git", "diff", "--stat"])]:
        try:
            proc = subprocess.run(
                git_args,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
            )
            out = (proc.stdout or "").strip()
            if proc.returncode == 0 and out:
                parts.append(f"[{label}]\n{out[:1200]}")
        except Exception:
            pass
    return "\n\n".join(parts)


def review_worker_output(
    task_prompt: str,
    criteria: Optional[str],
    turn_data: Dict[str, Any],
    git_evidence: str = "",
    fallback_summary: str = "",
) -> Dict[str, Any]:
    """
    透過無狀態 `agy -p` 對工人產出進行品管驗收：
    回傳 {"verdict": "PASS"|"REJECT", "feedback": str, "summary": str}
    """
    worker_reply = (turn_data.get("latest_reply") or fallback_summary or "").strip()
    tool_list = ", ".join(turn_data.get("tool_calls") or []) or "無紀錄"
    criteria_block = criteria.strip() if criteria else "確認任務需求已完成、無明顯錯誤且有實際驗證結果。"

    review_prompt = (
        "你是嚴謹的技術協調者與品管審查員。請根據以下資訊判斷底層工程 Agent 是否已確實完成任務：\n\n"
        f"【原始任務需求】\n{task_prompt}\n\n"
        f"【驗收標準】\n{criteria_block}\n\n"
        f"【工人呼叫的工具紀錄】\n{tool_list}\n\n"
        f"【工人最後回覆內容】\n{worker_reply[:3000]}\n\n"
        + (f"【工作區 Git 異動證據】\n{git_evidence}\n\n" if git_evidence else "")
        + "請嚴格以 JSON 格式回覆（不要加 markdown 圍欄），格式如下：\n"
        '{"verdict": "PASS" 或 "REJECT", "summary": "給主人的 2~3 句繁體中文精簡總結", "feedback": "若 REJECT，請給工人具體可執行的修正指示；若 PASS 則留空"}'
    )

    agy_bin = shutil.which("agy") or "/opt/homebrew/bin/agy"
    if not Path(agy_bin).exists() and not shutil.which("agy"):
        return {
            "verdict": "PASS",
            "summary": worker_reply[:1500] or "任務已完成。",
            "feedback": "",
        }

    try:
        proc = subprocess.run(
            [agy_bin, "-p", review_prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90.0,
        )
        raw_out = (proc.stdout or "").strip()
        # 嘗試抽取 JSON 區塊
        m = re.search(r"\{[\s\S]*\}", raw_out)
        if m:
            parsed = json.loads(m.group(0))
            verdict = str(parsed.get("verdict", "PASS")).upper()
            if verdict not in {"PASS", "REJECT"}:
                verdict = "PASS"
            return {
                "verdict": verdict,
                "summary": str(parsed.get("summary") or worker_reply[:1500]).strip(),
                "feedback": str(parsed.get("feedback") or "").strip(),
            }
    except Exception as e:
        print(f"[HerdrCoordinator] ⚠️ 驗收評審異常，降級為直接回報: {e}", file=sys.stderr)

    return {
        "verdict": "PASS",
        "summary": worker_reply[:1500] or "任務已完成。",
        "feedback": "",
    }


def record_task_to_history_and_memory(
    agent: str,
    display_title: str,
    task_prompt: str,
    summary: str,
    log_path: str = "",
    sync_idle_memory: bool = True,
) -> None:
    """將 Herdr 任務成果安全寫入 data/history.jsonl，並於主大腦閒置時靜默同步 3 行摘要"""
    try:
        from memory_manager import memory_manager
        actions = [f"herdr_coordinator ({agent})"]
        if log_path:
            actions.append(f"session_log: {log_path}")
        memory_manager.log_interaction(
            f"[Herdr 派工 · {display_title}] {task_prompt[:300]}",
            summary,
            actions=actions,
        )
    except Exception as e:
        print(f"[HerdrCoordinator] ⚠️ 寫入 history.jsonl 失敗: {e}", file=sys.stderr)

    if not sync_idle_memory:
        return

    try:
        import urllib.request
        from config import settings
        url = f"http://127.0.0.1:{settings.PORT}/api/internal/memory-sync"
        memo = (
            f"任務對象：{display_title}（Agent: {agent}）\n"
            f"派發需求：{task_prompt[:180]}\n"
            f"驗收結果：{summary[:350]}"
        )
        req_data = json.dumps({"summary": memo, "source": f"herdr:{agent}"}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.ASSISTANT_API_KEY:
            headers["X-Agent-Key"] = settings.ASSISTANT_API_KEY
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            resp.read()
    except Exception as e:
        print(f"[HerdrCoordinator] ℹ️ 主大腦靜默記憶同步略過: {e}", file=sys.stderr)


def update_batch_progress(
    batch_id: str,
    agent: str,
    display_title: str,
    status: str,
    summary: str,
    batch_total: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """追蹤多任務批次進度（使用檔案鎖防止併發寫入衝突）"""
    if not batch_id:
        return None
    batch_file = BASE_DIR / "data" / "herdr_batches.json"
    batch_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl as _fcntl
    except ImportError:
        _fcntl = None

    with open(batch_file, "a+", encoding="utf-8") as f:
        if _fcntl is not None:
            _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read().strip()
            all_batches = json.loads(raw) if raw else {}
            entry = all_batches.get(batch_id) or {"total": batch_total or 0, "tasks": {}}
            if batch_total and batch_total > entry.get("total", 0):
                entry["total"] = batch_total
            entry["tasks"][agent] = {
                "title": display_title,
                "status": status,
                "summary": summary[:300],
            }
            all_batches[batch_id] = entry
            f.seek(0)
            f.truncate()
            f.write(json.dumps(all_batches, ensure_ascii=False, indent=2))
            f.flush()
            return entry
        finally:
            if _fcntl is not None:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)


def dispatch_task(
    agent: str,
    prompt: str,
    name: Optional[str] = None,
    no_watch: bool = False,
    interval: float = 3.0,
    ttl_minutes: int = 120,
    criteria: Optional[str] = None,
    max_retries: int = 3,
    batch_id: Optional[str] = None,
    batch_total: Optional[int] = None,
    auto_review: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    派發任務給 Herdr Agent：
    1. 檢查目標是否忙碌 (working)，避免同一終端輸入衝突
    2. 記錄派發前 JSONL 基準線
    3. 注入協調指令規範鐵律並透過 herdr agent prompt 發送
    4. 若無指定 --no-watch，則透過 TaskManager 託管啟動背景 watch + 驗收守護進程
    """
    agent_info = get_herdr_agent(agent)
    if agent_info and agent_info.get("agent_status") == "working" and not force:
        err_msg = f"目標 Agent [{agent}] 目前正在執行中 (working)，為避免終端輸入衝突請稍候或加上 --force。"
        print(f"⚠️ {err_msg}", file=sys.stderr)
        return {"success": False, "agent": agent, "error": "agent_busy", "message": err_msg}

    baseline_lines = count_session_log_lines(agent, agent_info=agent_info)
    full_prompt = prompt if not criteria else f"{prompt}\n\n【驗收標準】\n{criteria}"
    injected_prompt = inject_coordination_rule(full_prompt)
    prompt_res = submit_herdr_prompt(agent, injected_prompt)
    if not prompt_res.get("success"):
        print(f"❌ 派發任務至 Herdr Agent [{agent}] 失敗: {prompt_res.get('error')}", file=sys.stderr)
        return prompt_res

    res = {
        "success": True,
        "agent": agent,
        "name": name,
        "baseline_lines": baseline_lines,
        "max_retries": max_retries,
        "injected_prompt": injected_prompt,
    }

    if not no_watch:
        try:
            from task_manager import TaskManager
            tm = TaskManager()
            watch_args = [
                "watch",
                "--agent", agent,
                "--interval", str(interval),
                "--task", prompt,
                "--max-retries", str(max_retries),
                "--baseline-lines", str(baseline_lines),
            ]
            if name:
                watch_args.extend(["--name", name])
            if criteria:
                watch_args.extend(["--criteria", criteria])
            if batch_id:
                watch_args.extend(["--batch-id", batch_id])
            if batch_total is not None:
                watch_args.extend(["--batch-total", str(batch_total)])
            if not auto_review:
                watch_args.append("--no-review")

            watch_task = tm.spawn(
                name=f"herdr-watch-{agent}",
                script="scripts/herdr_coordinator.py",
                args=watch_args,
                ttl_minutes=ttl_minutes,
            )
            res["watch_task"] = watch_task
            display_title = resolve_agent_display_name(agent, custom_name=name, agent_info=agent_info)
            print(f"🚀 任務已成功派發給 【{display_title}】！（最大自動重修上限：{max_retries} 次）")
            print(f"👀 背景協調者已啟動 (Task ID: {watch_task.get('task_id')})，將於驗收通過或需確認時推播通知。")
        except Exception as e:
            print(f"[HerdrCoordinator] ⚠️ 啟動 TaskManager 守護進程失敗: {e}", file=sys.stderr)
            res["watch_task_error"] = str(e)
    else:
        print(f"🚀 任務已成功派發給 Herdr Agent [{agent}] (略過背景守護)。")

    return res


def relay_answer(agent: str, answer: str) -> Dict[str, Any]:
    """回寫答案解鎖等待輸入的 Agent"""
    # 若為單一終端按鍵 (如 Enter, C-c)，使用 send-keys；否則使用 prompt 發送文字指令
    if answer.strip() in {"Enter", "Return", "C-c", "y", "n"}:
        res = send_herdr_keys(agent, answer.strip())
    else:
        res = submit_herdr_prompt(agent, answer)

    if res.get("success"):
        print(f"✅ 已成功將答覆回寫至 Herdr Agent [{agent}]：{answer}")
    else:
        print(f"❌ 回寫答覆至 Herdr Agent [{agent}] 失敗: {res.get('error')}", file=sys.stderr)
    return res


def _finalize_watch_completion(
    agent: str,
    visible_text: str,
    agent_info: Dict[str, Any],
    name: Optional[str],
    task_prompt: Optional[str],
    criteria: Optional[str],
    baseline_lines: int,
    retries_used: int,
    max_retries: int,
    auto_review: bool,
    batch_id: Optional[str],
    batch_total: Optional[int],
) -> Dict[str, Any]:
    """
    工人完工後的統一品管收尾：
    回傳 {"action": "retry", "new_baseline": int} 或 {"action": "done", "result": dict}
    """
    turn_data = read_session_turns(agent, agent_info=agent_info, baseline_lines=baseline_lines)
    raw_summary = extract_completion_summary(agent, visible_text, agent_info=agent_info)
    display_title = resolve_agent_display_name(agent, custom_name=name, agent_info=agent_info)

    if auto_review and task_prompt:
        cwd = agent_info.get("cwd") or agent_info.get("foreground_cwd")
        git_evidence = collect_workspace_evidence(cwd)
        review = review_worker_output(
            task_prompt=task_prompt,
            criteria=criteria,
            turn_data=turn_data,
            git_evidence=git_evidence,
            fallback_summary=raw_summary,
        )
        verdict = review.get("verdict", "PASS")
        reviewed_summary = review.get("summary") or raw_summary
        feedback = review.get("feedback") or ""

        if verdict == "REJECT" and retries_used < max_retries:
            attempt_no = retries_used + 1
            new_baseline = turn_data.get("total_lines") or count_session_log_lines(agent, agent_info=agent_info)
            retry_prompt = inject_coordination_rule(
                f"【協調者品管打回 · 第 {attempt_no}/{max_retries} 次重修】\n"
                f"未通過驗收原因與修正要求：\n{feedback or '請確認所有驗收標準皆已執行並通過測試。'}\n\n"
                "請直接修正並重新驗證完成。"
            )
            submit_herdr_prompt(agent, retry_prompt)
            print(f"[HerdrCoordinator] 🔁 【{display_title}】未通過驗收，已打回第 {attempt_no}/{max_retries} 次重修。")
            return {"action": "retry", "new_baseline": new_baseline}

        if verdict == "REJECT":
            final_text = f"⚠️ 已達最大重修次數 ({max_retries} 次) 仍未完全通過驗收：\n{feedback}\n\n最後摘要：\n{reviewed_summary}"
        else:
            final_text = reviewed_summary
    else:
        final_text = raw_summary

    notify_completion(agent, final_text, name=name, agent_info=agent_info)
    if task_prompt:
        record_task_to_history_and_memory(
            agent=agent,
            display_title=display_title,
            task_prompt=task_prompt,
            summary=final_text,
            log_path=turn_data.get("log_path", ""),
        )

    if batch_id:
        batch_entry = update_batch_progress(
            batch_id=batch_id,
            agent=agent,
            display_title=display_title,
            status="completed",
            summary=final_text,
            batch_total=batch_total,
        )
        if batch_entry and batch_entry.get("total", 0) > 0:
            done_cnt = len(batch_entry.get("tasks", {}))
            total_cnt = batch_entry["total"]
            if done_cnt >= total_cnt:
                lines = [f"• <b>{html.escape(t.get('title', k))}</b>：{html.escape(t.get('summary', '')[:120])}" for k, t in batch_entry["tasks"].items()]
                send_telegram_alert(f"🏁 <b>批次任務 {done_cnt}/{total_cnt} 全部驗收完成</b>\n\n" + "\n".join(lines))

    return {
        "action": "done",
        "result": {
            "status": "completed",
            "agent": agent,
            "summary": final_text,
            "retries_used": retries_used,
        },
    }


def watch_agent(
    agent: str,
    interval: float = 3.0,
    max_iterations: Optional[int] = None,
    timeout: Optional[float] = None,
    start_grace_seconds: float = 12.0,
    name: Optional[str] = None,
    task_prompt: Optional[str] = None,
    criteria: Optional[str] = None,
    max_retries: int = 3,
    baseline_lines: int = 0,
    batch_id: Optional[str] = None,
    batch_total: Optional[int] = None,
    auto_review: bool = True,
) -> Dict[str, Any]:
    """
    背景守護主循環（含自動驗收與打回重修迴圈）：
    1. 輪詢 agent get 與 read visible lines
    2. 提問辨識：偵測到提問且處於 idle/blocked，推播 Telegram 請示主人
    3. 完工辨識：由 working 轉為 idle/done 且無提問 ➔ 讀取 JSONL 驗收（不通過最多打回 max_retries 次）
    """
    start_time = time.time()
    has_seen_working = False
    waiting_input = False
    last_question_content: Optional[str] = None
    iteration = 0
    retries_used = 0
    current_baseline = baseline_lines

    display_title = resolve_agent_display_name(agent, custom_name=name)
    print(f"[HerdrCoordinator] 👀 開始守護 【{display_title}】 (輪詢間隔: {interval}s, 重修上限: {max_retries})...")

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
            time.sleep(interval)
            continue

        question_text = detect_question_or_confirmation(visible_text, status=agent_status)

        if question_text:
            if not waiting_input or question_text != last_question_content:
                waiting_input = True
                last_question_content = question_text
                notify_question(agent, question_text, name=name, agent_info=agent_info)
            time.sleep(interval)
            continue

        elapsed = time.time() - start_time
        if not has_seen_working and elapsed < start_grace_seconds:
            time.sleep(interval)
            continue

        outcome = _finalize_watch_completion(
            agent=agent,
            visible_text=visible_text,
            agent_info=agent_info,
            name=name,
            task_prompt=task_prompt,
            criteria=criteria,
            baseline_lines=current_baseline,
            retries_used=retries_used,
            max_retries=max_retries,
            auto_review=auto_review,
            batch_id=batch_id,
            batch_total=batch_total,
        )
        if outcome["action"] == "retry":
            retries_used += 1
            current_baseline = outcome["new_baseline"]
            has_seen_working = False
            waiting_input = False
            start_time = time.time()
            time.sleep(interval)
            continue
        return outcome["result"]


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
    sub_dispatch.add_argument("--name", "-n", default=None, help="自訂顯示名稱 (例如: 個人助理 · Git檢查)")
    sub_dispatch.add_argument("--criteria", "-c", default=None, help="驗收標準 (Definition of Done)")
    sub_dispatch.add_argument("--max-retries", type=int, default=3, help="品管驗收未通過時自動打回重修上限 (預設 3 次)")
    sub_dispatch.add_argument("--batch-id", default=None, help="多任務併發批次 ID")
    sub_dispatch.add_argument("--batch-total", type=int, default=None, help="該批次總任務數")
    sub_dispatch.add_argument("--no-review", action="store_true", help="略過品管自動驗收，完工直接通知")
    sub_dispatch.add_argument("--force", action="store_true", help="即使目標處於 working 也強制派發")
    sub_dispatch.add_argument("--no-watch", action="store_true", help="僅派發任務，不啟動背景守護進程")
    sub_dispatch.add_argument("--interval", type=float, default=3.0, help="守護者輪詢間隔 (秒)")
    sub_dispatch.add_argument("--ttl", type=int, default=120, help="守護進程生命週期上限 (分鐘)")

    # watch
    sub_watch = subparsers.add_parser("watch", help="啟動背景守護監聽特定 Agent")
    sub_watch.add_argument("--agent", "-a", required=True, help="目標 Agent 名稱或 Pane ID")
    sub_watch.add_argument("--name", "-n", default=None, help="自訂顯示名稱")
    sub_watch.add_argument("--task", default=None, help="原始派發任務需求（供自動驗收參考）")
    sub_watch.add_argument("--criteria", "-c", default=None, help="驗收標準 (Definition of Done)")
    sub_watch.add_argument("--max-retries", type=int, default=3, help="自動打回重修上限 (預設 3 次)")
    sub_watch.add_argument("--baseline-lines", type=int, default=0, help="派發前 JSONL 日誌基準行數")
    sub_watch.add_argument("--batch-id", default=None, help="多任務併發批次 ID")
    sub_watch.add_argument("--batch-total", type=int, default=None, help="該批次總任務數")
    sub_watch.add_argument("--no-review", action="store_true", help="略過品管自動驗收")
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
            name=args.name,
            no_watch=args.no_watch,
            interval=args.interval,
            ttl_minutes=args.ttl,
            criteria=args.criteria,
            max_retries=args.max_retries,
            batch_id=args.batch_id,
            batch_total=args.batch_total,
            auto_review=not args.no_review,
            force=args.force,
        )
        if not res.get("success"):
            sys.exit(1)

    elif args.subcommand == "watch":
        res = watch_agent(
            agent=args.agent,
            name=args.name,
            interval=args.interval,
            timeout=args.timeout,
            task_prompt=args.task,
            criteria=args.criteria,
            max_retries=args.max_retries,
            baseline_lines=args.baseline_lines,
            batch_id=args.batch_id,
            batch_total=args.batch_total,
            auto_review=not args.no_review,
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
