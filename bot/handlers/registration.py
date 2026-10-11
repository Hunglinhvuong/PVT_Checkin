"""Handler Telegram: /start, nhập họ tên, /cancel, duyệt/từ chối, /pending."""
from __future__ import annotations

import asyncio
import html
import logging

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from services import registration

log = logging.getLogger(__name__)
HTML = ParseMode.HTML


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _mention(user: dict) -> str:
    return f'<a href="tg://user?id={user["telegram_user_id"]}">{_esc(user["full_name"])}</a>'


def _decision_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Duyệt", callback_data=f"reg:ap:{user_id}"),
            InlineKeyboardButton("❌ Từ chối", callback_data=f"reg:rj:{user_id}"),
        ]]
    )


def _pending_text(user: dict) -> str:
    return (
        "🆕 <b>Đăng ký mới</b>\n"
        f"Họ tên: {_esc(user['full_name'])}\n"
        f"Telegram ID: <code>{user['telegram_user_id']}</code>"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and u and chat) or u.is_bot:
        return
    res = await asyncio.to_thread(registration.start, u.id, chat.id)
    if res.kind == "NEW":
        await msg.reply_text(
            "Chào bạn! Vui lòng trả lời tin nhắn này bằng <b>họ và tên</b> của bạn.",
            parse_mode=HTML,
            reply_markup=ForceReply(selective=True, input_field_placeholder="Họ và tên"),
        )
    elif res.kind == "PENDING":
        await msg.reply_text("⏳ Đăng ký của bạn đang chờ admin phê duyệt.")
    elif res.kind == "APPROVED":
        await msg.reply_text(
            f"Chào {_esc(res.user['full_name'])}! Bạn đã được duyệt.\n"
            "Để điểm danh, gửi ảnh chụp bằng Timemark vào nhóm này.",
            parse_mode=HTML,
        )
    else:  # REJECTED | DISABLED
        await msg.reply_text("⛔ Bạn không được phép điểm danh. Liên hệ admin nếu cần hỗ trợ.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and u and chat) or u.is_bot:
        return
    if await asyncio.to_thread(registration.cancel, u.id, chat.id):
        await msg.reply_text("Đã hủy đăng ký. Gửi /start để bắt đầu lại.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chỉ xử lý nếu người gửi đang ở trạng thái AWAITING_NAME trong chính chat này; còn lại bỏ qua im lặng."""
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and msg.text and u and chat) or u.is_bot:
        return
    res = await asyncio.to_thread(registration.submit_name, u.id, chat.id, msg.text)
    if res.kind == "NO_STATE":
        return
    if res.kind == "INVALID":
        await msg.reply_text(
            "Họ tên không hợp lệ (2–100 ký tự, chỉ gồm chữ cái). Vui lòng nhập lại hoặc gửi /cancel.",
            reply_markup=ForceReply(selective=True, input_field_placeholder="Họ và tên"),
        )
        return
    if res.kind == "EXISTS":
        await msg.reply_text("Bạn đã đăng ký trước đó. Gửi /start để xem trạng thái.")
        return

    await msg.reply_text("✅ Đã ghi nhận đăng ký. Vui lòng chờ admin phê duyệt.")
    await _notify_admins(context, res.user, res.admins, fallback_chat_id=chat.id)


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, user: dict, admin_rows: list[dict], fallback_chat_id: int) -> None:
    delivered = 0
    for a in admin_rows:
        try:
            await context.bot.send_message(
                a["telegram_user_id"], _pending_text(user), parse_mode=HTML, reply_markup=_decision_keyboard(user["id"])
            )
            delivered += 1
        except TelegramError as exc:
            # Admin chưa nhắn riêng /start cho bot thì không gửi DM được.
            log.warning("Không gửi được thông báo tới admin %s: %s", a["telegram_user_id"], exc)
    if delivered == 0:
        try:
            await context.bot.send_message(
                fallback_chat_id,
                _pending_text(user) + "\n(Chỉ admin mới bấm được các nút.)",
                parse_mode=HTML,
                reply_markup=_decision_keyboard(user["id"]),
            )
        except TelegramError as exc:
            log.error("Không gửi được thông báo duyệt: %s", exc)


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    _, action, raw_id = q.data.split(":")
    approve = action == "ap"
    res = await asyncio.to_thread(registration.decide, q.from_user.id, int(raw_id), approve)

    if res.kind == "NOT_ADMIN":
        await q.answer("Bạn không có quyền thực hiện thao tác này.", show_alert=True)
        return
    if res.kind in ("NOT_FOUND", "ALREADY"):
        status = res.user["status"] if res.user else "không tồn tại"
        await q.answer(f"Đã được xử lý trước đó ({status}).", show_alert=True)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        return

    user = res.user
    await q.answer("Đã duyệt." if approve else "Đã từ chối.")
    icon, verb = ("✅", "Đã duyệt") if approve else ("❌", "Đã từ chối")
    try:
        await q.edit_message_text(
            f"{icon} <b>{verb}</b>: {_esc(user['full_name'])} (<code>{user['telegram_user_id']}</code>)\n"
            f"Bởi: {_esc(q.from_user.full_name)}",
            parse_mode=HTML,
        )
    except TelegramError as exc:
        log.warning("Không sửa được tin nhắn duyệt: %s", exc)

    notify_chat = user.get("registered_chat_id") or user["telegram_user_id"]
    text = (
        f"✅ {_mention(user)} đã được duyệt. Bạn có thể điểm danh."
        if approve
        else f"❌ Đăng ký của {_mention(user)} bị từ chối. Liên hệ admin nếu cần hỗ trợ."
    )
    try:
        await context.bot.send_message(notify_chat, text, parse_mode=HTML)
    except TelegramError as exc:
        log.warning("Không thông báo được cho user %s: %s", user["telegram_user_id"], exc)


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and u and chat) or u.is_bot:
        return
    rows = await asyncio.to_thread(registration.list_pending, u.id)
    if rows is None:
        return  # không phải admin: bỏ qua im lặng, không lộ thông tin
    if chat.type != "private":
        await msg.reply_text("Vui lòng dùng /pending trong chat riêng với bot.")
        return
    if not rows:
        await msg.reply_text("Không có đăng ký nào chờ duyệt.")
        return
    for user in rows:
        await msg.reply_text(_pending_text(user), parse_mode=HTML, reply_markup=_decision_keyboard(user["id"]))
