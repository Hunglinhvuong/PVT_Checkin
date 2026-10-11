"""Integration test (cần TEST_DATABASE_URL, XÓA schema public của DB test)."""
import dataclasses
import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from config.settings import load_settings
from dashboard import data
from database.connection import close_pool, get_conn, init_pool
from database.migrate import migrate
from repositories import audit
from services import holidays, users_admin

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")
TZ = "Asia/Ho_Chi_Minh"
ADMIN = 9001
D = date(2026, 10, 12)  # thứ Hai
VN = timezone(timedelta(hours=7))


@pytest.fixture(scope="module", autouse=True)
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        aid = c.execute("INSERT INTO admins (telegram_user_id, role) VALUES (%s, 'ADMIN') RETURNING id", (ADMIN,)).fetchone()[0]
        for tg, name, status, appr in [
            (1, "An", "APPROVED", "2026-01-01"), (2, "Bình", "APPROVED", "2026-01-01"),
            (3, "Chi", "PENDING", None), (4, "Dũng", "APPROVED", "2026-12-31"),
        ]:
            c.execute(
                "INSERT INTO users (telegram_user_id, full_name, status, approved_at, approved_by) VALUES (%s, %s, %s, %s, %s)",
                (tg, name, status, appr, aid if appr else None),
            )
        uid = {r[0]: r[1] for r in c.execute("SELECT telegram_user_id, id FROM users")}

        def rec(tg, session, final, ver, sha, code, flags=(), d=D):
            rid = c.execute(
                "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, telegram_message_id, "
                "photo_id, photo_received_at, verification_status, final_status, flags) "
                "VALUES (%s, %s, %s, -1, %s, 'x', %s, %s, %s, %s) RETURNING id",
                (uid[tg], d, session, hash((tg, session, str(d))) % 10**9, datetime(d.year, d.month, d.day, 1, tzinfo=timezone.utc),
                 ver, final, list(flags)),
            ).fetchone()[0]
            c.execute("INSERT INTO attendance_photos (attendance_record_id, sha256, photo_code) VALUES (%s, %s, %s)", (rid, sha, code))

        rec(1, "MORNING", "VALID", "VERIFIED_AUTHENTIC", "a" * 64, "CODE_AAA")
        rec(1, "AFTERNOON", "LATE", "VERIFIED_AUTHENTIC", "b" * 64, "CODE_BBB")
        rec(2, "MORNING", "PENDING_REVIEW", "UNABLE_TO_VERIFY", "a" * 64, "50%_X", flags=["DUPLICATE_PHOTO_HASH"])
        rec(1, "MORNING", "VALID", "VERIFIED_AUTHENTIC", "c" * 64, "CODE_CCC", d=D - timedelta(days=1))
    init_pool(dataclasses.replace(load_settings(), database_url=URL))
    yield
    close_pool()


def test_read_only_transaction_blocks_writes():
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        data._read("UPDATE users SET full_name = 'x'")


def test_missing_users_excludes_pending_recently_approved_and_present():
    assert [u["full_name"] for u in data.missing_users(D, "MORNING", TZ)] == []  # An, Bình đã có; Chi chờ duyệt; Dũng duyệt sau
    assert [u["full_name"] for u in data.missing_users(D, "AFTERNOON", TZ)] == ["Bình"]


def test_overview_counts():
    ov = data.overview(D, TZ)
    assert ov["users"] == {"APPROVED": 3, "PENDING": 1}
    assert ov["sessions"]["MORNING"] == {"VALID": 1, "PENDING_REVIEW": 1}
    assert ov["sessions"]["AFTERNOON"] == {"LATE": 1}
    assert ov["working"] and ov["missing"] == {"MORNING": 0, "AFTERNOON": 1}
    assert ov["pending_review"] == 1 and ov["pending_upload"] == 4
    assert not data.overview(date(2026, 10, 10), TZ)["working"]  # thứ Bảy: không tính "thiếu"


def test_attendance_filters():
    assert len(data.attendance_rows(D, D)) == 3
    assert len(data.attendance_rows(D - timedelta(days=1), D)) == 4
    assert [r["full_name"] for r in data.attendance_rows(D, D, sessions=("AFTERNOON",))] == ["An"]
    assert [r["final_status"] for r in data.attendance_rows(D, D, finals=("PENDING_REVIEW",))] == ["PENDING_REVIEW"]
    uid = next(u["id"] for u in data.users_basic() if u["full_name"] == "Bình")
    assert len(data.attendance_rows(D, D, user_ids=(uid,))) == 1


