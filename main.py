import os
import re
import json
import time
import asyncio
import threading
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException, Depends, status, File, Form, UploadFile
from fastapi.responses import StreamingResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings
from agent import personal_agent
from memory_manager import memory_manager
from brain_engine import brain_router
from widget_html_utils import WidgetHtmlStreamFilter, extract_rich_blocks
from fast_intent import try_fast_intent
from task_queue import task_queue
from turn_queue import shared_turn_queue
from transcribe_service import transcribe_file, warm_transcriber
from telegram_service import (
    start_telegram_bot,
    send_telegram_sync,
    format_telegram_response,
    send_formatted_telegram_reply
)
from memory_scheduler import run_memory_scheduler, sync_and_commit_memory
from task_supervisor import (
    run_task_supervisor,
    enqueue_memory_sync,
    try_inject_memory_sync,
)

# 語音朗讀專用：全面清除 Emoji 表情符號與 Markdown 標記，避免 Siri 唸出「笑臉」等雜音
EMOJI_PATTERN = re.compile(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u200d\ufe0f]", flags=re.UNICODE)

def sanitize_for_tts(text: str) -> str:
    """全面清除所有 Emoji、Markdown 標記、連結目標與網址，確保語音朗讀純淨無雜音"""
    # Markdown 連結 [顯示文字](url) 僅保留顯示文字，避免唸出 file:// 或 https:// 路徑
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    clean = re.sub(r"(?:https?|file)://\S+", "", clean)
    clean = re.sub(r"(📍\s*)?地圖連結[：:]\s*", "", clean)
    clean = EMOJI_PATTERN.sub("", clean)
    clean = re.sub(r"[*#`_~>\[\]]", "", clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\s+([。，、！？.!?])", r"\1", clean).strip()
    return clean

class TTSSentenceExtractor:
    """串流切句與程式碼區塊過濾器：將 LLM 吐出的文字即時過濾程式碼並切分為朗讀句子"""
    def __init__(self):
        self.buffer = ""
        self.in_code_block = False
        self.sentence_buffer = ""
        self.delimiters = set("。！？；!?\n")

    def feed(self, delta: str) -> List[str]:
        sentences = []
        self.buffer += delta

        while True:
            if not self.in_code_block:
                idx = self.buffer.find("```")
                if idx != -1:
                    text_before = self.buffer[:idx]
                    self.buffer = self.buffer[idx + 3:]
                    self.in_code_block = True
                    sentences.extend(self._process_text(text_before))
                    continue
                else:
                    if self.buffer.endswith("`"):
                        safe_len = len(self.buffer) - (2 if self.buffer.endswith("``") else 1)
                        if safe_len > 0:
                            confirmed = self.buffer[:safe_len]
                            self.buffer = self.buffer[safe_len:]
                            sentences.extend(self._process_text(confirmed))
                    else:
                        confirmed = self.buffer
                        self.buffer = ""
                        sentences.extend(self._process_text(confirmed))
                    break
            else:
                idx = self.buffer.find("```")
                if idx != -1:
                    self.buffer = self.buffer[idx + 3:]
                    self.in_code_block = False
                    continue
                else:
                    if self.buffer.endswith("`"):
                        safe_len = len(self.buffer) - (2 if self.buffer.endswith("``") else 1)
                        self.buffer = self.buffer[safe_len:]
                    else:
                        self.buffer = ""
                    break
        return sentences

    def _process_text(self, text: str) -> List[str]:
        sentences = []
        for ch in text:
            self.sentence_buffer += ch
            if ch in self.delimiters:
                clean = sanitize_for_tts(self.sentence_buffer)
                if len(clean) >= 1 and any(c.isalnum() for c in clean):
                    sentences.append(clean)
                self.sentence_buffer = ""
            elif len(self.sentence_buffer) > 60 and ch in "，,":
                clean = sanitize_for_tts(self.sentence_buffer)
                if len(clean) >= 1 and any(c.isalnum() for c in clean):
                    sentences.append(clean)
                self.sentence_buffer = ""
        return sentences

    def flush(self) -> List[str]:
        sentences = []
        if not self.in_code_block and self.sentence_buffer:
            clean = sanitize_for_tts(self.sentence_buffer)
            if len(clean) >= 1 and any(c.isalnum() for c in clean):
                sentences.append(clean)
            self.sentence_buffer = ""
        return sentences

def extract_tts_sentences(buffer: str, in_code_block: bool = False) -> tuple[List[str], str, bool]:
    """輔助函式：自一段文字中提取完整 TTS 句子，並回傳 (句子列表, 剩餘緩衝, 是否仍在程式碼區塊)"""
    extractor = TTSSentenceExtractor()
    extractor.in_code_block = in_code_block
    sentences = extractor.feed(buffer)
    remaining = extractor.sentence_buffer if not extractor.in_code_block else ""
    return sentences, remaining, extractor.in_code_block

app = FastAPI(
    title="Mac mini Personal Assistant API",
    description="專為 Apple Watch Ultra 與 iOS 捷徑設計的個人助理後端",
    version="1.0.0"
)

# 掛載 HTML 報告目錄 (供 Tailscale 遠端檢視)
app.mount("/reports", StaticFiles(directory=str(settings.REPORTS_DIR), html=True), name="reports")

# 掛載靜態資源與 iOS SideStore 軟體源目錄
STATIC_DIR = settings.BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 允許跨來源請求 (包含 Tailscale 網域與本機)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """服務啟動時預熱啟動 agy 守護進程，實現零冷啟動，並啟動 Telegram 與記憶庫排程監聽"""
    if settings.AGENT_ENGINE == "agy":
        brain_router.start()
    def _warm_omp_resident() -> None:
        try:
            brain_router.get_engine("omp").start()
        except Exception as ex:
            print(f"[Boot] ⚠️ omp 常駐預熱失敗（將於首次使用時啟動）: {ex}")
    asyncio.create_task(asyncio.to_thread(_warm_omp_resident))
    if settings.TELEGRAM_BOT_TOKEN:
        asyncio.create_task(start_telegram_bot())
    asyncio.create_task(run_memory_scheduler())
    asyncio.create_task(run_task_supervisor())
    # 背景預熱轉錄模型（lazy 載入，首次請求不用等模型載入）
    asyncio.create_task(asyncio.to_thread(warm_transcriber))

class ChatRequest(BaseModel):
    query: str
    client: Optional[str] = "apple-watch"
    mode: Optional[str] = None # "sync" 或 "async"，未指定時 apple-watch 預設為 async，homepod 預設為 sync
    media: Optional[List[dict]] = None
    image_path: Optional[str] = None
    image_paths: Optional[List[str]] = None
    image_base64: Optional[str] = None
    files: Optional[List[dict]] = None
    level: Optional[str] = None
    tts_voice: Optional[str] = "zh-TW-HsiaoChenNeural"
    tts_rate: Optional[str] = "+0%"
    enable_tts: bool = True

class ChatResponse(BaseModel):
    reply: str
    actions: List[str]
    timestamp: str
    received_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_seconds: Optional[float] = None
    task_id: Optional[str] = None # async 模式回傳，供 GET /api/chat/result/{task_id} 輪詢


class AppStateRequest(BaseModel):
    active: bool = True


class UnreadNotifyRequest(BaseModel):
    title: str = "AI助理新回覆"
    message: str = ""
    force: bool = False


class PendingActionCreateRequest(BaseModel):
    agent: str
    title: str
    question: str
    options: List[str] = []
    source: str = "herdr"
    context_prompt: str = ""
    status: Optional[str] = None
    task_prompt: str = ""
    summary: str = ""
    elapsed_seconds: int = 0
    tool_count: int = 0
    tool_calls: List[str] = []
    live_preview: str = ""
    terminal_tail: str = ""
    retry_count: int = 0
    max_retries: int = 3


class PendingActionResolveRequest(BaseModel):
    answer: str = ""
    dismiss: bool = False
    action: Optional[str] = None


_PENDING_ACTIONS_LOCK = threading.Lock()


def _pending_actions_file():
    return settings.DATA_DIR / "pending_actions.json"


def _herdr_history_file():
    return settings.DATA_DIR / "herdr_history.json"


def _load_pending_actions() -> List[dict]:
    p = _pending_actions_file()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pending_actions(items: List[dict]) -> None:
    p = _pending_actions_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_herdr_history() -> List[dict]:
    p = _herdr_history_file()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_herdr_history(items: List[dict]) -> None:
    p = _herdr_history_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items[:100], ensure_ascii=False, indent=2), encoding="utf-8")


def _archive_herdr_action(item: dict) -> dict:
    """將已結案的 Herdr 派工卡片歸檔至 herdr_history.json 永久留存。"""
    now_iso = datetime.now().isoformat()
    archived = dict(item)
    archived["status"] = "archived"
    archived["archived_at"] = now_iso
    archived["updated_at"] = now_iso
    history = _load_herdr_history()
    history = [x for x in history if x.get("id") != archived.get("id")]
    history.insert(0, archived)
    _save_herdr_history(history)
    return archived


def _compute_action_counts(items: List[dict]) -> tuple[int, int]:
    """回傳 (需主人注意的決策/完工待檢閱數, 執行中工人數)"""
    attention = 0
    working = 0
    for x in items:
        st = (x.get("status") or "waiting_decision").strip().lower()
        if st in ("working", "retrying"):
            working += 1
        else:
            attention += 1
    return attention, working


