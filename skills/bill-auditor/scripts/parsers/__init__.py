#!/usr/bin/env python3
"""
Bank Parsers Dispatcher
"""

from typing import Optional, Dict, Any
from .base import BaseBillParser
from .taishin import TaishinParser
from .sinopac import SinopacParser
from .ctbc import CTBCParser
from .dbs import DBSParser
from .firstbank import FirstBankParser
from .cathay import CathayParser
from .ubot import UbotParser

PARSERS = [
    TaishinParser,
    SinopacParser,
    CTBCParser,
    DBSParser,
    FirstBankParser,
    CathayParser,
    UbotParser,
]

def get_parser_for_email(email_meta: dict, user_id: str = "", birth_mmdd: str = "") -> Optional[BaseBillParser]:
    subject = email_meta.get("subject", "")
    from_addr = email_meta.get("from", "")

    for cls in PARSERS:
        # 依關鍵字匹配
        if cls.bank_name in subject or cls.bank_name in from_addr:
            return cls(user_id=user_id, birth_mmdd=birth_mmdd)

    # 網域比對
    if "taishinbank" in from_addr:
        return TaishinParser(user_id=user_id, birth_mmdd=birth_mmdd)
    if "sinopac" in from_addr:
        return SinopacParser(user_id=user_id, birth_mmdd=birth_mmdd)
    if "ctbcbank" in from_addr:
        return CTBCParser(user_id=user_id, birth_mmdd=birth_mmdd)
    if "dbs.com" in from_addr:
        return DBSParser(user_id=user_id, birth_mmdd=birth_mmdd)
    if "firstbank" in from_addr:
        return FirstBankParser(user_id=user_id, birth_mmdd=birth_mmdd)
    if "cathaybk" in from_addr:
        return CathayParser(user_id=user_id, birth_mmdd=birth_mmdd)
    if "ubot" in from_addr:
        return UbotParser(user_id=user_id, birth_mmdd=birth_mmdd)

    return None
