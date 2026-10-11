"""Lệnh admin: quản lý ngày nghỉ."""
from __future__ import annotations

import asyncio
import re
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from services import holidays

USAGE = (
    "Cú pháp:\n"
    "/holiday list [năm]\n"
    "/holiday add YYYY-MM-DD Tên ngày nghỉ\n"
    "/holiday work YYYY-MM-DD Ghi chú  (ngày làm việc ngoại lệ, vd thứ Bảy làm bù)\n"
    "/holiday del YYYY-MM-DD"
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", s)).strip()[:100]


async def cmd_holiday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, u = update.effective_message, update.effective_user
    if not (msg and u) or u.is_bot:
        return
    if not await asyncio.to_thread(holidays.is_admin, u.id):
        return  # không phải admin: bỏ qua im lặng
    args = context.args or []
    if not args:
        await msg.reply_text(USAGE)
        return
    sub = args[0].lower()

    if sub == "list":
        try:
            year = int(args[1]) if len(args) > 1 else date.today().year
        except ValueError:
            await msg.reply_text(USAGE)
            return
        rows = await asyncio.to_thread(holidays.list_year, year)
        if not rows:
            await msg.reply_text(f"Chưa có ngày nghỉ/ngoại lệ nào cho năm {year}.")
            return
        lines = [
            f"{r['holiday_date']:%Y-%m-%d} · {r['name']} ({'làm việc' if r['is_working_day'] else 'nghỉ'})"
            for r in rows[:60]
        ]
        await msg.reply_text("\n".join(lines))
        return

    if sub in ("add", "work", "del") and len(args) >= 2:
        try:
            d = date.fromisoformat(args[1])
        except ValueError:
            await msg.reply_text("Ngày không hợp lệ, dùng định dạng YYYY-MM-DD.")
            return
        if sub == "del":
            res = await asyncio.to_thread(holidays.remove_day, u.id, d)
            await msg.reply_text("Đã xóa." if res == "OK" else "Không có cấu hình cho ngày này.")
            return
        name = _clean(" ".join(args[2:]))
        if not name:
            await msg.reply_text("Thiếu tên/ghi chú.\n" + USAGE)
            return
        ok = await asyncio.to_thread(holidays.set_day, u.id, d, name, sub == "work")
        if ok:
            await msg.reply_text(f"Đã lưu {d:%Y-%m-%d}: {name} ({'làm việc' if sub == 'work' else 'nghỉ'}).")
        return

    await msg.reply_text(USAGE)
