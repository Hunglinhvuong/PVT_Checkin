"""Nghiệp vụ đăng ký/duyệt. Hàm đồng bộ, mỗi hàm một transaction; handler gọi qua asyncio.to_thread."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from database.connection import get_conn
from repositories import admins, audit, conversations, users

_ALLOWED_PUNCT = set(" .'-")


def normalize_name(raw: str | None) -> str | None:
    """Chuẩn hóa NFC, gộp khoảng trắng. Trả None nếu không hợp lệ (2-100 ký tự, chỉ chữ + ' .-')."""
    if not raw:
        return None
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw)).strip()
    if not 2 <= len(name) <= 100:
        return None
    if not all(c.isalpha() or c in _ALLOWED_PUNCT or unicodedata.category(c).startswith("M") for c in name):
        return None
    if sum(c.isalpha() for c in name) < 2:
        return None
    return name


@dataclass(frozen=True)
class StartResult:
    kind: str  # NEW | PENDING | APPROVED | REJECTED | DISABLED
    user: dict | None = None


@dataclass(frozen=True)
class NameResult:
    kind: str  # NO_STATE | INVALID | EXISTS | CREATED
    user: dict | None = None
    admins: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class DecideResult:
    kind: str  # NOT_ADMIN | NOT_FOUND | ALREADY | OK
    user: dict | None = None


def start(telegram_user_id: int, chat_id: int) -> StartResult:
    with get_conn() as conn:
        user = users.get_by_telegram_id(conn, telegram_user_id)
        if user:
            return StartResult(user["status"], user)
        conversations.set_state(conn, telegram_user_id, chat_id, conversations.AWAITING_NAME)
        return StartResult("NEW")


def cancel(telegram_user_id: int, chat_id: int) -> bool:
    with get_conn() as conn:
        return conversations.clear(conn, telegram_user_id, chat_id) > 0


def submit_name(telegram_user_id: int, chat_id: int, raw_name: str | None) -> NameResult:
    with get_conn() as conn:
        state = conversations.get_state(conn, telegram_user_id, chat_id)
        if not state or state["state"] != conversations.AWAITING_NAME:
            return NameResult("NO_STATE")
        name = normalize_name(raw_name)
        if name is None:
            return NameResult("INVALID")
        user = users.create_pending(conn, telegram_user_id, name, chat_id)
        conversations.clear(conn, telegram_user_id, chat_id)
        if user is None:
            return NameResult("EXISTS", users.get_by_telegram_id(conn, telegram_user_id))
        audit.add(
            conn,
            action="USER_REGISTERED",
            entity_type="users",
            entity_id=user["id"],
            actor_telegram_id=telegram_user_id,
            actor_type="USER",
            new_value={"status": "PENDING", "full_name": name},
        )
        return NameResult("CREATED", user, admins.list_active(conn))


def decide(admin_telegram_id: int, user_id: int, approve: bool) -> DecideResult:
    new_status = "APPROVED" if approve else "REJECTED"
    with get_conn() as conn:
        admin = admins.get_active(conn, admin_telegram_id)
        if not admin or admin["role"] == "VIEWER":
            return DecideResult("NOT_ADMIN")
        user = users.decide(conn, user_id, new_status, admin["id"])
        if user is None:
            existing = users.get_by_id(conn, user_id)
            return DecideResult("ALREADY" if existing else "NOT_FOUND", existing)
        audit.add(
            conn,
            action="USER_APPROVED" if approve else "USER_REJECTED",
            entity_type="users",
            entity_id=user_id,
            actor_telegram_id=admin_telegram_id,
            actor_type="ADMIN",
            old_value={"status": "PENDING"},
            new_value={"status": new_status},
        )
        return DecideResult("OK", user)


def list_pending(admin_telegram_id: int) -> list[dict] | None:
    """None nếu người gọi không phải admin."""
    with get_conn() as conn:
        admin = admins.get_active(conn, admin_telegram_id)
        if not admin or admin["role"] == "VIEWER":
            return None
        return users.list_pending(conn)