def _upsert_pending_action(
    agent: str,
    title: str,
    question: str,
    options: Optional[List[str]] = None,
    source: str = "herdr",
    context_prompt: str = "",
    status: Optional[str] = None,
    task_prompt: str = "",
    summary: str = "",
    elapsed_seconds: int = 0,
    tool_count: int = 0,
    tool_calls: Optional[List[str]] = None,
    live_preview: str = "",
    terminal_tail: str = "",
    retry_count: int = 0,
    max_retries: int = 3,
) -> dict:
    import uuid
    now_iso = datetime.now().isoformat()
    agent_clean = agent.strip()
    effective_status = (status or "waiting_decision").strip().lower()

    with _PENDING_ACTIONS_LOCK:
        items = _load_pending_actions()
        existing = next((x for x in items if x.get("agent") == agent_clean), None)

        round_no = int((existing or {}).get("round_no") or 1)
        rounds = list((existing or {}).get("rounds") or [])
        prev_task_prompt = (existing or {}).get("task_prompt") or ""
        clean_task_prompt = (task_prompt or "").strip()

        # 若該 Agent 先前處於 completed，且收到新一輪派工 (working + 新 task_prompt)，自動將上一輪收合進 rounds
        if (
            existing
            and existing.get("status") == "completed"
            and effective_status == "working"
            and clean_task_prompt
            and clean_task_prompt != prev_task_prompt
        ):
            rounds.append({
                "round_no": round_no,
                "task_prompt": prev_task_prompt,
                "summary": (existing.get("summary") or existing.get("question") or "").strip(),
                "completed_at": existing.get("updated_at") or existing.get("created_at") or now_iso,
                "tool_count": int(existing.get("tool_count") or 0),
            })
            round_no += 1

        effective_task_prompt = clean_task_prompt or prev_task_prompt
        effective_summary = (summary or "").strip()
        if not effective_summary and effective_status == "completed":
            effective_summary = question.strip()
        elif not effective_summary and existing and effective_status != "working":
            effective_summary = (existing.get("summary") or "").strip()

        item = {
            "id": (existing or {}).get("id") or f"act_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            "agent": agent_clean,
            "title": title.strip() or (existing or {}).get("title", agent_clean),
            "question": question.strip(),
            "options": [o.strip() for o in (options or []) if o.strip()],
            "source": source,
            "context_prompt": (context_prompt or (existing or {}).get("context_prompt") or "").strip(),
            "created_at": (existing or {}).get("created_at") or now_iso,
            "updated_at": now_iso,
            "status": effective_status,
            "task_prompt": effective_task_prompt,
            "summary": effective_summary,
            "elapsed_seconds": int(elapsed_seconds if elapsed_seconds > 0 else ((existing or {}).get("elapsed_seconds") or 0 if effective_status != "working" else 0)),
            "tool_count": int(tool_count if tool_count > 0 else ((existing or {}).get("tool_count") or 0 if effective_status != "working" else 0)),
            "tool_calls": [str(t).strip() for t in (tool_calls or []) if str(t).strip()] or ((existing or {}).get("tool_calls") or [] if effective_status != "working" else []),
            "live_preview": (live_preview or "").strip(),
            "terminal_tail": (terminal_tail or "").strip() or ((existing or {}).get("terminal_tail") or "" if effective_status != "working" else ""),
            "retry_count": int(retry_count),
            "max_retries": int(max_retries),
            "round_no": round_no,
            "rounds": rounds,
        }
        items = [x for x in items if x.get("agent") != agent_clean]
        items.insert(0, item)
        _save_pending_actions(items)
    return item


_UNREAD_ALERT_TAG = "assistant_unread_reply"
_APP_STATE_LOCK = threading.Lock()
_APP_STATE = {
    "active": True,
    "has_unread_alert": False,
    "cleared_on_boot": False,
    "updated_at": 0.0,
}


def _notify_app_unread_reply(reply_text: str, title: str = "AI助理新回覆") -> bool:
    """當 iOS App 處於背景或已斷線時，發送唯一一則靜音置頂的 Home Assistant 重要通知。"""
    try:
        from scripts.ios_notify import IOSNotifier
        preview = sanitize_for_tts(reply_text or "").strip()
        if len(preview) > 140:
            preview = preview[:139].rstrip() + "…"
        if not preview:
            preview = "助理已完成回覆，點此開啟 App 查看完整內容。"
        ok = IOSNotifier().send_critical(
            title=title,
            message=preview,
            url="macminiassistant://open",
            tag=_UNREAD_ALERT_TAG,
            volume=0.0,
        )
        if ok:
            with _APP_STATE_LOCK:
                _APP_STATE["has_unread_alert"] = True
        return ok
    except Exception as ex:
        print(f"[AppNotify] ⚠️ 發送未讀重要通知失敗: {ex}")
        return False


def _clear_app_unread_alert(force: bool = False) -> bool:
    """當主人回到 iOS App 前景讀取訊息時，自動遠端撤回既有的唯一重要通知。"""
    with _APP_STATE_LOCK:
        should_clear = force or bool(_APP_STATE.get("has_unread_alert"))
        _APP_STATE["has_unread_alert"] = False
    if not should_clear:
        return False
    try:
        from scripts.ios_notify import IOSNotifier
        return IOSNotifier().clear(tag=_UNREAD_ALERT_TAG)
    except Exception as ex:
        print(f"[AppNotify] ⚠️ 回收重要通知失敗: {ex}")
        return False

def verify_token(
    x_agent_key: Optional[str] = Header(None), 
    authorization: Optional[str] = Header(None)
):
    """安全驗證：檢查 X-Agent-Key 或 Authorization Header"""
    if not settings.API_KEY or settings.API_KEY.lower() in ("none", ""):
        return True
    
    auth_token = None
    if x_agent_key:
        auth_token = x_agent_key
    elif authorization and authorization.startswith("Bearer "):
        auth_token = authorization.split("Bearer ", 1)[1].strip()
        
    if auth_token != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="無效的 API 密鑰 (Invalid Agent Key)"
        )
    return True

@app.get("/")
async def index():
    return {
        "status": "online",
        "service": "Mac mini Personal Assistant",
        "owner": os.getenv("ASSISTANT_OWNER", "<您的稱呼>"),
        "tailscale_domain": settings.TAILSCALE_DOMAIN,
        "docs_url": "/docs"
    }

@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "tailscale_ip": os.getenv("TAILSCALE_IP", ""),
        "tailscale_domain": settings.TAILSCALE_DOMAIN,
        "port": settings.PORT
    }

