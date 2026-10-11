"""Quản lý tài khoản admin (chỉ SUPER_ADMIN): thêm, khóa, mở lại. Có audit; chặn tự khóa và khóa SUPER_ADMIN cuối cùng."""
from __future__ import annotations

from database.connection import get_conn
from repositories import admins, audit

ROLES = ("SUPER_ADMIN", "ADMIN", "VIEWER")


def _super(conn, telegram_id: int) -> dict | None:
    a = admins.get_active(conn, telegram_id)
    return a if a and a["role"] == "SUPER_ADMIN" else None


def list_admins() -> list[dict]:
    with get_conn() as conn:
        return conn.execute("SELECT id, telegram_user_id, role, status, created_at FROM admins ORDER BY id").fetchall()


def add_admin(actor_telegram_id: int, telegram_id: int, role: str) -> str:
    """NOT_SUPER | BAD_INPUT | EXISTS | OK (ID đã bị khóa thì được mở lại với vai trò mới)"""
    with get_conn() as conn:
        if not _super(conn, actor_telegram_id):
            return "NOT_SUPER"
        if role not in ROLES or not isinstance(telegram_id, int) or telegram_id <= 0:
            return "BAD_INPUT"
        old = conn.execute("SELECT * FROM admins WHERE telegram_user_id = %s FOR UPDATE", (telegram_id,)).fetchone()
        if old and old["status"] == "ACTIVE":
            return "EXISTS"
        if old:
            conn.execute("UPDATE admins SET status = 'ACTIVE', role = %s WHERE id = %s", (role, old["id"]))
            entity_id = old["id"]
        else:
            entity_id = conn.execute(
                "INSERT INTO admins (telegram_user_id, role) VALUES (%s, %s) RETURNING id", (telegram_id, role)
            ).fetchone()["id"]
        audit.add(
            conn, action="ADMIN_ADDED", entity_type="admins", entity_id=entity_id,
            actor_telegram_id=actor_telegram_id, actor_type="ADMIN",
            old_value={"status": old["status"], "role": old["role"]} if old else None,
            new_value={"telegram_user_id": telegram_id, "role": role, "status": "ACTIVE"},
        )
        return "OK"


def set_status(actor_telegram_id: int, admin_id: int, status: str) -> str:
    """NOT_SUPER | BAD_INPUT | NOT_FOUND | SELF | LAST_SUPER | OK"""
    with get_conn() as conn:
        actor = _super(conn, actor_telegram_id)
        if not actor:
            return "NOT_SUPER"
        if status not in ("ACTIVE", "DISABLED"):
            return "BAD_INPUT"
        # Khóa mọi SUPER_ADMIN đang hoạt động để việc đếm "người cuối cùng" không bị đua
        supers = conn.execute("SELECT id FROM admins WHERE role = 'SUPER_ADMIN' AND status = 'ACTIVE' FOR UPDATE").fetchall()
        target = conn.execute("SELECT * FROM admins WHERE id = %s FOR UPDATE", (admin_id,)).fetchone()
        if target is None:
            return "NOT_FOUND"
        if status == "DISABLED":
            if target["id"] == actor["id"]:
                return "SELF"
            if target["role"] == "SUPER_ADMIN" and target["status"] == "ACTIVE" and len(supers) <= 1:
                return "LAST_SUPER"
        conn.execute("UPDATE admins SET status = %s WHERE id = %s", (status, admin_id))
        audit.add(
            conn, action="ADMIN_STATUS_CHANGED", entity_type="admins", entity_id=admin_id,
            actor_telegram_id=actor_telegram_id, actor_type="ADMIN",
            old_value={"status": target["status"]}, new_value={"status": status},
        )
        return "OK"
