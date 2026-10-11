"""Trình chạy migration SQL đơn giản: database/migrations/NNN_name.sql, chạy theo thứ tự, mỗi file một transaction."""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")
_LOCK_ID = 726_001  # advisory lock tránh chạy migration song song

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def migrate(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    files = sorted(p for p in migrations_dir.glob("*.sql"))
    bad = [p.name for p in files if not _NAME_RE.match(p.name)]
    if bad:
        raise ValueError(f"Tên migration không hợp lệ: {bad}")

    applied_now: list[str] = []
    with psycopg.connect(database_url, connect_timeout=10, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(_BOOTSTRAP)
            done = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
            for path in files:
                sql = path.read_text(encoding="utf-8")
                chk = _checksum(sql)
                if path.name in done:
                    if done[path.name] != chk:
                        raise RuntimeError(
                            f"Migration {path.name} đã áp dụng nhưng nội dung bị sửa. Hãy tạo migration mới."
                        )
                    continue
                log.info("Áp dụng migration %s", path.name)
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (path.name, chk),
                    )
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied_now