@app.get("/api/reports")
def get_reports_list():
    """取得所有已生成之 HTML 報告清單 (依修改時間由新到舊排序)"""
    import re
    reports = []
    reports_dir = settings.REPORTS_DIR
    if not reports_dir.exists():
        return {"reports": []}
    
    for f in reports_dir.glob("*.html"):
        if f.name == "index.html":
            continue
        try:
            stat = f.stat()
            content = f.read_text(encoding="utf-8", errors="ignore")[:4096]
            match = re.search(r"<title>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
            title = match.group(1).strip() if match else f.stem
            reports.append({
                "filename": f.name,
                "title": title,
                "size_bytes": stat.st_size,
                "size_formatted": f"{stat.st_size / 1024:.1f} KB" if stat.st_size < 1024 * 1024 else f"{stat.st_size / (1024*1024):.1f} MB",
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "mtime": stat.st_mtime,
                "url": f"/reports/{f.name}",
                "full_url": settings.get_report_url(f.name)
            })
        except Exception:
            pass

    reports.sort(key=lambda x: x["mtime"], reverse=True)
    return {"reports": reports, "total": len(reports)}


@app.get("/api/artifacts")
def get_artifacts(_authorized: bool = Depends(verify_token)):
    """取得助理曾正式交付給主人的產物清單（報告與圖片網址，不含未交付伺服器檔案）"""
    from artifacts_store import list_artifacts
    items = list_artifacts()
    return {"artifacts": items, "total": len(items)}


@app.delete("/api/artifacts/{artifact_id}")
def delete_artifact(artifact_id: str, _authorized: bool = Depends(verify_token)):
    """從 App 產物清單軟移除項目（不刪除伺服器硬碟上的真實檔案）"""
    from artifacts_store import hide_artifact, list_artifacts
    ok = hide_artifact(artifact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Artifact not found")
    items = list_artifacts()
    return {"success": True, "artifacts": items, "total": len(items)}

@app.get("/api/performance-review")
def get_performance_review(hours: int = 24, min_latency: float = 15.0, notify: bool = False):
    """手動觸發慢問答效能審查與改進提案生成"""
    from performance_reviewer import performance_reviewer
    return performance_reviewer.run_review(hours=hours, min_latency=min_latency, notify_telegram=notify)

@app.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest, 
    mode: Optional[str] = None, 
    _authorized: bool = Depends(verify_token)
):
    import time
    import mimetypes
    from pathlib import Path
    start_ts = time.perf_counter()
    received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    # 解析多模態 media 圖片
    effective_media: List[dict] = list(payload.media or [])
    if payload.image_path:
        img_p = Path(payload.image_path).resolve()
        if img_p.exists():
            mime_type, _ = mimetypes.guess_type(str(img_p))
            if not mime_type:
                mime_type = "image/jpeg"
            effective_media.append({
                "mime_type": mime_type,
                "uri": str(img_p)
            })
    if payload.image_paths:
        for p in payload.image_paths:
            img_p = Path(p).resolve()
            if img_p.exists():
                mime_type, _ = mimetypes.guess_type(str(img_p))
                effective_media.append({
                    "mime_type": mime_type or "image/jpeg",
                    "uri": str(img_p)
                })

    # 如果使用者有傳入 query 則進行處理；若只有圖片則預設提示
    user_query = payload.query.strip()
    if not user_query:
        if effective_media:
            user_query = f"請幫我分析這 {len(effective_media)} 張圖片。" if len(effective_media) > 1 else "請幫我分析這張圖片"
        else:
            return ChatResponse(
                reply="我沒有聽到任何內容，請再試一次。",
                actions=[],
                timestamp=datetime.now().isoformat(),
                received_at=received_at,
                finished_at=received_at,
                latency_seconds=0.0
            )
        
    client_source = payload.client or "apple-watch"

    # 1. 第一關：Fast-Path 極速通道（家電開關、環境溫濕度、AI 配額查詢）
    # 若有圖片附件，直接直通常駐多模態大腦；無圖片時才嘗試 Fast-Path
    if not effective_media:
        fast_reply = try_fast_intent(user_query)
        if fast_reply:
            end_ts = time.perf_counter()
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            latency = round(end_ts - start_ts, 3)

            if client_source == "homepod":
                output_reply = sanitize_for_tts(fast_reply)
            else:
                output_reply = fast_reply

            # 推送至 Telegram (方案 1: 置底一體化 Quote)
            tg_text = format_telegram_response(
                reply=output_reply,
                user_query=user_query,
                latency=latency,
                actions=["engine: fast-path"]
            )
            send_telegram_sync(tg_text, parse_mode="HTML")

            try:
                _stripped, _widget, _image_urls = extract_rich_blocks(output_reply)
                _meta = {"engine": "fast-path"}
                if _widget:
                    _meta["widget_html"] = _widget
                if _image_urls:
                    _meta["image_urls"] = _image_urls
                memory_manager.log_interaction(
                    _clean_history_query(user_query),
                    _stripped.strip() or output_reply,
                    actions=["engine: fast-path"],
                    metadata=_meta,
                )
            except Exception as _log_e:
                print(f"[chat] fast-path history log failed: {_log_e}")

            return ChatResponse(
                reply=output_reply,
                actions=["engine: fast-path"],
                timestamp=datetime.now().isoformat(),
                received_at=received_at,
                finished_at=finished_at,
                latency_seconds=latency
            )

    # 2. 第二關：直接由設備類型 (Device Type / client) 決定同步或非同步
    # 設備分流規則：
    # - apple-watch: 穿戴設備，一律非同步 (async) <0.05s 秒退，成果推播至 Telegram
    # - homepod: 語音音箱，一律同步 (sync) 等待語音文本產生並朗讀
    # - 其他設備 (或除錯覆寫): 可透過 mode 參數指定，預設為 sync
    device_mode_map = {
        "apple-watch": "async",
        "homepod": "sync",
    }
    effective_mode = mode or payload.mode or device_mode_map.get(client_source, "sync")

    if effective_mode == "async":
        # 非同步模式 (Apple Watch 等穿戴設備)：立即以 < 0.05 秒回傳確認，手錶捷徑秒退不卡住！
        task_id = task_queue.enqueue(user_query, client_source, received_at, media=effective_media if effective_media else None)
        latency = round(time.perf_counter() - start_ts, 3)
        ack_reply = "👌 已收到，稍後在 Telegram 傳送給您。"

        print(
            f"\n┌─────────────────────────────────────────────────────────────\n"
            f"│ ⏱️  [Timing Benchmark] 非同步任務已派發 (來源: /api/chat, 裝置: {client_source})\n"
            f"│ ⚡ API 回應耗時:       {latency:.3f} 秒 (佇列待處理: {task_queue.get_queue_size()})\n"
            f"│ 💬 Prompt 內容:        \"{user_query}\"\n"
            f"│ 🤖 設備端回覆:         \"{ack_reply}\"\n"
            f"└─────────────────────────────────────────────────────────────\n",
            flush=True
        )

        return ChatResponse(
            reply=ack_reply,
            actions=["mode: async-queued", f"queue_size: {task_queue.get_queue_size()}"],
            timestamp=datetime.now().isoformat(),
            received_at=received_at,
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            latency_seconds=latency,
            task_id=task_id
        )

    # 3. 第三關：同步模式 (例如 HomePod mini 需要等待文字產出並用語音唸給主人聽)
    existing_reports = {f.name: f.stat().st_mtime for f in settings.REPORTS_DIR.glob("*.html")}
    reply, actions = personal_agent.process(user_query, client=client_source, media=effective_media if effective_media else None)

    end_ts = time.perf_counter()
    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    latency = round(end_ts - start_ts, 3)

    clean_reply = reply.replace("[TELEGRAM_FULL_REPORT]", "\n\n").strip()
    if client_source == "homepod":
        output_reply = sanitize_for_tts(clean_reply)
    else:
        output_reply = clean_reply

    new_reports = [
        f for f in settings.REPORTS_DIR.glob("*.html")
        if f.name not in existing_reports or f.stat().st_mtime > existing_reports.get(f.name, 0)
    ]
    report_link_html = ""
    if new_reports:
        latest_report = max(new_reports, key=lambda f: f.stat().st_mtime)
        report_url = settings.get_report_url(latest_report.name)
        portal_url = f"http://{settings.TAILSCALE_DOMAIN}:{settings.PORT}/reports"
        report_link_html = f"\n\n🌐 <b>線上報告已生成</b>：\n👉 <a href=\"{report_url}\">點此開啟報告</a> ｜ 📂 <a href=\"{portal_url}\">報告總覽中心</a>"

    # 推送至 Telegram (自動支援長篇分段與置底 Quote 卡片)
    send_formatted_telegram_reply(
        reply=clean_reply,
        user_query=user_query,
        latency=latency,
        actions=actions,
        report_link_html=report_link_html
    )

    print(
        f"\n┌─────────────────────────────────────────────────────────────\n"
        f"│ ⏱️  [Timing Benchmark] 同步請求完成 (來源: /api/chat, 裝置: {client_source})\n"
        f"│ 📥 收到 Prompt 時間:   {received_at}\n"
        f"│ 📤 agy 處理完畢時間:  {finished_at}\n"
        f"│ ⚡ 總處理耗時 (Latency): {latency:.2f} 秒\n"
        f"│ 💬 Prompt 內容:        \"{user_query}\"\n"
        f"│ 🤖 設備端回覆:         \"{output_reply[:60]}{'...' if len(output_reply) > 60 else ''}\"\n"
        f"│ 📱 Telegram 同步:     {len(clean_reply)} 字元 (1:1 鏡像對齊)\n"
        f"└─────────────────────────────────────────────────────────────\n",
        flush=True
    )

    return ChatResponse(
        reply=output_reply,
        actions=actions,
        timestamp=datetime.now().isoformat(),
        received_at=received_at,
        finished_at=finished_at,
        latency_seconds=latency
    )

@app.get("/api/voice-chat/tts")
async def voice_tts_endpoint(
    text: str,
    voice: str = "zh-TW-HsiaoChenNeural",
    _authorized: bool = Depends(verify_token)
):
    """即時文字轉類神經語音端點 (微軟 Edge-TTS MP3 串流)"""
    from tts_service import synthesize_to_bytes
    audio_bytes = await synthesize_to_bytes(text, voice=voice)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="語音合成失敗或無有效文字")
    return Response(content=audio_bytes, media_type="audio/mpeg")

import threading as _threading

_ACTIVE_VOICE_CANCEL_EVENTS: set = set()
_VOICE_CANCEL_LOCK = _threading.Lock()


class CancelChatRequest(BaseModel):
    level: Optional[str] = None
    reason: Optional[str] = "user_cancelled"
    clear_queue: bool = False


_TURN_QUEUE = shared_turn_queue


def _on_stale_turn_cleanup(stale_snapshot: dict) -> None:
    try:
        partial = (stale_snapshot.get("reply") or "").strip()
        msg = f"{partial}\n\n[回覆逾時或連線中斷]" if partial else "[回覆逾時或大腦連線中斷]"
        memory_manager.log_interaction(
            stale_snapshot.get("query", ""),
            msg,
            actions=["interrupted_timeout"],
        )
    except Exception:
        pass


def _get_active_turn_snapshot() -> Optional[dict]:
    return _TURN_QUEUE.get_active_snapshot(on_stale=_on_stale_turn_cleanup)


def _get_pending_queue_snapshot() -> List[dict]:
    return _TURN_QUEUE.get_pending_snapshot()


@app.post("/api/voice-chat/cancel")
def cancel_voice_chat(
    payload: Optional[CancelChatRequest] = None,
    _authorized: bool = Depends(verify_token)
):
    """iOS 端一鍵停止/語音打斷專用端點：
    1. 預設僅觸發當前進行中 Turn 之 cancel_event，保留佇列中待執行項目（clear_queue=True 時一併清空）
    2. 對底層 agy acp 發送原生 `session/cancel`，於 6~15ms 內強制中止大腦思考與執行中工具
    """
    level = payload.level if payload else None
    clear_queue = bool(payload.clear_queue) if payload else False

    cancelled_turn_id = _TURN_QUEUE.cancel_active()
    cleared_count = _TURN_QUEUE.clear_pending_queue() if clear_queue else 0

    with _VOICE_CANCEL_LOCK:
        active_count = len(_ACTIVE_VOICE_CANCEL_EVENTS)
        if clear_queue or cancelled_turn_id is None:
            for ev in list(_ACTIVE_VOICE_CANCEL_EVENTS):
                ev.set()
    acp_res = brain_router.cancel_active_turns(level=level)
    print(f"[cancel_voice_chat] 🛑 收到取消請求 (turn={cancelled_turn_id}, cleared_queue={cleared_count}, active_streams={active_count}, acp_cancelled={acp_res})")
    return {
        "status": "cancelled",
        "cancelled_turn_id": cancelled_turn_id,
        "cleared_queue": cleared_count,
        "active_streams_signaled": active_count,
        "acp": acp_res
    }


@app.get("/api/voice-chat/queue")
def get_voice_chat_queue(_authorized: bool = Depends(verify_token)):
    """取得目前進行中任務與排隊佇列狀態"""
    return {
        "active_turn": _get_active_turn_snapshot(),
        "pending_queue": _get_pending_queue_snapshot(),
    }


@app.delete("/api/voice-chat/queue/{turn_id}")
@app.post("/api/voice-chat/queue/{turn_id}/cancel")
def delete_voice_chat_queue_item(
    turn_id: str,
    _authorized: bool = Depends(verify_token)
):
    """從排隊佇列移除尚未執行的訊息，或中斷指定進行中任務"""
    res = _TURN_QUEUE.remove_turn(turn_id)
    if res == "cancelling_active":
        acp_res = brain_router.cancel_active_turns()
        return {"status": res, "turn_id": turn_id, "acp": acp_res}
    if res == "removed_queued":
        return {"status": res, "turn_id": turn_id}
    raise HTTPException(status_code=404, detail="Turn not found in active or pending queue")


@app.delete("/api/voice-chat/queue")
def clear_voice_chat_pending_queue(_authorized: bool = Depends(verify_token)):
    """清空所有排隊中（尚未執行）的任務"""
    cleared = _TURN_QUEUE.clear_pending_queue()
    return {"status": "cleared", "cleared_count": cleared}


