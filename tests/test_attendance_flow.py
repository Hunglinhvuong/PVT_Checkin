"""Integration test (cần TEST_DATABASE_URL, XÓA schema public của DB test)."""
import dataclasses
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import psycopg
import pytest

from config.settings import load_settings
from database.connection import close_pool, get_conn, init_pool
from database.migrate import migrate
from services import attendance as att
from services import holidays, photo_storage

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")
TZ = "Asia/Ho_Chi_Minh"
ADMIN = 9001
MON_AM = datetime(2026, 10, 12, 0, 30, tzinfo=timezone.utc)  # 07:30 VN, thứ Hai, sáng
MON_PM = datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)  # 13:30 VN, chiều
SAT_AM = datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc)  # thứ Bảy
TUE_AM = datetime(2026, 10, 13, 0, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("INSERT INTO admins (telegram_user_id, role) VALUES (%s, 'ADMIN')", (ADMIN,))
        aid = c.execute("SELECT id FROM admins").fetchone()[0]
        for tg, status in [(1, "APPROVED"), (2, "APPROVED"), (3, "PENDING"), (4, "REJECTED")]:
            c.execute(
                "INSERT INTO users (telegram_user_id, full_name, status, approved_at, approved_by) "
                "VALUES (%s, %s, %s, CASE WHEN %s='APPROVED' THEN now() END, CASE WHEN %s='APPROVED' THEN %s END)",
                (tg, f"User {tg}", status, status, status, aid),
            )
    init_pool(dataclasses.replace(load_settings(), database_url=URL))
    yield
    close_pool()


_msg = iter(range(1000, 10**6))


def _intake(tg, at, sha="a" * 64, path="/tmp/none.jpg"):
    return att.PhotoIntake(tg, -100, next(_msg), "uniq", at, sha, path, "jpg", 123, "PHOTO")


def test_eligibility_kinds():
    assert att.precheck(99, MON_AM, TZ).kind == "NOT_REGISTERED"
    assert att.precheck(3, MON_AM, TZ).kind == "PENDING"
    assert att.precheck(4, MON_AM, TZ).kind == "REJECTED"
    assert att.precheck(1, SAT_AM, TZ).kind == "NON_WORKING_DAY"
    ok = att.precheck(1, MON_AM, TZ)
    assert (ok.kind, ok.session, ok.work_date) == ("OK", "MORNING", date(2026, 10, 12))
    assert att.precheck(1, MON_PM, TZ).session == "AFTERNOON"


def test_unapproved_cannot_record():
    assert att.record_intake(_intake(3, MON_AM), TZ).kind == "PENDING"


def test_duplicate_session_first_wins_other_session_ok():
    first = att.record_intake(_intake(1, MON_AM, sha="b" * 64), TZ)
    assert first.kind == "OK" and first.record["verification_status"] == "PENDING"
    assert att.record_intake(_intake(1, MON_AM, sha="c" * 64), TZ).kind == "DUPLICATE"
    assert att.record_intake(_intake(1, MON_PM, sha="d" * 64), TZ).kind == "OK"
    with get_conn() as conn:
        row = conn.execute("SELECT sha256 FROM attendance_photos WHERE attendance_record_id = %s", (first.record["id"],)).fetchone()
        assert row["sha256"] == "b" * 64  # ảnh đầu không bị ghi đè
        jobs = conn.execute("SELECT count(*) AS n FROM verification_jobs WHERE attendance_record_id = %s", (first.record["id"],)).fetchone()
        assert jobs["n"] == 1


def test_concurrent_same_session_single_record():
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda i: att.record_intake(_intake(2, TUE_AM, sha=f"{i:064x}"), TZ), range(6)))
    assert sorted(r.kind for r in res).count("OK") == 1
    assert {r.kind for r in res} <= {"OK", "DUPLICATE"}
    with get_conn() as conn:
        n = conn.execute("SELECT count(*) AS n FROM attendance_records WHERE user_id = (SELECT id FROM users WHERE telegram_user_id = 2) AND attendance_date = '2026-10-13'").fetchone()["n"]
        p = conn.execute("SELECT count(*) AS n FROM attendance_photos p JOIN attendance_records r ON r.id = p.attendance_record_id WHERE r.attendance_date = '2026-10-13'").fetchone()["n"]
    assert n == 1 and p == 1


