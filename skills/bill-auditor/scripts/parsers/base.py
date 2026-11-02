#!/usr/bin/env python3
"""
Base Bill Parser
提供各銀行帳單解析器繼承之基類，內建：
- In-memory PDF 解密與密碼嘗試
- Unicode 轉義字元解碼 (/UNICxxxx -> 正體中文)
- 敏感資訊遮罩 (卡號保留末四碼、身分證字號遮蔽)
"""

import io
import re
from typing import Optional, List, Dict, Any
import pypdf

class BaseBillParser:
    bank_name: str = "未知銀行"
    bank_code: str = "unknown"

    def __init__(self, user_id: str = "", birth_mmdd: str = ""):
        self.user_id = (user_id or "").strip().upper()
        self.birth_mmdd = (birth_mmdd or "").strip()

    def get_candidate_passwords(self) -> List[str]:
        """產生常用的候選密碼清單"""
        pwds = []
        if self.user_id:
            pwds.append(self.user_id)
            pwds.append(self.user_id.lower())
            if self.birth_mmdd:
                pwds.append(self.user_id[-2:] + self.birth_mmdd)  # 如台新: 後2碼+生日4碼
                pwds.append(self.user_id[-4:] + self.birth_mmdd)  # 如星展: 後4碼+生日4碼
                pwds.append(self.birth_mmdd + self.user_id[-4:])
            pwds.append(self.user_id[-4:])
        if self.birth_mmdd:
            pwds.append(self.birth_mmdd)
        return pwds

    def decrypt_pdf(self, pdf_bytes: bytes, custom_passwords: Optional[List[str]] = None) -> Optional[pypdf.PdfReader]:
        """在記憶體中解密 PDF，成功回傳 PdfReader，失敗回傳 None"""
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            if not reader.is_encrypted:
                return reader

            passwords = custom_passwords or self.get_candidate_passwords()
            for pwd in passwords:
                try:
                    res = reader.decrypt(pwd)
                    if res != 0:
                        return reader
                except Exception:
                    continue
            return None
        except Exception as e:
            print(f"[{self.bank_name}] PDF 解密失敗: {e}")
            return None

    @staticmethod
    def decode_unicode_escapes(text: str) -> str:
        """解析 /UNICxxxx 為標準文字 (針對中信、遠東等編碼格式)"""
        def repl(m):
            code = m.group(1)
            try:
                return chr(int(code, 16))
            except Exception:
                return m.group(0)
        return re.sub(r'/UNIC([0-9A-Fa-f]{4})', repl, text)

    @staticmethod
    def mask_sensitive_info(text: str, user_id: str = "") -> str:
        """自動遮蔽身分證字號與卡號"""
        res = text
        if user_id and len(user_id) >= 8:
            masked_id = user_id[:2] + "****" + user_id[-2:]
            res = res.replace(user_id, masked_id)
            res = res.replace(user_id.lower(), masked_id.lower())

        # 遮蔽 16 碼信用卡號 (保留末四碼)
        res = re.sub(r'\b(?:\d{4}[-\s]?){3}(\d{4})\b', r'****-****-****-\1', res)
        # 遮蔽身分證字號格式 [A-Z][12]\d{8}
        res = re.sub(r'\b([A-Z][12])\d{6}(\d{2})\b', r'\1******\2', res)
        return res

    def parse(self, email_meta: dict, pdf_bytes: Optional[bytes] = None) -> Optional[Dict[str, Any]]:
        """
        子類別必須實作之核心解析介面
        回傳結構化資料:
        {
            "bank_code": "taishin",
            "bank_name": "台新銀行",
            "statement_month": "2026-10",
            "statement_date": "2026-10-12",
            "due_date": "2026-10-27",
            "due_amount": 92049,
            "min_payment": 9295,
            "auto_pay": True,
            "auto_pay_account": "62837",
            "transactions": [
                {
                    "date": "10/04",
                    "post_date": "10/06",
                    "description": "街口電支－台灣電力股份有限公司",
                    "currency": "TWD",
                    "amount": 3441,
                    "foreign_amount": None,
                    "card_last4": "4104",
                    "category": "utilities"
                }
            ]
        }
        """
        raise NotImplementedError
