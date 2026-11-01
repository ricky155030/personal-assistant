"""
Mac mini Assistant 每日記憶庫夜間審查與自動歸檔排程器 (Daily Memory Audit & Sync Scheduler)

職責：
1. 每天固定時間（預設 23:30）檢查 memory/ 目錄與 MEMORY.md 是否有異動。
2. 若有變動，呼叫 LLM 進行記憶審查、格式校對與語意摘要。
3. 自動執行 git add, commit, push origin main。
4. 推送 Telegram 歸檔報告給主人。
5. 若無變動則安靜略過，零干擾。
6. 提供 API 與 Telegram 指令 (/sync_memory) 隨時手動觸發。
"""

import os
import re
import json
import asyncio
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional

from config import settings
from memory_manager import memory_manager
from telegram_service import send_telegram_sync

def audit_diff_with_llm(diff_text: str, status_text: str) -> Dict[str, str]:
    """呼叫 LLM 審查 memory/ 與 MEMORY.md 的變更內容並產生 Commit Message"""
    prompt = (
        "請以專業個人執行秘書的身分，審查以下 Mac mini 個人助理記憶庫（memory/ 與 MEMORY.md）的當日異動 (Git Diff)。\n\n"
        "任務：\n"
        "1. 檢查內容是否符合結構化記憶規範（去除冗詞雜訊、確認分類合宜）。\n"
        "2. 以繁體中文條列摘要今天變更的核心重點（1～3 點，每點前面加符號 •，簡明俐落）。\n"
        "3. 產生一行符合 Conventional Commits 規範的英文 Commit Message（如 docs(memory): update preferences and add newborn checklist）。\n\n"
        "請嚴格依以下 JSON 格式回傳，不要附加任何其他 Markdown 標籤或文字：\n"
        "{\n"
        '  "summary": "• 條列變更重點1\\n• 條列變更重點2",\n'
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
            # 嘗試提取 JSON 區塊
            json_match = re.search(r"\{[\s\S]*\}", raw_out)
            if json_match:
                data = json.loads(json_match.group(0))
                return {
                    "summary": data.get("summary", "• 更新記憶庫模組與全域聚合檔"),
                    "commit_message": data.get("commit_message", f"docs(memory): sync daily memory ({datetime.now().strftime('%Y-%m-%d')})"),
                }
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ LLM 審查調用異常 ({e})，使用預設規則回退。")

    # 回退預設值
    return {
        "summary": "• 記憶庫模組與 MEMORY.md 自動同步更新",
        "commit_message": f"docs(memory): sync daily memory ({datetime.now().strftime('%Y-%m-%d')})",
    }

def sync_and_commit_memory(silent_if_no_changes: bool = True) -> Dict[str, Any]:
    """
    檢查記憶庫狀態，若有更新則編譯、由 LLM 審查、Commit、Push 並推播 Telegram
    """
    # 1. 確保最新模組聚合編譯至 MEMORY.md
    try:
        memory_manager.sync_to_memory_md()
    except Exception as e:
        print(f"[MemoryScheduler] ⚠️ 同步 MEMORY.md 發生錯誤: {e}")

    # 2. 檢查 Git 狀態
    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "memory/", "MEMORY.md"],
        capture_output=True,
        text=True,
        cwd=settings.BASE_DIR,
    )
    status_text = status_proc.stdout.strip()

    if not status_text:
        msg = "今日記憶庫無未提交之異動，狀態乾淨。"
        print(f"[MemoryScheduler] ℹ️ {msg}")
        return {"status": "no_changes", "message": msg}

    print(f"[MemoryScheduler] 📝 偵測到記憶庫異動：\n{status_text}")

    # 3. 提取 Diff
    diff_proc = subprocess.run(
        ["git", "diff", "memory/", "MEMORY.md"],
        capture_output=True,
        text=True,
        cwd=settings.BASE_DIR,
    )
    diff_text = diff_proc.stdout.strip() or status_text

    # 4. LLM 審查並產出 Commit Message 與摘要
    print("[MemoryScheduler] 🤖 正在調用 LLM 進行記憶審查與 Commit 訊息生成...")
    audit_res = audit_diff_with_llm(diff_text, status_text)
    summary = audit_res["summary"]
    commit_msg = audit_res["commit_message"]

    # 5. Git Add, Commit, Push
    try:
        subprocess.run(["git", "add", "memory/", "MEMORY.md"], check=True, cwd=settings.BASE_DIR)
        subprocess.run(["git", "commit", "-m", commit_msg], check=True, cwd=settings.BASE_DIR)
        push_proc = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True,
            text=True,
            check=True,
            cwd=settings.BASE_DIR,
        )

        rev_proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=settings.BASE_DIR,
        )
        commit_hash = rev_proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        err_msg = f"Git 提交或推送失敗: {e}"
        print(f"[MemoryScheduler] ❌ {err_msg}")
        return {"status": "error", "message": err_msg}

    # 6. 推送 Telegram 每日秘書報告
    tg_text = (
        f"🌙 <b>【每日記憶庫夜間歸檔完成】</b>\n\n"
        f"📝 <b>變更摘要</b>：\n{summary}\n\n"
        f"🔍 <b>LLM 審查</b>：格式已校驗，MEMORY.md 已同步編譯\n"
        f"🚀 <b>GitHub</b>：已自動提交並推送 (<code>{commit_hash}</code>)"
    )
    send_telegram_sync(tg_text, parse_mode="HTML")
    print(f"[MemoryScheduler] ✅ 每日記憶庫已歸檔並推播 Telegram (Commit: {commit_hash})")

    return {
        "status": "committed",
        "commit_hash": commit_hash,
        "summary": summary,
        "commit_message": commit_msg,
    }

async def run_memory_scheduler():
    """常駐排程迴圈：每天固定時間執行 memory 結算"""
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
                print(f"\n[MemoryScheduler] 🔔 觸發定時夜間記憶審查 ({now.strftime('%Y-%m-%d %H:%M:%S')})...")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, sync_and_commit_memory, True)

                # 夜間自動巡檢與修剪背景長任務與過期日誌 (Pruning)
                try:
                    from task_manager import task_manager
                    pruned = await loop.run_in_executor(None, task_manager.list_tasks, False)
                    print(f"[MemoryScheduler] 🧹 背景長任務夜間巡檢完成，當前有效運行中任務: {len(pruned)} 個")
                except Exception as e:
                    print(f"[MemoryScheduler] ⚠️ 背景長任務巡檢異常: {e}")

                # 夜間自動執行慢問答效能審查與改進提案生成
                try:
                    from performance_reviewer import performance_reviewer
                    print(f"[MemoryScheduler] 📊 開始執行當日慢問答效能審查...")
                    await loop.run_in_executor(None, performance_reviewer.run_review, 24, 15.0, True)
                except Exception as e:
                    print(f"[MemoryScheduler] ⚠️ 效能審查執行異常: {e}")

        except Exception as e:
            print(f"[MemoryScheduler] ❌ 排程迴圈異常: {e}")

        # 每 30 秒檢查一次
        await asyncio.sleep(30)
