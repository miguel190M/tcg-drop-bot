from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


def send(text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("---- telegram (not configured) ----\n" + text)
        return False
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
            return False
    return True
