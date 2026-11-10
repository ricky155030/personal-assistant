#!/usr/bin/env python3
"""
Facebook 社團新貼文智慧監控腳本 (FB Group Monitor)
結合 Chrome-Assistant (CDP) 真實瀏覽器 Session、本地去重快取與 agy -p Inline Prompt 語意判讀。

支援子指令：
1. login: 啟動 Chrome-Assistant 並開啟 Facebook 供主人登入保存 Session
2. scan:  單次抓取目標社團最新貼文（可選搭配 --criteria 立即由 agy -p 判讀）
3. watch: 持續背景輪詢監控（設計由 task_manager.py 託管，預設每 60 秒巡檢一次，無新文 0 Token 消耗）
4. alias: 管理常用社團別名（儲存於 data/fb_group_aliases.json）
"""

import os
import re
import sys
import json
import time
import random
import hashlib
import html
import argparse
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# 將專案根目錄與 browser-relay 加入 sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
BROWSER_RELAY_DIR = BASE_DIR / ".agents" / "skills" / "browser-relay" / "scripts"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BROWSER_RELAY_DIR) not in sys.path:
    sys.path.insert(0, str(BROWSER_RELAY_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from config import settings
from browser_relay import ChromeAssistantClient
from playwright.sync_api import sync_playwright, Page

try:
    from scripts.ios_notify import send_live_activity, send_critical_alert, send_imessage
except Exception:
    send_live_activity = None
    send_critical_alert = None
    send_imessage = None

SEEN_POSTS_PATH = settings.DATA_DIR / "fb_seen_posts.json"
ALIASES_PATH = settings.DATA_DIR / "fb_group_aliases.json"
MAX_SEEN_PER_GROUP = 500


# ============================================================================
# 1. 社團網址正規化與別名管理 (Group URL & Aliases)
# ============================================================================

def load_aliases() -> Dict[str, str]:
    if ALIASES_PATH.exists():
        try:
            with open(ALIASES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_aliases(aliases: Dict[str, str]) -> None:
    ALIASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALIASES_PATH, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)


def resolve_group_url(group_input: str, alias_name: Optional[str] = None) -> Tuple[str, str]:
    """
    將使用者傳入的社團別名、社團 ID 或完整網址，解析為：
    (group_key, chronological_url)
    強制附加 ?sorting_setting=CHRONOLOGICAL 以確保抓取「最新貼文」而非演算法推薦。
    """
    aliases = load_aliases()
    raw = (group_input or "").strip()

    if not raw or raw in ("default", "預設", "預設社團"):
        if "default" in aliases:
            raw = aliases["default"]
        else:
            raise ValueError("尚未設定預設 FB 社團網址，請先提供完整 FB 社團網址或設定 alias。")
    elif raw in aliases:
        raw = aliases[raw]

    # 若只給純數字或代號，補齊為 FB Groups 網址
    if not raw.startswith(("http://", "https://")):
        if "facebook.com" in raw:
            raw = "https://" + raw.lstrip("/")
        else:
            raw = f"https://www.facebook.com/groups/{raw.strip('/')}/"

    parsed = urllib.parse.urlparse(raw)
    m = re.search(r"/groups/([^/?#]+)", parsed.path)
    if not m:
        raise ValueError(f"無法從網址識別 Facebook 社團 ID 或代號: {raw}")

    group_key = m.group(1).strip()
    canonical_base = f"https://www.facebook.com/groups/{group_key}/"
    chronological_url = f"{canonical_base}?sorting_setting=CHRONOLOGICAL"

    # 自動將最近一次有效社團存為 default，若有指定 alias_name 也一併儲存
    updated = False
    if aliases.get("default") != canonical_base:
        aliases["default"] = canonical_base
        updated = True
    if alias_name and aliases.get(alias_name) != canonical_base:
        aliases[alias_name] = canonical_base
        updated = True
    if updated:
        save_aliases(aliases)

    return group_key, chronological_url


# ============================================================================
# 2. 本地已讀快取管理 (Local Deduplication Store)
# ============================================================================

def load_seen_store() -> Dict[str, Dict[str, Any]]:
    if SEEN_POSTS_PATH.exists():
        try:
            with open(SEEN_POSTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"[FBMonitor] ⚠️ 讀取已讀快取異常: {e}", file=sys.stderr)
    return {}


def save_seen_store(store: Dict[str, Dict[str, Any]]) -> None:
    SEEN_POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_POSTS_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def is_post_seen(group_store: Dict[str, Any], post: Dict[str, Any]) -> bool:
    seen_ids = set(group_store.get("seen_ids", []))
    seen_hashes = set(group_store.get("seen_hashes", []))
    pid = post.get("post_id", "")
    thash = post.get("text_hash", "")
    if pid and pid in seen_ids:
        return True
    if thash and thash in seen_hashes:
        return True
    return False


def mark_posts_seen(
    group_store: Dict[str, Any],
    posts: List[Dict[str, Any]],
    eval_map: Optional[Dict[str, Dict[str, Any]]] = None,
    is_baseline: bool = False,
    llm_elapsed_sec: float = 0.0
) -> None:
    seen_ids: List[str] = group_store.get("seen_ids", [])
    seen_hashes: List[str] = group_store.get("seen_hashes", [])
    recent_posts: List[Dict[str, Any]] = group_store.get("recent_posts", [])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 以反序走訪（讓最新貼文排在 recent_posts 最前面）
    for p in reversed(posts):
        pid = p.get("post_id", "")
        thash = p.get("text_hash", "")
        if pid and pid not in seen_ids:
            seen_ids.append(pid)
        if thash and thash not in seen_hashes:
            seen_hashes.append(thash)

        ev = (eval_map or {}).get(pid, {})
        if is_baseline:
            status_tag = "BASELINE (啟動前既有舊文)"
            reason_str = "首次啟動建立基準線，略過判讀"
            summary_str = ""
        else:
            matched = bool(ev.get("matched"))
            status_tag = "MATCHED (🎯命中)" if matched else "EXCLUDED (排除)"
            reason_str = ev.get("reason", "")
            summary_str = ev.get("summary", "")

        clean_preview = re.sub(r"\s+", " ", (p.get("text") or "").replace("\n", " ")).strip()[:160]
        record = {
            "checked_at": now_str,
            "post_id": pid,
            "author": p.get("author", "社團成員"),
            "posted_ago": p.get("posted_ago", ""),
            "status": status_tag,
            "summary": summary_str,
            "reason": reason_str,
            "text_preview": clean_preview,
            "image_count": p.get("image_count", 0),
            "permalink": p.get("permalink", "")
        }
        if p.get("created_at"):
            record["created_at"] = p.get("created_at")
        if p.get("created_timestamp"):
            record["created_timestamp"] = p.get("created_timestamp")
        if llm_elapsed_sec > 0 and not is_baseline:
            record["llm_eval_sec"] = round(llm_elapsed_sec, 1)

        # 避免重複寫入同一個 post_id
        recent_posts = [r for r in recent_posts if r.get("post_id") != pid]
        recent_posts.insert(0, record)

    group_store["seen_ids"] = seen_ids[-MAX_SEEN_PER_GROUP:]
    group_store["seen_hashes"] = seen_hashes[-MAX_SEEN_PER_GROUP:]
    group_store["recent_posts"] = recent_posts[:100]
    group_store["updated_at"] = now_str


def record_poll_summary(
    group_store: Dict[str, Any],
    cycle: int,
    scanned_count: int,
    new_count: int,
    matched_count: int,
    group_title: str = "",
    criteria: str = "",
    elapsed_sec: float = 0.0,
    llm_elapsed_sec: float = 0.0
) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if group_title:
        group_store["group_title"] = group_title
    if criteria:
        group_store["last_criteria"] = criteria
    group_store["last_poll_at"] = now_str
    polls: List[Dict[str, Any]] = group_store.get("recent_polls", [])
    poll_entry = {
        "time": now_str,
        "cycle": cycle,
        "scanned": scanned_count,
        "new_posts": new_count,
        "matched": matched_count
    }
    if elapsed_sec > 0:
        poll_entry["elapsed_sec"] = round(elapsed_sec, 1)
    if llm_elapsed_sec > 0:
        poll_entry["llm_elapsed_sec"] = round(llm_elapsed_sec, 1)
    polls.insert(0, poll_entry)
    group_store["recent_polls"] = polls[:30]


# ============================================================================
# 3. 推播通知與時效分析 (Notifications & Timing)
# ============================================================================

def compute_post_timing(post: Dict[str, Any], now_dt: Optional[datetime] = None) -> Tuple[str, str, str]:
    """
    計算貼文發布時間、當前通知發送時間與兩者之時差
    回傳: (created_display, sent_display, diff_display)
    例如: ("2026-10-29 13:59:30", "2026-10-29 14:01:15", "1 分 45 秒")
    """
    if now_dt is None:
        now_dt = datetime.now()
    sent_display = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    created_ts = post.get("created_timestamp")
    created_at = post.get("created_at")
    posted_ago = post.get("posted_ago")

    if created_ts:
        try:
            created_dt = datetime.fromtimestamp(created_ts)
            created_display = created_dt.strftime("%Y-%m-%d %H:%M:%S")
            diff_sec = max(0, int((now_dt - created_dt).total_seconds()))
            if diff_sec < 60:
                diff_display = f"{diff_sec} 秒"
            elif diff_sec < 3600:
                mins = diff_sec // 60
                secs = diff_sec % 60
                diff_display = f"{mins} 分 {secs} 秒" if secs > 0 else f"{mins} 分鐘"
            else:
                hrs = diff_sec // 3600
                mins = (diff_sec % 3600) // 60
                diff_display = f"{hrs} 小時 {mins} 分"
            return created_display, sent_display, diff_display
        except Exception:
            pass

    if created_at:
        created_display = created_at
    elif posted_ago:
        created_display = f"約 {posted_ago}"
    else:
        created_display = "近期"

    diff_display = posted_ago or "稍早"
    return created_display, sent_display, diff_display


def send_telegram_alert(html_text: str) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        print("[FBMonitor] ⚠️ 未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，跳過推播。", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[FBMonitor] ⚠️ Telegram HTML 推播失敗 ({e})，嘗試純文字降級發送...", file=sys.stderr)
        plain_text = re.sub(r"<[^>]+>", "", html_text)
        fallback_payload = json.dumps({
            "chat_id": chat_id,
            "text": html.unescape(plain_text)
        }).encode("utf-8")
        try:
            req2 = urllib.request.Request(url, data=fallback_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=10.0) as resp2:
                return resp2.status == 200
        except Exception as e2:
            print(f"[FBMonitor] ❌ Telegram 純文字推播亦失敗: {e2}", file=sys.stderr)
            return False


def send_live_activity_alert(
    group_title: str,
    post: Dict[str, Any],
    eval_item: Dict[str, Any],
    now_dt: Optional[datetime] = None
) -> bool:
    """發送 iOS 靜音置頂警報 (Critical Alert) 與 iMessage 通知"""
    try:
        permalink = post.get("permalink") or post.get("post_url") or ""
        summary = eval_item.get("summary") or (post.get("text") or "")[:70]
        author = post.get("author") or "成員"
        title = "🎁 FB 好物命中"
        created_str, sent_str, diff_str = compute_post_timing(post, now_dt)
        message = f"【{group_title[:10]}】{author}：{summary}"

        # 點擊直接以 Facebook App 原生協議精準定位貼文（不經由瀏覽器）
        open_url = "fb://feed"
        if permalink:
            m = re.search(r"/groups/(\d+)/posts/(\d+)", permalink)
            if m:
                open_url = f"fb://group?id={m.group(1)}&object_id={m.group(2)}&view=permalink"
            else:
                encoded_url = urllib.parse.quote(permalink, safe="")
                open_url = f"fb://facewebmodal/f?href={encoded_url}"

        # 發送 100% 靜音 Critical Alert (永遠置頂、無聲、不受配額限制、可手動滑掉)
        post_id = post.get("post_id") or ""
        post_tag = f"fb_{post_id}" if post_id else None

        if send_critical_alert:
            notify_devices_str = os.getenv("HA_NOTIFY_DEVICES", "")
            target_devices = [d.strip() for d in notify_devices_str.split(",") if d.strip()]
            if not target_devices:
                default_dev = os.getenv("HA_NOTIFY_DEVICE", "")
                if default_dev:
                    target_devices = [default_dev]
            for dev in target_devices:
                try:
                    send_critical_alert(
                        title=title,
                        message=message,
                        url=open_url,
                        volume=0.0,
                        device=dev,
                        tag=post_tag
                    )
                except Exception as dev_err:
                    print(f"[FBMonitor] ⚠️ 推播至 {dev} 失敗: {dev_err}", file=sys.stderr)

        # 同時發送 iMessage (附帶發文時間、發送時間與時差)
        if send_imessage:
            target_url = permalink or (f"https://www.facebook.com/{post_id}" if post_id else "https://www.facebook.com")
            timing_block = (
                f"🕒 發文時間：{created_str}\n"
                f"🚀 發送時間：{sent_str}（時差：{diff_str}）"
            )

            recipients_str = os.getenv("IMESSAGE_NOTIFY_RECIPIENTS", "")
            recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
            if not recipients:
                recipients = ["老婆", os.getenv("ASSISTANT_OWNER", "me")]

            for to_user in recipients:
                try:
                    if to_user in ("老婆", "太太"):
                        imsg_body = f"老婆～FB 社團【{group_title}】有新的好物/贈物貼文：\n\n📌 {summary}\n👤 發布者：{author}\n{timing_block}\n\n🔗 貼文網址：\n{target_url}"
                    else:
                        imsg_body = f"FB 社團【{group_title}】命中通知：\n\n📌 {summary}\n👤 發布者：{author}\n{timing_block}\n\n🔗 貼文網址：\n{target_url}"
                    imsg_ok = send_imessage(
                        message=imsg_body,
                        to=to_user,
                        title="🎁 FB 社團好物快報"
                    )
                    if imsg_ok:
                        print(f"[FBMonitor] 💬 已成功發送 iMessage 給 {to_user}", flush=True)
                    else:
                        print(f"[FBMonitor] ⚠️ iMessage 發送至 {to_user} 未成功", file=sys.stderr)
                except Exception as imsg_err:
                    print(f"[FBMonitor] ⚠️ 傳送 iMessage 給 {to_user} 失敗: {imsg_err}", file=sys.stderr)

        return True
    except Exception as e:
        print(f"[FBMonitor] ⚠️ 置頂推播失敗: {e}", file=sys.stderr)
        return False


# ============================================================================
# 4. Chrome-Assistant CDP 頁面操控與貼文擷取 (DOM Extractor)
# ============================================================================

FB_EXTRACT_POSTS_JS = r"""
(groupKey) => {
    // 1. 檢查是否處於未登入狀態 (Login Wall)
    const loginForm = document.querySelector('form#login_form, input[name="email"], input[name="pass"], input[type="password"]');
    const isLoginUrl = location.pathname.includes('/login');
    const bodyText = (document.body ? document.body.innerText : '').slice(0, 1500);
    const hasLoginKeywords = (bodyText.includes('忘記密碼') && bodyText.includes('建立新帳號')) || (bodyText.includes('Forgot password') && bodyText.includes('Create new account'));
    if (isLoginUrl || ((loginForm || hasLoginKeywords) && !document.querySelector('[role="feed"]'))) {
        return { status: "NOT_LOGGED_IN", url: location.href, title: document.title, posts: [] };
    }

    // 2. 在新版 Facebook Comet 架構中，[role="feed"] 的直接子元素 (children) 即為各篇貼文容器，
    //    而貼文內部的 [role="article"] 則是「留言/回覆」區塊！
    const feed = document.querySelector('[role="feed"]');
    let candidates = [];
    if (feed && feed.children.length > 0) {
        candidates = Array.from(feed.children);
    } else {
        const allArticles = Array.from(document.querySelectorAll('[role="article"]'));
        candidates = allArticles.filter(el => {
            const label = (el.getAttribute('aria-label') || '').toLowerCase();
            if (label.includes('留言') || label.includes('comment') || label.includes('回覆') || label.includes('reply')) {
                return false;
            }
            return !allArticles.some(other => other !== el && other.contains(el));
        });
    }

    const noiseLines = new Set([
        'facebook', '讚', '留言', '分享', '傳送', '查看更多', '顯示較少', '所有心情：',
        '社團動態消息排序方式', '新貼文', '最新動態', '最相關', '·', '追蹤', '已編輯', '回覆',
        'like', 'comment', 'share', 'send', 'see more', 'see less', 'write a comment…', '留個言吧⋯⋯'
    ]);

    const results = [];

    for (const node of candidates) {
        if (!node.innerText || node.innerText.trim().length < 15) continue;

        // 1. 提取貼文 ID (支援 /posts/ID, /permalink/ID, 以及相片 set=gm.ID / set=pcb.ID)
        let postId = "";
        const links = Array.from(node.querySelectorAll('a[href]'));
        for (const a of links) {
            const href = a.getAttribute('href') || a.href || "";
            let m = href.match(/\/(?:posts|permalink)\/(\d+)/);
            if (!m) m = href.match(/[?&]set=(?:gm|pcb)\.(\d+)/);
            if (!m) m = href.match(/multi_permalinks=(\d+)/);
            if (!m) m = href.match(/story_fbid=(\d+)/);
            if (m && m[1]) {
                postId = m[1];
                break;
            }
        }

        // 2. 提取作者名稱 (從非留言區的 /groups/.../user/ 連結或標題取得)
        let author = "";
        for (const a of links) {
            if (a.closest('[role="article"]')) continue;
            const href = a.getAttribute('href') || "";
            const aria = (a.getAttribute('aria-label') || "").trim();
            const txt = (a.innerText || "").replace(/\u034f/g, "").trim();
            if (href.includes('/user/')) {
                if (txt && txt !== 'Facebook' && txt.length < 40) {
                    author = txt.split('\n')[0].trim();
                    break;
                }
                if (aria && aria.length < 40) {
                    author = aria;
                }
            }
        }
        if (!author) {
            const headingEl = node.querySelector('h2, h3, h4, strong');
            if (headingEl && !headingEl.closest('[role="article"]') && headingEl.innerText) {
                const hTxt = headingEl.innerText.replace(/\u034f/g, '').split('\n')[0].trim();
                if (hTxt && hTxt !== 'Facebook') author = hTxt;
            }
        }

        // 3. 提取相片無障礙描述 (如「可能是靴子和文字的圖像」)
        const imgDescs = [];
        for (const a of links) {
            if (a.closest('[role="article"]')) continue;
            const aria = (a.getAttribute('aria-label') || '').trim();
            if (aria && (aria.includes('圖像') || aria.includes('相片') || aria.includes('image') || aria.includes('photo')) && !aria.includes('未提供')) {
                if (!imgDescs.includes(aria)) imgDescs.push(aria);
            }
        }
        node.querySelectorAll('img[alt]').forEach(img => {
            const alt = (img.getAttribute('alt') || '').trim();
            if (alt && alt.length > 3 && (alt.includes('圖像') || alt.includes('image')) && !imgDescs.includes(alt)) {
                imgDescs.push(alt);
            }
        });

        // 4. 直接從 Live DOM 節點讀取 innerText（不可使用 detached cloneNode，否則 Chrome 不會計算 CSS 隱藏屬性，會混入防爬亂碼）
        //    並先記錄所有留言區塊 ([role="article"]) 的首行文字以便截斷留言
        const commentFirstLines = new Set();
        node.querySelectorAll('[role="article"]').forEach(art => {
            const cLines = (art.innerText || '').replace(/\u034f/g, '').split('\n').map(s => s.trim()).filter(Boolean);
            for (const cl of cLines) {
                if (cl !== 'Facebook' && cl.length > 1) {
                    commentFirstLines.add(cl);
                    break;
                }
            }
        });

        const rawLines = (node.innerText || '')
            .replace(/\u034f/g, '')
            .split('\n')
            .map(l => l.trim())
            .filter(Boolean);

        const cleanLines = [];
        let relativeTime = "";

        for (const line of rawLines) {
            const lower = line.toLowerCase();
            if (noiseLines.has(lower)) continue;
            // 跳過單一英數字元混淆碎片 (FB 防爬時間軸亂碼如 d, o, p, t, 1, 5...)
            if (/^[a-zA-Z0-9]$/.test(line)) continue;
            if (line === '分' || line === '鐘' || line === '小' || line === '時' || line === 'AI 內容') continue;
            // 偵測相對發文時間
            if (!relativeTime && /^(?:約\s*|大約\s*)?\d+\s*(?:秒|分鐘|小時|天)(?:前)?$|^剛剛$/.test(line)) {
                relativeTime = line;
                continue;
            }
            // 略過與作者同名的首行
            if (author && line === author) continue;
            // 遇到底部留言框、互動統計、或第一則留言時立即停止，徹底排除留言區干擾
            if (
                commentFirstLines.has(line) ||
                /^以.*的身分留言/.test(line) ||
                /^所有心情[:：]?$/.test(line) ||
                /^查看更多留言/.test(line) ||
                /^查看另外\s*\d+\s*則留言/.test(line) ||
                /^\d+\s*則留言$/.test(line) ||
                /^撰寫公開留言/.test(line)
            ) {
                break;
            }
            cleanLines.push(line);
        }

        if (!author && cleanLines.length > 0 && cleanLines[0].includes('匿名')) {
            author = cleanLines.shift();
        }

        let finalText = cleanLines.join('\n').trim();
        if (imgDescs.length > 0) {
            finalText += (finalText ? '\n' : '') + `[附圖視覺提示: ${imgDescs.join(' / ')}]`;
        }

        if (!finalText) continue;
        if (finalText === '社團動態消息排序方式\n新貼文' || finalText === '新貼文') continue;

        const imgs = Array.from(node.querySelectorAll('img[src*="scontent"]'))
            .filter(img => !img.closest('[role="article"]') && ((img.width || 0) > 80 || (img.naturalWidth || 0) > 80));
        const imgUrls = imgs.map(img => img.src).filter(Boolean).slice(0, 2);

        const permalink = postId
            ? `https://www.facebook.com/groups/${groupKey}/posts/${postId}/`
            : `https://www.facebook.com/groups/${groupKey}/?sorting_setting=CHRONOLOGICAL`;

        results.push({
            post_id: postId,
            permalink: permalink,
            author: author || "社團成員",
            posted_ago: relativeTime || "近期",
            text: finalText.slice(0, 1500),
            image_count: imgs.length,
            image_urls: imgUrls
        });
    }

    return {
        status: "OK",
        url: location.href,
        title: document.title,
        posts: results
    };
}
"""

FB_EXPAND_SEE_MORE_JS = r"""
() => {
    let clicked = 0;
    const buttons = Array.from(document.querySelectorAll('div[role="button"], span[role="button"]'));
    for (const btn of buttons) {
        const t = (btn.innerText || '').replace(/\u034f/g, '').trim();
        if (t === '查看更多' || t === 'See more' || t === '顯示更多') {
            try {
                btn.click();
                clicked++;
            } catch (e) {}
        }
    }
    return clicked;
}
"""


def compute_text_hash(text: str) -> str:
    """將貼文核心文字正規化（移除空白、時間與標點）後計算 SHA-256 指紋"""
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"^(剛剛|\d+\s*(秒|分鐘|小時|天))", "", normalized)
    core = normalized[:140]
    return hashlib.sha256(core.encode("utf-8")).hexdigest()[:20]


def _create_persistent_cdp_tab(cdp_url: str, url: str) -> None:
    """透過 Chrome 原生 HTTP CDP 端點建立持久化分頁（不會隨 Playwright session 結束而自動關閉）"""
    encoded = urllib.parse.quote(url, safe=":/?&=#")
    req = urllib.request.Request(f"{cdp_url}/json/new?{encoded}", method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=5.0):
            pass
    except Exception:
        with urllib.request.urlopen(f"{cdp_url}/json/new?{encoded}", timeout=5.0):
            pass


def _cleanup_cdp_targets(cdp_url: str, domain_keywords: Tuple[str, ...] = ("facebook.com", "fbsbx.com")) -> None:
    """
    巡檢完畢後，透過 ChromeAssistantClient 確保保留至少 1 個空白分頁保活視窗，
    並精準關閉工作分頁與所有殘留之 shared_worker / iframe，再執行殭屍分頁 GC。
    """
    client = ChromeAssistantClient()
    client.cleanup_domain_targets(domain_keywords)
    client.gc_tabs(max_tabs=8)


def fetch_group_posts(group_key: str, chronological_url: str, max_posts: int = 8) -> Dict[str, Any]:
    """
    透過 Chrome-Assistant (Playwright CDP) 連線至目標社團頁面，
    在跨進程 CDP 互斥鎖保護下確保以 CHRONOLOGICAL 排序載入，並在分段捲動過程中收集最新貼文。
    嚴格隔離分頁所有權，絕不挪用非 Facebook / 非空白之前景分頁。
    """
    client = ChromeAssistantClient()
    client.ensure_chrome(headless=False)

    collected_posts: List[Dict[str, Any]] = []
    seen_keys = set()
    page_status = "OK"
    page_url = chronological_url
    page_title = group_key

    def merge_batch(batch_res: Dict[str, Any]) -> None:
        nonlocal page_status, page_url, page_title
        if batch_res.get("status") == "NOT_LOGGED_IN":
            page_status = "NOT_LOGGED_IN"
        page_url = batch_res.get("url") or page_url
        page_title = batch_res.get("title") or page_title
        for post in batch_res.get("posts", []):
            t_hash = compute_text_hash(post.get("text", ""))
            post["text_hash"] = t_hash
            if not post.get("post_id"):
                post["post_id"] = f"hash_{t_hash}"
            dedup_k = post["post_id"]
            if dedup_k not in seen_keys and t_hash not in seen_keys and len(collected_posts) < max_posts:
                seen_keys.add(dedup_k)
                seen_keys.add(t_hash)
                collected_posts.append(post)

    with client.session_lock(f"fb-monitor:{group_key}", timeout=45.0):
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(client.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            # [INFRA-02] 嚴格分頁隔離：僅重用已開啟的 facebook.com 分頁或空白保活分頁，絕不搶奪其他前景任務分頁
            target_page: Optional[Page] = None
            for page in context.pages:
                if f"/groups/{group_key}" in (page.url or ""):
                    target_page = page
                    break
            if target_page is None:
                for page in context.pages:
                    if "facebook.com" in (page.url or ""):
                        target_page = page
                        break
            if target_page is None:
                for page in context.pages:
                    if page.url in ("about:blank", "chrome://newtab/"):
                        target_page = page
                        break
            if target_page is None:
                client.create_persistent_tab(chronological_url)
                time.sleep(1.0)
                for page in context.pages:
                    if "facebook.com" in (page.url or ""):
                        target_page = page
                        break
                if target_page is None:
                    # 開啟全新獨立分頁，絕不劫持 context.pages[-1]
                    target_page = context.new_page()

            # 導向最新貼文排序網址
            try:
                target_page.goto(chronological_url, wait_until="domcontentloaded", timeout=35000)
            except Exception as e:
                print(f"[FBMonitor] ⚠️ 頁面載入超時或警告 ({e})，嘗試繼續解析 DOM...")

            # 等待 [role="feed"] 渲染出貼文節點
            try:
                target_page.wait_for_function(
                    "() => { const f = document.querySelector('[role=\"feed\"]'); return f && f.children.length >= 3; }",
                    timeout=8000
                )
            except Exception:
                time.sleep(2.0)

            target_page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.8)

            # 分段捲動 + 展開全文 + 即時提取（避免 FB 虛擬 DOM 把滑過的貼文清空）
            for step in range(4):
                try:
                    target_page.evaluate(FB_EXPAND_SEE_MORE_JS)
                    time.sleep(0.25)
                    batch = target_page.evaluate(FB_EXTRACT_POSTS_JS, group_key)
                    merge_batch(batch)
                    if page_status == "NOT_LOGGED_IN" or len(collected_posts) >= max_posts:
                        break
                    target_page.evaluate("window.scrollBy(0, 800)")
                    time.sleep(0.8)
                except Exception as e:
                    print(f"[FBMonitor] ⚠️ 第 {step+1} 段捲動擷取時發生小異常: {e}")

            # 提取 FB 原生精準發布時間 (creation_time / publish_time)
            try:
                post_times = target_page.evaluate(r"""() => {
                    const map = {};
                    const scripts = Array.from(document.querySelectorAll("script"));
                    for (const s of scripts) {
                        const txt = s.innerText || "";
                        if (!txt.includes("creation_time") && !txt.includes("publish_time")) continue;
                        const m1 = txt.matchAll(/"post_id":"(\d+)"[^{}]{0,200}?"(?:creation_time|publish_time)":(\d+)/g);
                        for (const m of m1) map[m[1]] = parseInt(m[2]);
                        const m2 = txt.matchAll(/"(?:creation_time|publish_time)":(\d+)[^{}]{0,200}?"post_id":"(\d+)"/g);
                        for (const m of m2) map[m[2]] = parseInt(m[1]);
                    }
                    return map;
                }""")
                if isinstance(post_times, dict):
                    for p in collected_posts:
                        pid = p.get("post_id")
                        if pid and pid in post_times:
                            ts = post_times[pid]
                            p["created_timestamp"] = ts
                            p["created_at"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                print(f"[FBMonitor] ⚠️ 提取貼文精確發布時間異常: {e}", file=sys.stderr)

        # 巡檢完畢後（若非等待主人登入狀態），立即關閉 FB 分頁與背景 Worker，確保待機期間 0% CPU
        if page_status != "NOT_LOGGED_IN":
            _cleanup_cdp_targets(client.cdp_url, ("facebook.com", "fbsbx.com"))

    return {
        "status": page_status,
        "url": page_url,
        "title": page_title,
        "posts": collected_posts
    }


# ============================================================================
# 5. agy -p Inline Prompt 語意＋視覺判讀器 (LLM Multimodal Evaluator)
# ============================================================================

def download_post_images(posts: List[Dict[str, Any]], tmp_dir: Path) -> List[Path]:
    """下載新貼文的附圖至 /tmp 暫存目錄供 agy 視覺辨識，回傳所有已下載之檔案路徑以便後續清理"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[Path] = []
    for p in posts:
        local_imgs: List[str] = []
        urls = p.get("image_urls") or []
        for idx, url in enumerate(urls[:2]):
            try:
                file_path = tmp_dir / f"fb_{p['post_id']}_{idx}.jpg"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    file_path.write_bytes(resp.read())
                local_imgs.append(str(file_path))
                downloaded.append(file_path)
            except Exception as e:
                print(f"[FBMonitor] ⚠️ 下載貼文附圖失敗 ({p.get('post_id')}): {e}", file=sys.stderr)
        p["local_images"] = local_imgs
    return downloaded


def evaluate_posts_with_agy(posts: List[Dict[str, Any]], criteria: str) -> Optional[List[Dict[str, Any]]]:
    """
    將新發現的貼文與附圖批次打包，透過 `agy -p` 進行無狀態語意＋視覺篩選。
    注意：工作目錄設為 /tmp 以避免載入專案巨型上下文，判讀完成後自動清除暫存圖片。
    """
    if not posts:
        return []

    tmp_img_dir = Path("/tmp/fb_monitor_imgs")
    downloaded_files = download_post_images(posts, tmp_img_dir)

    compact_posts = [
        {
            "post_id": p["post_id"],
            "author": p.get("author", ""),
            "text": p.get("text", "")[:900],
            "image_count": p.get("image_count", 0),
            "local_images": p.get("local_images", [])
        }
        for p in posts
    ]

    prompt = (
        "你是一個精準的 Facebook 社團貼文篩選器（支援文字與圖片視覺辨識）。\n"
        f"【主人想尋找的目標貼文條件】：\n{criteria}\n\n"
        "【待判讀的新貼文清單 (JSON)】：\n"
        f"{json.dumps(compact_posts, ensure_ascii=False, indent=2)}\n\n"
        "【重要判讀規則】：\n"
        "1. 若貼文內文已經明確寫出商品名稱與買賣/贈送性質，可直接依文字快速判斷。\n"
        "2. 若貼文文字極短或未寫明具體物品名稱（例如只寫「贈」、「贈送」、「清空間」等），且 `local_images` 有圖片路徑，**請務必呼叫 `view_file` 工具查看該圖片檔案**，辨識照片中的實際物品是否符合主人的目標條件！\n"
        "3. 請逐篇判斷並嚴格只回傳 JSON 陣列（不要加上任何 Markdown 語法或額外說明）：\n"
        "[\n"
        "  {\n"
        '    "post_id": "對應的 post_id",\n'
        '    "matched": true 或 false,\n'
        '    "summary": "若 matched 為 true，用 15~35 字精煉摘要貼文重點（含從文字或圖片辨識出的品名/尺寸/自取地點）；若 false 填空字串",\n'
        '    "reason": "簡短說明命中或排除的原因（含圖片辨識出的物品名稱，30字內）"\n'
        "  }\n"
        "]"
    )

    try:
        cmd = [settings.AGY_BIN, "--dangerously-skip-permissions", "-p", prompt]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            cwd="/tmp"
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print(f"[FBMonitor] ⚠️ agy -p 執行失敗 (code={proc.returncode}): {proc.stderr}", file=sys.stderr)
            return None

        raw_out = proc.stdout.strip()
        match = re.search(r"\[[\s\S]*\]", raw_out)
        if not match:
            print(f"[FBMonitor] ⚠️ 無法從 agy -p 輸出解析 JSON 陣列: {raw_out[:200]}", file=sys.stderr)
            return None

        parsed = json.loads(match.group(0))
        if isinstance(parsed, list):
            return parsed
    except Exception as e:
        print(f"[FBMonitor] ⚠️ 呼叫 agy -p 發生異常: {e}", file=sys.stderr)
    finally:
        for f in downloaded_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    return None


def format_match_notification(
    group_title: str,
    criteria: str,
    post: Dict[str, Any],
    eval_item: Dict[str, Any],
    now_dt: Optional[datetime] = None
) -> str:
    """產生結構化 Telegram HTML 通知卡片（附帶發文時間、通知發送時間與時差）"""
    summary = html.escape(eval_item.get("summary") or "發現符合條件之社團新貼文")
    reason = html.escape(eval_item.get("reason") or "符合指定篩選條件")
    author = html.escape(post.get("author") or "社團成員")
    snippet = html.escape((post.get("text") or "")[:350])
    if len(post.get("text") or "") > 350:
        snippet += "⋯⋯"
    permalink = html.escape(post.get("permalink") or "")
    safe_group = html.escape(group_title or "Facebook 社團")
    safe_criteria = html.escape(criteria)

    created_str, sent_str, diff_str = compute_post_timing(post, now_dt)

    lines = [
        f"🎯 <b>【FB 社團命中通知】{summary}</b>",
        f"🏠 <b>社團</b>：{safe_group}",
        f"👤 <b>作者</b>：{author}（附圖 {post.get('image_count', 0)} 張）",
        f"🕒 <b>發文時間</b>：{html.escape(created_str)}",
        f"🚀 <b>通知時間</b>：{html.escape(sent_str)}（時差：{html.escape(diff_str)}）",
        f"💡 <b>判讀原因</b>：{reason}",
        "",
        f"<blockquote>{snippet}</blockquote>",
        "",
        f"🔗 <a href=\"{permalink}\">👉 點此直接開啟 FB 貼文</a>",
        f"───────────────",
        f"🔍 <i>監控條件：{safe_criteria}</i>"
    ]
    return "\n".join(lines)


# ============================================================================
# 6. CLI 指令實作 (login / scan / watch / alias)
# ============================================================================

def auto_login_fb() -> bool:
    """自動從 .env 讀取 FB_USERNAME 與 FB_PASSWORD 嘗試自動補登"""
    fb_user = os.getenv("FB_USERNAME", "").strip()
    fb_pass = os.getenv("FB_PASSWORD", "").strip()
    if not fb_user or not fb_pass:
        return False
    try:
        client = ChromeAssistantClient()
        client.ensure_chrome(headless=False)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(client.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            target_page = None
            for page in context.pages:
                if "facebook.com" in (page.url or ""):
                    target_page = page
                    break
            if not target_page:
                return False

            email_input = target_page.locator('input[name="email"], input[placeholder*="電子郵件"], input[placeholder*="Email"]').first
            pass_input = target_page.locator('input[name="pass"], input[type="password"], input[placeholder*="密碼"]').first
            if email_input.is_visible(timeout=3000):
                email_input.fill(fb_user)
                time.sleep(0.3)
                pass_input.fill(fb_pass)
                time.sleep(0.3)
                pass_input.press("Enter")
                time.sleep(3.0)
                return True
    except Exception as e:
        print(f"[FBMonitor] ⚠️ 自動登入嘗試失敗: {e}", file=sys.stderr)
    return False


def cmd_login(target_url: Optional[str] = None) -> int:
    url = target_url or "https://www.facebook.com/"
    client = ChromeAssistantClient()
    client.ensure_chrome(headless=False)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(client.cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        target_page: Optional[Page] = None
        for page in context.pages:
            if "facebook.com" in (page.url or ""):
                target_page = page
                break
        if target_page is None:
            _create_persistent_cdp_tab(client.cdp_url, url)
            time.sleep(1.0)
            for page in context.pages:
                if "facebook.com" in (page.url or ""):
                    target_page = page
                    break
            if target_page is None:
                target_page = context.pages[-1]
        try:
            target_page.bring_to_front()
            if url != "https://www.facebook.com/" or "facebook.com" not in (target_page.url or ""):
                target_page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        detail = {"title": target_page.title(), "url": target_page.url}

    print(json.dumps({
        "status": "OK",
        "message": "已在 Chrome-Assistant 開啟並置頂 Facebook 分頁，請在 Mac mini 畫面上完成登入（Session 將永久保存）。",
        "detail": detail
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_scan(group_input: str, criteria: Optional[str], max_posts: int, notify: bool, alias_name: Optional[str]) -> int:
    group_key, chrono_url = resolve_group_url(group_input, alias_name=alias_name)
    print(f"[FBMonitor] 🔍 正在單次掃描社團 ({group_key}): {chrono_url}")
    data = fetch_group_posts(group_key, chrono_url, max_posts=max_posts)

    if data.get("status") == "NOT_LOGGED_IN":
        print(json.dumps({
            "status": "NOT_LOGGED_IN",
            "message": "Chrome-Assistant 尚未登入 Facebook，請先執行 login 指令並在瀏覽器中登入。",
            "url": data.get("url")
        }, ensure_ascii=False, indent=2))
        return 1

    posts = data.get("posts", [])
    group_title = data.get("title", group_key)

    if not criteria:
        print(json.dumps({
            "status": "OK",
            "group_key": group_key,
            "group_title": group_title,
            "post_count": len(posts),
            "posts": posts
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"[FBMonitor] 🤖 擷取到 {len(posts)} 篇貼文，正在透過 agy -p 判讀條件：「{criteria}」...")
    eval_results = evaluate_posts_with_agy(posts, criteria)
    if eval_results is None:
        print("[FBMonitor] ❌ agy -p 判讀失敗。", file=sys.stderr)
        return 2

    eval_map = {item.get("post_id"): item for item in eval_results if isinstance(item, dict)}
    matched_list = []

    for p in posts:
        ev = eval_map.get(p["post_id"], {})
        p["evaluation"] = ev
        if ev.get("matched"):
            matched_list.append((p, ev))
            if notify:
                now_notify = datetime.now()
                msg_html = format_match_notification(group_title, criteria, p, ev, now_dt=now_notify)
                send_telegram_alert(msg_html)
                send_live_activity_alert(group_title, p, ev, now_dt=now_notify)

    print(json.dumps({
        "status": "OK",
        "group_key": group_key,
        "group_title": group_title,
        "criteria": criteria,
        "total_scanned": len(posts),
        "matched_count": len(matched_list),
        "posts": posts
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_watch(
    group_input: str,
    criteria: str,
    interval_sec: int = 60,
    max_posts: int = 8,
    check_latest_on_start: int = 1,
    alias_name: Optional[str] = None
) -> int:
    group_key, chrono_url = resolve_group_url(group_input, alias_name=alias_name)
    print(f"=== 🚀 啟動 FB 社團智慧監控 ===")
    print(f"• 社團代號: {group_key}")
    print(f"• 監控網址: {chrono_url}")
    print(f"• 篩選條件: {criteria}")
    print(f"• 巡檢間隔: 每 {interval_sec} 秒巡檢一次")
    print("=" * 40, flush=True)

    login_alert_sent = False
    cycle = 0

    # 載入快取
    store = load_seen_store()
    group_store = store.setdefault(group_key, {"seen_ids": [], "seen_hashes": []})
    group_title = group_store.get("group_title") or group_key

    # 首次啟動：若為全新社團快取，先做一次基準線掃描，將畫面舊文登記為已讀
    is_first_baseline = group_key not in store or not store[group_key].get("seen_hashes")
    if is_first_baseline:
        print(f"[FBMonitor] 📌 首次啟動：執行首輪社團掃描以建立基準線...", flush=True)
        init_data = fetch_group_posts(group_key, chrono_url, max_posts=max_posts)
        if init_data.get("posts"):
            b_posts = init_data["posts"]
            mark_posts_seen(group_store, b_posts, is_baseline=True)
            save_seen_store(store)
            group_title = init_data.get("title") or group_title
            print(f"[FBMonitor] ✅ 已將 {len(b_posts)} 篇既有貼文寫入基準線快取。", flush=True)

    while True:
        cycle += 1
        now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        t_start = time.time()

        try:
            data = fetch_group_posts(group_key, chrono_url, max_posts=max_posts)
            status = data.get("status")
            if status == "NOT_LOGGED_IN":
                if not login_alert_sent:
                    attempted = auto_login_fb()
                    if attempted:
                        send_telegram_alert(
                            "🔐 <b>【FB 社團監控】自動登入已觸發</b>\n"
                            "偵測到 Facebook 登入過期，系統已自動從 .env 填入帳號密碼並送出！\n"
                            "若您的 Facebook 有啟用雙重驗證 (2FA)，請在手機或 Mac 上點選<b>「核可登入」</b>即可恢復巡檢。"
                        )
                    else:
                        send_telegram_alert(
                            "⚠️ <b>【FB 社團監控提醒】</b>\n"
                            "偵測到 <code>Chrome-Assistant</code> 尚未登入 Facebook 或 Session 已過期。\n"
                            "請在 Mac mini 的 Chrome-Assistant 視窗登入一次，監控將自動恢復運作！"
                        )
                    login_alert_sent = True
            else:
                login_alert_sent = False
                posts = data.get("posts", [])
                group_title = data.get("title") or group_title
                unseen_posts = [p for p in posts if not is_post_seen(group_store, p)]
                elapsed = time.time() - t_start

                if not unseen_posts:
                    print(
                        f"[{now_str}] 🔄 [第 {cycle} 輪] 首頁共 {len(posts)} 篇貼文，無新貼文（耗時 {elapsed:.1f}s，0 Token 待機中）。",
                        flush=True
                    )
                    record_poll_summary(
                        group_store, cycle, len(posts), 0, 0,
                        group_title, criteria, elapsed_sec=elapsed
                    )
                    save_seen_store(store)
                else:
                    print(
                        f"[{now_str}] 🌟 [第 {cycle} 輪] 發現 {len(unseen_posts)} 篇新貼文！呼叫 agy -p 進行視覺＋語意審核...",
                        flush=True
                    )
                    t_llm_start = time.time()
                    eval_results = evaluate_posts_with_agy(unseen_posts, criteria)
                    llm_time = time.time() - t_llm_start

                    eval_map = {}
                    if eval_results is not None:
                        eval_map = {item.get("post_id"): item for item in eval_results if isinstance(item, dict)}

                    matched_count = 0
                    for p in unseen_posts:
                        ev = eval_map.get(p["post_id"], {})
                        is_match = bool(ev.get("matched"))
                        reason = ev.get("reason", "")
                        print(
                            f"  -> 貼文 [{p['post_id']}] 作者:{p.get('author')} | 命中:{is_match} | 原因:{reason}",
                            flush=True
                        )
                        if is_match:
                            matched_count += 1
                            now_notify = datetime.now()
                            msg_html = format_match_notification(group_title, criteria, p, ev, now_dt=now_notify)
                            send_telegram_alert(msg_html)
                            send_live_activity_alert(group_title, p, ev, now_dt=now_notify)

                    total_elapsed = time.time() - t_start
                    mark_posts_seen(group_store, unseen_posts, eval_map=eval_map, is_baseline=False, llm_elapsed_sec=llm_time)
                    record_poll_summary(
                        group_store, cycle, len(posts), len(unseen_posts), matched_count,
                        group_title, criteria, elapsed_sec=total_elapsed, llm_elapsed_sec=llm_time
                    )
                    save_seen_store(store)
                    print(
                        f"[{now_str}] 🎯 [第 {cycle} 輪] 判讀完成：共命中 {matched_count}/{len(unseen_posts)} 篇（總耗時 {total_elapsed:.1f}s）。",
                        flush=True
                    )
        except Exception as e:
            print(f"[{now_str}] ⚠️ [第 {cycle} 輪] 巡檢異常: {e}", file=sys.stderr, flush=True)

        # 擬真抖動睡眠 (預設 60 秒 ± 15%)
        jitter = random.uniform(-0.15, 0.15) * interval_sec
        sleep_sec = max(10, int(interval_sec + jitter))
        time.sleep(sleep_sec)


def format_time_ago(time_str: str) -> str:
    """計算時間字串距今多久（口語化顯示）"""
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        diff_sec = max(0, int((datetime.now() - dt).total_seconds()))
        if diff_sec < 60:
            return f"{diff_sec} 秒前"
        elif diff_sec < 3600:
            return f"{diff_sec // 60} 分鐘前"
        elif diff_sec < 86400:
            return f"{diff_sec // 3600} 小時前"
        else:
            return f"{diff_sec // 86400} 天前"
    except Exception:
        return time_str


def format_duration(started_at_str: str) -> str:
    """計算運行時長（口語化顯示）"""
    try:
        dt = datetime.strptime(started_at_str, "%Y-%m-%d %H:%M:%S")
        diff_sec = max(0, int((datetime.now() - dt).total_seconds()))
        if diff_sec < 60:
            return f"{diff_sec} 秒"
        elif diff_sec < 3600:
            mins = diff_sec // 60
            secs = diff_sec % 60
            return f"{mins} 分 {secs} 秒" if secs > 0 else f"{mins} 分鐘"
        else:
            hrs = diff_sec // 3600
            mins = (diff_sec % 3600) // 60
            return f"{hrs} 小時 {mins} 分"
    except Exception:
        return started_at_str


def get_active_monitor_task() -> Optional[Dict[str, Any]]:
    """檢查 TaskManager 是否有執行中的 FB 監控背景任務"""
    tasks_path = settings.DATA_DIR / "background_tasks.json"
    if not tasks_path.exists():
        return None
    try:
        with open(tasks_path, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        for t in tasks:
            if "fb_group_monitor" in t.get("script", "") and t.get("status") == "RUNNING":
                pid = t.get("pid", 0)
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        return t
                    except OSError:
                        continue
    except Exception:
        pass
    return None


def print_recent_summary(group_input: str = "default", limit: int = 5, as_json: bool = False) -> int:
    """輸出近期巡檢輪次統計與處理過的貼文清單（支援精美終端格式與機器可讀 JSON）"""
    group_key, chrono_url = resolve_group_url(group_input)
    store = load_seen_store()
    group_store = store.get(group_key, {})
    recent_polls = group_store.get("recent_polls", [])[:8]
    recent_posts = group_store.get("recent_posts", [])[:limit]
    active_task = get_active_monitor_task()

    group_title = group_store.get("group_title") or group_key
    last_criteria = group_store.get("last_criteria") or "（尚未記錄條件）"
    last_poll_at = group_store.get("last_poll_at") or group_store.get("updated_at") or "無紀錄"
    last_poll_ago = format_time_ago(last_poll_at) if last_poll_at != "無紀錄" else "無紀錄"
    total_seen = len(group_store.get("seen_ids", []))

    if as_json:
        data = {
            "status": "OK",
            "group_key": group_key,
            "group_title": group_title,
            "group_url": chrono_url,
            "daemon": {
                "is_running": active_task is not None,
                "task_id": active_task.get("task_id") if active_task else None,
                "pid": active_task.get("pid") if active_task else None,
                "started_at": active_task.get("started_at") if active_task else None,
                "uptime": format_duration(active_task["started_at"]) if active_task and active_task.get("started_at") else None
            },
            "last_criteria": last_criteria,
            "last_poll_at": last_poll_at,
            "last_poll_ago": last_poll_ago,
            "total_seen_posts": total_seen,
            "recent_polls": recent_polls,
            "recent_posts_count": len(recent_posts),
            "recent_posts": recent_posts
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    # 格式化輸出精美終端報告
    print("=" * 72)
    print("📊 【FB 社團監控】即時狀態與近期巡檢總覽")
    print("=" * 72)
    print(f"🏠 監控社團: {group_title} (ID: {group_key})")
    print(f"🔗 社團網址: {chrono_url}")

    if active_task:
        uptime_str = format_duration(active_task.get("started_at", ""))
        print(f"⚡ 守護進程: 🟢 運行中 (Task ID: {active_task.get('task_id')}, PID: {active_task.get('pid')}, 已持續運作: {uptime_str})")
    else:
        print("⚡ 守護進程: ⚪ 未在背景運行 (可使用 watch 指令由 task_manager 託管啟動)")

    print(f"⏱️ 巡檢頻率: 每 60 秒巡檢一次 (上次巡檢: {last_poll_at}，約 {last_poll_ago})")
    print(f"📦 快取庫存: 累計追蹤 {total_seen} 篇貼文指紋 (0 Token 舊文秒濾)")
    print(f"🎯 監控條件:")
    for line in last_criteria.strip().split("\n"):
        print(f"   {line}")

    # 最近巡檢輪次
    print("\n" + "-" * 72)
    print("🔄 最近巡檢輪次紀錄 (Recent Poll Cycles)")
    print("-" * 72)
    if not recent_polls:
        print("   （尚無巡檢輪次紀錄）")
    else:
        print(f" {'輪次':<6} | {'巡檢時間':<19} | {'掃描':<5} | {'新文':<5} | {'🎯命中':<6} | {'耗時'}")
        print(" " + "-" * 68)
        for p in recent_polls:
            cyc = f"#{p.get('cycle', '-')}"
            ptime = p.get('time', '-')
            scan = f"{p.get('scanned', 0)} 篇"
            newp = f"{p.get('new_posts', 0)} 篇"
            match = f"{p.get('matched', 0)} 篇"
            elap = f"{p.get('elapsed_sec', 0.0):.1f}s"
            if p.get('new_posts', 0) == 0:
                elap += " (0 Token)"
            print(f" {cyc:<6} | {ptime:<19} | {scan:<5} | {newp:<5} | {match:<6} | {elap}")

    # 最近處理貼文
    print("\n" + "-" * 72)
    print(f"📝 最近處理貼文清單 (Recent Posts - 顯示最近 {len(recent_posts)} 篇)")
    print("-" * 72)
    if not recent_posts:
        print("   （尚無貼文處理紀錄）")
    else:
        for idx, post in enumerate(recent_posts, 1):
            status = post.get("status", "")
            if "MATCHED" in status:
                status_icon = "🎯 【MATCHED 命中】"
            elif "BASELINE" in status:
                status_icon = "📌 【BASELINE 舊文】"
            else:
                status_icon = "🚫 【EXCLUDED 排除】"

            author = post.get("author", "未知作者")
            checked_at = post.get("checked_at", "")
            created_at = post.get("created_at") or post.get("posted_ago") or "未知"
            eval_sec = f", 判讀耗時: {post['llm_eval_sec']}s" if "llm_eval_sec" in post else ""
            img_info = f" (附圖 {post.get('image_count', 0)} 張)" if post.get("image_count", 0) > 0 else ""

            print(f"[{idx}] {status_icon} {author}{img_info}")
            print(f"    • 發文時間: {created_at} | 巡檢時間: {checked_at}{eval_sec}")

            summary = post.get("summary")
            reason = post.get("reason")
            if summary:
                print(f"    • 🎯 命中摘要: {summary}")
            if reason:
                print(f"    • 💡 判定原因: {reason}")

            preview = post.get("text_preview", "").strip()
            if preview:
                if len(preview) > 90:
                    preview = preview[:90] + "..."
                print(f"    • 內文預覽: {preview}")

            permalink = post.get("permalink", "")
            if permalink:
                print(f"    • 貼文連結: {permalink}")
            print()

    print("=" * 72)
    return 0


def cmd_history(group_input: str, limit: int = 10, as_json: bool = False) -> int:
    return print_recent_summary(group_input, limit=limit, as_json=as_json)


def main() -> int:
    # 支援簡寫位置參數：若第一個參數不是子指令名稱，自動視為 watch 模式 (group_url criteria [interval])
    known_subcommands = {"login", "scan", "watch", "history", "status", "recent", "alias", "-h", "--help"}
    if len(sys.argv) >= 3 and sys.argv[1] not in known_subcommands and not sys.argv[1].startswith("-"):
        group_arg = sys.argv[1]
        criteria_arg = sys.argv[2]
        interval_arg = int(sys.argv[3]) if len(sys.argv) >= 4 and sys.argv[3].isdigit() else 60
        return cmd_watch(group_arg, criteria_arg, interval_sec=interval_arg)

    parser = argparse.ArgumentParser(description="Facebook 社團新貼文智慧監控工具")

    # 頂層快捷參數 (Top-level flags)
    parser.add_argument("--recent", "-r", nargs="?", const=5, type=int, default=None, help="快速查看近期巡檢結果與貼文清單（預設 5 篇，可指定篇數如 -r 10）")
    parser.add_argument("--status", "-s", action="store_true", help="快速查看當前監控守護狀態與最近巡檢摘要")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀之 JSON 格式（配合 --recent / --status / history 使用）")
    parser.add_argument("--group", default="default", help="社團網址、ID 或別名 (預設 default)")
    parser.add_argument("--limit", type=int, default=None, help="顯示貼文篇數上限")

    subparsers = parser.add_subparsers(dest="command", required=False)

    # 1. login
    sp_login = subparsers.add_parser("login", help="開啟 Chrome-Assistant 的 Facebook 畫面以供登入")
    sp_login.add_argument("--url", default="https://www.facebook.com/", help="開啟之網址")

    # 2. scan
    sp_scan = subparsers.add_parser("scan", help="單次抓取目標社團最新貼文並可選判讀")
    sp_scan.add_argument("--group", default="default", help="社團網址、ID 或別名 (預設讀取 default)")
    sp_scan.add_argument("--criteria", default=None, help="選填：若提供則呼叫 agy -p 篩選")
    sp_scan.add_argument("--max-posts", type=int, default=8, help="抓取最新貼文數量上限 (預設 8)")
    sp_scan.add_argument("--notify", action="store_true", help="若有命中是否發送 Telegram 通知")
    sp_scan.add_argument("--alias", default=None, help="選填：順便將此社團儲存為指定別名")

    # 3. watch
    sp_watch = subparsers.add_parser("watch", help="持續背景輪詢監控 (由 task_manager.py 託管)")
    sp_watch.add_argument("--group", default="default", help="社團網址、ID 或別名 (預設讀取 default)")
    sp_watch.add_argument("--criteria", required=True, help="你想尋找的貼文類型與條件 (自然語言)")
    sp_watch.add_argument("--interval", type=int, default=60, help="社團最新貼文巡檢間隔秒數 (預設 60 秒 = 1 分鐘)")
    sp_watch.add_argument("--max-posts", type=int, default=8, help="每次檢查最新幾篇貼文 (預設 8)")
    sp_watch.add_argument("--check-latest-on-start", type=int, default=1, help="首次無快取啟動時，測試判讀最新幾篇 (預設 1)")
    sp_watch.add_argument("--alias", default=None, help="選填：順便將此社團儲存為指定別名")

    # 4. status
    sp_status = subparsers.add_parser("status", help="快速查看當前監控守護狀態與最近巡檢摘要")
    sp_status.add_argument("--group", default="default", help="社團網址、ID 或別名 (預設 default)")
    sp_status.add_argument("--limit", type=int, default=5, help="顯示最近幾篇貼文 (預設 5)")
    sp_status.add_argument("--json", action="store_true", help="輸出 JSON 格式")

    # 5. history / recent
    sp_history = subparsers.add_parser("history", aliases=["recent"], help="查詢近期巡檢輪次紀錄與巡檢過的貼文內容")
    sp_history.add_argument("--group", default="default", help="社團網址、ID 或別名 (預設 default)")
    sp_history.add_argument("--limit", type=int, default=10, help="顯示最近幾篇巡檢過的貼文 (預設 10)")
    sp_history.add_argument("--json", action="store_true", help="輸出 JSON 格式")

    # 6. alias
    sp_alias = subparsers.add_parser("alias", help="管理常用 FB 社團別名")
    sp_alias.add_argument("action", choices=["list", "set"], help="list 或 set")
    sp_alias.add_argument("--name", default="default", help="別名名稱 (如: 預設、恩典牌、台中二手)")
    sp_alias.add_argument("--url", default=None, help="社團網址")

    args = parser.parse_args()

    # 頂層快捷參數優先判斷 (--recent / --status 或未給任何子指令)
    if args.status or args.recent is not None or args.command is None:
        effective_limit = 5
        if args.limit is not None:
            effective_limit = args.limit
        elif args.recent is not None:
            effective_limit = args.recent
        return print_recent_summary(args.group, limit=effective_limit, as_json=args.json)

    if args.command == "login":
        return cmd_login(args.url)
    elif args.command == "scan":
        return cmd_scan(args.group, args.criteria, args.max_posts, args.notify, args.alias)
    elif args.command == "watch":
        return cmd_watch(
            args.group,
            args.criteria,
            interval_sec=args.interval,
            max_posts=args.max_posts,
            check_latest_on_start=args.check_latest_on_start,
            alias_name=args.alias
        )
    elif args.command in ("history", "recent"):
        return cmd_history(args.group, limit=args.limit, as_json=args.json)
    elif args.command == "status":
        return print_recent_summary(args.group, limit=args.limit, as_json=args.json)
    elif args.command == "alias":
        aliases = load_aliases()
        if args.action == "list":
            print(json.dumps(aliases, ensure_ascii=False, indent=2))
            return 0
        elif args.action == "set":
            if not args.url:
                print("請提供 --url 參數", file=sys.stderr)
                return 1
            group_key, chrono_url = resolve_group_url(args.url, alias_name=args.name)
            print(json.dumps({
                "status": "OK",
                "alias": args.name,
                "group_key": group_key,
                "chronological_url": chrono_url,
                "aliases": load_aliases()
            }, ensure_ascii=False, indent=2))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
