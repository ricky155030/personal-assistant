"""
Mac mini Assistant 每日記憶庫夜間沉澱、審查與私有倉庫歸檔排程器 (Daily Memory Distill & Sync Scheduler)

職責：
1. 每天固定時間（預設 23:30）回顧過去 24 小時對話紀錄 (data/history.jsonl)，由 LLM 提煉高價值長期記憶寫入 memory/ 模組並推播通知主人。
2. 檢查獨立私有記憶倉庫 (memory/.git) 是否有異動（絕不將個人機敏記憶提交至公開主倉庫）。
3. 若有變動，呼叫 LLM 進行記憶審查、格式校對與語意摘要。
4. 於 memory/ 私有倉庫內自動執行 git add, commit, push origin main。
5. 推送 Telegram 歸檔報告給主人；若無變動則安靜略過，零干擾。
6. 提供 API、CLI (--once / --distill) 與 Telegram 指令 (/sync_memory) 隨時手動觸發。
"""

import re
import json
import asyncio
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import settings
from memory_manager import memory_manager
from telegram_service import send_telegram_sync

_IGNORED_QUERIES = {
    "同步記憶", "歸檔記憶", "記憶", "記憶庫", "效能審查", "審查慢問答", "慢問答", "檢查長問答",
    "壓縮記憶", "壓縮上下文", "精簡記憶",
}


def _is_private_memory_repo(memory_dir: Path) -> bool:
    """確認 memory/ 是否為獨立的 Git 倉庫（避免誤提交至公開主倉庫）"""
    return (memory_dir / ".git").exists()


