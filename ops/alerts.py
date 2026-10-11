"""Gửi cảnh báo vận hành tới các admin qua Telegram (không phụ thuộc DB, dùng được khi DB hỏng)."""
from __future__ import annotations

import logging

from config.settings import Settings
from services import notifier

log = logging.getLogger(__name__)


def send_admins(settings: Settings, text: str) -> bool:
    """True nếu gửi được cho ít nhất một admin (admin phải đã nhắn /start riêng cho bot)."""
    if not settings.bot_token or not settings.admin_telegram_ids:
        log.error("Không thể gửi cảnh báo: thiếu BOT_TOKEN hoặc ADMIN_TELEGRAM_IDS")
        return False
    ok = False
    for tg in settings.admin_telegram_ids:
        ok = notifier.reply(settings.bot_token, tg, None, text[:3500]) or ok
    return ok
