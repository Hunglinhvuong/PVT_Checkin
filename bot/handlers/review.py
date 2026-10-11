"""Lệnh admin xử lý ngoại lệ: /review, /resolve, /reverify (chỉ trong chat riêng, người khác bị bỏ qua im lặng)."""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from services import review
from services.workcalendar import SESSION_LABEL

_STATUS_MSG = {
    "NOT_FOUND": "Không tìm thấy bản ghi.",
    "NOT_PENDING": "Bản ghi không ở trạng thái chờ kiểm tra.",
    "NOT_ELIGIBLE": "Chỉ xác minh lại được bản ghi đang chờ kiểm tra và không đang xử lý.",
    "ALREADY_QUEUED": "Bản ghi đã có yêu cầu xác minh đang chờ.",
    "BAD_INPUT": "Sai cú pháp hoặc thiếu lý do (tối thiểu 3 ký tự).",
}


async def _guard(update: Update) -> bool:
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and u and chat) or u.is_bot:
        return False
    if chat.type != "private":
        return False  # không hiển thị dữ liệu điểm danh nơi công khai; admin dùng chat riêng với bot
    return True


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    rows = await asyncio.to_thread(review.list_pending_review, update.effective_user.id)
    if rows is None:
        return
    if not rows:
        await update.effective_message.reply_text("Không có bản ghi nào chờ kiểm tra.")
        return
    lines = []
    for r in rows:
        extra = []
        if r["flags"]:
            extra.append("cờ: " + ",".join(r["flags"]))
        if r["last_error"]:
            extra.append("lỗi: " + r["last_error"][:80])
        lines.append(
            f"#{r['id']} · {r['full_name']} · {r['attendance_date']:%d/%m} {SESSION_LABEL[r['session']]} · "
            f"{r['verification_status']}" + (" · " + " · ".join(extra) if extra else "")
        )
    lines.append("\n/resolve <id> VALID|LATE|REJECTED <lý do>\n/reverify <id> <lý do>")
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    a = context.args or []
    try:
        rid, decision, reason = int(a[0]), a[1], " ".join(a[2:])
    except (IndexError, ValueError):
        rid, decision, reason = 0, "", ""
    res = await asyncio.to_thread(review.resolve, update.effective_user.id, rid, decision, reason)
    if res == "NOT_ADMIN":
        return
    await update.effective_message.reply_text(
        f"Đã ghi nhận #{rid}: {decision.upper()}." if res == "OK" else _STATUS_MSG[res]
    )


async def cmd_reverify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    a = context.args or []
    try:
        rid, reason = int(a[0]), " ".join(a[1:])
    except (IndexError, ValueError):
        rid, reason = 0, ""
    res = await asyncio.to_thread(review.reverify, update.effective_user.id, rid, reason)
    if res == "NOT_ADMIN":
        return
    await update.effective_message.reply_text(
        f"Đã đưa #{rid} vào hàng đợi xác minh lại." if res == "OK" else _STATUS_MSG[res]
    )
