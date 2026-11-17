"""
Personal Agent 核心直通介面 (Direct Passthrough)
將請求直通至 fast_intent 極速通道或 agy_singleton 常駐代理守護進程，全面消除歷史過渡代碼。
支援依據 client 端點 (homepod / apple-watch) 分流語音輸出長度。
iOS Voice 長度以客戶端 `ResponseLengthOption.promptInstruction`（【回覆長度約束：...】）為準，
後端僅在缺失時補預設 80~120 字，確保 agy/omp/claude 各大腦規則一致（見 AGENTS.md 模式 D）。
"""
from typing import Tuple, List, Generator, Optional
from brain_engine import brain_router
from fast_intent import try_fast_intent

LENGTH_CONSTRAINT_MARKER = "【回覆長度約束"
DEFAULT_IOS_LENGTH_LINE = "1. 長度限制（預設標準模式）：主人正在戶外散步或開車免持聆聽，回覆必須極度精簡俐落、直奔核心結論！總字數嚴格控制在 80~120 字以內（確保能在 30 秒內朗讀完畢），絕不長篇贅述。若查詢已攜帶【回覆長度約束：...】（標準/細節/深度），以客戶端指定為準，本條不覆寫。\n"
IOS_VOICE_STYLE_LINES = (
    "2. 嚴禁無謂代碼塊：嚴格禁止使用 ```text 或代碼區塊包覆一般文字、天氣或清單！一般說明請使用流暢口語段落或精簡符號條列（- ）。只有當主人明確要求撰寫程式碼時，才允許出現代碼區塊。\n"
    "3. 純淨口語風格：切勿唸出生硬符號或長網址，保持像隨身技術夥伴一樣自然、流暢、親切。\n"
    "4. 語音同音字智慧糾錯：輸入來自 iOS 即時語音轉文字，若中英文專有名詞、檔名或指令出現同音諧音誤差，請直接依上下文與專案背景自動還原正確語意執行。\n"
    "5. 可選視覺小卡：遇到股價、天氣、數據對比或結構化摘要等適合卡片呈現的內容時，可在回覆末尾附加 `<widget_html>...</widget_html>`（純淨自包含 HTML/CSS/SVG，不會影響語音朗讀）。\n"
)


def build_ios_voice_query(query: str) -> str:
    """組裝 iOS 語音 prompt：客戶端已帶長度約束則保留，否則補預設；供各大腦共用單一規則。"""
    q = query or ""
    if LENGTH_CONSTRAINT_MARKER in q:
        length_line = "1. 長度限制：查詢已攜帶【回覆長度約束：...】（標準 80~150 / 細節 250~350 / 深度無限制），嚴格遵照該約束執行，不另行壓縮。\n"
    else:
        length_line = DEFAULT_IOS_LENGTH_LINE
    return (
        "[來源管道: iOS 隨身語音對話 App (免持對話模式)]\n"
        "【語音輸出嚴格規範】\n"
        f"{length_line}"
        f"{IOS_VOICE_STYLE_LINES}\n"
        f"{q}"
    )

class PersonalAgent:
    def _log_fast(self, query: str, reply: str) -> None:
        try:
            from memory_manager import memory_manager as _mm
            from widget_html_utils import extract_widget_html as _extract
            _stripped, _widget = _extract(reply)
            _meta = {"engine": "fast-path"}
            if _widget:
                _meta["widget_html"] = _widget
            _mm.log_interaction(
                query,
                (_stripped or "").strip() or reply,
                actions=["engine: fast-path"],
                metadata=_meta,
            )
        except Exception:
            pass

    def process(self, query: str, client: str = "apple-watch", media: Optional[List[dict]] = None, level: Optional[str] = None) -> Tuple[str, List[str]]:
        """直通處理查詢：優先經由 Fast-Path，未命中或有圖片則直通常駐 agy 代理"""
        if not media:
            fast_reply = try_fast_intent(query)
            if fast_reply:
                self._log_fast(query, fast_reply)
                return fast_reply, ["engine: fast-path"]
        # 依據來源管道加上分流標籤，引導 LLM 動態調整長度與風格
        if client == "homepod":
            formatted_query = f"[來源管道: HomePod 音箱] {query}"
        elif client == "apple-watch":
            formatted_query = f"[來源管道: Apple Watch] {query}"
        elif client == "ios-voice":
            formatted_query = build_ios_voice_query(query)
        else:
            formatted_query = query
        
        reply, actions, ttft, total = brain_router.send_query(formatted_query, media=media, level=level)
        if media and not any("multimodal" in a for a in actions):
            actions.append(f"multimodal: {len(media)} images" if len(media) > 1 else "multimodal: image")
        return reply, actions

    def stream(self, query: str, client: str = "apple-watch", media: Optional[List[dict]] = None, level: Optional[str] = None) -> Generator[Tuple[str, str], None, dict]:
        """直通串流處理"""
        if not media:
            fast_reply = try_fast_intent(query)
            if fast_reply:
                self._log_fast(query, fast_reply)
                yield ("text", fast_reply)
                return {"status": "SUCCESS", "mode": "fast-path", "clean_reply": fast_reply}
        
        if client == "homepod":
            formatted_query = f"[來源管道: HomePod 音箱] {query}"
        elif client == "apple-watch":
            formatted_query = f"[來源管道: Apple Watch] {query}"
        elif client == "ios-voice":
            formatted_query = build_ios_voice_query(query)
        else:
            formatted_query = query

        return (yield from brain_router.send_query_stream(formatted_query, media=media, level=level))

personal_agent = PersonalAgent()