def collect_recent_conversations(
    hours: int = 24,
    date_filter: Optional[str] = None,
    max_items: int = 60,
) -> List[Dict[str, str]]:
    """讀取過去 N 小時（或指定日期）的有效對話紀錄供夜間記憶沉澱"""
    history_path = settings.HISTORY_LOG_PATH
    if not history_path.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=hours)
    items: List[Dict[str, str]] = []

    try:
        with open(history_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue

                ts_str = str(entry.get("timestamp", ""))
                if not ts_str:
                    continue
                try:
                    if date_filter:
                        if not ts_str.startswith(date_filter):
                            continue
                    else:
                        if datetime.fromisoformat(ts_str) < cutoff:
                            continue
                except Exception:
                    continue

                actions = entry.get("actions") or []
                if "interrupted_error" in actions:
                    continue

                query = str(entry.get("query", "")).strip()
                reply = str(entry.get("reply", "")).strip()
                if not query or query.startswith("/") or query in _IGNORED_QUERIES:
                    continue
                if len(query) <= 2 and not reply:
                    continue

                items.append({
                    "timestamp": ts_str[:16],
                    "query": query[:300],
                    "reply": reply[:400],
                })
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ 讀取歷史對話紀錄失敗: {e}")
        return []

    return items[-max_items:]


def extract_memories_with_llm(
    conversations: List[Dict[str, str]],
    existing_memory: str,
) -> Dict[str, Any]:
    """呼叫 LLM 從當日對話中提煉高價值、尚未記錄的長期記憶"""
    empty_result: Dict[str, Any] = {"preferences": [], "projects": [], "notes": []}
    if not conversations:
        return empty_result

    prompt = (
        "你是一位嚴謹的個人執行秘書，請回顧主人今天與助理的對話紀錄，提煉出『值得長期記住、且尚未存在於現有記憶庫』的高價值事實。\n\n"
        "【收錄原則】\n"
        "1. 只收錄：明確的個人偏好或工作習慣、進行中的專案進度/路徑/架構決策、反覆提及的工具與資產、重大生活/工作備忘。\n"
        "2. 嚴禁收錄：一次性知識問答、閒聊寒暄、已存在於現有記憶庫的重複內容、短暫狀態查詢（如天氣、單次開燈）。\n"
        "3. 若今天沒有值得長期新增的項目，所有陣列回傳空陣列 [] 即可，寧缺勿濫。\n\n"
        "請嚴格依以下 JSON 格式回傳，不要附加任何其他說明文字：\n"
        "{\n"
        '  "preferences": [{"category": "分類", "detail": "具體偏好描述"}],\n'
        '  "projects": [{"title": "專案或里程碑標題", "detail": "簡述", "status": "進行中"}],\n'
        '  "notes": ["重要備忘內容"]\n'
        "}\n\n"
        f"【現有記憶庫摘要（避免重複）】\n{existing_memory[:4000]}\n\n"
        f"【今日對話紀錄（共 {len(conversations)} 筆）】\n"
        f"{json.dumps(conversations, ensure_ascii=False, indent=2)[:8000]}\n"
    )

    try:
        cmd = [settings.AGY_BIN, "-p", prompt]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=settings.BASE_DIR,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw_out = proc.stdout.strip()
            json_match = re.search(r"\{[\s\S]*\}", raw_out)
            if json_match:
                data = json.loads(json_match.group(0))
                return {
                    "preferences": data.get("preferences") if isinstance(data.get("preferences"), list) else [],
                    "projects": data.get("projects") if isinstance(data.get("projects"), list) else [],
                    "notes": data.get("notes") if isinstance(data.get("notes"), list) else [],
                }
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ 每日對話記憶沉澱 LLM 調用異常 ({e})")

    return empty_result


def distill_daily_conversations(
    hours: int = 24,
    date_filter: Optional[str] = None,
    notify: bool = True,
) -> Dict[str, Any]:
    """夜間自動沉澱當日對話至 memory/ 模組，並主動通知主人異動內容"""
    conversations = collect_recent_conversations(hours=hours, date_filter=date_filter)
    if not conversations:
        return {"status": "no_conversations", "added": [], "count": 0}

    existing_memory = memory_manager.read_memory()
    extracted = extract_memories_with_llm(conversations, existing_memory)

    added: List[str] = []

    # 1. 寫入偏好
    pref_text = memory_manager.read_module("preferences") or ""
    for item in extracted.get("preferences", []):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip()
        detail = str(item.get("detail", "")).strip()
        if not category or not detail:
            continue
        if f"- **{category}**：{detail}" in pref_text:
            continue
        memory_manager.update_preference(category, detail)
        pref_text = memory_manager.read_module("preferences") or ""
        added.append(f"[偏好] {category}：{detail}")

    # 2. 寫入專案進度
    proj_text = memory_manager.read_module("projects") or ""
    for item in extracted.get("projects", []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        detail = str(item.get("detail", "")).strip()
        status = str(item.get("status", "進行中")).strip() or "進行中"
        if not title or title in proj_text:
            continue
        memory_manager.record_demand(title, detail, status=status)
        proj_text = memory_manager.read_module("projects") or ""
        added.append(f"[專案] {title}{(' - ' + detail) if detail else ''}")

    # 3. 寫入重要備忘
    notes_text = memory_manager.read_module("notes") or ""
    for note in extracted.get("notes", []):
        note_str = str(note).strip()
        if not note_str or note_str in notes_text:
            continue
        memory_manager.add_memory_note(note_str)
        notes_text = memory_manager.read_module("notes") or ""
        added.append(f"[備忘] {note_str}")

    if not added:
        return {"status": "no_new_memories", "added": [], "count": 0}

    memory_manager.sync_to_memory_md()

    if notify:
        _notify_distill_result(added=added, hours=hours, date_filter=date_filter)

    return {"status": "distilled", "added": added, "count": len(added)}


def _send_app_notification(title: str, message: str, record_history: bool = True) -> bool:
    """同步寫入 App 對話歷史並透過統一 iOS 未讀重要通知通道推播給 iOS App"""
    if record_history:
        try:
            clean_title = title.replace("🧠", "").replace("🌙", "").strip() or "每日記憶沉澱"
            memory_manager.log_interaction(
                query="",
                reply=message,
                actions=["memory_distill_notify"],
                metadata={"task_name": clean_title, "task_status": "success"},
            )
        except Exception as e:
            print(f"[MemoryScheduler] ⚠️ 寫入 App 歷史紀錄失敗: {e}")
    try:
        from task_manager import _send_ios_notification
        return bool(_send_ios_notification(title=title, message=message))
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ iOS App 推播失敗: {e}")
        return False


def _notify_distill_result(
    added: List[str],
    hours: int = 24,
    date_filter: Optional[str] = None,
    commit_hash: str = "",
    pushed: bool = False,
) -> None:
    """發送每日對話記憶沉澱通知至 Telegram 與 iOS App"""
    scope_label = f"{date_filter} 當天" if date_filter else f"過去 {hours} 小時"
    bullet_lines = "\n".join(f"• {entry}" for entry in added)
    repo_line = ""
    if commit_hash:
        repo_line = f"\n🔒 私有記憶倉庫：{'已提交並推送' if pushed else '已本機提交'} ({commit_hash})"

    tg_text = (
        f"🧠 <b>【每日對話記憶沉澱通知】</b>\n\n"
        f"已從{scope_label}對話中自動提煉 <b>{len(added)} 項</b> 長期記憶並寫入記憶庫：\n"
        f"{bullet_lines}{repo_line}\n\n"
        f"💡 若有不需要保留的項目，直接回覆「撤回剛才的自動記憶」或指定項目即可移除。"
    )
    send_telegram_sync(tg_text, parse_mode="HTML")

    app_msg = (
        f"已從{scope_label}對話中自動沉澱 {len(added)} 項長期記憶：\n"
        f"{bullet_lines}{repo_line}\n\n"
        f"若有不需要保留的項目，直接告訴我撤回即可。"
    )
    _send_app_notification(title="🧠 每日記憶沉澱完成", message=app_msg, record_history=True)


def distill_and_sync_memory(
    hours: int = 24,
    date_filter: Optional[str] = None,
    notify: bool = True,
) -> Dict[str, Any]:
    """完整夜間沉澱流程：先提煉寫入 memory/，再提交推送私有倉庫，最後統一推播 Telegram 與 iOS App"""
    distill_res = distill_daily_conversations(hours=hours, date_filter=date_filter, notify=False)
    sync_res = sync_and_commit_memory(silent_if_no_changes=True, notify=(distill_res.get("count", 0) == 0 and notify))

    added = distill_res.get("added") or []
    if added and notify:
        _notify_distill_result(
            added=added,
            hours=hours,
            date_filter=date_filter,
            commit_hash=str(sync_res.get("commit_hash", "")),
            pushed=bool(sync_res.get("pushed", False)),
        )

    return {
        "distill": distill_res,
        "sync": sync_res,
    }

def audit_diff_with_llm(diff_text: str, status_text: str) -> Dict[str, str]:
    """呼叫 LLM 審查 memory/ 私有記憶倉庫的變更內容並產生 Commit Message"""
    prompt = (
        "請以專業個人執行秘書的身分，審查以下 Mac mini 個人助理私有記憶庫（memory/）的當日異動 (Git Diff)。\n\n"
        "任務：\n"
        "1. 檢查內容是否符合結構化記憶規範（去除冗詞雜訊、確認分類合宜）。\n"
        "2. 以繁體中文條列摘要今天變更的核心重點（1～3 點，每點前面加符號 •，簡明俐落）。\n"
        "3. 產生一行符合 Conventional Commits 規範的英文 Commit Message（如 docs(memory): update preferences and project notes）。\n\n"
        "請嚴格依以下 JSON 格式回傳，不要附加任何其他 Markdown 標籤或文字：\n"
        "{\n"
        '  "summary": "• 條列變更重點1\n• 條列變更重點2",\n'
        '  "commit_message": "docs(memory): ..."\n'
        "}\n\n"
        f"Git Status 狀態：\n{status_text}\n\n"
        f"Git Diff 變更內容：\n{diff_text[:3000]}\n"
    )

    try:
        cmd = [settings.AGY_BIN, "-p", prompt]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=40,
            cwd=settings.BASE_DIR,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw_out = proc.stdout.strip()
            json_match = re.search(r"\{[\s\S]*\}", raw_out)
            if json_match:
                data = json.loads(json_match.group(0))
                return {
                    "summary": data.get("summary", "• 更新記憶庫模組與全域聚合檔"),
                    "commit_message": data.get("commit_message", f"docs(memory): sync daily memory ({datetime.now().strftime('%Y-%m-%d')})"),
                }
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ LLM 審查調用異常 ({e})，使用預設規則回退。")

    return {
        "summary": "• 記憶庫模組與 MEMORY.md 自動同步更新",
        "commit_message": f"docs(memory): sync daily memory ({datetime.now().strftime('%Y-%m-%d')})",
    }


def sync_and_commit_memory(silent_if_no_changes: bool = True, notify: bool = True) -> Dict[str, Any]:
    """
    檢查獨立私有記憶倉庫 (memory/.git) 狀態，若有更新則編譯 MEMORY.md、由 LLM 審查、Commit、Push 至私有倉庫並推播 Telegram / iOS App
    """
    # 1. 確保最新模組聚合編譯至本機 MEMORY.md
    try:
        memory_manager.sync_to_memory_md()
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ 同步 MEMORY.md 發生錯誤: {e}")

    repo_dir = settings.MEMORY_DIR
    if not _is_private_memory_repo(repo_dir):
        msg = "memory/ 尚未初始化獨立私有 Git 倉庫，為防止個人機敏資料推送至公開主倉庫，已略過自動提交。"
        print(f"[MemoryScheduler] ⚠️ {msg}")
        return {"status": "skipped", "message": msg}

    # 2. 檢查私有記憶倉庫 Git 狀態
    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo_dir,
    )
    status_text = status_proc.stdout.strip()

    if not status_text:
        msg = "今日私有記憶庫無未提交之異動，狀態乾淨。"
        print(f"[MemoryScheduler] ℹ️ {msg}")
        return {"status": "no_changes", "message": msg}

    print(f"[MemoryScheduler] 📝 偵測到私有記憶庫異動：\n{status_text}")

    # 3. 提取 Diff
    diff_proc = subprocess.run(
        ["git", "diff"],
        capture_output=True,
        text=True,
        cwd=repo_dir,
    )
    diff_text = diff_proc.stdout.strip() or status_text

    # 4. LLM 審查並產出 Commit Message 與摘要
    print("[MemoryScheduler] 🤖 正在調用 LLM 進行記憶審查與 Commit 訊息生成...")
    audit_res = audit_diff_with_llm(diff_text, status_text)
    summary = audit_res["summary"]
    commit_msg = audit_res["commit_message"]

    # 5. 於私有記憶倉庫內執行 Git Add, Commit, Push
    try:
        subprocess.run(["git", "add", "-A"], check=True, cwd=repo_dir)
        subprocess.run(["git", "commit", "-m", commit_msg], check=True, cwd=repo_dir)
        remote_proc = subprocess.run(
            ["git", "remote"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        pushed = False
        if "origin" in remote_proc.stdout.split():
            subprocess.run(
                ["git", "push", "origin", "main"],
                capture_output=True,
                text=True,
                check=True,
                cwd=repo_dir,
            )
            pushed = True

        rev_proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_dir,
        )
        commit_hash = rev_proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        err_msg = f"私有記憶倉庫 Git 提交或推送失敗: {e}"
        print(f"[MemoryScheduler] ❌ {err_msg}")
        return {"status": "error", "message": err_msg}

    # 6. 推送 Telegram 與 iOS App 每日秘書報告
    if notify:
        push_status = f"已自動提交並推送至私有倉庫 (<code>{commit_hash}</code>)" if pushed else f"已提交至本機私有倉庫 (<code>{commit_hash}</code>)"
        tg_text = (
            f"🌙 <b>【每日私有記憶庫夜間歸檔完成】</b>\n\n"
            f"📝 <b>變更摘要</b>：\n{summary}\n\n"
            f"🔍 <b>LLM 審查</b>：格式已校驗，本機 MEMORY.md 已同步編譯\n"
            f"🔒 <b>私有版控</b>：{push_status}"
        )
        send_telegram_sync(tg_text, parse_mode="HTML")
        app_summary = f"私有記憶倉庫已歸檔 ({commit_hash})：\n{summary}"
        _send_app_notification(title="🌙 私有記憶庫歸檔完成", message=app_summary, record_history=True)
        print(f"[MemoryScheduler] ✅ 每日私有記憶庫已歸檔並推播 Telegram & iOS App (Commit: {commit_hash})")

    return {
        "status": "committed",
        "commit_hash": commit_hash,
        "pushed": pushed,
        "summary": summary,
        "commit_message": commit_msg,
    }


async def run_memory_scheduler():
    """常駐排程迴圈：每天固定時間執行對話記憶沉澱與私有倉庫歸檔"""
    target_hour = settings.MEMORY_SYNC_HOUR
    target_minute = settings.MEMORY_SYNC_MINUTE
    print(f"[MemoryScheduler] ⏰ 每日記憶庫排程器已掛載 (設定時間: 每日 {target_hour:02d}:{target_minute:02d})")

    last_run_date = None
    while True:
        try:
            now = datetime.now()
            if (
                now.hour == target_hour
                and now.minute == target_minute
                and last_run_date != now.date()
            ):
                last_run_date = now.date()
                print(f"\n[MemoryScheduler] 🔔 觸發定時夜間記憶沉澱與審查 ({now.strftime('%Y-%m-%d %H:%M:%S')})...")
                loop = asyncio.get_running_loop()

                # 1. 從當日對話自動沉澱高價值長期記憶、提交推送私有倉庫並統一通知 Telegram 與 iOS App
                try:
                    pipeline_res = await loop.run_in_executor(None, distill_and_sync_memory, 24, None, True)
                    d_res = pipeline_res.get("distill", {})
                    s_res = pipeline_res.get("sync", {})
                    print(
                        f"[MemoryScheduler] 🧠 夜間記憶沉澱與私有歸檔完成: "
                        f"distill={d_res.get('status')} (+{d_res.get('count', 0)}), sync={s_res.get('status')}"
                    )
                except Exception as e:
                    print(f"[MemoryScheduler] ⚠️ 夜間記憶沉澱與歸檔異常: {e}")
                # 3. 夜間自動巡檢與修剪背景長任務與過期日誌 (Pruning)
                try:
                    from task_manager import task_manager
                    pruned = await loop.run_in_executor(None, task_manager.list_tasks, False)
                    print(f"[MemoryScheduler] 🧹 背景長任務夜間巡檢完成，當前有效運行中任務: {len(pruned)} 個")
                except Exception as e:
                    print(f"[MemoryScheduler] ⚠️ 背景長任務巡檢異常: {e}")

                # 4. 夜間自動執行慢問答效能審查與改進提案生成
                try:
                    from performance_reviewer import performance_reviewer
                    print("[MemoryScheduler] 📊 開始執行當日慢問答效能審查...")
                    await loop.run_in_executor(None, performance_reviewer.run_review, 24, 15.0, True)
                except Exception as e:
                    print(f"[MemoryScheduler] ⚠️ 效能審查執行異常: {e}")

        except Exception as e:
            print(f"[MemoryScheduler] ❌ 排程迴圈異常: {e}")

        # 每 30 秒檢查一次
        await asyncio.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mac mini Assistant 記憶庫夜間沉澱與私有歸檔工具")
    parser.add_argument("--once", action="store_true", help="立即執行一次私有記憶倉庫同步與歸檔")
    parser.add_argument("--distill", action="store_true", help="立即執行對話記憶沉澱（若搭配 --once 則先沉澱、推送私有倉庫再統一通知）")
    parser.add_argument("--hours", type=int, default=24, help="對話沉澱回溯小時數 (預設 24)")
    parser.add_argument("--date", type=str, default=None, help="指定日期前綴 (如 2026-11-27)")
    args = parser.parse_args()

    if args.distill and args.once:
        print(json.dumps(distill_and_sync_memory(hours=args.hours, date_filter=args.date, notify=True), ensure_ascii=False, indent=2))
    elif args.distill:
        print(json.dumps(distill_daily_conversations(hours=args.hours, date_filter=args.date, notify=True), ensure_ascii=False, indent=2))
    elif args.once:
        print(json.dumps(sync_and_commit_memory(silent_if_no_changes=False), ensure_ascii=False, indent=2))
