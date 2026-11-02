#!/usr/bin/env python3
"""
Sinopac Bank Statement Parser (永豐銀行)
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class SinopacParser(BaseBillParser):
    bank_name = "永豐銀行"
    bank_code = "sinopac"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        if not pdf_bytes:
            return None

        reader = self.decrypt_pdf(pdf_bytes, [self.user_id] if self.user_id else None)
        if not reader:
            return None

        text_p1 = reader.pages[0].extract_text()
        text_all = "\n".join([p.extract_text() for p in reader.pages])

        # 1. 提取結帳日與截止日
        stmt_date = ""
        due_date = ""
        m_stmt = re.search(r'結帳日\s*(\d{4}/\d{2}/\d{2})', text_p1)
        if m_stmt:
            stmt_date = m_stmt.group(1).strip()

        m_due = re.search(r'繳款截止日\s*(\d{4}/\d{2}/\d{2})', text_p1)
        if m_due:
            due_date = m_due.group(1).strip()

        # 2. 提取本期應繳總金額與扣款金額
        due_amount = 0
        min_payment = 0
        m_due_amt = re.search(r'您的預定扣款金額\s*([\d,]+)\s*元', text_p1)
        if m_due_amt:
            due_amount = int(m_due_amt.group(1).replace(",", ""))
        else:
            m_total = re.search(r'本期應繳總金額\s*\n\s*([\d,]+)', text_p1)
            if m_total:
                due_amount = int(m_total.group(1).replace(",", ""))

        m_min = re.search(r'本期最低應繳金額\s*([\d,]+)', text_p1)
        if m_min:
            min_payment = int(m_min.group(1).replace(",", ""))

        # 3. 自動扣繳判定
        auto_pay = "預定扣款金額" in text_p1 or "自動扣款" in text_p1
        auto_pay_account = "永豐大戶" if auto_pay else ""

        # 4. 月份
        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            stmt_month = f"{parts[0]}-{int(parts[1]):02d}"

        # 5. 解析逐筆消費 (Page 2)
        transactions = []
        for page in reader.pages[1:]:
            lines = page.extract_text().splitlines()
            for line in lines:
                line_str = line.strip()
                # 匹配格式: 09/14 09/17 2204 優步－永和豆漿大王 光復店 239
                m_tx = re.match(r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(\d{4})\s+(.+?)\s+([-\d,]+)$', line_str)
                if m_tx:
                    tx_date, post_date, card_last4, desc, amt_str = m_tx.groups()
                    desc = desc.strip()
                    if "自扣已入帳" in desc:
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
                        "currency": "TWD",
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
            "auto_pay": auto_pay,
            "auto_pay_account": auto_pay_account,
            "transactions": transactions
        }
