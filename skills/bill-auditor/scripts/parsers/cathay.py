#!/usr/bin/env python3
"""
Cathay Bank Statement Parser (國泰世華)
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class CathayParser(BaseBillParser):
    bank_name = "國泰世華"
    bank_code = "cathay"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        if not pdf_bytes:
            # 支援純文字摘要
            body = email_meta.get("body", "")
            return None

        reader = self.decrypt_pdf(pdf_bytes, [self.user_id] if self.user_id else None)
        if not reader:
            return None

        text_pages = [p.extract_text() for p in reader.pages]
        text_all = "\n".join(text_pages)

        # 1. 提取結帳日與截止日 (115/09/20, 115/10/06)
        stmt_date = ""
        due_date = ""
        m_dates = re.findall(r'(\d{2,3}/\d{2}/\d{2})', text_all)
        if len(m_dates) >= 2:
            stmt_date = m_dates[0]
            due_date = m_dates[1]

        # 2. 提取應繳金額
        due_amount = 0
        min_payment = 0
        m_amt = re.search(r'新臺幣TWD\s+[\d,]+\s+[\d,]+\s+([\d,]+)\s+0\s+0\s+0\s+([\d,]+)', text_all)
        if m_amt:
            due_amount = int(m_amt.group(1).replace(",", ""))
            min_payment = int(m_amt.group(2).replace(",", ""))

        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            year_ad = int(parts[0]) + 1911 if int(parts[0]) < 1900 else int(parts[0])
            stmt_month = f"{year_ad}-{int(parts[1]):02d}"

        # 3. 提取逐筆消費 (酷澎、蝦皮等)
        transactions = []
        for page in text_pages:
            for line in page.splitlines():
                line_str = line.strip()
                # 匹配格式: 08/24 08/28 ＣＯＵＰＡＮＧ 1,628 5551 TW TWD
                m_tx = re.match(r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([-\d,]+)\s+(\d{4})\s+([A-Z]{2})\s+([A-Z]{3})', line_str)
                if m_tx:
                    tx_date, post_date, desc, amt_str, card_last4, country, curr = m_tx.groups()
                    desc = desc.strip()
                    if "自動扣繳" in desc:
                        continue
                    try:
                        amt = int(amt_str.replace(",", ""))
                    except ValueError:
                        continue

                    transactions.append({
                        "date": tx_date,
                        "post_date": post_date,
                        "description": self.mask_sensitive_info(desc, self.user_id),
                        "amount": amt,
                        "currency": curr,
                        "foreign_amount": None,
                        "card_last4": card_last4,
                        "bank": self.bank_name
                    })

        return {
            "bank_code": self.bank_code,
            "bank_name": self.bank_name,
            "statement_month": stmt_month,
            "statement_date": stmt_date,
            "due_date": due_date,
            "due_amount": due_amount,
            "min_payment": min_payment,
            "auto_pay": True,
            "auto_pay_account": "國泰世華帳戶",
            "transactions": transactions
        }