@app.post("/api/voice-chat/stream")
async def voice_chat_stream(
    payload: ChatRequest,
    _authorized: bool = Depends(verify_token)
):
    """iOS 隨身語音/Vibe Coding 專用 SSE 串流端點：
    同時推流文字增量 (text_delta)、工具進度 (status) 與純口語切句 (tts_sentence)。
    支援中途透過 /api/voice-chat/cancel 或斷開連線觸發 ACP 原生 `session/cancel`。
    """
    import time
    import threading
    import mimetypes
    from pathlib import Path

    start_ts = time.perf_counter()
    user_query = payload.query.strip()
    client_source = payload.client or "ios-voice"
    level = payload.level
    cancel_event = threading.Event()
    client_disconnected = threading.Event()
    worker_finished = threading.Event()
    with _VOICE_CANCEL_LOCK:
        _ACTIVE_VOICE_CANCEL_EVENTS.add(cancel_event)

    # 解析多模態 media 圖片
    import base64
    import uuid
    effective_media: List[dict] = []
    upload_dir = settings.BASE_DIR / "data" / "ios_uploads"
    for item in (payload.media or []):
        if "uri" in item:
            effective_media.append(item)
        elif "data_base64" in item:
            try:
                upload_dir.mkdir(parents=True, exist_ok=True)
                raw_bytes = base64.b64decode(item["data_base64"])
                saved_file = upload_dir / f"ios_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                saved_file.write_bytes(raw_bytes)
                effective_media.append({
                    "mime_type": item.get("mime_type", "image/jpeg"),
                    "uri": str(saved_file.resolve())
                })
            except Exception as ex:
                print(f"[voice_chat_stream] Failed to decode media base64: {ex}")

    if payload.image_base64:
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_bytes = base64.b64decode(payload.image_base64)
            saved_file = upload_dir / f"ios_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            saved_file.write_bytes(raw_bytes)
            effective_media.append({
                "mime_type": "image/jpeg",
                "uri": str(saved_file.resolve())
            })
        except Exception as ex:
            print(f"[voice_chat_stream] Failed to decode image_base64: {ex}")

    if payload.image_path:
        img_p = Path(payload.image_path).resolve()
        if img_p.exists():
            mime_type, _ = mimetypes.guess_type(str(img_p))
            effective_media.append({
                "mime_type": mime_type or "image/jpeg",
                "uri": str(img_p)
            })
    if payload.image_paths:
        for p in payload.image_paths:
            img_p = Path(p).resolve()
            if img_p.exists():
                mime_type, _ = mimetypes.guess_type(str(img_p))
                effective_media.append({
                    "mime_type": mime_type or "image/jpeg",
                    "uri": str(img_p)
                })

    file_contexts: List[str] = []
    if payload.files:
        from share_file_parser import sanitize_filename
        upload_dir.mkdir(parents=True, exist_ok=True)
        for item in payload.files[:5]:
            b64_data = (item.get("data_base64") or "").strip() if isinstance(item, dict) else ""
            if not b64_data:
                continue
            try:
                raw_bytes = base64.b64decode(b64_data)
                safe_name = sanitize_filename(str(item.get("filename") or "upload.bin"), fallback="upload.bin")
                saved_file = upload_dir / f"ios_doc_{int(time.time())}_{uuid.uuid4().hex[:6]}_{safe_name}"
                saved_file.write_bytes(raw_bytes)
                # 僅傳遞本機檔案路徑給大腦（agy / omp），由大腦自行決定讀取方式，避免全文預覽或 PDF media 導致 token 暴衝或空回覆
                file_contexts.append(f"📎 檔案：{safe_name}（本機路徑：{saved_file.resolve()}）")
            except Exception as ex:
                print(f"[voice_chat_stream] Failed to decode uploaded file: {ex}")

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()
    enable_tts = bool(getattr(payload, "enable_tts", True))
    tts_voice = payload.tts_voice or "zh-TW-HsiaoChenNeural"
    tts_rate = payload.tts_rate or "+0%"
    tts_queue: asyncio.Queue = asyncio.Queue()

    async def tts_worker():
        from tts_service import synthesize_to_base64
        while True:
            item = await tts_queue.get()
            if item is None:
                tts_queue.task_done()
                break
            seq, sentence = item
            if cancel_event.is_set() or client_disconnected.is_set():
                tts_queue.task_done()
                continue
            try:
                audio_b64 = await synthesize_to_base64(sentence, voice=tts_voice, rate=tts_rate)
                if audio_b64 and not cancel_event.is_set() and not client_disconnected.is_set():
                    await event_queue.put({
                        "type": "audio_chunk",
                        "seq": seq,
                        "sentence": sentence,
                        "audio_base64": audio_b64,
                        "format": "mp3"
                    })
            except Exception as ex:
                print(f"[tts_worker] Error synthesizing sentence {seq}: {ex}")
            finally:
                tts_queue.task_done()

    tts_task = asyncio.create_task(tts_worker()) if enable_tts else None

    if not user_query and not effective_media and not file_contexts:
        with _VOICE_CANCEL_LOCK:
            _ACTIVE_VOICE_CANCEL_EVENTS.discard(cancel_event)
        async def empty_gen():
            msg = "我沒有聽到任何內容，請再試一次。"
            yield f"data: {json.dumps({'type': 'tts_sentence', 'sentence': msg}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'text_delta', 'delta': msg}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'full_reply': msg, 'latency': 0.0, 'thinking_level': level or 'med', 'report_url': ''}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            empty_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )

    if not user_query and effective_media and not file_contexts:
        user_query = f"請幫我分析這 {len(effective_media)} 張圖片。" if len(effective_media) > 1 else "請幫我分析這張圖片"
    elif not user_query and file_contexts:
        user_query = f"請幫我解析這 {len(file_contexts)} 份檔案。" if len(file_contexts) > 1 else "請幫我解析這份檔案。"

    if file_contexts:
        user_query = f"{user_query}\n\n" + "\n\n".join(file_contexts)

    existing_reports = {f.name: f.stat().st_mtime for f in settings.REPORTS_DIR.glob("*.html")}

    def stream_worker():
        actions = [f"client: {client_source}"]
        full_reply_parts = []
        turn_result = {}
        extractor = TTSSentenceExtractor()
        widget_filter = WidgetHtmlStreamFilter()
        sentence_seq = 0
        active_turn_id, is_active_now, queue_pos = _TURN_QUEUE.enqueue_or_activate(
            _clean_history_query(user_query),
            cancel_event,
            level=level,
            client=client_source,
        )

        try:
            if not is_active_now:
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    {"type": "queued", "turn_id": active_turn_id, "position": queue_pos},
                )
                activated = _TURN_QUEUE.wait_until_active_or_cancelled(active_turn_id, cancel_event)
                if not activated:
                    if enable_tts:
                        loop.call_soon_threadsafe(tts_queue.put_nowait, None)
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "cancelled", "turn_id": active_turn_id},
                    )
                    return

            gen = personal_agent.stream(
                user_query,
                client=client_source,
                media=effective_media if effective_media else None,
                level=level
            )
            while True:
                if cancel_event.is_set():
                    brain_router.cancel_active_turns(level=level)
                    break
                try:
                    ev_type, ev_data = next(gen)
                except StopIteration as stop_err:
                    turn_result = stop_err.value or {}
                    break

                if cancel_event.is_set():
                    brain_router.cancel_active_turns(level=level)
                    break

                if ev_type == "thought":
                    _TURN_QUEUE.update_active(active_turn_id, thought_delta=ev_data)
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "thought_delta", "delta": ev_data}
                    )
                elif ev_type == "status":
                    status_text = ev_data
                    _TURN_QUEUE.update_active(active_turn_id, tool_status=status_text)
                    clean_status = sanitize_for_tts(status_text)
                    actions.append(f"tool: {status_text[:30]}")
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "status", "text": status_text, "tts": clean_status}
                    )
                elif ev_type == "text":
                    delta = ev_data
                    full_reply_parts.append(delta)
                    visible_delta = widget_filter.feed(delta)
                    if visible_delta:
                        _TURN_QUEUE.update_active(active_turn_id, text_delta=visible_delta)
                        loop.call_soon_threadsafe(
                            event_queue.put_nowait,
                            {"type": "text_delta", "delta": visible_delta}
                        )
                        sentences = extractor.feed(visible_delta)
                        if not client_disconnected.is_set():
                            for sent in sentences:
                                seq = sentence_seq
                                sentence_seq += 1
                                loop.call_soon_threadsafe(
                                    event_queue.put_nowait,
                                    {"type": "tts_sentence", "sentence": sent, "seq": seq}
                                )
                                if enable_tts:
                                    loop.call_soon_threadsafe(
                                        tts_queue.put_nowait,
                                        (seq, sent)
                                    )

            was_cancelled = cancel_event.is_set() or bool(turn_result.get("cancelled"))
            if was_cancelled:
                if enable_tts:
                    loop.call_soon_threadsafe(tts_queue.put_nowait, None)
                print(f"[voice_chat_stream] 🛑 本次語音請求已中途取消，略過後續 TTS 與 Telegram 推播: {user_query[:30]}")
                return

            # Flush 剩餘未結尾句子（若客戶端已離線則略過 TTS 合成，直接完成任務寫入歷史與 Telegram）
            rem_visible = widget_filter.flush()
            if rem_visible:
                _TURN_QUEUE.update_active(active_turn_id, text_delta=rem_visible)
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    {"type": "text_delta", "delta": rem_visible}
                )
                if not client_disconnected.is_set():
                    for sent in extractor.feed(rem_visible):
                        seq = sentence_seq
                        sentence_seq += 1
                        loop.call_soon_threadsafe(
                            event_queue.put_nowait,
                            {"type": "tts_sentence", "sentence": sent, "seq": seq}
                        )
                        if enable_tts:
                            loop.call_soon_threadsafe(
                                tts_queue.put_nowait,
                                (seq, sent)
                            )
            if not client_disconnected.is_set():
                for sent in extractor.flush():
                    seq = sentence_seq
                    sentence_seq += 1
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "tts_sentence", "sentence": sent, "seq": seq}
                    )
                    if enable_tts:
                        loop.call_soon_threadsafe(
                            tts_queue.put_nowait,
                            (seq, sent)
                        )

            # 通知 tts_queue 已送完所有句子，並等待 TTS 合成排隊完成（純文字模式 enable_tts=False 直接跳過不阻塞）
            if enable_tts:
                loop.call_soon_threadsafe(tts_queue.put_nowait, None)
                if not client_disconnected.is_set():
                    try:
                        fut = asyncio.run_coroutine_threadsafe(tts_queue.join(), loop)
                        fut.result(timeout=15.0)
                    except Exception as e:
                        print(f"[voice_chat_stream] TTS wait timeout: {e}")

            if cancel_event.is_set():
                print(f"[voice_chat_stream] 🛑 TTS 期間收到取消訊號，略過 Telegram 推播")
                return

            total_latency = round(time.perf_counter() - start_ts, 3)
            full_reply = "".join(full_reply_parts) or turn_result.get("response", "") or turn_result.get("clean_reply", "")
            stripped_reply, extracted_widget, extracted_image_urls = extract_rich_blocks(full_reply)
            clean_reply = stripped_reply.replace("[TELEGRAM_FULL_REPORT]", "\n\n").strip()

            new_reports = [
                f for f in settings.REPORTS_DIR.glob("*.html")
                if f.name not in existing_reports or f.stat().st_mtime > existing_reports.get(f.name, 0)
            ]
            report_url = ""
            report_link_html = ""
            if new_reports:
                latest_report = max(new_reports, key=lambda f: f.stat().st_mtime)
                report_url = settings.get_report_url(latest_report.name)
                portal_url = f"http://{settings.TAILSCALE_DOMAIN}:{settings.PORT}/reports"
                report_link_html = f"\n\n🌐 <b>線上報告已生成</b>：\n👉 <a href=\"{report_url}\">點此開啟報告</a> ｜ 📂 <a href=\"{portal_url}\">報告總覽中心</a>"

            quick_replies = turn_result.get("quick_replies") or []
            meta_payload = dict(turn_result.get("metadata") or {})
            if extracted_widget and not meta_payload.get("widget_html"):
                meta_payload["widget_html"] = extracted_widget
            if extracted_image_urls and not meta_payload.get("image_urls"):
                meta_payload["image_urls"] = extracted_image_urls
            done_payload = {
                "type": "done",
                "full_reply": clean_reply,
                "latency": total_latency,
                "thinking_level": level or "med",
                "report_url": report_url,
                "quick_replies": quick_replies,
                "metadata": meta_payload or None,
            }
            loop.call_soon_threadsafe(event_queue.put_nowait, done_payload)

            # App 語音/文字對話通道已關閉 Telegram 同步；若主人切至背景或斷線未讀，
            # 改發送唯一一則靜音置頂的 Home Assistant 重要通知，待回 App 讀取後自動撤回。
            with _APP_STATE_LOCK:
                app_in_foreground = bool(_APP_STATE.get("active", True))
            if client_disconnected.is_set() or not app_in_foreground:
                _notify_app_unread_reply(clean_reply)

        except Exception as e:
            print(f"[voice_chat_stream] Worker exception: {e}")
            partial = "".join(full_reply_parts).strip()
            try:
                err_msg = f"{partial}\n\n[大腦連線中斷：{e}]" if partial else f"[大腦連線中斷：{e}]"
                memory_manager.log_interaction(_clean_history_query(user_query), err_msg, actions=["interrupted_error"])
            except Exception:
                pass
            loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "error", "error": str(e)})
        finally:
            _TURN_QUEUE.finish_active(active_turn_id)
            worker_finished.set()
            with _VOICE_CANCEL_LOCK:
                _ACTIVE_VOICE_CANCEL_EVENTS.discard(cancel_event)
            loop.call_soon_threadsafe(event_queue.put_nowait, None)

    threading.Thread(target=stream_worker, daemon=True).start()

    async def event_generator():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(event_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            if not worker_finished.is_set() and not cancel_event.is_set():
                client_disconnected.set()
                print(f"[voice_chat_stream] 🔌 SSE 客戶端中斷連線（例如螢幕休眠），背景繼續完成任務並寫入歷史紀錄")
            with _VOICE_CANCEL_LOCK:
                _ACTIVE_VOICE_CANCEL_EVENTS.discard(cancel_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

def _save_and_archive_upload(file: UploadFile) -> str:
    """上傳檔存暫存＋留底最後 20 個到 data/uploads/（真實錄音調參/診斷用）"""
    import os
    import shutil
    import tempfile
    import time
    from pathlib import Path

    suffix = os.path.splitext(file.filename or "")[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    uploads = Path("data/uploads")
    uploads.mkdir(parents=True, exist_ok=True)
    for old in sorted(uploads.glob("*"))[:-19]:
        old.unlink(missing_ok=True)
    shutil.copyfile(tmp_path, uploads / f"{time.strftime('%H%M%S')}{suffix}")
    return tmp_path

_BG_CHAT_TASKS: set = set()

def _dispatch_chat(query: str):
    """管線用：背景執行 /api/chat 同款流程（chat() 內部自行推 Telegram）。
    強參照 set 防 Task 被 GC。"""
    task = asyncio.create_task(
        asyncio.to_thread(chat, ChatRequest(query=query, client="apple-watch"), None, True)
    )
    _BG_CHAT_TASKS.add(task)
    task.add_done_callback(_BG_CHAT_TASKS.discard)

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form("zh"),
    _authorized: bool = Depends(verify_token),
):
    """語音轉錄 (OpenAI 相容)：multipart 上傳音檔 → {"text": ...}

    m4a/wav/mp3 皆可；language 為 BCP-47 前綴，預設 zh（跳過語言偵測、走繁體後處理）。
    """
    tmp_path = _save_and_archive_upload(file)
    try:
        return await asyncio.to_thread(transcribe_file, tmp_path, language)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        import os
        os.unlink(tmp_path)

@app.post("/v1/voice-pipeline")
async def voice_pipeline(
    file: UploadFile = File(...),
    language: Optional[str] = Form("zh"),
    _authorized: bool = Depends(verify_token),
):
    """語音管線（手錶專用）：上傳音檔 → 轉錄 → **伺服器端**自動接 /api/chat 推 Telegram。

    手錶只需完成這一次上傳；之後放下手腕（app 被 watchOS 掛起）不影響送出——
    chat 與 Telegram 推送全部由本服務背景完成，不再由手錶發第二次請求。
    """
    tmp_path = _save_and_archive_upload(file)
    try:
        result = await asyncio.to_thread(transcribe_file, tmp_path, language)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        import os
        os.unlink(tmp_path)
    _dispatch_chat(result["text"])
    return {**result, "chat": "queued"}

@app.get("/api/memory")
def get_memory(module: Optional[str] = None):
    """查看當前記憶內容 (可傳入 ?module=profile|preferences|homeassistant|projects|notes 查閱特定模組)"""
    if module:
        content = memory_manager.read_module(module)
        if content is None:
            raise HTTPException(status_code=404, detail=f"模組 '{module}' 不存在於 memory/ 目錄")
        return {
            "module": module,
            "content": content,
            "path": str(settings.MEMORY_DIR / f"{module}.md")
        }
    return {
        "content": memory_manager.read_memory(),
        "path": str(settings.AGENT_MD_PATH),
        "modules_dir": str(settings.MEMORY_DIR)
    }

@app.get("/api/chat/result/{task_id}")
def chat_task_result(task_id: str, _authorized: bool = Depends(verify_token)):
    """供 iOS / Watch 客戶端輪詢非同步任務的真正回覆 (status: pending | done | error)"""
    result = task_queue.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="查無此 task_id")
    return result


def _clean_history_query(raw_query: str) -> str:
    """移除 iOS/Watch/HomePod 的系統前綴規範、跨大腦交接提要與展開的檔案內文，保留使用者對話文字與精簡檔案標記"""
    q = (raw_query or "").strip()
    if q.startswith("【接續先前待決策事項（來自 Action Queue）】"):
        title_match = re.search(r"^決策標題：(.+)$", q, re.MULTILINE)
        ans_match = re.search(r"^主人已選擇 / 回覆：(.+)$", q, re.MULTILINE)
        if ans_match:
            ans_text = ans_match.group(1).strip()
            if title_match and title_match.group(1).strip():
                return f"已決策（{title_match.group(1).strip()}）：{ans_text}"
            return f"已決策：{ans_text}"

    prefix_markers = (
        "[來源管道:",
        "[跨大腦記憶交接提要",
        "【語音輸出嚴格規範】",
        "【回覆長度約束",
    )
    if any(marker in q for marker in prefix_markers):
        parts = q.split("\n\n")
        while len(parts) > 1 and any(marker in parts[0] for marker in prefix_markers):
            parts.pop(0)
        q = "\n\n".join(parts).strip()

    # 若包含由上傳檔案展開的「📎 檔案：<檔名>（本機路徑：...）」與全文預覽，僅保留精簡的「📎 <檔名>」標籤
    file_marker = "📎 檔案："
    if file_marker in q:
        idx = q.find(f"\n\n{file_marker}")
        if idx != -1:
            user_part = q[:idx].strip()
            file_part = q[idx + 2:]
        elif q.startswith(file_marker):
            user_part = ""
            file_part = q
        else:
            user_part = q
            file_part = ""

        if file_part:
            filenames = re.findall(r"📎 檔案：([^\n（(]+)", file_part)
            cleaned_tags = [f"📎 {name.strip()}" for name in filenames if name.strip()]
            if cleaned_tags:
                tags_block = "\n".join(cleaned_tags)
                q = f"{user_part}\n\n{tags_block}".strip() if user_part else tags_block
            else:
                q = user_part

    return q


def _infer_history_source(raw_query: str, actions: Optional[List[str]] = None) -> str:
    if raw_query.startswith("[背景任務 · ") or any(
        str(a).startswith(("task_report", "task_manager")) for a in (actions or [])
    ):
        return "background_report"
    if "[來源管道: iOS" in raw_query:
        return "ios"
    if "[來源管道: Apple Watch" in raw_query:
        return "watch"
    if "[來源管道: HomePod" in raw_query:
        return "homepod"
    return "telegram"


@app.get("/api/history")
def chat_history(
    limit: int = 40,
    since: Optional[str] = None,
    before: Optional[str] = None,
    _authorized: bool = Depends(verify_token),
):
    """回傳統一時間軸對話紀錄（data/history.jsonl，支援 since/before 增量與分頁拉取）"""
    limit = max(1, min(limit, 200))
    entries = []
    try:
        with open(settings.HISTORY_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                ts = item.get("timestamp", "")
                if since and ts <= since:
                    continue
                if before and ts >= before:
                    continue
                raw_q = item.get("query", "")
                clean_q = "" if raw_q.startswith("[背景任務 · ") else _clean_history_query(raw_q)
                meta_obj = item.get("metadata")
                qr_list = (meta_obj.get("quick_replies") if isinstance(meta_obj, dict) else None) or item.get("quick_replies") or []
                entries.append({
                    "timestamp": ts,
                    "query": clean_q,
                    "reply": item.get("reply", ""),
                    "source": item.get("source") or _infer_history_source(raw_q, item.get("actions")),
                    "actions": item.get("actions", []),
                    "metadata": meta_obj,
                    "images": item.get("images", []),
                    "quick_replies": qr_list,
                })
    except FileNotFoundError:
        pass
    return {
        "history": entries[-limit:],
        "active_turn": _get_active_turn_snapshot(),
        "pending_queue": _get_pending_queue_snapshot(),
    }


@app.get("/api/history/search")
def search_chat_history(
    q: str = "",
    role: str = "all",
    limit: int = 50,
    _authorized: bool = Depends(verify_token),
):
    """全文搜尋對話紀錄（同時搜尋提問與助理回覆，由新到舊回傳）"""
    terms = [t.strip().lower() for t in (q or "").split() if t.strip()]
    if not terms:
        return {"results": [], "total": 0}

    role_norm = (role or "all").strip().lower()
    if role_norm not in ("all", "user", "assistant"):
        role_norm = "all"
    limit = max(1, min(limit, 100))

    raw_lines: list[str] = []
    try:
        with open(settings.HISTORY_LOG_PATH, encoding="utf-8") as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        return {"results": [], "total": 0}

    results = []
    for line in reversed(raw_lines):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue

        raw_q = item.get("query", "") or ""
        clean_q = "" if raw_q.startswith("[背景任務 · ") else _clean_history_query(raw_q)
        reply = item.get("reply", "") or ""

        q_lower = clean_q.lower()
        r_lower = reply.lower()
        user_match = bool(clean_q) and all(t in q_lower for t in terms)
        ast_match = bool(reply) and all(t in r_lower for t in terms)

        if role_norm == "user" and not user_match:
            continue
        if role_norm == "assistant" and not ast_match:
            continue
        if role_norm == "all" and not (user_match or ast_match):
            continue

        matched_in = "both" if (user_match and ast_match) else ("user" if user_match else "assistant")
        results.append({
            "timestamp": item.get("timestamp", ""),
            "query": clean_q,
            "reply": reply,
            "matched_in": matched_in,
            "source": item.get("source") or _infer_history_source(raw_q, item.get("actions")),
        })
        if len(results) >= limit:
            break

    return {"results": results, "total": len(results)}


@app.get("/api/history/images/{filename}")
def get_history_image(
    filename: str,
    _authorized: bool = Depends(verify_token),
):
    """安全讀取伺服器保存的壓縮歷史對話圖片"""
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name != filename or ".." in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    img_dir = (settings.DATA_DIR / "history_images").resolve()
    img_path = (img_dir / safe_name).resolve()
    if img_path.parent != img_dir or not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(img_path), media_type="image/jpeg")


@app.post("/api/memory/sync-now")
async def trigger_memory_sync():
    """手動觸發記憶庫審查、Git 歸檔與 Telegram 推播"""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, sync_and_commit_memory, False)
    return result


