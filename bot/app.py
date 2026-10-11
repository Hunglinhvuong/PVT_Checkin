from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot.handlers import admin as admin_h
from bot.handlers import attendance as att_h
from bot.handlers import office as office_h
from bot.handlers import registration as h
from bot.handlers import review as review_h
from config.settings import Settings
from database.connection import close_pool, init_pool
from services import photo_storage

log = logging.getLogger(__name__)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Lỗi xử lý update", exc_info=context.error)


async def _retry_uploads(context: ContextTypes.DEFAULT_TYPE) -> None:
    n = await asyncio.to_thread(photo_storage.retry_pending)
    if n:
        log.info("Retry upload: thành công %d ảnh", n)


def run_bot(settings: Settings) -> None:
    settings.require("bot_token")
    photo_storage.configure(settings)

    async def post_init(app: Application) -> None:
        await asyncio.to_thread(init_pool, settings)

    async def post_shutdown(app: Application) -> None:
        await asyncio.to_thread(close_pool)

    builder = (
        Application.builder()
        .token(settings.bot_token)
        .concurrent_updates(8)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if settings.telegram_proxy:
        builder = builder.proxy(settings.telegram_proxy).get_updates_proxy(settings.telegram_proxy)
    app = builder.build()
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", h.cmd_start))
    app.add_handler(CommandHandler("cancel", h.cmd_cancel))
    app.add_handler(CommandHandler("pending", h.cmd_pending))
    app.add_handler(CommandHandler("holiday", admin_h.cmd_holiday))
    app.add_handler(CommandHandler("office", office_h.cmd_office))
    app.add_handler(CommandHandler("review", review_h.cmd_review))
    app.add_handler(CommandHandler("resolve", review_h.cmd_resolve))
    app.add_handler(CommandHandler("reverify", review_h.cmd_reverify))
    app.add_handler(CallbackQueryHandler(h.on_decision, pattern=r"^reg:(ap|rj):\d+$"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, att_h.on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_text))
    app.add_error_handler(_on_error)

    if app.job_queue is not None:
        app.job_queue.run_repeating(_retry_uploads, interval=300, first=60, name="retry_uploads")
    else:
        log.warning("Thiếu job-queue: cài 'python-telegram-bot[job-queue]' để bật retry upload tự động")

    log.info("Bot khởi động (polling)")
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY], bootstrap_retries=-1)