def test_same_message_redelivery_is_duplicate():
    wed = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
    i = _intake(1, wed, sha="e" * 64)
    assert att.record_intake(i, TZ).kind == "OK"
    assert att.record_intake(i, TZ).kind == "DUPLICATE"


def test_same_hash_other_user_is_flagged():
    thu = datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc)
    assert att.record_intake(_intake(1, thu, sha="f" * 64), TZ).flagged is False
    res = att.record_intake(_intake(2, thu, sha="f" * 64), TZ)
    assert res.kind == "OK" and res.flagged
    with get_conn() as conn:
        r = conn.execute("SELECT flags, final_status FROM attendance_records WHERE id = %s", (res.record["id"],)).fetchone()
    assert r["flags"] == ["DUPLICATE_PHOTO_HASH"] and r["final_status"] == "PENDING_REVIEW"


def test_holiday_overrides_and_admin_only():
    assert holidays.set_day(555, date(2026, 10, 10), "x", True) is False
    assert holidays.set_day(ADMIN, date(2026, 10, 10), "Làm bù", True)
    assert att.precheck(1, SAT_AM, TZ).kind == "OK"
    assert holidays.set_day(ADMIN, date(2026, 10, 16), "Nghỉ thử", False)
    assert att.precheck(1, datetime(2026, 10, 16, 0, 30, tzinfo=timezone.utc), TZ).kind == "NON_WORKING_DAY"
    assert holidays.remove_day(ADMIN, date(2026, 10, 16)) == "OK"
    assert holidays.remove_day(ADMIN, date(2026, 10, 16)) == "NOT_FOUND"
    assert holidays.remove_day(555, date(2026, 10, 10)) == "NOT_ADMIN"
    with get_conn() as conn:
        n = conn.execute("SELECT count(*) AS n FROM audit_logs WHERE action LIKE 'HOLIDAY_%'").fetchone()["n"]
    assert n == 3


def test_upload_success_failure_and_retry(monkeypatch, tmp_path):
    fri = datetime(2026, 10, 23, 0, 30, tzinfo=timezone.utc)
    f = tmp_path / "p.jpg"
    f.write_bytes(b"\xff\xd8\xff" + b"0" * 10)
    res = att.record_intake(_intake(1, fri, sha="9" * 64, path=str(f)), TZ)
    pid = res.photo["id"]
    calls = []

    def boom(path, public_id):
        calls.append(public_id)
        raise RuntimeError("mạng lỗi")

    monkeypatch.setattr(photo_storage, "_upload", boom)
    assert photo_storage.upload_photo(pid) is False
    with get_conn() as conn:
        row = conn.execute("SELECT upload_attempts, cloudinary_public_id FROM attendance_photos WHERE id = %s", (pid,)).fetchone()
    assert row["upload_attempts"] == 1 and row["cloudinary_public_id"] is None

    def ok(path, public_id):
        calls.append(public_id)
        return {"public_id": public_id, "secure_url": "https://res.cloudinary.com/x/" + public_id}

    monkeypatch.setattr(photo_storage, "_upload", ok)
    assert photo_storage.retry_pending() >= 1
    expected = f"attendance/2026-10-23/{res.user['id']}_MORNING_{res.record['id']}"
    assert calls[-1] == expected and calls[0] == expected  # cùng public_id => không tạo trùng
    with get_conn() as conn:
        row = conn.execute("SELECT cloudinary_public_id, uploaded_at FROM attendance_photos WHERE id = %s", (pid,)).fetchone()
    assert row["cloudinary_public_id"] == expected and row["uploaded_at"] is not None
    assert photo_storage.upload_photo(pid) is True and len(calls) == 2  # đã upload: không gọi lại


# ------------------------------------------------------------------ gửi lại ảnh sau Status -2301
MON2 = datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)  # thứ Hai 07:30 VN


def _reject_no_code(record_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE attendance_records SET verification_status = 'UNABLE_TO_VERIFY', final_status = 'REJECTED', "
            "resubmit_allowed = TRUE WHERE id = %s", (record_id,))


