#!/usr/bin/env python3
"""
Taishin Bank Statement Parser
"""

import re
from typing import Optional, Dict, Any, List
from .base import BaseBillParser

class TaishinParser(BaseBillParser):
    bank_name = "台新銀行"
    bank_code = "taishin"

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        if not pdf_bytes:
            return None

        # 台新密碼: 身分證後2碼 + 生日4碼
        custom_pwd = [self.user_id[-2:] + self.birth_mmdd] if (self.user_id and self.birth_mmdd) else None
        reader = self.decrypt_pdf(pdf_bytes, custom_pwd)
        if not reader:
            return None

        text_p1 = reader.pages[0].extract_text()
        text_all = "\n".join([p.extract_text() for p in reader.pages])

        # 1. 提取結帳日與截止日 (格式如 115/10/12, 115/10/27)
        stmt_date = ""
        due_date = ""
        m_stmt = re.search(r'帳單結帳日\s*(\d{2,3}/\d{2}/\d{2})', text_p1)
        if m_stmt:
            stmt_date = m_stmt.group(1).strip()

        m_due = re.search(r'繳款截止日\s*(\d{2,3}/\d{2}/\d{2})', text_p1)
        if m_due:
            due_date = m_due.group(1).strip()

        # 2. 提取應繳金額
        due_amount = 0
        min_payment = 0
        m_due_amt = re.search(r'=本期累計應繳金額\s*([\d,]+)', text_p1)
        if m_due_amt:
            due_amount = int(m_due_amt.group(1).replace(",", ""))

        m_min_amt = re.search(r'本期最低應繳金額\s*([\d,]+)', text_p1)
        if m_min_amt:
            min_payment = int(m_min_amt.group(1).replace(",", ""))

        # 3. 判定自動扣繳帳號
        auto_pay = "自動轉帳扣繳" in text_all or "您已辦理自動扣款" in text_all
        auto_pay_account = ""
        m_acc = re.search(r'自動扣款帳號：\((?:00288\*{5}|[\d\*]+)(\d{6})\)', text_all)
        if m_acc:
            auto_pay_account = m_acc.group(1)

        # 4. 推導 statement_month (例如從 stmt_date 115/10/12 -> 2026-10)
        stmt_month = ""
        if stmt_date:
            parts = stmt_date.split("/")
            year_ad = int(parts[0]) + 1911 if int(parts[0]) < 1900 else int(parts[0])
            stmt_month = f"{year_ad}-{int(parts[1]):02d}"

        # 5. 解析逐筆消費 (主要在 Page 2 之後)
        transactions = []
        current_card = ""
        
        # 匹配卡號標題，如: Richart卡(原@GoGo悠遊御璽) (卡號末四碼:4509)
        # 匹配消費行，如: 115/09/19 115/09/21 街口電支－台灣大電信費TAIPEI 995 TW
        for page in reader.pages[1:]:
            lines = page.extract_text().splitlines()
            for line in lines:
                line_str = line.strip()
                m_card = re.search(r'\(卡號末四碼:(\d{4})\)', line_str)
                if m_card:
                    current_card = m_card.group(1)
                    continue

                # 匹配交易格式: 日期(115/xx/xx) 入帳日 說明 金額 ...
                m_tx = re.match(r'^(\d{2,3}/\d{2}/\d{2})\s+(\d{2,3}/\d{2}/\d{2})\s+(.+?)\s+([-\d,]+)(?:\s+(.*))?$', line_str)
                if m_tx:
                    tx_date, post_date, desc, amt_str, extra = m_tx.groups()
                    desc = desc.strip()
                    if "自動轉帳扣繳" in desc or "年費" in desc:
                        continue
                    try:
                        amt = int(amt_str.replace(",", ""))
                    except ValueError:
                        continue

                    # 判定分類與外幣
                    foreign_amt = None
                    currency = "TWD"
                    if extra:
                        m_f = re.search(r'([A-Z]{3})\s+([\d,\.]+)', extra)
                        if m_f:
                            currency = m_f.group(1)
                            foreign_amt = m_f.group(2)

                    transactions.append({
                        "date": tx_date,
                        "post_date": post_date,
                        "description": self.mask_sensitive_info(desc, self.user_id),
                        "amount": amt,
                        "currency": currency,
                        "foreign_amount": foreign_amt,
                        "card_last4": current_card,
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
