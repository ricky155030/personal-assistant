#!/usr/bin/env python3
"""
CTBC Bank Statement Parser (中國信託)
支援:
1. 加密 PDF 電子帳單 (含 /UNICxxxx 編碼解碼)
2. 純文字/HTML 信用卡帳單繳款通知
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class CTBCParser(BaseBillParser):
    bank_name = "中國信託"
    bank_code = "ctbc"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        # 情況 A: 有 PDF 附件
        if pdf_bytes:
            reader = self.decrypt_pdf(pdf_bytes, [self.user_id] if self.user_id else None)
            if reader:
                return self._parse_pdf(reader)

        # 情況 B: 降級解析純文字信件內文
        body = email_meta.get("body", "") or email_meta.get("snippet", "")
        if body and "信用卡帳單" in email_meta.get("subject", ""):
            return self._parse_text_body(body, email_meta.get("date", ""))

        return None

    def _parse_pdf(self, reader) -> Dict[str, Any]:
        text_pages = [self.decode_unicode_escapes(p.extract_text()) for p in reader.pages]
        text_all = "\n".join(text_pages)

        # 1. 提取結帳日與繳款截止日
        stmt_date = ""
        due_date = ""
        m_stmt = re.search(r'(\d{2,3}/\d{2}/\d{2})\s+[\d,]+\s+([\d,]+)\s+0\s+[\d,]+', text_pages[1] if len(text_pages) > 1 else text_all)
        if m_stmt:
            stmt_date = m_stmt.group(1).strip()

        m_due = re.search(r'(\d{2,3}/\d{2}/\d{2})', text_pages[0])
        if m_due:
            due_date = m_due.group(1).strip()

        # 2. 提取應繳金額
        due_amount = 0
        m_amt = re.search(r'([\d,]+)\s+0\s+[\d,]+\s+0\s+\+([\d,]+)', text_all)
        if m_amt:
            due_amount = int(m_amt.group(2).replace(",", ""))
        else:
            m_amt2 = re.search(r'\$([\d,]+)', text_pages[-1])
            if m_amt2:
                due_amount = int(m_amt2.group(1).replace(",", ""))

        # 3. 月份推導
        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            year_ad = int(parts[0]) + 1911 if int(parts[0]) < 1900 else int(parts[0])
            stmt_month = f"{year_ad}-{int(parts[1]):02d}"

        # 4. 解析逐筆消費 (Page 2)
        transactions = []
        if len(text_pages) > 1:
            for line in text_pages[1].splitlines():
                line_str = line.strip()
                # 匹配行: 115/08/21 115/08/24 NETFLIX.COM 560 3325 SG TWD 560.00
                m_tx = re.search(r'(\d{2,3}/\d{2}/\d{2})\s+(\d{2,3}/\d{2}/\d{2})\s+(.+?)\s+([-\d,]+)\s+(\d{4})(?:\s+([A-Z]{2,3}))?', line_str)
                if m_tx:
                    tx_date, post_date, desc, amt_str, card_last4, extra = m_tx.groups()
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
            "min_payment": 1000,
            "auto_pay": True,
            "auto_pay_account": "中信帳戶",
            "transactions": transactions
        }

    def _parse_text_body(self, body: str, email_date: str) -> Dict[str, Any]:
        # 從純文字信件解析
        m_amt = re.search(r'帳單金額\s*[:：]?\s*([\d,]+)', body)
        due_amount = int(m_amt.group(1).replace(",", "")) if m_amt else 0

        m_month = re.search(r'帳單月份\s*[:：]?\s*(\d{4}/\d{2})', body)
        stmt_month = m_month.group(1).replace("/", "-") if m_month else ""

        m_due = re.search(r'繳款截止日\s*[:：]?\s*(\d{4}/\d{2}/\d{2})', body)
        due_date = m_due.group(1) if m_due else ""

        m_acc = re.search(r'扣款帳號\s*[:：]?\s*([^ \n]+)', body)
        acc = m_acc.group(1) if m_acc else "中信帳戶"

        return {
            "bank_code": self.bank_code,
            "bank_name": self.bank_name,
            "statement_month": stmt_month,
            "statement_date": "",
            "due_date": due_date,
            "due_amount": due_amount,
            "min_payment": 1000,
            "auto_pay": True,
            "auto_pay_account": self.mask_sensitive_info(acc, self.user_id),
            "transactions": []
        }
