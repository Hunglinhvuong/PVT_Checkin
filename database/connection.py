"""Kết nối PostgreSQL (psycopg 3) với connection pool."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config.settings import Settings

log = logging.getLogger(__name__)
_pool: ConnectionPool | None = None


def init_pool(settings: Settings) -> ConnectionPool:
    global _pool
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL chưa được cấu hình")
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"row_factory": dict_row},
            timeout=10,
            open=False,
        )
        _pool.open(wait=True, timeout=10)
        log.info("Đã mở connection pool (%s-%s)", settings.db_pool_min, settings.db_pool_max)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """Transaction tự commit khi thoát bình thường, rollback khi có lỗi."""
    if _pool is None:
        raise RuntimeError("Pool chưa khởi tạo, gọi init_pool() trước")
    with _pool.connection() as conn:
        yield conn


def check_connection(settings: Settings) -> dict:
    """Kiểm tra kết nối nhanh, không dùng pool. Trả về thông tin phiên bản/giờ."""
    with psycopg.connect(settings.database_url, connect_timeout=10, row_factory=dict_row) as conn:
        row = conn.execute("SELECT version() AS version, now() AS server_time").fetchone()
        return dict(row)
