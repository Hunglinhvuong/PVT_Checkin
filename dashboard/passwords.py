"""Password hashing and dashboard account configuration."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
_MIN_ITERATIONS = 100_000
_MAX_ITERATIONS = 2_000_000
_HEX_RE = re.compile(r"^[0-9a-f]+$")


class DashboardAuthConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DashboardUser:
    username: str
    password_hash: str
    telegram_id: int


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Mật khẩu dashboard phải có ít nhất 12 ký tự.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        iterations = int(rounds)
    except (AttributeError, ValueError):
        return False
    if (
        algorithm != ALGORITHM
        or not _MIN_ITERATIONS <= iterations <= _MAX_ITERATIONS
        or len(salt) != 32
        or len(expected) != 64
        or not _HEX_RE.fullmatch(salt)
        or not _HEX_RE.fullmatch(expected)
    ):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return hmac.compare_digest(actual, expected)


def parse_dashboard_users(raw: Sequence[Mapping] | None) -> tuple[DashboardUser, ...]:
    if not raw:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise DashboardAuthConfigError("dashboard_users phải là danh sách tài khoản trong Streamlit Secrets.")
    if not all(isinstance(entry, Mapping) for entry in raw):
        raise DashboardAuthConfigError("Mỗi tài khoản dashboard_users phải có username, password_hash và telegram_id.")

    users = []
    names = set()
    for entry in raw:
        username = str(entry.get("username", "")).strip()
        encoded = str(entry.get("password_hash", "")).strip()
        try:
            telegram_id = int(entry["telegram_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DashboardAuthConfigError("telegram_id trong dashboard_users phải là số nguyên dương.") from exc
        if not username or not _valid_password_hash(encoded) or telegram_id <= 0:
            raise DashboardAuthConfigError("Tài khoản dashboard_users có cấu hình không hợp lệ.")
        if username in names:
            raise DashboardAuthConfigError(f"username dashboard bị trùng: {username}")
        names.add(username)
        users.append(DashboardUser(username, encoded, telegram_id))
    return tuple(users)


def _valid_password_hash(encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        iterations = int(rounds)
    except (AttributeError, ValueError):
        return False
    return (
        algorithm == ALGORITHM
        and _MIN_ITERATIONS <= iterations <= _MAX_ITERATIONS
        and len(salt) == 32
        and len(expected) == 64
        and bool(_HEX_RE.fullmatch(salt))
        and bool(_HEX_RE.fullmatch(expected))
    )
