"""Nhận ảnh điểm danh từ Telegram."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from telegram import Document, PhotoSize, Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import ContextTypes

from services import attendance, photo_storage
from services.workcalendar import SESSION_LABEL

log = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024  # trang verify.timemark.com chỉ nhận tối đa 10 MB
ALLOWED_EXT = {"jpg", "png"}  # trang chỉ hỗ trợ PNG/JPG/GIF; không chuyển đổi vì sẽ làm đổi ảnh gốc
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.5
GET_FILE_TIMEOUTS = {"read_timeout": 15, "connect_timeout": 10, "pool_timeout": 10}
DOWNLOAD_TIMEOUTS = {"read_timeout": 45, "connect_timeout": 10, "pool_timeout": 10}
_T = TypeVar("_T")


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


async def _retry_telegram_operation(
    stage: str, operation: Callable[[], Awaitable[_T]], file_size: int | None
) -> _T:
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            result = await operation()
            if attempt > 1:
                log.info(
                    "Tải ảnh Telegram thành công sau retry: stage=%s retries=%d size_bytes=%s",
                    stage, attempt - 1, file_size,
                )
            return result
        except TelegramError as exc:
            network_error = isinstance(exc, NetworkError) and not isinstance(exc, BadRequest)
            retryable = network_error and attempt < MAX_DOWNLOAD_ATTEMPTS
            if not retryable:
                log.warning(
                    "Thao tác ảnh Telegram thất bại: stage=%s attempt=%d/%d size_bytes=%s "
                    "error_type=%s network_error=%s",
                    stage, attempt, MAX_DOWNLOAD_ATTEMPTS, file_size, type(exc).__name__,
                    network_error,
                )
                raise
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            log.warning(
                "Lỗi mạng Telegram khi xử lý ảnh, sẽ retry: stage=%s attempt=%d/%d "
                "size_bytes=%s error_type=%s retry_in=%.2fs",
                stage, attempt, MAX_DOWNLOAD_ATTEMPTS, file_size, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("Hết lượt thử tải ảnh Telegram")


async def _download_photo(tg_obj: PhotoSize | Document) -> bytes:
    tg_file = await _retry_telegram_operation(
        "get_file",
        lambda: tg_obj.get_file(**GET_FILE_TIMEOUTS),
        tg_obj.file_size,
    )
    data = await _retry_telegram_operation(
        "download",
        lambda: tg_file.download_as_bytearray(**DOWNLOAD_TIMEOUTS),
        tg_obj.file_size,
    )
    return bytes(data)


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
        data = await _download_photo(tg_obj)
    except TelegramError:
        try:
            await msg.reply_text("Không tải được ảnh, vui lòng gửi lại.")
        except TelegramError as reply_exc:
            log.warning(
                "Không gửi được thông báo lỗi tải ảnh: error_type=%s",
                type(reply_exc).__name__,
            )
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
            photo_storage.save_temp, photo_storage.incoming_photo_dir(settings), chat.id, msg.message_id, ext, data
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
