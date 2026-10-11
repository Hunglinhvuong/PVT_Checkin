"""Seed dữ liệu khởi tạo: admin từ ADMIN_TELEGRAM_IDS."""
from __future__ import annotations

import logging

import psycopg

log = logging.getLogger(__name__)


def seed_admins(database_url: str, telegram_ids: tuple[int, ...]) -> int:
    if not telegram_ids:
        return 0
    inserted = 0
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        for tid in telegram_ids:
            cur = conn.execute(
                "INSERT INTO admins (telegram_user_id, role) VALUES (%s, 'SUPER_ADMIN') "
                "ON CONFLICT (telegram_user_id) DO NOTHING",
                (tid,),
            )
            inserted += cur.rowcount
    log.info("Seed admin: thêm mới %d/%d", inserted, len(telegram_ids))
    return inserted


def seed_office(database_url: str, name: str, lat: float, lon: float) -> int:
    """Chỉ thêm khi chưa có văn phòng đang hoạt động (không ghi đè cấu hình admin đã đổi)."""
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        n = conn.execute(
            "INSERT INTO office_locations (name, latitude, longitude) SELECT %s, %s, %s "
            "WHERE NOT EXISTS (SELECT 1 FROM office_locations WHERE is_active)",
            (name, lat, lon),
        ).rowcount
    log.info("Seed văn phòng: %s", "đã thêm" if n else "đã có sẵn, bỏ qua")
    return n
