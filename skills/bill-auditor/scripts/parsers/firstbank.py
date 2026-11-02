#!/usr/bin/env python3
"""
First Bank Statement Parser (第一銀行)
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class FirstBankParser(BaseBillParser):
    bank_name = "第一銀行"
    bank_code = "firstbank"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        if not pdf_bytes:
            return None

        reader = self.decrypt_pdf(pdf_bytes, [self.user_id] if self.user_id else None)
        if not reader:
            return None

        text_pages = [p.extract_text() for p in reader.pages]
        text_all = "\n".join(text_pages)

        # 1. 提取結帳日與扣繳日
        stmt_date = ""
        due_date = ""
        m_stmt = re.search(r'帳單結帳日期\s*\n\s*新臺幣\s+[\d\s]+\s+(\d{2,3}/\d{2}/\d{2})', text_all)
        if not m_stmt:
            m_stmt = re.search(r'(\d{2,3}/\d{2}/\d{2})', text_all)
        if m_stmt:
            stmt_date = m_stmt.group(1).strip()

        m_due = re.search(r'本期將於\s*(\d{2,3}/\d{2}/\d{2})\s*由帳號\s*([^\s]+)\s*自動扣繳', text_all)
        auto_pay = False
        auto_pay_account = ""
        if m_due:
            due_date = m_due.group(1).strip()
            auto_pay = True
            auto_pay_account = m_due.group(2).strip()

        # 2. 提取應繳金額
        due_amount = 0
        m_amt = re.search(r'您的本期金額總計\s*([\d,]+)', text_all)
        if m_amt:
            due_amount = int(m_amt.group(1).replace(",", ""))
        else:
            m_amt2 = re.search(r'□應繳總額：([\d,]+)', text_all)
            if m_amt2:
                due_amount = int(m_amt2.group(1).replace(",", ""))

        # 3. 月份推導
        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            year_ad = int(parts[0]) + 1911 if int(parts[0]) < 1900 else int(parts[0])
            stmt_month = f"{year_ad}-{int(parts[1]):02d}"

        # 4. 提取逐筆消費
        transactions = []
        for page in text_pages:
            lines = page.splitlines()
            for line in lines:
                line_str = line.strip()
                # 匹配如: 10/02 10/06 ＭＹＰＡ－ＨｉＥＶ 1,000 8790
                m_tx = re.match(r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([\d,]+)\s+(\d{4})', line_str)
                if m_tx:
                    tx_date, post_date, desc, amt_str, card_last4 = m_tx.groups()
                    try:
                        amt = int(amt_str.replace(",", ""))
                    except ValueError:
                        continue

                    transactions.append({
                        "date": tx_date,
                        "post_date": post_date,
                        "description": self.mask_sensitive_info(desc.strip(), self.user_id),
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
            "min_payment": due_amount,
            "auto_pay": auto_pay,
            "auto_pay_account": self.mask_sensitive_info(auto_pay_account, self.user_id),
            "transactions": transactions
        }