class InternalMemorySyncRequest(BaseModel):
    summary: str
    source: str = "herdr-coordinator"
    report_to_user: bool = False
    task_name: Optional[str] = None
    task_status: Optional[str] = None
    exit_code: Optional[int] = None


@app.post("/api/internal/memory-sync")
async def internal_memory_sync(
    payload: InternalMemorySyncRequest,
    _authorized: bool = Depends(verify_token),
):
    """背景任務完工後，趁主大腦閒置時靜默注入摘要（或由大腦整理後寫入純助理回報）；忙碌則入列"""
    summary = (payload.summary or "").strip()
    if not summary:
        return {"status": "ignored", "synced": False, "reason": "empty_summary"}

    task_meta = {
        k: v
        for k, v in {
            "task_name": payload.task_name,
            "task_status": payload.task_status,
            "exit_code": payload.exit_code,
        }.items()
        if v is not None
    } or None

    loop = asyncio.get_running_loop()
    ok, reason = await loop.run_in_executor(
        None,
        try_inject_memory_sync,
        summary,
        payload.source,
        payload.report_to_user,
        task_meta,
    )
    if ok:
        return {"status": "ok", "synced": True}
    if reason in ("brain_busy", "worker_not_ready"):
        q_len = enqueue_memory_sync(
            summary,
            payload.source,
            report_to_user=payload.report_to_user,
            metadata=task_meta,
        )
        return {"status": "queued", "synced": False, "reason": reason, "queued_count": q_len}
    return {"status": "skipped", "synced": False, "reason": reason}


