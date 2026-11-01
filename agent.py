"""
Personal Agent 核心直通介面 (Direct Passthrough)
將請求直通至 fast_intent 極速通道或常駐代理守護進程 (支援 agy 與 claude 引擎切換)。
支援依據 client 端點 (homepod / apple-watch) 分流語音輸出長度。
"""
from typing import Tuple, List, Generator, Optional
from config import settings
from fast_intent import try_fast_intent

def get_brain():
    """依據 AGENT_ENGINE 配置動態取得常駐大腦單例 (agy 或 claude)"""
    engine = getattr(settings, "AGENT_ENGINE", "agy").lower()
    if engine == "claude":
        from claude_singleton import claude_singleton
        return claude_singleton
    from agy_singleton import agy_singleton
    return agy_singleton

class PersonalAgent:
    def process(self, query: str, client: str = "apple-watch", media: Optional[List[dict]] = None, level: Optional[str] = None) -> Tuple[str, List[str]]:
        """直通處理查詢：優先經由 Fast-Path，未命中或有圖片則直通常駐代理"""
        if not media:
            fast_reply = try_fast_intent(query)
            if fast_reply:
                return fast_reply, ["engine: fast-path"]
        
        # 依據來源管道加上分流標籤，引導 LLM 動態調整長度與風格
        if client == "homepod":
            formatted_query = f"[來源管道: HomePod 音箱] {query}"
        elif client == "apple-watch":
            formatted_query = f"[來源管道: Apple Watch] {query}"
        else:
            formatted_query = query
        
        brain = get_brain()
        reply, actions, ttft, total = brain.send_query(formatted_query, media=media, level=level)
        if media and not any("multimodal" in a for a in actions):
            actions.append(f"multimodal: {len(media)} images" if len(media) > 1 else "multimodal: image")
        return reply, actions

    def stream(self, query: str, client: str = "apple-watch", media: Optional[List[dict]] = None, level: Optional[str] = None) -> Generator[Tuple[str, str], None, dict]:
        """直通串流處理"""
        if not media:
            fast_reply = try_fast_intent(query)
            if fast_reply:
                yield ("text", fast_reply)
                return {"status": "SUCCESS", "mode": "fast-path", "clean_reply": fast_reply}
        
        if client == "homepod":
            formatted_query = f"[來源管道: HomePod 音箱] {query}"
        elif client == "apple-watch":
            formatted_query = f"[來源管道: Apple Watch] {query}"
        else:
            formatted_query = query

        brain = get_brain()
        return (yield from brain.send_query_stream(formatted_query, media=media, level=level))

personal_agent = PersonalAgent()
