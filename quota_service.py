import json
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

QUOTA_HOOK_PATH = Path.home() / ".gemini" / "antigravity-cli" / "hooks" / "fetch-local-quota.mjs"
CACHE_PATH = Path.home() / ".gemini" / "tmp" / "real_quota_cache.json"
TZ_TAIPEI = timezone(timedelta(hours=8))

def _format_taipei_time(iso_str: Optional[str]) -> str:
    """將 ISO 時間格式轉為台北時間字串"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(TZ_TAIPEI)
        now = datetime.now(TZ_TAIPEI)
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ""

class QuotaService:
    """管理與查詢 Gemini 及 3P 模型的 5小時 / 7天 (Weekly) 額度配額"""

    def fetch_live_quota(self, force_refresh: bool = False) -> Dict[str, Any]:
        """獲取即時額度資料，若快取存在且未逾 30 秒則直接讀取，否則調用 Hook 更新"""
        cache_data = None
        now_ms = time.time() * 1000

        # 檢查快取有效性
        if CACHE_PATH.exists():
            try:
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
            except Exception:
                cache_data = None

        need_refresh = force_refresh or not cache_data
        if cache_data and not force_refresh:
            updated_at = cache_data.get("updatedAt", 0)
            # 若快取超過 30 秒則重新拉取
            if now_ms - updated_at > 30 * 1000:
                need_refresh = True

        if need_refresh and QUOTA_HOOK_PATH.exists():
            try:
                subprocess.run(
                    ["node", str(QUOTA_HOOK_PATH)],
                    capture_output=True,
                    timeout=4,
                    check=False
                )
                if CACHE_PATH.exists():
                    with open(CACHE_PATH, "r", encoding="utf-8") as f:
                        cache_data = json.load(f)
            except Exception as e:
                print(f"[QuotaService] ⚠️ 刷新 Hook 執行失敗: {e}")

        return cache_data or {}

    def get_quota_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """解析結構化的額度摘要"""
        raw = self.fetch_live_quota(force_refresh=force_refresh)
        models = raw.get("models", {})
        weekly = raw.get("weekly", {})

        # Gemini 5h
        g_5h = models.get("gemini", {})
        g_5h_pct = round(g_5h.get("remaining_percentage", 100.0), 1)
        g_5h_reset = g_5h.get("reset_time")
        g_5h_in = g_5h.get("refreshes_in", "")

        # Gemini Weekly 7d
        g_w = weekly.get("gemini", {})
        g_w_pct = round(g_w.get("remaining_percentage", 100.0), 1)
        g_w_reset = g_w.get("reset_time")
        g_w_in = g_w.get("refreshes_in", "")

        # Gemini 4 Argon
        argon_model = (
            models.get("gemini4argonmedium")
            or models.get("gemini4argonhigh")
            or models.get("gemini4argonlow")
            or next((v for k, v in models.items() if "argon" in k.lower()), {})
        )
        argon_weekly = (
            weekly.get("gemini-argon-wtu")
            or weekly.get("gemini4argonmedium")
            or next((v for k, v in weekly.items() if "argon" in k.lower()), {})
        )

        argon_pct = round(argon_model.get("remaining_percentage", argon_weekly.get("remaining_percentage", 100.0)), 1)
        argon_reset = argon_model.get("reset_time") or argon_weekly.get("reset_time")
        argon_in = argon_model.get("refreshes_in") or argon_weekly.get("refreshes_in", "")

        argon_w_pct = round(argon_weekly.get("remaining_percentage", 100.0), 1) if argon_weekly else argon_pct
        argon_w_reset = argon_weekly.get("reset_time") or argon_reset
        argon_w_in = argon_weekly.get("refreshes_in") or argon_in

        # Credits
        plan_status = raw.get("planStatus", {})
        plan_info = plan_status.get("planInfo", {})
        credits_data = {
            "available_prompt_credits": plan_status.get("availablePromptCredits", 0),
            "available_flow_credits": plan_status.get("availableFlowCredits", 0),
            "monthly_prompt_credits": plan_info.get("monthlyPromptCredits", 0),
            "monthly_flow_credits": plan_info.get("monthlyFlowCredits", 0),
            "plan_name": plan_info.get("planName", raw.get("planTier", "Pro")),
        }

        # Background tasks / Subagents
        tasks_data = {
            "running_count": 0,
            "tasks": [],
            "active_names": []
        }
        try:
            from task_manager import task_manager
            running_list = task_manager.list_tasks(include_finished=False)
            tasks_data = {
                "running_count": len(running_list),
                "tasks": [
                    {
                        "task_id": t.get("task_id"),
                        "name": t.get("name"),
                        "started_at": t.get("started_at"),
                        "pid": t.get("pid"),
                        "status": t.get("status", "RUNNING")
                    }
                    for t in running_list
                ],
                "active_names": [t.get("name") for t in running_list if t.get("name")]
            }
        except Exception:
            pass

        # 3P (Claude / GPT) 5h
        p3_5h = models.get("3p", {})
        p3_5h_pct = round(p3_5h.get("remaining_percentage", 100.0), 1)
        p3_5h_in = p3_5h.get("refreshes_in", "")

        # 3P Weekly 7d
        p3_w = weekly.get("3p", {})
        p3_w_pct = round(p3_w.get("remaining_percentage", 100.0), 1)
        p3_w_in = p3_w.get("refreshes_in", "")

        argon_dict = {
            "remaining_pct": argon_pct,
            "reset_time": argon_reset,
            "reset_time_taipei": _format_taipei_time(argon_reset),
            "refreshes_in": argon_in,
            "weekly_remaining_pct": argon_w_pct,
            "weekly_reset_time": argon_w_reset,
            "weekly_reset_time_taipei": _format_taipei_time(argon_w_reset),
            "weekly_refreshes_in": argon_w_in,
        }

        return {
            "email": raw.get("email", ""),
            "plan_tier": raw.get("planTier", ""),
            "updated_at": raw.get("updatedAt", 0),
            "gemini": {
                "rolling_5h": {
                    "remaining_pct": g_5h_pct,
                    "reset_time": g_5h_reset,
                    "reset_time_taipei": _format_taipei_time(g_5h_reset),
                    "refreshes_in": g_5h_in
                },
                "weekly_7d": {
                    "remaining_pct": g_w_pct,
                    "reset_time": g_w_reset,
                    "reset_time_taipei": _format_taipei_time(g_w_reset),
                    "refreshes_in": g_w_in
                }
            },
            "argon": argon_dict,
            "gemini_argon": argon_dict,
            "credits": credits_data,
            "background_tasks": tasks_data,
            "subagents": tasks_data,
            "third_party": {
                "rolling_5h": {
                    "remaining_pct": p3_5h_pct,
                    "refreshes_in": p3_5h_in
                },
                "weekly_7d": {
                    "remaining_pct": p3_w_pct,
                    "refreshes_in": p3_w_in
                }
            }
        }

    def format_telegram_message(self, context_status: Optional[Dict[str, Any]] = None) -> str:
        """生成 Telegram 專用富文本 HTML 訊息"""
        summary = self.get_quota_summary(force_refresh=True)
        g_5h = summary["gemini"]["rolling_5h"]
        g_w = summary["gemini"]["weekly_7d"]
        p3_5h = summary["third_party"]["rolling_5h"]
        p3_w = summary["third_party"]["weekly_7d"]

        def _bar(pct: float) -> str:
            filled = int(round(pct / 10))
            return "🟩" * filled + "⬜" * (10 - filled)

        lines = [
            "📊 <b>[助理系統狀態與 AI 配額監控]</b>\n"
        ]

        # 常駐進程與 Context 上下文狀態 (若提供)
        if context_status:
            status_dot = "🟢 就緒" if context_status.get("is_ready", True) else "🔴 未就緒"
            if context_status.get("unified_mode"):
                sid = (context_status.get("conversation_id") or "N/A")[:8]
                cur_model = context_status.get("current_model") or "gemini-3.8-flash-low"
                resumed_tag = "♻️ 已無損接續記憶" if context_status.get("resumed_from_disk") else "✨ 全新對話"
                title_str = f" ｜ 💬 {context_status['session_title']}" if context_status.get("session_title") else ""
                lines.append(f"🤖 <b>ACP 統一共享大腦</b>：{status_dot}")
                lines.append(f"• 🔗 <b>共享 Session</b>：<code>{sid}</code> ({resumed_tag}{title_str})")
                lines.append(f"• 🔀 <b>當前模型</b>：<code>{cur_model}</code> (支援 Low / Med / High 同 Session 無損秒切)")
            elif "low_brain" in context_status and "med_brain" in context_status and "high_brain" in context_status:
                lines.append(f"🤖 <b>三大腦進程池</b>：{status_dot}")
                lb = context_status["low_brain"]
                mb = context_status["med_brain"]
                hb = context_status["high_brain"]
                l_id = (lb.get('conversation_id') or 'N/A')[:8]
                m_id = (mb.get('conversation_id') or 'N/A')[:8]
                h_id = (hb.get('conversation_id') or 'N/A')[:8]
                lines.append(f"• ⚡ <b>Low (日常)</b>：<code>{lb['model']}</code> (<code>{l_id}</code> | {lb['percent']}%)")
                lines.append(f"• 🧠 <b>Med (推導)</b>：<code>{mb['model']}</code> (<code>{m_id}</code> | {mb['percent']}%)")
                lines.append(f"• 🔬 <b>High (深研)</b>：<code>{hb['model']}</code> (<code>{h_id}</code> | {hb['percent']}%)")
            elif "fast_brain" in context_status and "deep_brain" in context_status:
                lines.append(f"🤖 <b>雙大腦進程池</b>：{status_dot}")
                fb = context_status["fast_brain"]
                db = context_status["deep_brain"]
                f_id = (fb.get('conversation_id') or 'N/A')[:8]
                d_id = (db.get('conversation_id') or 'N/A')[:8]
                lines.append(f"• ⚡ <b>Fast (Low)</b>：<code>{fb['model']}</code> (<code>{f_id}</code> | {fb['percent']}%)")
                lines.append(f"• 🧠 <b>Deep (Med)</b>：<code>{db['model']}</code> (<code>{d_id}</code> | {db['percent']}%)")
            else:
                lines.append(f"• ID: <code>{context_status.get('conversation_id', 'unknown')}</code>")
            lines.append(f"🧠 <b>Context 上下文</b>：<b>{context_status.get('current_tokens', 0):,}</b> / {context_status.get('limit', 0):,} tokens (<b>{context_status.get('percent', 0):.1f}%</b>)")
            idle_thresh = context_status.get('idle_minutes_threshold', 10)
            if context_status.get("pending_handoff"):
                lines.append(f"• ⚠️ <b>交接排程</b>：已達重置門檻，待連續閒置 {idle_thresh} 分鐘後自動執行 Session Handoff\n")
            else:
                lines.append(f"• 重啟保護門檻：<b>{context_status.get('threshold', 0):.0%}</b> (超過且連續閒置 {idle_thresh} 分鐘自動 Session Handoff)\n")

        engine = (context_status.get("engine") if context_status else "")
        if not engine:
            try:
                from config import settings
                engine = getattr(settings, "AGENT_ENGINE", "agy")
            except Exception:
                engine = "agy"

        if engine.lower() != "claude":
            if summary.get("email") or summary.get("plan_tier"):
                account_info = summary.get("email", "")
                if summary.get("plan_tier"):
                    account_info += f" ({summary.get('plan_tier')})"
                lines.append(f"👤 <b>方案帳號</b>：<code>{account_info}</code>\n")

            # Gemini 配額
            lines.append("🔵 <b>Google Gemini 配額</b>")
            lines.append(f"• <b>5 小時區間 (Rolling 5h)</b>：<b>{g_5h['remaining_pct']}%</b> 剩餘")
            if g_5h.get("refreshes_in"):
                reset_hint = f" (約台北時間 {g_5h['reset_time_taipei']})" if g_5h.get("reset_time_taipei") else ""
                lines.append(f"  ⏳ 刷新倒數：<code>{g_5h['refreshes_in']}</code>{reset_hint}")
            lines.append(f"  {_bar(g_5h['remaining_pct'])}\n")

            lines.append(f"• <b>7 天週區間 (Weekly 7d)</b>：<b>{g_w['remaining_pct']}%</b> 剩餘")
            if g_w.get("refreshes_in"):
                reset_hint = f" (約台北時間 {g_w['reset_time_taipei']})" if g_w.get("reset_time_taipei") else ""
                lines.append(f"  ⏳ 刷新倒數：<code>{g_w['refreshes_in']}</code>{reset_hint}")
            lines.append(f"  {_bar(g_w['remaining_pct'])}\n")

            # Gemini 4 Argon
            argon = summary.get("argon", {})
            if argon.get("remaining_pct") is not None:
                lines.append("🧪 <b>Gemini 4 Argon (Pro 推理)</b>")
                lines.append(f"• <b>剩餘配額</b>：<b>{argon['remaining_pct']}%</b>")
                if argon.get("refreshes_in"):
                    reset_hint = f" (約台北時間 {argon['reset_time_taipei']})" if argon.get("reset_time_taipei") else ""
                    lines.append(f"  ⏳ 刷新倒數：<code>{argon['refreshes_in']}</code>{reset_hint}")
                lines.append(f"  {_bar(argon['remaining_pct'])}\n")

            # Prompt / Flow Credits
            credits = summary.get("credits", {})
            if credits.get("available_prompt_credits") or credits.get("available_flow_credits"):
                lines.append("💳 <b>Prompt / Flow Credits</b>")
                lines.append(f"• Prompt Credits：<b>{credits.get('available_prompt_credits', 0):,}</b> / {credits.get('monthly_prompt_credits', 0):,}")
                lines.append(f"• Flow Credits：<b>{credits.get('available_flow_credits', 0):,}</b> / {credits.get('monthly_flow_credits', 0):,}\n")

            # 背景常駐 / 子代理任務
            bg_tasks = summary.get("background_tasks", {})
            if bg_tasks.get("running_count", 0) > 0:
                lines.append(f"⚙️ <b>背景子代理 / 常駐任務 ({bg_tasks['running_count']} 個進行中)</b>")
                for t in bg_tasks.get("tasks", []):
                    lines.append(f"• 🟢 <code>{t.get('name', '未命名')}</code> (PID: {t.get('pid', 'N/A')}, 啟動: {t.get('started_at', '')})")
                lines.append("")

        # 第三方模型
        if p3_5h.get("remaining_pct") is not None or p3_w.get("remaining_pct") is not None:
            lines.append("🟣 <b>第三方模型 (Claude / GPT 3P)</b>")
            lines.append(f"• 5 小時區間：<b>{p3_5h['remaining_pct']}%</b> 剩餘 ({p3_5h.get('refreshes_in', '')})")
            lines.append(f"• 7 天週區間：<b>{p3_w['remaining_pct']}%</b> 剩餘 ({p3_w.get('refreshes_in', '')})\n")

        return "\n".join(lines)

    def format_voice_summary(self) -> str:
        """生成語音模式（Apple Watch TTS）專用的 1~2 句話極簡匯報"""
        summary = self.get_quota_summary()
        g_5h = summary["gemini"]["rolling_5h"]
        g_w = summary["gemini"]["weekly_7d"]
        return f"Gemini 5小時額度剩餘百分之{int(g_5h['remaining_pct'])}，7天週額度剩餘百分之{int(g_w['remaining_pct'])}，約在{g_5h['refreshes_in']}後重置。"

quota_service = QuotaService()
