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

        # 3P (Claude / GPT) 5h
        p3_5h = models.get("3p", {})
        p3_5h_pct = round(p3_5h.get("remaining_percentage", 100.0), 1)
        p3_5h_in = p3_5h.get("refreshes_in", "")

        # 3P Weekly 7d
        p3_w = weekly.get("3p", {})
        p3_w_pct = round(p3_w.get("remaining_percentage", 100.0), 1)
        p3_w_in = p3_w.get("refreshes_in", "")

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
            if "low_brain" in context_status and "med_brain" in context_status and "high_brain" in context_status:
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
