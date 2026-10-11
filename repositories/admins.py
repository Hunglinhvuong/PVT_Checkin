from __future__ import annotations

from psycopg import Connection


def get_active(conn: Connection, telegram_user_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM admins WHERE telegram_user_id = %s AND status = 'ACTIVE'", (telegram_user_id,)
    ).fetchone()


def list_active(conn: Connection) -> list[dict]:
    return conn.execute(
        "SELECT * FROM admins WHERE status = 'ACTIVE' AND role IN ('SUPER_ADMIN', 'ADMIN') ORDER BY id"
    ).fetchall()
