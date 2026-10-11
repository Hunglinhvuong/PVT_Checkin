"""Kiểm tra cấu hình + kết nối DB + tình trạng migration.  Chạy: python -m scripts.check_db"""
from __future__ import annotations

import logging
import sys

import psycopg

from config.logging_config import setup_logging
from config.settings import ConfigError, load_settings
from database.connection import check_connection


def main() -> int:
    try:
        s = load_settings()
        setup_logging(s.log_level, s.log_dir)
        log = logging.getLogger("check_db")
        if not s.database_url:
            raise ConfigError("DATABASE_URL chưa được cấu hình")
        info = check_connection(s)
        log.info("Kết nối OK: %s", info["version"])
        log.info("Giờ server (UTC): %s", info["server_time"])
        with psycopg.connect(s.database_url) as conn:
            exists = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
            if exists:
                rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                log.info("Migration đã áp dụng: %s", [r[0] for r in rows] or "không có")
            else:
                log.warning("Chưa chạy migration. Chạy: python main.py migrate")
        return 0
    except ConfigError as exc:
        print(f"[CẤU HÌNH] {exc}", file=sys.stderr)
    except psycopg.Error as exc:
        print(f"[DB] {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
