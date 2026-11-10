import os
import re
import json
import asyncio
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Header, HTTPException, Depends, status, File, Form, UploadFile
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings
from agent import personal_agent
from memory_manager import memory_manager
from agy_singleton import agy_singleton
from fast_intent import try_fast_intent
from task_queue import task_queue
from transcribe_service import transcribe_file, warm_transcriber
from telegram_service import (
    start_telegram_bot,
    send_telegram_sync,
    format_telegram_response,
    send_formatted_telegram_reply
)
from memory_scheduler import run_memory_scheduler, sync_and_commit_memory

# 語音朗讀專用：全面清除 Emoji 表情符號與 Markdown 標記，避免 Siri 唸出「笑臉」等雜音
EMOJI_PATTERN = re.compile(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u200d\ufe0f]", flags=re.UNICODE)

def sanitize_for_tts(text: str) -> str:
    """全面清除所有 Emoji、Markdown 標記與網址，確保 Siri 語音朗讀純淨無雜音"""
    clean = re.sub(r"https?://\S+", "", text)
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
        agy_singleton.start()
    if settings.TELEGRAM_BOT_TOKEN:
        asyncio.create_task(start_telegram_bot())
    asyncio.create_task(run_memory_scheduler())
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
    level: Optional[str] = None
    tts_voice: Optional[str] = "zh-TW-HsiaoChenNeural"
    tts_rate: Optional[str] = "+0%"

class ChatResponse(BaseModel):
    reply: str
    actions: List[str]
    timestamp: str
    received_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_seconds: Optional[float] = None
    task_id: Optional[str] = None # async 模式回傳，供 GET /api/chat/result/{task_id} 輪詢

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

            print(
                f"\n┌─────────────────────────────────────────────────────────────\n"
                f"│ ⏱️  [Timing Benchmark] 極速通道命中 (來源: /api/chat, 裝置: {client_source})\n"
                f"│ ⚡ 總處理耗時 (Latency): {latency:.3f} 秒\n"
                f"│ 💬 Prompt 內容:        \"{user_query}\"\n"
                f"│ 🤖 設備端回覆:         \"{output_reply}\"\n"
                f"└─────────────────────────────────────────────────────────────\n",
                flush=True
            )

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


