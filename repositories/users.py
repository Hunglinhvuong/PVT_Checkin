from __future__ import annotations

from psycopg import Connection


def get_by_telegram_id(conn: Connection, telegram_user_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM users WHERE telegram_user_id = %s", (telegram_user_id,)
    ).fetchone()


def get_by_id(conn: Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


def create_pending(conn: Connection, telegram_user_id: int, full_name: str, chat_id: int) -> dict | None:
    """Trả về None nếu telegram_user_id đã tồn tại (an toàn khi đua)."""
    return conn.execute(
        "INSERT INTO users (telegram_user_id, full_name, status, registered_chat_id) "
        "VALUES (%s, %s, 'PENDING', %s) "
        "ON CONFLICT (telegram_user_id) DO NOTHING RETURNING *",
        (telegram_user_id, full_name, chat_id),
    ).fetchone()


def decide(conn: Connection, user_id: int, new_status: str, admin_id: int) -> dict | None:
    """Chỉ chuyển trạng thái từ PENDING (nguyên tử). None nếu đã được xử lý."""
    if new_status not in ("APPROVED", "REJECTED"):
        raise ValueError(new_status)
    return conn.execute(
        "UPDATE users SET status = %s, approved_at = now(), approved_by = %s "
        "WHERE id = %s AND status = 'PENDING' RETURNING *",
        (new_status, admin_id, user_id),
    ).fetchone()


def list_pending(conn: Connection, limit: int = 20) -> list[dict]:
    return conn.execute(
        "SELECT * FROM users WHERE status = 'PENDING' ORDER BY created_at LIMIT %s", (limit,)
    ).fetchall()
