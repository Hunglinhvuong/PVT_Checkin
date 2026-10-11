from __future__ import annotations

from psycopg import Connection
from psycopg.types.json import Jsonb

AWAITING_NAME = "AWAITING_NAME"


def set_state(
    conn: Connection, telegram_user_id: int, chat_id: int, state: str, data: dict | None = None, ttl_minutes: int = 30
) -> None:
    conn.execute(
        "INSERT INTO conversation_states (telegram_user_id, telegram_chat_id, state, data, expires_at) "
        "VALUES (%s, %s, %s, %s, now() + make_interval(mins => %s)) "
        "ON CONFLICT (telegram_user_id, telegram_chat_id) DO UPDATE "
        "SET state = EXCLUDED.state, data = EXCLUDED.data, expires_at = EXCLUDED.expires_at",
        (telegram_user_id, chat_id, state, Jsonb(data or {}), ttl_minutes),
    )


def get_state(conn: Connection, telegram_user_id: int, chat_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM conversation_states WHERE telegram_user_id = %s AND telegram_chat_id = %s "
        "AND (expires_at IS NULL OR expires_at > now())",
        (telegram_user_id, chat_id),
    ).fetchone()


def clear(conn: Connection, telegram_user_id: int, chat_id: int) -> int:
    return conn.execute(
        "DELETE FROM conversation_states WHERE telegram_user_id = %s AND telegram_chat_id = %s",
        (telegram_user_id, chat_id),
    ).rowcount