def test_daily_series_and_queue():
    s = data.daily_series(D, 14)
    assert {(r["attendance_date"], r["final_status"]): r["n"] for r in s}[(D, "VALID")] == 1
    q = data.review_queue()
    assert [r["full_name"] for r in q] == ["Bình"]
    d = data.record_detail(q[0]["id"])
    assert d["photo_code"] == "50%_X" and d["telegram_user_id"] == 2


def test_photo_search_text_is_escaped():
    assert [r["photo_code"] for r in data.photo_search(D, D, text="50%_")] == ["50%_X"]
    assert data.photo_search(D, D, text="%") != [] and len(data.photo_search(D, D, text="zzz")) == 0
    assert len(data.photo_search(D, D, text="CODE_AAA")) == 1
    assert len(data.photo_search(D, D, text="a" * 10)) == 2  # theo tiền tố SHA-256 (ảnh trùng hash)


def test_anomalies():
    an = data.anomalies()
    assert [r["full_name"] for r in an["flagged"]] == ["Bình"]
    dup = an["duplicate_hashes"]
    assert len(dup) == 1 and dup[0]["sha256"] == "a" * 64 and dup[0]["n"] == 2 and len(dup[0]["record_ids"]) == 2


def test_dashboard_actions_are_audited_with_dashboard_source():
    tok = audit.SOURCE.set("DASHBOARD")
    try:
        assert holidays.set_day(ADMIN, date(2026, 12, 25), "Thử nghiệm", False)
        uid = next(u["id"] for u in data.users_basic() if u["full_name"] == "An")
        assert users_admin.change_status(ADMIN, uid, "DISABLED", "nghỉ việc") == "OK"
    finally:
        audit.SOURCE.reset(tok)
    assert users_admin.change_status(ADMIN, uid, "APPROVED", "") == "BAD_INPUT"
    assert users_admin.change_status(ADMIN, uid, "REJECTED", "không cho phép") == "NOT_ALLOWED"
    assert users_admin.change_status(555, uid, "APPROVED", "lý do") == "NOT_ADMIN"
    assert users_admin.change_status(ADMIN, uid, "APPROVED", "quay lại làm") == "OK"  # xuất hiện bình thường (ngoài DASHBOARD)
    rows = data.audit_rows(date(2020, 1, 1), date(2030, 1, 1), TZ)
    by_action = {(r["action"], r["actor_type"]) for r in rows}
    assert ("HOLIDAY_SET", "DASHBOARD") in by_action and ("USER_STATUS_CHANGED", "DASHBOARD") in by_action
    assert ("USER_STATUS_CHANGED", "ADMIN") in by_action  # thao tác ngoài dashboard giữ nguồn ADMIN
    assert [r["action"] for r in data.audit_rows(date(2020, 1, 1), date(2030, 1, 1), TZ, actions=("HOLIDAY_SET",))] == ["HOLIDAY_SET"]
    assert "HOLIDAY_SET" in data.audit_actions() and "users" in data.audit_entity_types()
    with get_conn() as conn:
        u = conn.execute("SELECT status, approved_by FROM users WHERE id = %s", (uid,)).fetchone()
    assert u["status"] == "APPROVED" and u["approved_by"]  # ràng buộc APPROVED cần approved_by


def test_superseded_records_are_hidden_from_counts():
    d = D - timedelta(days=3)
    with get_conn() as conn:
        uid = conn.execute("SELECT id FROM users WHERE telegram_user_id = 2").fetchone()["id"]
        conn.execute(
            "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, telegram_message_id, "
            "photo_id, photo_received_at, final_status, superseded_at) VALUES (%s, %s, 'MORNING', -1, 777001, 'x', now(), 'REJECTED', now())",
            (uid, d))
    assert data.attendance_rows(d, d) == []
    assert data.overview(d, TZ)["sessions"]["MORNING"] == {}
    assert "Bình" in [u["full_name"] for u in data.missing_users(d, "MORNING", TZ)]  # bản ghi cũ không tính là đã điểm danh
