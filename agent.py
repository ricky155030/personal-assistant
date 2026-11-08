"""
Personal Agent 核心直通介面 (Direct Passthrough)
將請求直通至 fast_intent 極速通道或 agy_singleton 常駐代理守護進程，全面消除歷史過渡代碼。
支援依據 client 端點 (homepod / apple-watch) 分流語音輸出長度。
"""
from typing import Tuple, List, Generator, Optional
from agy_singleton import agy_singleton
from fast_intent import try_fast_intent

class PersonalAgent:
    def process(self, query: str, client: str = "apple-watch", media: Optional[List[dict]] = None, level: Optional[str] = None) -> Tuple[str, List[str]]:
        """直通處理查詢：優先經由 Fast-Path，未命中或有圖片則直通常駐 agy 代理"""
        if not media:
            fast_reply = try_fast_intent(query)
            if fast_reply:
                return fast_reply, ["engine: fast-path"]
        
        # 依據來源管道加上分流標籤，引導 LLM 動態調整長度與風格
        if client == "homepod":
            formatted_query = f"[來源管道: HomePod 音箱] {query}"
        elif client == "apple-watch":
            formatted_query = f"[來源管道: Apple Watch] {query}"
        elif client == "ios-voice":
            formatted_query = (
                "[來源管道: iOS 隨身語音對話 App (免持對話模式)]\n"
                "【語音輸出嚴格規範】\n"
                "1. 長度限制（最重要）：主人正在戶外散步或開車免持聆聽，回覆必須極度精簡俐落、直奔核心結論！總字數嚴格控制在 80~120 字以內（確保能在 30 秒內朗讀完畢），絕不長篇贅述。\n"
                "2. 嚴禁無謂代碼塊：嚴格禁止使用 ```text 或代碼區塊包覆一般文字、天氣或清單！一般說明請使用流暢口語段落或精簡符號條列（- ）。只有當主人明確要求撰寫程式碼時，才允許出現代碼區塊。\n"
                "3. 純淨口語風格：切勿唸出生硬符號或長網址，保持像隨身技術夥伴一樣自然、流暢、親切。\n"
                "4. 語音同音字智慧糾錯：輸入來自 iOS 即時語音轉文字，若中英文專有名詞、檔名或指令出現同音諧音誤差，請直接依上下文與專案背景自動還原正確語意執行。\n\n"
                f"{query}"
            )
        else:
            formatted_query = query
        
        reply, actions, ttft, total = agy_singleton.send_query(formatted_query, media=media, level=level)
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
        elif client == "ios-voice":
            formatted_query = (
                "[來源管道: iOS 隨身語音對話 App (免持對話模式)]\n"
                "【語音輸出嚴格規範】\n"
                "1. 長度限制（最重要）：主人正在戶外散步或開車免持聆聽，回覆必須極度精簡俐落、直奔核心結論！總字數嚴格控制在 80~120 字以內（確保能在 30 秒內朗讀完畢），絕不長篇贅述。\n"
                "2. 嚴禁無謂代碼塊：嚴格禁止使用 ```text 或代碼區塊包覆一般文字、天氣或清單！一般說明請使用流暢口語段落或精簡符號條列（- ）。只有當主人明確要求撰寫程式碼時，才允許出現代碼區塊。\n"
                "3. 純淨口語風格：切勿唸出生硬符號或長網址，保持像隨身技術夥伴一樣自然、流暢、親切。\n"
                "4. 語音同音字智慧糾錯：輸入來自 iOS 即時語音轉文字，若中英文專有名詞、檔名或指令出現同音諧音誤差，請直接依上下文與專案背景自動還原正確語意執行。\n\n"
                f"{query}"
            )
        else:
            formatted_query = query

        return (yield from agy_singleton.send_query_stream(formatted_query, media=media, level=level))

personal_agent = PersonalAgent()
