"""Integration test: cần TEST_DATABASE_URL (DB trống riêng cho test). Tự bỏ qua nếu không có."""
import os
import uuid

import psycopg
import pytest

from database.migrate import migrate

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        yield c


def _admin(conn):
    return conn.execute(
        "INSERT INTO admins (telegram_user_id) VALUES (%s) RETURNING id", (uuid.uuid4().int % 10**12,)
    ).fetchone()[0]


def _user(conn, status="PENDING", admin_id=None):
    return conn.execute(
        "INSERT INTO users (telegram_user_id, full_name, status, approved_at, approved_by) "
        "VALUES (%s, 'Nguyen Van A', %s, CASE WHEN %s = 'APPROVED' THEN now() END, %s) RETURNING id",
        (uuid.uuid4().int % 10**12, status, status, admin_id),
    ).fetchone()[0]


def _record(conn, user_id, msg_id, date="2026-10-12", session="MORNING"):
    return conn.execute(
        "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, "
        "telegram_message_id, photo_id, photo_received_at) VALUES (%s, %s, %s, -100, %s, 'p', now()) RETURNING id",
        (user_id, date, session, msg_id),
    ).fetchone()[0]


def test_migrate_idempotent():
    assert migrate(URL) == []


def test_unique_user_date_session(conn):
    uid = _user(conn)
    _record(conn, uid, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _record(conn, uid, 2)
    _record(conn, uid, 3, session="AFTERNOON")


def test_approved_requires_approver(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        _user(conn, "APPROVED", None)
    _user(conn, "APPROVED", _admin(conn))


def test_audit_append_only(conn):
    conn.execute("INSERT INTO audit_logs (action, entity_type) VALUES ('X', 'users')")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE audit_logs SET action = 'Y'")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM audit_logs")


def test_single_active_job_per_record(conn):
    rid = _record(conn, _user(conn), 99, date="2026-10-13")
    conn.execute("INSERT INTO verification_jobs (attendance_record_id) VALUES (%s)", (rid,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO verification_jobs (attendance_record_id) VALUES (%s)", (rid,))


def test_single_active_office(conn):
    conn.execute("INSERT INTO office_locations (name, latitude, longitude) VALUES ('HQ', 21.0, 105.8)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO office_locations (name, latitude, longitude) VALUES ('B', 21.1, 105.9)")
