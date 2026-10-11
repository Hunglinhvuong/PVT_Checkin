"""Nhận ảnh điểm danh từ Telegram."""
from __future__ import annotations

import asyncio
import hashlib
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from services import attendance, photo_storage
from services.workcalendar import SESSION_LABEL

log = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024  # trang verify.timemark.com chỉ nhận tối đa 10 MB
ALLOWED_EXT = {"jpg", "png"}  # trang chỉ hỗ trợ PNG/JPG/GIF; không chuyển đổi vì sẽ làm đổi ảnh gốc


def _reject_text(el: attendance.Eligibility | attendance.IntakeResult) -> str:
    k = el.kind
    if k == "NOT_REGISTERED":
        return "Bạn chưa đăng ký. Gửi /start để đăng ký."
    if k == "PENDING":
        return "⏳ Đăng ký của bạn đang chờ admin duyệt, chưa thể điểm danh."
    if k in ("REJECTED", "DISABLED"):
        return "⛔ Bạn không được phép điểm danh."
    if k == "NON_WORKING_DAY":
        why = f" ({el.holiday_name})" if el.holiday_name else ""
        return f"📅 Hôm nay không phải ngày làm việc{why}, không điểm danh."
    if k == "DUPLICATE":
        return (
            f"ℹ️ Bạn đã được ghi nhận điểm danh {SESSION_LABEL[el.session]} ngày "
            f"{el.work_date:%d/%m}. Ảnh này được bỏ qua."
        )
    if k == "ATTEMPTS_EXCEEDED":
        return "⛔ Bạn đã gửi quá số lần cho phép trong buổi này. Vui lòng liên hệ admin."
    return "Không xử lý được ảnh này."


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and u and chat) or u.is_bot or chat.type == "channel":
        return
    settings = context.bot_data["settings"]
    if settings.attendance_chat_ids and chat.id not in settings.attendance_chat_ids:
        return
    if msg.forward_origin is not None:
        await msg.reply_text("⛔ Không nhận ảnh chuyển tiếp. Hãy tự chụp bằng Timemark và gửi trực tiếp.")
        return

    pre = await asyncio.to_thread(attendance.precheck, u.id, msg.date, settings.timezone)
    if pre.kind != "OK":
        await msg.reply_text(_reject_text(pre))
        return

    if msg.photo:
        tg_obj, source = msg.photo[-1], "PHOTO"
    else:
        tg_obj, source = msg.document, "DOCUMENT"
    if tg_obj.file_size and tg_obj.file_size > MAX_BYTES:
        await msg.reply_text("Ảnh quá lớn (tối đa 10 MB).")
        return
    try:
        tg_file = await tg_obj.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except TelegramError as exc:
        log.warning("Không tải được ảnh từ Telegram: %s", exc)
        await msg.reply_text("Không tải được ảnh, vui lòng gửi lại.")
        return

    ext = photo_storage.detect_image_ext(data)
    if ext is None or len(data) > MAX_BYTES:
        await msg.reply_text("File không phải ảnh hợp lệ hoặc quá lớn (tối đa 10 MB).")
        return
    if ext not in ALLOWED_EXT:
        await msg.reply_text("Chỉ nhận ảnh JPG/PNG (trang xác minh Timemark không hỗ trợ định dạng khác).")
        return

    temp_path = None
    try:
        temp_path = await asyncio.to_thread(
            photo_storage.save_temp, settings.upload_tmp_dir, chat.id, msg.message_id, ext, data
        )
        intake = attendance.PhotoIntake(
            telegram_user_id=u.id,
            chat_id=chat.id,
            message_id=msg.message_id,
            file_unique_id=tg_obj.file_unique_id,
            received_at=msg.date,
            sha256=hashlib.sha256(data).hexdigest(),
            temp_path=temp_path,
            file_ext=ext,
            file_size=len(data),
            source=source,
        )
        res = await asyncio.to_thread(attendance.record_intake, intake, settings.timezone)
    except Exception:  # noqa: BLE001
        log.exception("Lỗi tiếp nhận ảnh của user %s", u.id)
        photo_storage.delete_temp(temp_path)
        await msg.reply_text("Lỗi hệ thống khi tiếp nhận ảnh, vui lòng gửi lại sau.")
        return

    if res.kind != "OK":
        photo_storage.delete_temp(temp_path)
        await msg.reply_text(_reject_text(res))
        return

    head = "🔁 Đã nhận ảnh gửi lại" if res.superseded_id else "📥 Đã nhận ảnh điểm danh"
    text = f"{head} {SESSION_LABEL[res.session]} ngày {res.work_date:%d/%m}. Đang chờ xác minh."
    if res.flagged:
        text += "\n⚠️ Ảnh cần admin kiểm tra."
    try:
        await msg.reply_text(text)
    except TelegramError as exc:
        log.warning("Không trả lời được xác nhận: %s", exc)

    # Upload sau khi đã xác nhận; nếu lỗi, job retry định kỳ sẽ xử lý.
    await asyncio.to_thread(photo_storage.upload_photo, res.photo["id"])
