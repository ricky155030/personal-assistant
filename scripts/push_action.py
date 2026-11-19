#!/usr/bin/env python3
"""將主大腦的反問確認或待拍板事項寫入 Action Queue，附帶自包含完整上下文。"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import settings


def push_brain_action(
    topic: str,
    title: str,
    question: str,
    context_prompt: str,
    options: list[str] | None = None,
) -> dict:
    """推送一筆附帶完整前後文的待決策項目至本機 Action Queue API。"""
    headers = {"Content-Type": "application/json"}
    if settings.API_KEY:
        headers["X-Agent-Key"] = settings.API_KEY

    payload = {
        "agent": f"brain:{topic.strip()}",
        "title": title.strip(),
        "question": question.strip(),
        "options": [o.strip() for o in (options or []) if o.strip()],
        "source": "brain",
        "context_prompt": context_prompt.strip(),
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{settings.PORT}/api/actions/pending",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="推送主大腦自包含反問至 Action Queue")
    parser.add_argument("--topic", required=True, help="短識別碼（如 restaurant_booking）")
    parser.add_argument("--title", required=True, help="卡片標題（如 主大腦確認 · 餐廳訂位）")
    parser.add_argument("--question", required=True, help="給主人看的精簡問題")
    parser.add_argument("--context", required=True, help="自包含前後文（原始需求與已知條件）")
    parser.add_argument("--options", nargs="*", default=[], help="可選項目清單")
    args = parser.parse_args()

    res = push_brain_action(
        topic=args.topic,
        title=args.title,
        question=args.question,
        context_prompt=args.context,
        options=args.options,
    )
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