def test_resubmit_after_no_valid_code_supersedes_without_deleting_history():
    first = att.record_intake(_intake(1, MON2, sha="1" * 64), TZ)
    assert first.kind == "OK"
    _reject_no_code(first.record["id"])
    assert att.precheck(1, MON2, TZ).kind == "OK"  # được phép gửi ảnh khác

    second = att.record_intake(_intake(1, MON2, sha="2" * 64), TZ)
    assert second.kind == "OK" and second.superseded_id == first.record["id"]
    with get_conn() as conn:
        old = conn.execute("SELECT superseded_at, superseded_by, resubmit_allowed, final_status FROM attendance_records WHERE id = %s", (first.record["id"],)).fetchone()
        n_photos = conn.execute("SELECT count(*) AS n FROM attendance_photos WHERE attendance_record_id = %s", (first.record["id"],)).fetchone()["n"]
        active = conn.execute("SELECT count(*) AS n FROM attendance_records WHERE user_id = (SELECT id FROM users WHERE telegram_user_id = 1) AND attendance_date = '2026-10-26' AND session = 'MORNING' AND superseded_at IS NULL").fetchone()["n"]
    assert old["superseded_at"] and old["superseded_by"] == second.record["id"] and not old["resubmit_allowed"]
    assert old["final_status"] == "REJECTED" and n_photos == 1  # lịch sử giữ nguyên
    assert active == 1
    assert att.precheck(1, MON2, TZ).kind == "DUPLICATE"  # ảnh mới đang chờ xác minh: các ảnh sau bị bỏ qua


def test_attempts_are_capped_at_three_per_session():
    tue = datetime(2026, 10, 27, 0, 30, tzinfo=timezone.utc)
    for i in range(3):
        res = att.record_intake(_intake(2, tue, sha=f"{i + 5:064x}"), TZ)
        assert res.kind == "OK", i
        _reject_no_code(res.record["id"])
    assert att.precheck(2, tue, TZ).kind == "ATTEMPTS_EXCEEDED"
    assert att.record_intake(_intake(2, tue, sha="f" * 63 + "0"), TZ).kind == "ATTEMPTS_EXCEEDED"


def test_edited_photo_can_be_resubmitted():
    wed = datetime(2026, 10, 28, 0, 30, tzinfo=timezone.utc)
    res = att.record_intake(_intake(1, wed, sha="7" * 64), TZ)
    with get_conn() as conn:
        conn.execute("UPDATE attendance_records SET verification_status = 'VERIFIED_FAKE', final_status = 'REJECTED' WHERE id = %s", (res.record["id"],))
    assert att.record_intake(_intake(1, wed, sha="8" * 64), TZ).kind == "OK"


def test_concurrent_resubmissions_only_one_wins():
    thu = datetime(2026, 10, 29, 0, 30, tzinfo=timezone.utc)
    first = att.record_intake(_intake(1, thu, sha="9" * 64), TZ)
    _reject_no_code(first.record["id"])
    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(lambda i: att.record_intake(_intake(1, thu, sha=f"{i + 100:064x}"), TZ), range(5)))
    assert sorted(r.kind for r in res).count("OK") == 1 and {r.kind for r in res} <= {"OK", "DUPLICATE"}
    with get_conn() as conn:
        active = conn.execute("SELECT count(*) AS n FROM attendance_records WHERE attendance_date = '2026-10-29' AND superseded_at IS NULL AND user_id = (SELECT id FROM users WHERE telegram_user_id = 1)").fetchone()["n"]
        total = conn.execute("SELECT count(*) AS n FROM attendance_records WHERE attendance_date = '2026-10-29' AND user_id = (SELECT id FROM users WHERE telegram_user_id = 1)").fetchone()["n"]
    assert active == 1 and total == 2


def test_date_mismatch_record_is_superseded_and_old_becomes_rejected():
    fri = datetime(2026, 10, 30, 0, 30, tzinfo=timezone.utc)
    first = att.record_intake(_intake(2, fri, sha="d" * 64), TZ)
    with get_conn() as conn:
        conn.execute(
            "UPDATE attendance_records SET verification_status = 'VERIFIED_AUTHENTIC', final_status = 'PENDING_REVIEW', "
            "flags = ARRAY['CAPTURE_DATE_MISMATCH'], resubmit_allowed = TRUE WHERE id = %s", (first.record["id"],))
    second = att.record_intake(_intake(2, fri, sha="e" * 64), TZ)
    assert second.kind == "OK" and second.superseded_id == first.record["id"]
    with get_conn() as conn:
        old = conn.execute("SELECT final_status, superseded_by, flags FROM attendance_records WHERE id = %s", (first.record["id"],)).fetchone()
    assert old["final_status"] == "REJECTED" and old["superseded_by"] == second.record["id"]
    assert old["flags"] == ["CAPTURE_DATE_MISMATCH"]  # lịch sử/cờ giữ nguyên


