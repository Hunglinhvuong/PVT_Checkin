"""Đổi trạng thái nhân viên đã được xử lý (khóa/mở lại/duyệt lại). Duyệt đăng ký PENDING dùng registration.decide."""
from __future__ import annotations

from database.connection import get_conn
from repositories import admins, audit, users

ALLOWED = {("APPROVED", "DISABLED"), ("DISABLED", "APPROVED"), ("REJECTED", "APPROVED")}


def change_status(admin_telegram_id: int, user_id: int, new_status: str, reason: str) -> str:
    """NOT_ADMIN | BAD_INPUT | NOT_FOUND | NOT_ALLOWED | OK"""
    reason = (reason or "").strip()
    with get_conn() as conn:
        admin = admins.get_active(conn, admin_telegram_id)
        if not admin or admin["role"] not in ("SUPER_ADMIN", "ADMIN"):
            return "NOT_ADMIN"
        if len(reason) < 3:
            return "BAD_INPUT"
        user = conn.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if user is None:
            return "NOT_FOUND"
        if (user["status"], new_status) not in ALLOWED:
            return "NOT_ALLOWED"
        if new_status == "APPROVED":
            conn.execute(
                "UPDATE users SET status = 'APPROVED', approved_at = now(), approved_by = %s WHERE id = %s",
                (admin["id"], user_id),
            )
        else:
            conn.execute("UPDATE users SET status = %s WHERE id = %s", (new_status, user_id))
        audit.add(
            conn, action="USER_STATUS_CHANGED", entity_type="users", entity_id=user_id,
            actor_telegram_id=admin_telegram_id, actor_type="ADMIN",
            old_value={"status": user["status"]}, new_value={"status": new_status}, reason=reason,
        )
        return "OK"
