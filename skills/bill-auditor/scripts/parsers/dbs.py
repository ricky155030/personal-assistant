#!/usr/bin/env python3
"""
DBS Bank Statement Parser (星展銀行)
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class DBSParser(BaseBillParser):
    bank_name = "星展銀行"
    bank_code = "dbs"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        if not pdf_bytes:
            return None

        # 星展密碼: 身分證後4碼 + 生日4碼
        custom_pwd = [self.user_id[-4:] + self.birth_mmdd] if (self.user_id and self.birth_mmdd) else None
        reader = self.decrypt_pdf(pdf_bytes, custom_pwd)
        if not reader:
            return None

        text_pages = [p.extract_text() for p in reader.pages]
        text_all = "\n".join(text_pages)

        # 1. 提取結帳日與截止日
        stmt_date = ""
        due_date = ""
        m_dates = re.search(r'(\d{4}/\d{2}/\d{2})\s+(\d{4}/\d{2}/\d{2})\s+[\d,]+', text_pages[0])
        if m_dates:
            stmt_date = m_dates.group(1).strip()
            due_date = m_dates.group(2).strip()

        if not due_date:
            m_due = re.search(r'繳交截止日：\s*(\d{4}年\d{1,2}月\d{1,2}日)', text_all)
            if m_due:
                due_date = m_due.group(1).replace("年", "/").replace("月", "/").replace("日", "")

        # 2. 提取應繳金額與最低應繳
        due_amount = 0
        min_payment = 0
        m_due_amt = re.search(r'應繳總金額\s*\n\s*([\d,]+)', text_all)
        if m_due_amt:
            due_amount = int(m_due_amt.group(1).replace(",", ""))

        m_min = re.search(r'最低應繳金額\s*\n\s*([\d,]+)', text_all)
        if m_min:
            min_payment = int(m_min.group(1).replace(",", ""))

        # 3. 月份推導
        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            stmt_month = f"{parts[0]}-{int(parts[1]):02d}"

        # 4. 解析逐筆消費 (通常在 Page 3 之後)
        transactions = []
        for page in text_pages:
            if "您本期的消費明細如下" in page:
                lines = page.splitlines()
                for line in lines:
                    line_str = line.strip()
                    # 匹配格式: 2026/09/11 2026/09/15 MATSUYA KANAZAWASHIMEN / JPN ISHIKAWA 2026/09/10 1950.00/JPY 403
                    # 或台幣: 2026/09/13 2026/09/16 桃園捷運Ａ１２站閘門 / TW TAOYUAN 35
                    m_tx = re.match(r'^(\d{4}/\d{2}/\d{2})\s+(\d{4}/\d{2}/\d{2})\s+(.+?)\s+([-\d,]+)$', line_str)
                    if m_tx:
                        tx_date, post_date, desc_part, amt_str = m_tx.groups()
                        try:
                            amt = int(amt_str.replace(",", ""))
                        except ValueError:
                            continue

                        # 拆分外幣與商店
                        currency = "TWD"
                        foreign_amt = None
                        m_for = re.search(r'([\d\.]+)/([A-Z]{3})\s*$', desc_part)
                        if m_for:
                            foreign_amt = m_for.group(1)
                            currency = m_for.group(2)
                            desc_clean = desc_part[:m_for.start()].strip()
                        else:
                            desc_clean = desc_part.strip()

                        # 簡化日期為 MM/DD
                        d_short = "/".join(tx_date.split("/")[1:])

                        transactions.append({
                            "date": d_short,
                            "post_date": "/".join(post_date.split("/")[1:]),
                            "description": self.mask_sensitive_info(desc_clean, self.user_id),
                            "amount": amt,
                            "currency": currency,
                            "foreign_amount": foreign_amt,
                            "card_last4": "0000",
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
            "auto_pay": False,
            "auto_pay_account": "",
            "transactions": transactions
        }