class SharedFileItem(BaseModel):
    filename: str
    mime_type: Optional[str] = None
    data_base64: str


class SharePayload(BaseModel):
    prompt: str = "幫我快速摘要重點"
    url: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None
    images_base64: Optional[List[str]] = None
    files: Optional[List[SharedFileItem]] = None


def _process_share_payload_sync(
    prompt: str,
    url: str,
    text: str,
    title: str,
    media: Optional[List[dict]] = None,
    file_contexts: Optional[List[str]] = None,
) -> None:
    """背景處理 iOS Share Extension 傳來的網址、文字、圖片或文件，完畢後寫入對話紀錄並推播 Telegram"""
    parts = []
    if title:
        parts.append(f"標題：{title}")
    if url:
        parts.append(f"網址：{url}")
    if text and text != url:
        parts.append(f"內容：{text[:2000]}")
    if media:
        parts.append(f"附帶多媒體數量：{len(media)} 個")
    if file_contexts:
        parts.append("\n\n".join(file_contexts))
    shared_block = "\n".join(parts)

    display_target = url or title or text[:120] or (f"📎 {len(file_contexts)} 份文件" if file_contexts else (f"📷 {len(media)} 張圖片" if media else ""))
    display_query = f"📤 {prompt}\n{display_target}".strip()

    full_prompt = (
        f"[來源管道: iOS 分享擴充]\n"
        f"【回覆風格】簡明扼要、重點條列，使用完整 Markdown 格式。\n\n"
        f"{prompt}\n\n{shared_block}"
    )

    share_cancel_ev = _threading.Event()
    share_turn_id, is_active_now, _ = _TURN_QUEUE.enqueue_or_activate(
        display_query, share_cancel_ev, client="ios-share"
    )
    try:
        if not is_active_now:
            activated = _TURN_QUEUE.wait_until_active_or_cancelled(share_turn_id, share_cancel_ev)
            if not activated:
                return
        clean_reply, actions, _, _ = brain_router.send_query(full_prompt, media=media)
    except Exception as ex:
        clean_reply = f"分享內容處理失敗：{ex}"
        actions = ["share_extension_error"]
    finally:
        _TURN_QUEUE.finish_active(share_turn_id)

    try:
        memory_manager.log_interaction(
            display_query,
            clean_reply,
            actions=actions or ["share_extension"],
            image_paths=[m["uri"] for m in media if m.get("uri")] if media else None,
        )
    except Exception as log_ex:
        print(f"[Share] 寫入對話紀錄失敗: {log_ex}")

    try:
        send_formatted_telegram_reply(
            f"📤 分享任務完成｜{prompt}\n\n{clean_reply}"
        )
    except Exception as tg_ex:
        print(f"[Share] Telegram 推播失敗: {tg_ex}")


@app.post("/api/share")
async def receive_ios_share(
    payload: SharePayload,
    _authorized: bool = Depends(verify_token),
):
    """接收 iOS Share Extension 傳來的連結/文字/圖片/文件與自訂 Prompt，秒級回傳並於背景處理"""
    import base64
    import time
    import uuid
    from share_file_parser import sanitize_filename

    prompt = (payload.prompt or "").strip() or "幫我快速摘要重點"
    url = (payload.url or "").strip()
    text = (payload.text or "").strip()
    title = (payload.title or "").strip()
    raw_images = [img for img in (payload.images_base64 or []) if img and img.strip()][:9]
    raw_files = [f for f in (payload.files or []) if f.data_base64 and f.data_base64.strip()][:5]

    if not (url or text or title or raw_images or raw_files):
        raise HTTPException(status_code=400, detail="分享內容不能為空")

    effective_media: List[dict] = []
    file_contexts: List[str] = []
    upload_dir = settings.BASE_DIR / "data" / "ios_uploads"

    if raw_images:
        upload_dir.mkdir(parents=True, exist_ok=True)
        for img_b64 in raw_images:
            try:
                raw_bytes = base64.b64decode(img_b64)
                saved_file = upload_dir / f"share_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                saved_file.write_bytes(raw_bytes)
                effective_media.append({
                    "mime_type": "image/jpeg",
                    "uri": str(saved_file.resolve()),
                })
            except Exception as ex:
                print(f"[Share] Failed to decode shared image: {ex}")

    if raw_files:
        upload_dir.mkdir(parents=True, exist_ok=True)
        for item in raw_files:
            try:
                raw_bytes = base64.b64decode(item.data_base64)
                safe_name = sanitize_filename(item.filename, fallback="shared_doc.bin")
                saved_file = upload_dir / f"share_{int(time.time())}_{uuid.uuid4().hex[:6]}_{safe_name}"
                saved_file.write_bytes(raw_bytes)
                file_contexts.append(f"📎 檔案：{safe_name}（本機路徑：{saved_file.resolve()}）")
            except Exception as ex:
                print(f"[Share] Failed to decode shared file {item.filename}: {ex}")

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _process_share_payload_sync,
        prompt,
        url,
        text,
        title,
        effective_media or None,
        file_contexts or None,
    )
    return {
        "status": "accepted",
        "message": "已交由 Mac mini 背景處理",
        "image_count": len(raw_images),
        "file_count": len(file_contexts),
    }

@app.get("/api/tasks/tracked")
def get_tracked_tasks(_authorized: bool = Depends(verify_token)):
    """回傳進行中與最近失敗/異常背景任務清單（過濾掉成功的已完成任務）"""
    from task_manager import task_manager
    running = task_manager.list_tasks(include_finished=False)
    failed = task_manager.list_failed_tasks(limit=30)
    return {
        "running": [
            {
                "task_id": t.get("task_id", ""),
                "name": t.get("name", ""),
                "status": t.get("status", "RUNNING"),
                "started_at": t.get("started_at", ""),
                "finished_at": t.get("finished_at", ""),
                "ttl_minutes": t.get("ttl_minutes", 0),
                "heartbeat_timeout_minutes": t.get("heartbeat_timeout_minutes", 0),
            }
            for t in running
        ],
        "failed": failed,
    }