def test_wrong_session_record_can_be_replaced():
    mon = datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc)
    first = att.record_intake(_intake(2, mon, sha="a" * 64), TZ)
    with get_conn() as conn:
        conn.execute(
            "UPDATE attendance_records SET final_status = 'PENDING_REVIEW', flags = ARRAY['CAPTURE_SESSION_MISMATCH'] "
            "WHERE id = %s", (first.record["id"],))
    second = att.record_intake(_intake(2, mon, sha="b" * 64), TZ)
    assert second.kind == "OK" and second.superseded_id == first.record["id"]


# ------------------------------------------------------------------ Giai đoạn 7: tải đồng thời & lỗi Cloudinary
def test_many_users_send_photos_concurrently_without_cross_assignment():
    from collections import Counter

    n = 20
    with get_conn() as conn:
        aid = conn.execute("SELECT id FROM admins LIMIT 1").fetchone()["id"]
        for i in range(n):
            conn.execute(
                "INSERT INTO users (telegram_user_id, full_name, status, approved_at, approved_by) "
                "VALUES (%s, %s, 'APPROVED', now(), %s)", (5000 + i, f"NV {i}", aid))
    mon = datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc)  # thứ Hai 07:30 VN

    def send(args):
        i, k = args
        mid = 70000 + i * 10 + k  # message_id mã hóa người gửi để kiểm tra không bị gán nhầm
        return i, att.record_intake(att.PhotoIntake(5000 + i, -100, mid, f"u{i}", mon, f"{i * 10 + k:064x}", "/tmp/x.jpg", "jpg", 10, "PHOTO"), TZ)

    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(send, [(i, k) for i in range(n) for k in range(3)]))
    ok_per_user = Counter(i for i, r in results if r.kind == "OK")
    assert len(ok_per_user) == n and set(ok_per_user.values()) == {1}
    assert {r.kind for _, r in results} <= {"OK", "DUPLICATE"}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.telegram_user_id AS tg, r.telegram_message_id AS mid FROM attendance_records r "
            "JOIN users u ON u.id = r.user_id WHERE u.telegram_user_id >= 5000 AND r.attendance_date = '2026-11-02'").fetchall()
        photos = conn.execute("SELECT count(*) AS n FROM attendance_photos p JOIN attendance_records r ON r.id = p.attendance_record_id WHERE r.attendance_date = '2026-11-02'").fetchone()["n"]
        jobs = conn.execute("SELECT count(*) AS n FROM verification_jobs j JOIN attendance_records r ON r.id = j.attendance_record_id WHERE r.attendance_date = '2026-11-02'").fetchone()["n"]
    assert len(rows) == n and all((r["mid"] - 70000) // 10 == r["tg"] - 5000 for r in rows)  # ảnh của ai thuộc về người đó
    assert photos == n and jobs == n


def test_cloudinary_down_keeps_record_and_stops_retrying_after_max_attempts(monkeypatch, tmp_path):
    mon = datetime(2026, 11, 9, 0, 30, tzinfo=timezone.utc)
    f = tmp_path / "p.jpg"
    f.write_bytes(b"\xff\xd8\xff" + b"0" * 10)
    res = att.record_intake(_intake(1, mon, sha="c" * 64, path=str(f)), TZ)
    assert res.kind == "OK"
    calls = []

    def down(path, public_id):
        calls.append(public_id)
        raise ConnectionError("Cloudinary không phản hồi")

    monkeypatch.setattr(photo_storage, "_upload", down)
    for _ in range(photo_storage.MAX_UPLOAD_ATTEMPTS + 3):
        photo_storage.retry_pending()
    with get_conn() as conn:
        p = conn.execute("SELECT upload_attempts, cloudinary_public_id, last_upload_error FROM attendance_photos WHERE id = %s", (res.photo["id"],)).fetchone()
    assert p["upload_attempts"] == photo_storage.MAX_UPLOAD_ATTEMPTS == len(calls)  # dừng thử sau số lần tối đa
    assert p["cloudinary_public_id"] is None and "Cloudinary" in p["last_upload_error"]
    assert f.exists()  # file tạm được giữ để xử lý tay/thử lại sau
    assert att.precheck(1, mon, TZ).kind == "DUPLICATE"  # bản ghi điểm danh không bị mất
