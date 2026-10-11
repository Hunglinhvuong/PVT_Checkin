"""/office: xem/đặt tọa độ văn phòng (admin, chỉ trong chat riêng với bot)."""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from services import holidays, offices

USAGE = "Cú pháp:\n/office\n/office set <vĩ độ> <kinh độ> [tên]"


async def cmd_office(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, u, chat = update.effective_message, update.effective_user, update.effective_chat
    if not (msg and u and chat) or u.is_bot or chat.type != "private":
        return
    if not await asyncio.to_thread(holidays.is_admin, u.id):
        return  # không phải admin: bỏ qua im lặng
    args = context.args or []
    if not args:
        o = await asyncio.to_thread(offices.get_office)
        await msg.reply_text(
            f"{o['name']}: {o['latitude']}, {o['longitude']}" if o else "Chưa cấu hình văn phòng.\n" + USAGE
        )
        return
    if args[0].lower() != "set" or len(args) < 3:
        await msg.reply_text(USAGE)
        return
    try:
        lat, lon = float(args[1].rstrip(",")), float(args[2].rstrip(","))
    except ValueError:
        await msg.reply_text("Tọa độ không hợp lệ.\n" + USAGE)
        return
    res = await asyncio.to_thread(offices.set_office, u.id, " ".join(args[3:]), lat, lon)
    if res == "OK":
        await msg.reply_text(f"Đã đặt văn phòng: {lat}, {lon}")
    elif res == "BAD_INPUT":
        await msg.reply_text("Tọa độ ngoài phạm vi hợp lệ.")