@app.get("/api/tasks/{task_id}/log")
def get_task_log_endpoint(
    task_id: str,
    tail: int = 200,
    _authorized: bool = Depends(verify_token),
):
    """安全讀取指定背景任務的日誌尾部內容"""
    from task_manager import task_manager
    res = task_manager.get_task_log(task_id, tail_lines=tail)
    if not res.get("exists"):
        err = res.get("error", "not_found")
        code = 400 if err == "invalid_task_id" else 404
        raise HTTPException(status_code=code, detail=err)
    return res


@app.get("/api/status")
@app.get("/api/usage")
@app.get("/api/quota")
def get_usage(refresh: bool = False):
    """查看 Gemini 5h / 7days 與 3P 模型的即時額度配額 (預設使用 30s 快取，傳入 ?refresh=true 強制即時重抓)"""
    from quota_service import quota_service
    stat = brain_router.get_status()
    quota_data = quota_service.get_quota_summary(force_refresh=refresh)
    brain_catalog = brain_router.get_catalog()
    return {
        "status": "ok",
        "quota": quota_data,
        "context": stat,
        "brain": brain_catalog,
        "supports_quota": brain_catalog.get("supports_quota", True),
        "background_tasks": quota_data.get("background_tasks", {})
    }


class BrainSelectRequest(BaseModel):
    engine: str
    model: Optional[str] = None


@app.get("/api/brain/options")
def get_brain_options(_authorized: bool = Depends(verify_token)):
    """取得所有可用主腦引擎（agy / omp）、各引擎精選模型白名單與是否支援額度查詢"""
    return brain_router.get_catalog()


@app.post("/api/brain/select")
def select_brain_engine(
    payload: BrainSelectRequest,
    _authorized: bool = Depends(verify_token)
):
    """切換目前使用的主腦引擎與指定模型"""
    try:
        catalog = brain_router.select_engine(payload.engine.strip(), payload.model)
        return {"status": "ok", **catalog}
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@app.post("/api/quota/compact")
def compact_context(_authorized: bool = Depends(verify_token)):
    """觸發 ACP 原生 `/compact` 無損壓縮對話記憶，釋放 Context Window 空間"""
    res = brain_router.compact_session()
    stat = brain_router.get_status()
    return {
        "status": "ok" if res.get("success") else "error",
        "compact": res,
        "context": stat
    }


from pathlib import Path

VOICE_VOCABULARY_PATH = Path(__file__).resolve().parent / "data" / "voice_vocabulary.json"
DEFAULT_VOICE_VOCABULARY: List[str] = [
    "等一下", "Gemini", "Mac mini", "HomePod", "Apple Watch", "Tailscale",
    "FastAPI", "SwiftUI", "Python", "main.py", "agent.py", "agy", "omp", "Herdr",
    "SideStore", "Coupang", "酷澎", "Home Assistant", "Telegram",
    "Whisper", "GitHub", "Google Calendar", "Google Sheets",
    "Xcode", "API", "Token", "Subagent", "TaskManager", "Chrome",
    "FinLab", "TAIFEX", "TX", "MTX", "TMF", "00631L", "0050",
    "VIX", "MDD", "CAGR", "Sharpe", "P/C Ratio", "Put/Call Ratio",
    "Buy Call", "Buy Put", "Sell Call", "Sell Put", "Overfit",
    "冷氣", "新風機", "待辦事項", "待買清單",
]


def _dedupe_words(words: List[str]) -> List[str]:
    seen = set()
    cleaned: List[str] = []
    for w in words:
        s = str(w).strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    return cleaned


def _load_voice_vocabulary() -> dict:
    if VOICE_VOCABULARY_PATH.exists():
        try:
            with open(VOICE_VOCABULARY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("vocabulary"), list):
                    vocab = _dedupe_words(data["vocabulary"])
                    if vocab:
                        return {
                            "vocabulary": vocab,
                            "updated_at": data.get("updated_at", ""),
                        }
        except Exception as exc:
            print(f"[VoiceVocab] ⚠️ Failed to load voice_vocabulary.json: {exc}")
    return {"vocabulary": list(DEFAULT_VOICE_VOCABULARY), "updated_at": ""}