@app.post("/api/voice-chat/cancel")
def cancel_voice_chat(
    payload: Optional[CancelChatRequest] = None,
    _authorized: bool = Depends(verify_token)
):
    """iOS 端一鍵停止/語音打斷專用端點：
    1. 觸發所有活躍 SSE 串流之 cancel_event，立即中止 TTS 合成並攔截 Telegram 推播
    2. 對底層 agy acp 發送原生 `session/cancel`，於 6~15ms 內強制中止大腦思考與執行中工具
    """
    level = payload.level if payload else None
    with _VOICE_CANCEL_LOCK:
        active_count = len(_ACTIVE_VOICE_CANCEL_EVENTS)
        for ev in list(_ACTIVE_VOICE_CANCEL_EVENTS):
            ev.set()
    acp_res = agy_singleton.cancel_active_turns(level=level)
    print(f"[cancel_voice_chat] 🛑 收到取消請求 (active_streams={active_count}, acp_cancelled={acp_res})")
    return {
        "status": "cancelled",
        "active_streams_signaled": active_count,
        "acp": acp_res
    }


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

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()
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
            if cancel_event.is_set():
                tts_queue.task_done()
                continue
            try:
                audio_b64 = await synthesize_to_base64(sentence, voice=tts_voice, rate=tts_rate)
                if audio_b64 and not cancel_event.is_set():
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

    tts_task = asyncio.create_task(tts_worker())

    if not user_query and not effective_media:
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

    if not user_query and effective_media:
        user_query = f"請幫我分析這 {len(effective_media)} 張圖片。" if len(effective_media) > 1 else "請幫我分析這張圖片"

    existing_reports = {f.name: f.stat().st_mtime for f in settings.REPORTS_DIR.glob("*.html")}

    def stream_worker():
        actions = [f"client: {client_source}"]
        full_reply_parts = []
        turn_result = {}
        extractor = TTSSentenceExtractor()
        sentence_seq = 0

        try:
            gen = personal_agent.stream(
                user_query,
                client=client_source,
                media=effective_media if effective_media else None,
                level=level
            )
            while True:
                if cancel_event.is_set():
                    agy_singleton.cancel_active_turns(level=level)
                    break
                try:
                    ev_type, ev_data = next(gen)
                except StopIteration as stop_err:
                    turn_result = stop_err.value or {}
                    break

                if cancel_event.is_set():
                    agy_singleton.cancel_active_turns(level=level)
                    break

                if ev_type == "thought":
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "thought_delta", "delta": ev_data}
                    )
                elif ev_type == "status":
                    status_text = ev_data
                    clean_status = sanitize_for_tts(status_text)
                    actions.append(f"tool: {status_text[:30]}")
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "status", "text": status_text, "tts": clean_status}
                    )
                elif ev_type == "text":
                    delta = ev_data
                    full_reply_parts.append(delta)
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "text_delta", "delta": delta}
                    )
                    sentences = extractor.feed(delta)
                    for sent in sentences:
                        seq = sentence_seq
                        sentence_seq += 1
                        loop.call_soon_threadsafe(
                            event_queue.put_nowait,
                            {"type": "tts_sentence", "sentence": sent, "seq": seq}
                        )
                        loop.call_soon_threadsafe(
                            tts_queue.put_nowait,
                            (seq, sent)
                        )

            was_cancelled = cancel_event.is_set() or bool(turn_result.get("cancelled"))
            if was_cancelled:
                loop.call_soon_threadsafe(tts_queue.put_nowait, None)
                print(f"[voice_chat_stream] 🛑 本次語音請求已中途取消，略過後續 TTS 與 Telegram 推播: {user_query[:30]}")
                return

            # Flush 剩餘未結尾句子
            for sent in extractor.flush():
                seq = sentence_seq
                sentence_seq += 1
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    {"type": "tts_sentence", "sentence": sent, "seq": seq}
                )
                loop.call_soon_threadsafe(
                    tts_queue.put_nowait,
                    (seq, sent)
                )

            # 通知 tts_queue 已送完所有句子，並等待 TTS 合成排隊完成
            loop.call_soon_threadsafe(tts_queue.put_nowait, None)
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
            clean_reply = full_reply.replace("[TELEGRAM_FULL_REPORT]", "\n\n").strip()

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
            done_payload = {
                "type": "done",
                "full_reply": clean_reply,
                "latency": total_latency,
                "thinking_level": level or "med",
                "report_url": report_url,
                "quick_replies": quick_replies
            }
            loop.call_soon_threadsafe(event_queue.put_nowait, done_payload)

            # 串流結束後，同步至 Telegram
            try:
                send_formatted_telegram_reply(
                    reply=clean_reply,
                    user_query=user_query,
                    latency=total_latency,
                    actions=actions,
                    report_link_html=report_link_html,
                    thinking_level=turn_result.get("thinking_level", level),
                    thought_text=turn_result.get("thought"),
                    quick_replies=quick_replies
                )
            except Exception as tg_e:
                print(f"[voice_chat_stream] Telegram sync error: {tg_e}")

        except Exception as e:
            print(f"[voice_chat_stream] Worker exception: {e}")
            loop.call_soon_threadsafe(event_queue.put_nowait, {"type": "error", "error": str(e)})
        finally:
            worker_finished.set()
            with _VOICE_CANCEL_LOCK:
                _ACTIVE_VOICE_CANCEL_EVENTS.discard(cancel_event)
            loop.call_soon_threadsafe(event_queue.put_nowait, None)

    threading.Thread(target=stream_worker, daemon=True).start()

    async def event_generator():
        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            if not worker_finished.is_set() and not cancel_event.is_set():
                cancel_event.set()
                agy_singleton.cancel_active_turns(level=level)
                print(f"[voice_chat_stream] 🔌 SSE 客戶端中斷連線，已自動觸發 ACP session/cancel")
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


@app.get("/api/history")
def chat_history(limit: int = 20, _authorized: bool = Depends(verify_token)):
    """回傳最近的對話紀錄（data/history.jsonl 尾端 limit 筆，由舊到新，上限 200）"""
    limit = max(1, min(limit, 200))
    entries = []
    try:
        with open(settings.HISTORY_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(line)
    except FileNotFoundError:
        pass
    return {"history": [json.loads(entry) for entry in entries[-limit:]]}


@app.post("/api/memory/sync-now")
async def trigger_memory_sync():
    """手動觸發記憶庫審查、Git 歸檔與 Telegram 推播"""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, sync_and_commit_memory, False)
    return result

@app.get("/api/status")
@app.get("/api/usage")
@app.get("/api/quota")
def get_usage(refresh: bool = False):
    """查看 Gemini 5h / 7days 與 3P 模型的即時額度配額 (預設使用 30s 快取，傳入 ?refresh=true 強制即時重抓)"""
    from quota_service import quota_service
    stat = agy_singleton.get_status()
    quota_data = quota_service.get_quota_summary(force_refresh=refresh)
    return {
        "status": "ok",
        "quota": quota_data,
        "context": stat,
        "background_tasks": quota_data.get("background_tasks", {})
    }


@app.post("/api/quota/compact")
def compact_context(_authorized: bool = Depends(verify_token)):
    """觸發 ACP 原生 `/compact` 無損壓縮對話記憶，釋放 Context Window 空間"""
    res = agy_singleton.compact_session()
    stat = agy_singleton.get_status()
    return {
        "status": "ok" if res.get("success") else "error",
        "compact": res,
        "context": stat
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
