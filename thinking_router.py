"""
Thinking Level 前置路由器 (Thinking Router)
在請求送入模型前的 0.001 秒內，依據語意特徵、句型結構與複雜度進行零延遲分類。

架構設計原則：
  - 預設思考等級走 med (Gemini 3.8 Flash Medium)：
    自然問答、購物比較、旅遊推薦、育兒知識、決策推導等無窮情境，均自動享受中等推理深度，
    無需再手動維護無窮的關鍵字清單。
  - 明確操作型白名單走 low (Gemini 3.8 Flash Low)：
    僅限家中智慧家電開關、傳訊給老婆/iMessage、時間/天氣、極短問候與待辦登記等「閉集 (Closed-set)」極速動作。
  - 深度報告與程式除錯走 high (Gemini 3.8 Flash High)：
    顯式指定深度、架構分析、代碼除錯、詳細專案報告等。
"""

import re
from typing import Tuple

HIGH_KEYWORDS = [
    "詳細", "深入", "深度", "完整說明", "詳細說明", "詳細點", "詳細解釋", "詳細分析",
    "深入分析", "深度分析", "完整報告", "深度報告", "詳細報告", "做一份報告", "做網頁報告", "做報告",
    "寫程式", "寫代碼", "寫code", "程式碼", "代碼", "除錯", "debug",
    "架構設計", "架構分析", "全面剖析", "技術方案", "重構代碼", "deadlock", "死鎖"
]

HIGH_PATTERNS = [
    re.compile(r"(做|產出|整理|生成|製作).*(一份)?(報告|網頁報告|圖表)"),
    re.compile(r"(完整|深度|深入|全面).*(分析|剖析|評估|報告)"),
]

# 僅有已知的極速操作型意圖 (Closed-set Fast Intents) 進入 Low
LOW_PATTERNS = [
    # 1. 智慧家電控制與狀態查詢
    re.compile(r"(打開|開啟|開|關掉|關閉|關)\s*(一下)?\s*(全部|所有)?\s*(客廳|房間|臥室|玄關|走廊|走道|廚房|廁所|浴室|陽台|書房|更衣室|餐廳)?\s*(燈|大燈|頂燈|吸頂燈|氛圍燈|夜燈|小燈|冷氣|空調|除濕機|新風機|電視|風扇|電扇|插座|窗簾|離子夾)"),
    re.compile(r"(客廳|房間|臥室|玄關|走廊|走道|廚房|廁所|浴室|陽台|書房|更衣室|餐廳)?\s*(燈|大燈|頂燈|吸頂燈|氛圍燈|夜燈|小燈|冷氣|空調|除濕機|新風機|電視|風扇|電扇|插座|窗簾|離子夾)\s*(打開|開啟|開|關掉|關閉|關)"),
    re.compile(r"(冷氣|空調)?\s*(溫度)?\s*(調到|設為|設定為|調為|調成)\s*[0-9]+(\.[0-9]+)?\s*度?"),
    re.compile(r"(客廳|房間|臥室|室內|家裡|目前|現在)\s*(的)?\s*(溫度|濕度|溫濕度|幾度)"),
    re.compile(r"^(開燈|關燈|開冷氣|關冷氣|開電視|關電視|開風扇|關風扇|開新風|關新風|開除濕機|關除濕機)$"),

    # 2. 傳訊息給老婆 / iMessage
    re.compile(r"(傳訊息|發訊息|傳訊|發訊|傳|發)\s*(給)?\s*(老婆|太太|老婆大人)"),
    re.compile(r"(跟|向|幫我跟|幫我向)\s*(老婆|太太|老婆大人)\s*(說|講|報備)"),
    re.compile(r"(發|傳)\s*(一則|個)?\s*(iMessage|簡訊)"),

    # 3. 日常時鐘與簡單天氣
    re.compile(r"^(現在幾點|現在几點|現在時間|今天幾號|今天星期幾|今天禮拜幾)[？?嗎啊吧呢\s]*$"),
    re.compile(r"(今天天氣|現在天氣|出門要帶傘嗎|今天氣溫|天氣如何|會下雨嗎)"),

    # 4. 快速新增待辦 / 提醒
    re.compile(r"^(提醒我|新增待辦|加到待辦|記在待辦|幫我記在待辦|記到待辦|記一下|幫我記一下|新增行事曆|記在行事曆)\s*"),

    # 5. 極簡問候與確認
    re.compile(r"^(早安|午安|晚安|你好|嗨|哈囉|hello|hi|hey|ok|好的|收到|謝謝|多謝|感謝|掰掰|再見)[！!。~～\s]*$", re.IGNORECASE),

    # 6. 純即時位置查詢
    re.compile(r"^(我現在在哪裡|我現在在哪|我目前在哪裡|我目前在哪|現在位置|目前位置|我現在的位置|我目前的位置|現在在哪裡|現在在哪)[？?嗎啊吧呢\s]*$")
]


def classify_thinking_level(query: str, client: str = "") -> str:
    """
    零延遲分類使用者問題所屬思考強度。
    分流優先順序：
      1. 顯式指令覆寫 (/high, /med, /low)
      2. High 關鍵字與特徵句型 (深度、代碼、架構、報告)
      3. Low 白名單 (家電控制、傳訊給老婆、時鐘天氣、簡短待辦/問候)
      4. 預設 Fallback: med (所有生活決策、購物、旅遊、知識與自然對話)
    回傳值: 'low' | 'med' | 'high'
    """
    if not query:
        return "med"

    q = query.strip()
    # 去除來源管道標記，例如 [來源管道: Apple Watch]
    q_clean = re.sub(r"^\[來源管道:[^\]]+\]\s*", "", q).strip()

    # 1. 顯式指令覆寫 (優先級最高)
    if q_clean.startswith("/high") or q_clean.startswith("！高") or q_clean.startswith("!high"):
        return "high"
    if q_clean.startswith("/med") or q_clean.startswith("/medium") or q_clean.startswith("！中") or q_clean.startswith("!med"):
        return "med"
    if q_clean.startswith("/low") or q_clean.startswith("！低") or q_clean.startswith("!low"):
        return "low"

    # 2. 命中 High 特徵 (深度報告、代碼除錯、顯式要求詳細)
    if any(k in q_clean for k in HIGH_KEYWORDS) or any(p.search(q_clean) for p in HIGH_PATTERNS):
        return "high"

    # 3. 命中 Low 白名單 (家電控制、傳訊給老婆、時鐘天氣、極短問候)
    if any(p.search(q_clean) for p in LOW_PATTERNS):
        return "low"

    # 4. 預設全面走 Med (涵蓋所有購物、旅遊、育兒、決策比較與開放性知識)
    return "med"


def clean_query_prefix(query: str) -> str:
    """清理指令前綴（如 /high, /med, /low）以還原乾淨 prompt"""
    q = query.strip()
    q = re.sub(r"^\[來源管道:[^\]]+\]\s*", "", q).strip()
    for prefix in ["/high", "/med", "/medium", "/low", "！高", "!high", "！中", "!med", "！低", "!low"]:
        if q.startswith(prefix):
            return q[len(prefix):].strip()
    return q
