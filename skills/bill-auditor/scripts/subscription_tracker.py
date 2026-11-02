#!/usr/bin/env python3
"""
Subscription & Recurring Expenses Tracker
自動從已解析之帳單中辨識定期扣款（SaaS、串流娛樂、公用事業、電信費等）
"""

import re
from typing import List, Dict, Any

SUBSCRIPTION_KEYWORDS = [
    (r'netflix', 'Netflix 串流影音', '娛樂串流'),
    (r'spotify', 'Spotify 音樂串流', '娛樂串流'),
    (r'disney\+', 'Disney+ 串流影音', '娛樂串流'),
    (r'youtube.*premium', 'YouTube Premium', '娛樂串流'),
    (r'apple\.com/bill|itunes', 'Apple 訂閱服務 (iCloud/App)', '數位服務'),
    (r'google.*storage|google.*one', 'Google One 雲端硬碟', '數位服務'),
    (r'openai|chatgpt', 'OpenAI ChatGPT Plus', 'AI 生產力'),
    (r'anthropic|claude', 'Anthropic Claude Pro', 'AI 生產力'),
    (r'cursor', 'Cursor AI 程式碼編輯器', 'AI 生產力'),
    (r'github', 'GitHub 訂閱/Copilot', '開發工具'),
    (r'stripe.*z\.ai', 'Z.AI 程式輔助工具', 'AI 生產力'),
    (r'台灣電力|台電', '台灣電力公司 (電費)', '水電瓦斯'),
    (r'台灣自來水|水費', '台灣自來水公司 (水費)', '水電瓦斯'),
    (r'新竹瓦斯|天然氣|瓦斯費', '天然氣瓦斯費', '水電瓦斯'),
    (r'台灣大電信|中華電信|遠傳電信', '電信月租費', '通訊網路'),
    (r'遠通電收', 'eTag 國道通行費自動儲值', '交通出行'),
    (r'芬嚮資訊', '芬嚮資訊 (定期軟體/平台)', '數位服務')
]

def analyze_subscriptions(all_transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    從所有交易中比對固定訂閱與公用事業扣款
    回傳聚合後之固定支出項目列表
    """
    detected = {}

    for tx in all_transactions:
        desc = tx.get("description", "").lower()
        amt = tx.get("amount", 0)
        bank = tx.get("bank", "")
        card_last4 = tx.get("card_last4", "")
        tx_date = tx.get("date", "")

        matched_name = None
        matched_cat = "一般固定扣款"

        for pattern, name, cat in SUBSCRIPTION_KEYWORDS:
            if re.search(pattern, desc, re.IGNORECASE):
                matched_name = name
                matched_cat = cat
                break

        if matched_name:
            key = f"{matched_name}_{card_last4}"
            if key not in detected:
                detected[key] = {
                    "name": matched_name,
                    "category": matched_cat,
                    "bank": bank,
                    "card_last4": card_last4,
                    "latest_amount": amt,
                    "latest_date": tx_date,
                    "occurrences": 1,
                    "history": [f"{tx_date}: NT${amt}"]
                }
            else:
                detected[key]["occurrences"] += 1
                detected[key]["history"].append(f"{tx_date}: NT${amt}")
                if tx_date > detected[key]["latest_date"]:
                    detected[key]["latest_date"] = tx_date
                    detected[key]["latest_amount"] = amt

    # 轉為列表並排序 (依金額由大到小)
    results = list(detected.values())
    results.sort(key=lambda x: x["latest_amount"], reverse=True)
    return results
