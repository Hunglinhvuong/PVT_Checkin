"""Gửi thông báo Telegram từ tiến trình worker (không chạy bot), qua Bot API bằng urllib."""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

PENDING_UPLOAD_NOTICE = (
    "⏳ Timemark chưa nhận được dữ liệu ảnh. Hãy mở ứng dụng Timemark để đồng bộ; "
    "hệ thống sẽ tự thử lại mỗi 10 phút (tối đa 5 lần)."
)


def reply(token: str, chat_id: int, reply_to_message_id: int | None, text: str) -> bool:
    if not token:
        return False
    body = {"chat_id": chat_id, "text": text, "allow_sending_without_reply": True}
    if reply_to_message_id:
        body["reply_to_message_id"] = reply_to_message_id
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as exc:  # noqa: BLE001 - không để lỗi thông báo làm hỏng worker; không log URL (chứa token)
        log.warning("Gửi thông báo Telegram thất bại: %s", type(exc).__name__)
        return False


def message_for(
    verification_status: str, final_status: str | None, reason: str | None = None, deadline=None,
    resubmit_allowed: bool = True, capture_date=None, expected_date=None,
) -> str:
    if reason == "DATE_MISMATCH":  # ảnh đã được Timemark xác minh nhưng ngày chụp không khớp ngày điểm danh
        cd = f"{capture_date:%d/%m/%Y}" if capture_date else "?"
        ed = f"{expected_date:%d/%m/%Y}" if expected_date else "?"
        base = f"❌ Ảnh đã xác minh nhưng ngày chụp ({cd}) không phải ngày điểm danh ({ed})."
        if resubmit_allowed:
            return base + " Vui lòng chụp lại bằng Timemark và gửi lại."
        return base + " Bạn đã hết số lần gửi lại trong buổi này, vui lòng liên hệ admin."
    if verification_status == "VERIFIED_AUTHENTIC" and final_status == "VALID":
        return "✅ Điểm danh hợp lệ (đúng giờ)."
    if verification_status == "VERIFIED_AUTHENTIC" and final_status == "LATE":
        return "⏰ Điểm danh bị tính muộn" + (f" (hạn {deadline:%H:%M})." if deadline else ".")
    if verification_status == "VERIFIED_AUTHENTIC" and final_status == "RECEIVED":
        return "✅ Ảnh đã được Timemark xác minh."
    if verification_status == "VERIFIED_FAKE":
        if reason == "EDITED":
            return "❌ Ảnh đã bị chỉnh sửa (Timemark không xác nhận), không được chấp nhận."
        return "❌ Ảnh không vượt qua xác minh Timemark."
    if verification_status == "UNABLE_TO_VERIFY" and reason == "NO_VALID_CODE":
        if resubmit_allowed:
            return "❌ Ảnh không có code hợp lệ, cần gửi ảnh khác."
        return "❌ Ảnh không có code hợp lệ và bạn đã hết số lần gửi lại trong buổi này. Vui lòng liên hệ admin."
    if verification_status == "UNABLE_TO_VERIFY" and reason == "PENDING_UPLOAD":
        return "⚠️ Timemark vẫn chưa nhận được dữ liệu ảnh sau nhiều lần thử. Admin sẽ kiểm tra."
    return "⚠️ Ảnh cần admin kiểm tra."
