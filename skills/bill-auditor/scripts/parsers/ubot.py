#!/usr/bin/env python3
"""
UBOT Bank Statement Parser (聯邦銀行)
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class UbotParser(BaseBillParser):
    bank_name = "聯邦銀行"
    bank_code = "ubot"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        # 若有繳款成功通知信
        subject = email_meta.get("subject", "")
        body = email_meta.get("body", "") or email_meta.get("snippet", "")

        if "入帳通知" in subject:
            m_amt = re.search(r'付款金額為\s*([\d,]+)\s*元', body)
            amt = int(m_amt.group(1).replace(",", "")) if m_amt else 0
            m_date = re.search(r'已於\s*(\d{8})\s*收到', body)
            d_str = m_date.group(1) if m_date else ""
            return {
                "bank_code": self.bank_code,
                "bank_name": self.bank_name,
                "statement_month": f"{d_str[:4]}-{d_str[4:6]}" if len(d_str) >= 6 else "",
                "statement_date": "",
                "due_date": "",
                "due_amount": amt,
                "min_payment": amt,
                "auto_pay": False,
                "auto_pay_account": "",
                "is_paid": True,
                "transactions": []
            }

        if not pdf_bytes:
            return None

        reader = self.decrypt_pdf(pdf_bytes, [self.user_id, self.birth_mmdd, self.user_id[-4:] + self.birth_mmdd])
        if not reader:
            return None

        text_pages = [p.extract_text() for p in reader.pages]
        text_all = "\n".join(text_pages)

        # 1. 提取結帳日與截止日 (115/09/06, 115/09/21)
        stmt_date = ""
        due_date = ""
        m_dates = re.findall(r'(\d{2,3}/\d{2}/\d{2})', text_all)
        if len(m_dates) >= 2:
            due_date = m_dates[0]
            stmt_date = m_dates[1]

        # 2. 提取應繳金額
        due_amount = 0
        m_tot = re.search(r'總計\s*([\d,]+)', text_all)
        if m_tot:
            due_amount = int(m_tot.group(1).replace(",", ""))

        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            year_ad = int(parts[0]) + 1911 if int(parts[0]) < 1900 else int(parts[0])
            stmt_month = f"{year_ad}-{int(parts[1]):02d}"

        # 3. 提取逐筆消費 (EVOASIS, 特斯拉等)
        transactions = []
        for page in text_pages:
            for line in page.splitlines():
                line_str = line.strip()
                # 匹配格式: 08/18 08/15 綠界－源點科技股份有限公司 TW 366
                m_tx = re.match(r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([A-Z]{2})\s+([-\d,]+)$', line_str)
                if m_tx:
                    post_date, tx_date, desc, country, amt_str = m_tx.groups()
                    desc = desc.strip()
                    try:
                        amt = int(amt_str.replace(",", ""))
                    except ValueError:
                        continue

                    transactions.append({
                        "date": tx_date,
                        "post_date": post_date,
                        "description": self.mask_sensitive_info(desc, self.user_id),
                        "amount": amt,
                        "currency": "TWD",
                        "foreign_amount": None,
                        "card_last4": "0104",
                        "bank": self.bank_name
                    })

        return {
            "bank_code": self.bank_code,
            "bank_name": self.bank_name,
            "statement_month": stmt_month,
            "statement_date": stmt_date,
            "due_date": due_date,
            "due_amount": due_amount,
            "min_payment": due_amount,
            "auto_pay": False,
            "auto_pay_account": "",
            "transactions": transactions
        }
