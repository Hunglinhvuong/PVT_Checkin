"""Quản lý ngày nghỉ/ngày làm ngoại lệ (admin)."""
from __future__ import annotations

from datetime import date

from database.connection import get_conn
from repositories import admins, audit, holidays


def _admin(conn, telegram_id: int) -> dict | None:
    a = admins.get_active(conn, telegram_id)
    return a if a and a["role"] in ("SUPER_ADMIN", "ADMIN") else None


def _snap(row: dict | None) -> dict | None:
    return None if row is None else {
        "date": row["holiday_date"].isoformat(), "name": row["name"], "is_working_day": row["is_working_day"]
    }


def is_admin(telegram_id: int) -> bool:
    with get_conn() as conn:
        return _admin(conn, telegram_id) is not None


def set_day(admin_telegram_id: int, d: date, name: str, is_working_day: bool) -> bool:
    """False nếu không phải admin."""
    with get_conn() as conn:
        if not _admin(conn, admin_telegram_id):
            return False
        old = holidays.get(conn, d)
        new = holidays.upsert(conn, d, name, is_working_day)
        audit.add(
            conn, action="HOLIDAY_SET", entity_type="holiday_calendar", entity_id=new["id"],
            actor_telegram_id=admin_telegram_id, actor_type="ADMIN", old_value=_snap(old), new_value=_snap(new),
        )
        return True


def remove_day(admin_telegram_id: int, d: date) -> str:
    """NOT_ADMIN | NOT_FOUND | OK"""
    with get_conn() as conn:
        if not _admin(conn, admin_telegram_id):
            return "NOT_ADMIN"
        old = holidays.delete(conn, d)
        if old is None:
            return "NOT_FOUND"
        audit.add(
            conn, action="HOLIDAY_REMOVED", entity_type="holiday_calendar", entity_id=old["id"],
            actor_telegram_id=admin_telegram_id, actor_type="ADMIN", old_value=_snap(old),
        )
        return "OK"


def list_year(year: int) -> list[dict]:
    with get_conn() as conn:
        return holidays.list_year(conn, year)
