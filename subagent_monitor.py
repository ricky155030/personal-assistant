"""即時掃描與解析本機 Antigravity (agy) 運行中子代理狀態與終端機／工具執行 Log。"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BRAIN_DIR = Path.home() / ".gemini" / "antigravity-cli" / "brain"
DEFAULT_STALE_SECONDS = 120
_CONV_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{4,128}$")
_SECRET_RE = re.compile(
    r"(?i)(Bearer\s+[A-Za-z0-9._\-]{12,}|sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|"
    r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^\s'\",;]{8,})"
)


def _sanitize_text(text: str, max_len: int = 240) -> str:
    """遮罩潛在機敏憑證並裁切單行長度。"""
    if not text:
        return ""
    cleaned = _SECRET_RE.sub("[REDACTED]", text.replace("\r", " ").strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "…"
    return cleaned


def _parse_iso_ts(ts_str: Optional[str]) -> Optional[float]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        norm = ts_str.strip()
        if norm.endswith("Z"):
            norm = norm[:-1] + "+00:00"
        return datetime.fromisoformat(norm).timestamp()
    except Exception:
        return None


def _format_hhmmss(ts_str: Optional[str]) -> str:
    ts = _parse_iso_ts(ts_str)
    if ts is None:
        return "--:--:--"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _parse_tool_args(raw_args: Any) -> Dict[str, Any]:
    if isinstance(raw_args, dict):
        parsed: Dict[str, Any] = {}
        for k, v in raw_args.items():
            if isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                try:
                    parsed[k] = json.loads(v)
                    continue
                except Exception:
                    pass
            parsed[k] = v
        return parsed
    if isinstance(raw_args, str):
        try:
            obj = json.loads(raw_args)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {}


def _summarize_tool_call(name: str, raw_args: Any) -> str:
    args = _parse_tool_args(raw_args)
    if name == "run_command":
        cmd = _sanitize_text(str(args.get("CommandLine") or args.get("command") or ""), 120)
        return f"run_command: {cmd}" if cmd else "run_command"
    if name in ("read_file", "write_file", "edit_file"):
        path_val = str(args.get("Path") or args.get("path") or "")
        short_path = Path(path_val).name if path_val else ""
        return f"{name}: {short_path}" if short_path else name
    if name == "send_message":
        return "send_message: 回報父代理"
    if name == "invoke_subagent":
        role = _sanitize_text(str(args.get("Role") or ""), 60)
        return f"invoke_subagent: {role}" if role else "invoke_subagent"
    if name == "search_web":
        q = _sanitize_text(str(args.get("query") or ""), 80)
        return f"search_web: {q}" if q else "search_web"
    for key in ("toolSummary", "Description", "Prompt", "Path", "Action"):
        if args.get(key):
            return f"{name}: {_sanitize_text(str(args[key]), 100)}"
    return name


def _extract_subagent_meta(conv_dir: Path, events: List[dict]) -> Tuple[bool, str, str]:
    """判斷是否為 agy 子代理對話，並回傳 (is_subagent, role, type_name)。"""
    msg_dir = conv_dir / ".system_generated" / "messages"
    if msg_dir.is_dir():
        msg_files = sorted(msg_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for mf in msg_files:
            try:
                data = json.loads(mf.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            tool_call = (data.get("sourceMetadata") or {}).get("tool", {}).get("toolCall", {})
            if tool_call.get("name") == "invoke_subagent":
                args = _parse_tool_args(tool_call.get("argumentsJson"))
                role = _sanitize_text(str(args.get("Role") or ""), 80) or "Subagent"
                type_name = _sanitize_text(str(args.get("TypeName") or "self"), 32)
                return True, role, type_name

    # 回退檢查第一筆 SYSTEM_MESSAGE 是否來自父代理
    if events:
        first = events[0]
        if first.get("source") == "SYSTEM" and first.get("type") == "SYSTEM_MESSAGE":
            content = str(first.get("content") or "")
            if "sender=" in content and "[Message]" in content:
                m_role = re.search(r"Role[:=]\s*([^\n,;]+)", content)
                role = _sanitize_text(m_role.group(1), 80) if m_role else "Subagent"
                return True, role, "self"

    return False, "", ""


def _read_transcript_events(transcript_path: Path) -> List[dict]:
    events: List[dict] = []
    try:
        raw_lines = transcript_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return events
    for ln in raw_lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                events.append(obj)
        except Exception:
            continue
    return events


def _is_working(events: List[dict], mtime: float, now: float, stale_seconds: int) -> bool:
    """判定子代理是否仍在工作中。

    結束條件：
    1. 最後一筆為 MODEL PLANNER_RESPONSE 且 status == DONE 且無未完成 tool_calls。
    2. 超過 stale_seconds（預設 120 秒）未更新。
    """
    if not events:
        return False
    age = max(0.0, now - mtime)
    if age > stale_seconds:
        return False

    last = events[-1]
    ev_type = last.get("type") or ""
    status = last.get("status") or ""
    tool_calls = last.get("tool_calls") or []

    if ev_type == "PLANNER_RESPONSE" and status == "DONE" and not tool_calls:
        return False

    return True


def _build_log_lines(events: List[dict], tail: int = 20) -> Tuple[List[str], int, str, str]:
    """從 transcript 事件組裝終端機與工具執行摘要 Log。

    回傳 (log_lines, tool_count, current_tool, current_step)。
    """
    lines: List[str] = []
    tool_count = 0
    current_tool = ""
    current_step = "初始化任務中…"

    for ev in events:
        ts = _format_hhmmss(ev.get("created_at"))
        ev_type = ev.get("type") or ""
        if ev_type == "PLANNER_RESPONSE":
            tool_calls = ev.get("tool_calls") or []
            for tc in tool_calls:
                tool_count += 1
                t_name = str(tc.get("name") or "tool")
                summary = _summarize_tool_call(t_name, tc.get("args"))
                current_tool = t_name
                current_step = summary
                lines.append(f"[{ts}] 🔧 {summary}")
            if not tool_calls and ev.get("content"):
                snippet = _sanitize_text(str(ev.get("content") or ""), 140)
                if snippet:
                    lines.append(f"[{ts}] 💬 {snippet}")
        elif ev_type == "GENERIC":
            content = str(ev.get("content") or "")
            if not content:
                continue
            # 擷取命令退出碼與精簡輸出行
            out_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
            filtered: List[str] = []
            for ln in out_lines:
                if ln.startswith(("Created At:", "Completed At:")):
                    continue
                filtered.append(ln)
            if not filtered:
                continue
            # 若為 Command exited with code N，優先保留狀態與最後幾行輸出
            for ln in filtered[-4:]:
                prefix = "  ↳ "
                if "Command exited with code 0" in ln:
                    prefix = "  ✅ "
                elif "Command exited with code" in ln or "error" in ln.lower() or "fail" in ln.lower():
                    prefix = "  ⚠️ "
                lines.append(f"[{ts}]{prefix}{_sanitize_text(ln, 180)}")

    if events and events[-1].get("type") == "GENERIC":
        current_step = f"分析工具結果（已完成 {tool_count} 次工具呼叫）"
    elif events and events[-1].get("type") == "SYSTEM_MESSAGE":
        current_step = "接收父代理指令，規劃步驟中…"

    if tail > 0 and len(lines) > tail:
        lines = lines[-tail:]
    return lines, tool_count, current_tool, current_step


def list_active_subagents(
    brain_dir: Optional[Path] = None,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    tail: int = 20,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """掃描並回傳目前正在工作中的 agy 子代理清單（依最近更新時間由新到舊排序）。"""
    root = brain_dir or DEFAULT_BRAIN_DIR
    if not root.is_dir():
        return []

    cur_now = now if now is not None else time.time()
    candidates: List[Tuple[float, Path, Path]] = []

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        transcript = entry / ".system_generated" / "logs" / "transcript.jsonl"
        if not transcript.is_file():
            continue
        try:
            mtime = transcript.stat().st_mtime
        except OSError:
            continue
        if (cur_now - mtime) <= stale_seconds:
            candidates.append((mtime, entry, transcript))

    candidates.sort(key=lambda item: item[0], reverse=True)
    active: List[Dict[str, Any]] = []

    for mtime, conv_dir, transcript in candidates:
        events = _read_transcript_events(transcript)
        if not _is_working(events, mtime, cur_now, stale_seconds):
            continue
        is_sub, role, type_name = _extract_subagent_meta(conv_dir, events)
        if not is_sub:
            continue

        started_ts = _parse_iso_ts(events[0].get("created_at")) or mtime
        elapsed = max(0, int(cur_now - started_ts))
        log_lines, tool_count, current_tool, current_step = _build_log_lines(events, tail=tail)
        updated_at = str(events[-1].get("created_at") or "")
        if not updated_at:
            updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

        active.append({
            "conversation_id": conv_dir.name,
            "role": role,
            "type_name": type_name,
            "status": "working",
            "started_at": str(events[0].get("created_at") or ""),
            "updated_at": updated_at,
            "elapsed_seconds": elapsed,
            "tool_count": tool_count,
            "current_tool": current_tool,
            "current_step": current_step,
            "logs": log_lines,
        })

    return active


def get_subagent_logs(
    conv_id: str,
    brain_dir: Optional[Path] = None,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    tail: int = 40,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """取得指定子代理的最新終端機／工具執行 Log 與狀態。"""
    if not conv_id or not _CONV_ID_RE.match(conv_id):
        return None
    root = brain_dir or DEFAULT_BRAIN_DIR
    conv_dir = root / conv_id
    transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript.is_file():
        return None

    cur_now = now if now is not None else time.time()
    try:
        mtime = transcript.stat().st_mtime
    except OSError:
        return None

    events = _read_transcript_events(transcript)
    if not events:
        return None

    _, role, type_name = _extract_subagent_meta(conv_dir, events)
    working = _is_working(events, mtime, cur_now, stale_seconds)
    started_ts = _parse_iso_ts(events[0].get("created_at")) or mtime
    elapsed = max(0, int(cur_now - started_ts))
    log_lines, tool_count, current_tool, current_step = _build_log_lines(events, tail=max(1, min(tail, 200)))

    return {
        "conversation_id": conv_id,
        "role": role or "Subagent",
        "type_name": type_name or "self",
        "status": "working" if working else "finished",
        "elapsed_seconds": elapsed,
        "tool_count": tool_count,
        "current_tool": current_tool,
        "current_step": current_step if working else "已完成任務",
        "logs": log_lines,
        "log_text": "\n".join(log_lines),
        "total_events": len(events),
    }
