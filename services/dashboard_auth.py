"""Đăng nhập admin cho dashboard bằng mã 6 số một lần gửi qua Telegram (chứng minh quyền sở hữu Telegram ID admin).

- Xem dashboard không cần đăng nhập; chỉ thao tác sửa mới cần. Quyền ghi vẫn do các service kiểm tra lại theo bảng admins.
- Mã hết hạn sau 5 phút, tối đa 5 lần nhập sai/mã, tối đa 3 yêu cầu mỗi ID và 30 yêu cầu tổng mỗi 10 phút.
- Yêu cầu từ ID không phải admin vẫn được ghi nhận (không có mã) để không lộ ID nào là admin qua giới hạn tần suất.
- Chỉ lưu hash có muối của mã (mã chỉ có 10^6 khả năng nên hash không chống được dò offline nếu lộ DB; bảo vệ chính là hạn dùng/số lần thử).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from database.connection import get_conn
from repositories import admins, audit

CODE_TTL_MINUTES = 5
MAX_VERIFY_ATTEMPTS = 5
MAX_REQUESTS_PER_ID = 3
MAX_REQUESTS_TOTAL = 30
WINDOW_MINUTES = 10
EDIT_ROLES = ("SUPER_ADMIN", "ADMIN")  # VIEWER không có quyền sửa nên không đăng nhập


@dataclass(frozen=True)
class IssueResult:
    kind: str  # OK | RATE_LIMITED
    code: str | None = None  # None nếu ID không phải admin: không gửi gì, nhưng phản hồi giống hệt


@dataclass(frozen=True)
class VerifyResult:
    kind: str  # OK | INVALID | LOCKED | NONE (chưa yêu cầu/hết hạn/đã dùng/không còn là admin)
    admin: dict | None = None


def _hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _editor(conn, telegram_id: int) -> dict | None:
    a = admins.get_active(conn, telegram_id)
    return a if a and a["role"] in EDIT_ROLES else None


def get_editor(telegram_id: int) -> dict | None:
    """Admin đang hoạt động có quyền sửa (dùng để kiểm tra lại phiên mỗi lần vẽ trang)."""
    with get_conn() as conn:
        return _editor(conn, telegram_id)


def issue_code(telegram_user_id: int) -> IssueResult:
    with get_conn() as conn:
        conn.execute("DELETE FROM dashboard_login_codes WHERE created_at < now() - interval '1 day'")
        per_id = conn.execute(
            "SELECT count(*) AS n FROM dashboard_login_codes WHERE telegram_user_id = %s "
            "AND created_at > now() - make_interval(mins => %s)", (telegram_user_id, WINDOW_MINUTES),
        ).fetchone()["n"]
        total = conn.execute(
            "SELECT count(*) AS n FROM dashboard_login_codes WHERE created_at > now() - make_interval(mins => %s)",
            (WINDOW_MINUTES,),
        ).fetchone()["n"]
        if per_id >= MAX_REQUESTS_PER_ID or total >= MAX_REQUESTS_TOTAL:
            return IssueResult("RATE_LIMITED")

        if _editor(conn, telegram_user_id) is None:
            conn.execute(
                "INSERT INTO dashboard_login_codes (telegram_user_id, expires_at) VALUES (%s, now())", (telegram_user_id,)
            )
            return IssueResult("OK", None)

        conn.execute(  # mã mới vô hiệu hóa mã cũ chưa dùng
            "UPDATE dashboard_login_codes SET used_at = now() WHERE telegram_user_id = %s AND used_at IS NULL",
            (telegram_user_id,),
        )
        code = f"{secrets.randbelow(10**6):06d}"
        salt = secrets.token_hex(8)
        conn.execute(
            "INSERT INTO dashboard_login_codes (telegram_user_id, salt, code_hash, expires_at) "
            "VALUES (%s, %s, %s, now() + make_interval(mins => %s))",
            (telegram_user_id, salt, _hash(salt, code), CODE_TTL_MINUTES),
        )
        return IssueResult("OK", code)


def verify_code(telegram_user_id: int, code: str) -> VerifyResult:
    code = (code or "").strip()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM dashboard_login_codes WHERE telegram_user_id = %s AND code_hash IS NOT NULL "
            "AND used_at IS NULL AND expires_at > now() ORDER BY id DESC LIMIT 1 FOR UPDATE",
            (telegram_user_id,),
        ).fetchone()
        if row is None:
            return VerifyResult("NONE")
        if row["attempts"] >= MAX_VERIFY_ATTEMPTS:
            return VerifyResult("LOCKED")
        ok = code.isdigit() and len(code) == 6 and hmac.compare_digest(_hash(row["salt"], code), row["code_hash"])
        if not ok:
            conn.execute("UPDATE dashboard_login_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
            return VerifyResult("INVALID")
        admin = _editor(conn, telegram_user_id)
        if admin is None:
            return VerifyResult("NONE")
        conn.execute("UPDATE dashboard_login_codes SET used_at = now() WHERE id = %s", (row["id"],))
        audit.add(
            conn, action="DASHBOARD_LOGIN", entity_type="admins", entity_id=admin["id"],
            actor_telegram_id=telegram_user_id, actor_type="DASHBOARD", new_value={"role": admin["role"]},
        )
        return VerifyResult("OK", admin)
