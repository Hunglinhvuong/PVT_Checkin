"""Integration test (cần TEST_DATABASE_URL, XÓA schema public của DB test)."""
import dataclasses
import os

import psycopg
import pytest

from config.settings import load_settings
from database.connection import close_pool, get_conn, init_pool
from database.migrate import migrate
from services import admin_accounts as acc
from services import dashboard_auth as auth

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")
SUPER, ADMIN, VIEWER, OFF = 1001, 1002, 1003, 1004


@pytest.fixture(scope="module", autouse=True)
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("INSERT INTO admins (telegram_user_id, role, status) VALUES (%s,'SUPER_ADMIN','ACTIVE'),(%s,'ADMIN','ACTIVE'),"
                  "(%s,'VIEWER','ACTIVE'),(%s,'ADMIN','DISABLED')", (SUPER, ADMIN, VIEWER, OFF))
    init_pool(dataclasses.replace(load_settings(), database_url=URL))
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean():
    with get_conn() as conn:
        conn.execute("DELETE FROM dashboard_login_codes")


def _wrong(code):
    return "000000" if code != "000000" else "111111"


def test_login_success_audited_and_single_use():
    code = auth.issue_code(ADMIN).code
    assert code and len(code) == 6 and code.isdigit()
    with get_conn() as conn:
        row = conn.execute("SELECT code_hash, salt FROM dashboard_login_codes").fetchone()
    assert code not in (row["code_hash"], row["salt"])  # không lưu mã gốc
    res = auth.verify_code(ADMIN, code)
    assert res.kind == "OK" and res.admin["role"] == "ADMIN"
    assert auth.verify_code(ADMIN, code).kind == "NONE"  # dùng một lần
    with get_conn() as conn:
        a = conn.execute("SELECT actor_type, actor_telegram_id FROM audit_logs WHERE action = 'DASHBOARD_LOGIN'").fetchone()
    assert (a["actor_type"], a["actor_telegram_id"]) == ("DASHBOARD", ADMIN)


def test_wrong_code_attempts_then_locked_even_with_right_code():
    code = auth.issue_code(ADMIN).code
    for _ in range(auth.MAX_VERIFY_ATTEMPTS):
        assert auth.verify_code(ADMIN, _wrong(code)).kind == "INVALID"
    assert auth.verify_code(ADMIN, code).kind == "LOCKED"


def test_format_errors_count_as_invalid():
    code = auth.issue_code(ADMIN).code
    assert auth.verify_code(ADMIN, "12ab56").kind == "INVALID" and auth.verify_code(ADMIN, "").kind == "INVALID"
    assert auth.verify_code(ADMIN, code).kind == "OK"


def test_expired_code():
    code = auth.issue_code(ADMIN).code
    with get_conn() as conn:
        conn.execute("UPDATE dashboard_login_codes SET expires_at = now() - interval '1 second'")
    assert auth.verify_code(ADMIN, code).kind == "NONE"


def test_new_code_invalidates_previous():
    first = auth.issue_code(ADMIN).code
    second = auth.issue_code(ADMIN).code
    if first != second:
        assert auth.verify_code(ADMIN, first).kind == "INVALID"
    assert auth.verify_code(ADMIN, second).kind == "OK"


def test_non_admins_get_identical_response_but_no_code():
    for tg in (VIEWER, OFF, 424242):
        res = auth.issue_code(tg)
        assert (res.kind, res.code) == ("OK", None)
        assert auth.verify_code(tg, "123456").kind == "NONE"


def test_rate_limit_per_id_applies_to_non_admins_too():
    for tg in (ADMIN, 777):
        kinds = [auth.issue_code(tg).kind for _ in range(auth.MAX_REQUESTS_PER_ID + 1)]
        assert kinds == ["OK"] * auth.MAX_REQUESTS_PER_ID + ["RATE_LIMITED"]


def test_global_rate_limit():
    for i in range(auth.MAX_REQUESTS_TOTAL):
        assert auth.issue_code(50_000 + i).kind == "OK"
    assert auth.issue_code(ADMIN).kind == "RATE_LIMITED"


def test_admin_disabled_after_code_issued_cannot_login():
    code = auth.issue_code(ADMIN).code
    with get_conn() as conn:
        conn.execute("UPDATE admins SET status = 'DISABLED' WHERE telegram_user_id = %s", (ADMIN,))
    try:
        assert auth.verify_code(ADMIN, code).kind == "NONE"
        assert auth.get_editor(ADMIN) is None
    finally:
        with get_conn() as conn:
            conn.execute("UPDATE admins SET status = 'ACTIVE' WHERE telegram_user_id = %s", (ADMIN,))


def test_admin_accounts_rules():
    assert acc.add_admin(ADMIN, 2001, "ADMIN") == "NOT_SUPER"  # ADMIN thường không thêm được admin
    assert acc.add_admin(SUPER, 2001, "ROOT") == "BAD_INPUT" and acc.add_admin(SUPER, -5, "ADMIN") == "BAD_INPUT"
    assert acc.add_admin(SUPER, 2001, "ADMIN") == "OK" and acc.add_admin(SUPER, 2001, "ADMIN") == "EXISTS"
    ids = {r["telegram_user_id"]: r["id"] for r in acc.list_admins()}
    assert acc.set_status(SUPER, ids[SUPER], "DISABLED") == "SELF"
    assert acc.set_status(SUPER, ids[2001], "DISABLED") == "OK"
    assert acc.add_admin(SUPER, 2001, "VIEWER") == "OK"  # mở lại với vai trò mới
    assert [r["role"] for r in acc.list_admins() if r["telegram_user_id"] == 2001] == ["VIEWER"]
    assert acc.set_status(SUPER, 999999, "DISABLED") == "NOT_FOUND" and acc.set_status(ADMIN, ids[2001], "DISABLED") == "NOT_SUPER"


def test_cannot_disable_last_super_admin():
    ids = {r["telegram_user_id"]: r["id"] for r in acc.list_admins()}
    assert acc.add_admin(SUPER, 3001, "SUPER_ADMIN") == "OK"
    ids = {r["telegram_user_id"]: r["id"] for r in acc.list_admins()}
    assert acc.set_status(3001, ids[SUPER], "DISABLED") == "OK"  # còn 3001 nên khóa được SUPER cũ
    assert acc.set_status(3001, ids[3001], "DISABLED") == "SELF"
    with get_conn() as conn:
        conn.execute("UPDATE admins SET status = 'ACTIVE' WHERE telegram_user_id = %s", (SUPER,))
    assert acc.set_status(SUPER, ids[3001], "DISABLED") == "OK"
    assert acc.set_status(SUPER, ids[SUPER], "DISABLED") == "SELF"