def _save_voice_vocabulary(words: List[str]) -> dict:
    vocab = _dedupe_words(words)
    payload = {
        "vocabulary": vocab,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    VOICE_VOCABULARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VOICE_VOCABULARY_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


class VoiceVocabularyUpdateRequest(BaseModel):
    vocabulary: Optional[List[str]] = None
    add: Optional[List[str]] = None
    remove: Optional[List[str]] = None


@app.get("/api/voice/vocabulary")
def get_voice_vocabulary(_authorized: bool = Depends(verify_token)):
    """取得動態語音辨識熱詞詞庫（供 iOS App 啟動時同步注入 SFSpeechRecognizer.contextualStrings）"""
    data = _load_voice_vocabulary()
    return {"status": "ok", **data}


@app.post("/api/voice/vocabulary")
def update_voice_vocabulary(
    payload: VoiceVocabularyUpdateRequest,
    _authorized: bool = Depends(verify_token),
):
    """更新動態語音辨識熱詞詞庫（支援整份覆寫或增量增刪）"""
    if payload.vocabulary is not None:
        words = payload.vocabulary
    else:
        words = _load_voice_vocabulary()["vocabulary"]
        if payload.add:
            words = words + payload.add
        if payload.remove:
            rm_set = {str(w).strip() for w in payload.remove if str(w).strip()}
            words = [w for w in words if w not in rm_set]
    saved = _save_voice_vocabulary(words)
    return {"status": "ok", **saved}


@app.post("/api/app/state")
def update_app_state(req: AppStateRequest, _authorized: bool = Depends(verify_token)):
    """回報 iOS App 前景/背景狀態；回到前景讀取時自動撤回未讀的唯一重要通知。"""
    with _APP_STATE_LOCK:
        prev_active = bool(_APP_STATE.get("active", True))
        has_unread = bool(_APP_STATE.get("has_unread_alert", False))
        need_boot_clear = not bool(_APP_STATE.get("cleared_on_boot", False))
        _APP_STATE["active"] = bool(req.active)
        _APP_STATE["updated_at"] = time.time()
        should_clear = bool(req.active) and (has_unread or not prev_active or need_boot_clear)
        if req.active:
            _APP_STATE["cleared_on_boot"] = True
    if should_clear:
        threading.Thread(target=_clear_app_unread_alert, kwargs={"force": True}, daemon=True).start()
    return {"status": "ok", "active": bool(req.active), "clear_scheduled": should_clear}


@app.post("/api/internal/unread-notify")
def internal_unread_notify(req: UnreadNotifyRequest, _authorized: bool = Depends(verify_token)):
    """供背景任務（如 TaskManager）透過統一 iOS 未讀重要通知通道發送回報；若 App 在前景則略過不打擾。"""
    with _APP_STATE_LOCK:
        app_in_foreground = bool(_APP_STATE.get("active", True))
    if app_in_foreground and not req.force:
        return {"status": "skipped", "reason": "app_in_foreground", "sent": False}
    sent = _notify_app_unread_reply(req.message, title=req.title)
    return {"status": "ok" if sent else "error", "sent": sent}


def _extract_model_from_terminal_text(text: str) -> str:
    """從 herdr 終端機可視畫面或狀態列擷取目前啟用中的模型名稱。"""
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        m_agy = re.search(r"模型:\s*([^│\n]+)", ln)
        if m_agy:
            return m_agy.group(1).strip()
        m_set = re.search(r"Model set to\s+([^\n]+)", ln)
        if m_set:
            return m_set.group(1).strip()
        m_omp = re.search(r"((?:Muse Spark|Claude|Gemini|GPT)[^·│\n]+)", ln)
        if m_omp and ("·" in ln or "/" in ln or "%" in ln):
            return m_omp.group(1).strip()
    return ""


def _detect_herdr_agent_model(pane_id: str, engine: str, agent_session: Optional[dict] = None) -> str:
    """偵測指定 herdr pane 目前使用中的模型名稱，優先讀終端狀態列，必要時回退查 session 日誌。"""
    import subprocess
    from pathlib import Path

    if pane_id:
        try:
            proc = subprocess.run(
                ["herdr", "agent", "read", pane_id, "--source", "visible", "--lines", "15"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0:
                raw = json.loads(proc.stdout or "{}")
                visible_text = raw.get("result", {}).get("text") or raw.get("text") or proc.stdout or ""
                found = _extract_model_from_terminal_text(visible_text)
                if found:
                    return found
        except Exception:
            pass

    sess_val = (agent_session or {}).get("value") or ""
    if engine == "omp" and sess_val:
        try:
            p = Path(sess_val)
            if p.exists():
                tail_lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]
                for raw_ln in reversed(tail_lines):
                    try:
                        obj = json.loads(raw_ln)
                    except Exception:
                        continue
                    if obj.get("type") == "model_change" and obj.get("modelId"):
                        return str(obj["modelId"])
                    msg = obj.get("message") or {}
                    if isinstance(msg, dict) and msg.get("model"):
                        return str(msg["model"])
        except Exception:
            pass
    return ""


@app.get("/api/herdr/agents")
def list_herdr_agents(_authorized: bool = Depends(verify_token)):
    """即時查詢本機線上 Herdr Agent 清單，供 iOS 輸入框 @ 選單挑選派工目標。"""
    import subprocess
    from pathlib import Path

    try:
        proc = subprocess.run(
            ["herdr", "agent", "list"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return {"agents": [], "error": proc.stderr.strip() or "herdr exited non-zero"}
        raw = json.loads(proc.stdout or "{}")
        raw_agents = raw.get("result", {}).get("agents", [])
        agents = []
        for item in raw_agents:
            cwd = item.get("cwd") or item.get("foreground_cwd") or ""
            project = Path(cwd).name if cwd else (item.get("name") or item.get("pane_id") or "unknown")
            name = item.get("name") or project
            pane_id = item.get("pane_id") or project
            engine = item.get("agent") or ""
            model_name = _detect_herdr_agent_model(pane_id, engine, item.get("agent_session"))
            agents.append({
                "id": pane_id,
                "label": project,
                "name": name,
                "project": project,
                "engine": engine,
                "model": model_name,
                "status": item.get("agent_status") or "unknown",
                "pane_id": pane_id,
                "cwd": cwd,
                "focused": bool(item.get("focused", False)),
            })
        return {"agents": agents}
    except Exception as ex:
        return {"agents": [], "error": str(ex)}


@app.get("/api/actions/pending")
def list_pending_actions(_authorized: bool = Depends(verify_token)):
    """取得目前 Herdr 派工督工抽屜（進行中 / 待決策 / 完工待檢閱）與歷史歸檔紀錄。"""
    with _PENDING_ACTIONS_LOCK:
        items = _load_pending_actions()
        history = _load_herdr_history()
    attention_count, working_count = _compute_action_counts(items)
    return {
        "actions": items,
        "count": len(items),
        "attention_count": attention_count,
        "working_count": working_count,
        "history": history,
    }


@app.post("/api/actions/pending")
def create_pending_action(req: PendingActionCreateRequest, _authorized: bool = Depends(verify_token)):
    """新增或更新一筆 Herdr 派工進度、待決策或完工項目至 Action Queue。"""
    item = _upsert_pending_action(
        agent=req.agent,
        title=req.title,
        question=req.question,
        options=req.options,
        source=req.source,
        context_prompt=req.context_prompt,
        status=req.status,
        task_prompt=req.task_prompt,
        summary=req.summary,
        elapsed_seconds=req.elapsed_seconds,
        tool_count=req.tool_count,
        tool_calls=req.tool_calls,
        live_preview=req.live_preview,
        terminal_tail=req.terminal_tail,
        retry_count=req.retry_count,
        max_retries=req.max_retries,
    )
    with _PENDING_ACTIONS_LOCK:
        items = _load_pending_actions()
        history = _load_herdr_history()
    attention_count, working_count = _compute_action_counts(items)
    return {
        "status": "ok",
        "action": item,
        "count": len(items),
        "attention_count": attention_count,
        "working_count": working_count,
        "history": history,
    }


@app.post("/api/actions/pending/{action_id}/resolve")
def resolve_pending_action(action_id: str, req: PendingActionResolveRequest, _authorized: bool = Depends(verify_token)):
    """處理主人於督工抽屜做出的決策、原視窗續派修改（Follow-up）或結案歸檔。"""
    import subprocess
    import sys

    with _PENDING_ACTIONS_LOCK:
        items = _load_pending_actions()
        history_items = _load_herdr_history()
        target = next((x for x in items if x.get("id") == action_id), None)
        from_history = False
        if not target:
            target = next((x for x in history_items if x.get("id") == action_id), None)
            from_history = target is not None
        if not target:
            raise HTTPException(status_code=404, detail="找不到該待決策或派工項目")

    relayed = False
    dispatched_to = None
    now_iso = datetime.now().isoformat()
    target_status = (target.get("status") or "waiting_decision").strip().lower()
    is_tracked_lifecycle = bool(target.get("task_prompt") or target.get("rounds") or target_status in ("working", "retrying", "completed", "archived"))

    if req.dismiss:
        with _PENDING_ACTIONS_LOCK:
            if from_history:
                new_hist = [x for x in _load_herdr_history() if x.get("id") != action_id]
                _save_herdr_history(new_hist)
                remaining = _load_pending_actions()
            else:
                if is_tracked_lifecycle and not (target.get("source") or "").startswith("brain"):
                    _archive_herdr_action(target)
                remaining = [x for x in _load_pending_actions() if x.get("id") != action_id]
                _save_pending_actions(remaining)
            history_out = _load_herdr_history()
        attention_count, working_count = _compute_action_counts(remaining)
        return {
            "status": "ok",
            "relayed": False,
            "dispatched_to": "archived" if is_tracked_lifecycle else None,
            "actions": remaining,
            "count": len(remaining),
            "attention_count": attention_count,
            "working_count": working_count,
            "history": history_out,
        }

    answer_text = (req.answer or "").strip()
    if not answer_text:
        raise HTTPException(status_code=400, detail="回覆內容不可為空")

    source_str = (target.get("source") or "herdr").strip().lower()
    if source_str.startswith("brain"):
        title_str = (target.get("title") or "").strip()
        ctx = (target.get("context_prompt") or "").strip()
        q = (target.get("question") or "").strip()
        combined_prompt = (
            "【接續先前待決策事項（來自 Action Queue）】\n"
            + (f"決策標題：{title_str}\n" if title_str else "")
            + (f"原始任務背景：{ctx}\n" if ctx else "")
            + (f"先前反問內容：{q}\n" if q else "")
            + f"主人已選擇 / 回覆：{answer_text}\n\n"
            + "請根據以上完整前後文直接接續完成任務並回報結果。"
        )
        threading.Thread(
            target=chat,
            args=(ChatRequest(query=combined_prompt, client="ios-app"), None, True),
            daemon=True,
        ).start()
        relayed = True
        dispatched_to = "brain"
        with _PENDING_ACTIONS_LOCK:
            remaining = [x for x in _load_pending_actions() if x.get("id") != action_id]
            _save_pending_actions(remaining)
            history_out = _load_herdr_history()
    else:
        agent_target = (target.get("agent") or "").strip()
        is_followup = (req.action == "followup") or (target_status in ("completed", "archived")) or from_history

        if is_followup:
            if agent_target:
                proc = subprocess.run(
                    [
                        sys.executable,
                        "scripts/herdr_coordinator.py",
                        "dispatch",
                        "--agent",
                        agent_target,
                        "--prompt",
                        answer_text,
                        "--force",
                    ],
                    cwd=str(settings.BASE_DIR),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode != 0:
                    raise HTTPException(status_code=502, detail=proc.stderr.strip() or "續派任務失敗")
                relayed = True
                dispatched_to = "herdr_followup"

            with _PENDING_ACTIONS_LOCK:
                current_items = _load_pending_actions()
                prev_rounds = list(target.get("rounds") or [])
                prev_round_no = int(target.get("round_no") or 1)
                if target.get("task_prompt") or target.get("summary") or target.get("question"):
                    prev_rounds.append({
                        "round_no": prev_round_no,
                        "task_prompt": (target.get("task_prompt") or "").strip(),
                        "summary": (target.get("summary") or target.get("question") or "").strip(),
                        "completed_at": target.get("updated_at") or target.get("created_at") or now_iso,
                        "tool_count": int(target.get("tool_count") or 0),
                    })
                updated_card = dict(target)
                updated_card["status"] = "working"
                updated_card["round_no"] = prev_round_no + 1
                updated_card["rounds"] = prev_rounds
                updated_card["task_prompt"] = answer_text
                updated_card["question"] = "🔨 續派任務已送出，工人執行中..."
                updated_card["summary"] = ""
                updated_card["options"] = []
                updated_card["live_preview"] = f"第 {prev_round_no + 1} 輪續派：{answer_text[:80]}"
                updated_card["elapsed_seconds"] = 0
                updated_card["tool_count"] = 0
                updated_card["tool_calls"] = []
                updated_card["retry_count"] = 0
                updated_card["updated_at"] = now_iso

                remaining = [x for x in current_items if x.get("id") != action_id and x.get("agent") != agent_target]
                remaining.insert(0, updated_card)
                _save_pending_actions(remaining)

                if from_history:
                    new_hist = [x for x in _load_herdr_history() if x.get("id") != action_id]
                    _save_herdr_history(new_hist)
                history_out = _load_herdr_history()
        else:
            if agent_target:
                proc = subprocess.run(
                    [sys.executable, "scripts/herdr_coordinator.py", "relay", "--agent", agent_target, "--answer", answer_text],
                    cwd=str(settings.BASE_DIR),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode != 0:
                    raise HTTPException(status_code=502, detail=proc.stderr.strip() or "relay 回寫失敗")
                relayed = True
                dispatched_to = "herdr"

            with _PENDING_ACTIONS_LOCK:
                current_items = _load_pending_actions()
                if is_tracked_lifecycle:
                    remaining = []
                    for x in current_items:
                        if x.get("id") == action_id:
                            updated = dict(x)
                            updated["status"] = "working"
                            updated["options"] = []
                            updated["question"] = f"🔨 已回覆決策（{answer_text}），工人繼續執行中..."
                            updated["live_preview"] = f"已送出決策：{answer_text[:80]}"
                            updated["updated_at"] = now_iso
                            remaining.append(updated)
                        else:
                            remaining.append(x)
                else:
                    remaining = [x for x in current_items if x.get("id") != action_id]
                _save_pending_actions(remaining)
                history_out = _load_herdr_history()

    attention_count, working_count = _compute_action_counts(remaining)
    return {
        "status": "ok",
        "relayed": relayed,
        "dispatched_to": dispatched_to,
        "actions": remaining,
        "count": len(remaining),
        "attention_count": attention_count,
        "working_count": working_count,
        "history": history_out,
    }


@app.post("/api/system/safe-reload")
def trigger_safe_reload(_authorized: bool = Depends(verify_token)):
    """排程背景安全重載（等語音空檔後透過 launchd 重啟後端服務）"""
    import subprocess
    import sys
    from pathlib import Path

    cmd = [
        sys.executable,
        "task_manager.py",
        "spawn-cmd",
        "--name",
        "App手動安全重載",
        "--cmd",
        f"{sys.executable} scripts/idle_safe_reload.py --quiet 4 --interval 2 </dev/null",
        "--notify",
        "ios",
        "--ttl",
        "5",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parent),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=proc.stderr.strip() or "無法排程安全重載")
    task_id = ""
    try:
        task_id = json.loads(proc.stdout).get("task_id", "")
    except Exception:
        pass
    return {
        "status": "scheduled",
        "task_id": task_id,
        "message": "已排程安全重載，將於語音空檔自動重啟 Mac mini 後端。",
    }

@app.get("/api/demands")
def get_demands():
    """查看已記錄的需求日誌清單"""
    if not settings.DEMANDS_LOG_PATH.exists():
        return {"demands": []}
    
    demands = []
    with open(settings.DEMANDS_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    demands.append(json.loads(line))
                except Exception:
                    pass
    return {"demands": demands}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
