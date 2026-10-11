from __future__ import annotations

from contextvars import ContextVar

from psycopg import Connection
from psycopg.types.json import Jsonb

# Nguồn thao tác: dashboard đặt "DASHBOARD" để các thao tác admin được ghi nhận đúng nguồn (actor_type).
SOURCE: ContextVar[str | None] = ContextVar("audit_source", default=None)


def add(
    conn: Connection,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    actor_telegram_id: int | None = None,
    actor_type: str = "SYSTEM",
    old_value: dict | None = None,
    new_value: dict | None = None,
    reason: str | None = None,
) -> None:
    if actor_type == "ADMIN" and SOURCE.get():
        actor_type = SOURCE.get()
    conn.execute(
        "INSERT INTO audit_logs (actor_telegram_id, actor_type, action, entity_type, entity_id, "
        "old_value, new_value, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            actor_telegram_id,
            actor_type,
            action,
            entity_type,
            entity_id,
            Jsonb(old_value) if old_value is not None else None,
            Jsonb(new_value) if new_value is not None else None,
            reason,
        ),
    )
