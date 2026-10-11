"""Tọa độ văn phòng (cấu hình trong CSDL, admin đổi được, có audit)."""
from __future__ import annotations

from database.connection import get_conn
from repositories import admins, audit, offices


def get_office() -> dict | None:
    with get_conn() as conn:
        return offices.get_active(conn)


def set_office(admin_telegram_id: int, name: str, lat: float, lon: float) -> str:
    """NOT_ADMIN | BAD_INPUT | OK"""
    with get_conn() as conn:
        a = admins.get_active(conn, admin_telegram_id)
        if not a or a["role"] not in ("SUPER_ADMIN", "ADMIN"):
            return "NOT_ADMIN"
        name = " ".join((name or "").split())[:100] or "Văn phòng"
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            return "BAD_INPUT"
        old = offices.get_active(conn)
        new = offices.replace_active(conn, name, lat, lon)
        audit.add(
            conn, action="OFFICE_SET", entity_type="office_locations", entity_id=new["id"],
            actor_telegram_id=admin_telegram_id, actor_type="ADMIN",
            old_value={"name": old["name"], "latitude": old["latitude"], "longitude": old["longitude"]} if old else None,
            new_value={"name": name, "latitude": lat, "longitude": lon},
        )
        return "OK"
