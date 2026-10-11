"""Integration test (cần TEST_DATABASE_URL, XÓA schema public của DB test). Dùng verifier giả, không cần Playwright."""
import dataclasses
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import count

import psycopg
import pytest

from config.settings import load_settings
from database.connection import close_pool, get_conn, init_pool
from database.migrate import migrate
from services import offices, review, verification
from services.timemark_verifier import VerificationOutcome
from workers.verification_worker import process_job

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")
ADMIN = 9001
_day = count(0)
_last = {"d": None}  # ngày của bản ghi vừa tạo bằng _mk()


def _cap(h=7, m=30, s=0, day_offset=0):
    """Thời gian chụp (giờ VN) trong ngày của bản ghi vừa tạo."""
    d = _last["d"] + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=timezone(timedelta(hours=7)))


@pytest.fixture(scope="module", autouse=True)
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        aid = c.execute("INSERT INTO admins (telegram_user_id, role) VALUES (%s, 'ADMIN') RETURNING id", (ADMIN,)).fetchone()[0]
        c.execute("INSERT INTO users (telegram_user_id, full_name, status, approved_at, approved_by) "
                  "VALUES (1, 'User 1', 'APPROVED', now(), %s)", (aid,))
    init_pool(dataclasses.replace(load_settings(), database_url=URL))
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean_queue():
    with get_conn() as conn:
        conn.execute("DELETE FROM verification_jobs")


def _mk(ext="jpg", size=1000, flags=()):
    """Tạo bản ghi + ảnh + job QUEUED (ngày khác nhau để không vướng unique)."""
    d = datetime(2026, 11, 1).date() + timedelta(days=next(_day))
    _last["d"] = d
    with get_conn() as conn:
        uid = conn.execute("SELECT id FROM users WHERE telegram_user_id = 1").fetchone()["id"]
        rid = conn.execute(
            "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, telegram_message_id, "
            "photo_id, photo_received_at, flags) VALUES (%s, %s, 'MORNING', -1, %s, 'x', %s, %s) RETURNING id",
            (uid, d, 100000 + next(_day), _cap(7, 35), list(flags)),
        ).fetchone()["id"]
        conn.execute("INSERT INTO attendance_photos (attendance_record_id, sha256, file_ext, file_size_bytes, temp_path) "
                     "VALUES (%s, %s, %s, %s, '/nonexistent')", (rid, f"{rid:064x}", ext, size))
        conn.execute("INSERT INTO verification_jobs (attendance_record_id) VALUES (%s)", (rid,))
    return rid


def _rec(rid):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM attendance_records WHERE id = %s", (rid,)).fetchone()


def _job_status(rid):
    with get_conn() as conn:
        return conn.execute("SELECT status, attempts FROM verification_jobs WHERE attendance_record_id = %s ORDER BY id DESC LIMIT 1", (rid,)).fetchone()


def _authentic(**kw):
    base = dict(status="VERIFIED_AUTHENTIC", status_text="authentic", result_text="ok", capture_time=_cap(),
                photo_code="YX3BEH29N42MKA", gps_state="NONE", fields={"GPS": "No Data", "Device Model": "Redmi K60"})
    base.update(kw)
    return VerificationOutcome(**base)


def test_authentic_flow():
    rid = _mk()
    job = verification.claim_next("w1")
    assert job.record_id == rid and _rec(rid)["verification_status"] == "PROCESSING"
    comp = verification.complete(job, _authentic())
    r = _rec(rid)
    assert comp.kind == "FINAL"
    assert (r["verification_status"], r["final_status"], r["punctuality_status"]) == ("VERIFIED_AUTHENTIC", "VALID", "ON_TIME")
    assert r["photo_taken_at"] == _cap() and r["processed_at"] is not None
    assert _job_status(rid)["status"] == "DONE"
    with get_conn() as conn:
        p = conn.execute("SELECT photo_code, gps_raw, device_model FROM attendance_photos WHERE attendance_record_id = %s", (rid,)).fetchone()
        lg = conn.execute("SELECT status, finished_at, parsed FROM verification_logs WHERE attendance_record_id = %s ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    assert (p["photo_code"], p["gps_raw"], p["device_model"]) == ("YX3BEH29N42MKA", "No Data", "Redmi K60")
    assert lg["status"] == "VERIFIED_AUTHENTIC" and lg["finished_at"] and lg["parsed"]["photo_code"] == "YX3BEH29N42MKA"


def test_fake_is_rejected_and_not_found_goes_to_review():
    r1, r2 = _mk(), _mk()
    verification.complete(verification.claim_next("w"), VerificationOutcome("VERIFIED_FAKE", status_text="fake"))
    verification.complete(verification.claim_next("w"), VerificationOutcome("VERIFIED_NOT_FOUND", status_text="not found"))
    assert _rec(r1)["final_status"] == "REJECTED" and _rec(r1)["photo_taken_at"] is None
    assert (_rec(r2)["verification_status"], _rec(r2)["final_status"]) == ("VERIFIED_NOT_FOUND", "PENDING_REVIEW")


def test_authentic_without_time_or_unreadable_gps_is_flagged_for_review():
    r1, r2 = _mk(), _mk()
    verification.complete(verification.claim_next("w"), _authentic(capture_time=None))
    verification.complete(verification.claim_next("w"), _authentic(gps_state="UNPARSEABLE"))
    assert "NO_CAPTURE_TIME" in _rec(r1)["flags"] and _rec(r1)["final_status"] == "PENDING_REVIEW"
    assert "GPS_UNREADABLE" in _rec(r2)["flags"] and _rec(r2)["final_status"] == "PENDING_REVIEW"


def test_existing_flag_blocks_auto_receive():
    rid = _mk(flags=["DUPLICATE_PHOTO_HASH"])
    with get_conn() as conn:
        conn.execute("UPDATE attendance_records SET final_status = 'PENDING_REVIEW' WHERE id = %s", (rid,))
    verification.complete(verification.claim_next("w"), _authentic())
    assert _rec(rid)["final_status"] == "PENDING_REVIEW"


def test_automation_error_retries_then_exhausts():
    rid = _mk()
    err = VerificationOutcome("AUTOMATION_ERROR", error="TIMEOUT")
    for attempt in (1, 2):
        job = verification.claim_next("w")
        assert job.attempts == attempt
        assert verification.complete(job, err).kind == "RETRY"
        assert _rec(rid)["verification_status"] == "PENDING" and verification.claim_next("w") is None  # đang backoff
        with get_conn() as conn:
            conn.execute("UPDATE verification_jobs SET run_after = now() WHERE attendance_record_id = %s", (rid,))
    job = verification.claim_next("w")
    comp = verification.complete(job, err)
    r = _rec(rid)
    assert comp.kind == "FINAL" and (r["verification_status"], r["final_status"]) == ("AUTOMATION_ERROR", "PENDING_REVIEW")
    assert _job_status(rid)["status"] == "FAILED"


def test_stale_running_job_is_recovered():
    rid = _mk()
    verification.claim_next("dead-worker")
    with get_conn() as conn:
        conn.execute("UPDATE verification_jobs SET locked_at = now() - interval '1 hour' WHERE attendance_record_id = %s", (rid,))
    job = verification.claim_next("w2")
    assert job.record_id == rid and job.attempts == 2


def test_crash_loop_finalized():
    rid = _mk()
    with get_conn() as conn:
        conn.execute("UPDATE verification_jobs SET attempts = max_attempts WHERE attendance_record_id = %s", (rid,))
    assert verification.claim_next("w") is None
    r = _rec(rid)
    assert (r["verification_status"], r["final_status"]) == ("AUTOMATION_ERROR", "PENDING_REVIEW")
    assert _job_status(rid)["status"] == "FAILED"


def test_concurrent_claims_are_distinct():
    ids = {_mk() for _ in range(5)}
    with ThreadPoolExecutor(5) as ex:
        jobs = list(ex.map(lambda i: verification.claim_next(f"w{i}"), range(5)))
    assert {j.record_id for j in jobs} == ids


class _Boom:
    def verify(self, path, *, tag, debug_dir=None):
        raise RuntimeError("chromium chết")


def test_process_job_unsupported_format_goes_manual(tmp_path):
    _mk(ext="heic")
    job = verification.claim_next("w")
    out = process_job(job, _Boom(), dataclasses.replace(load_settings(), upload_tmp_dir=tmp_path))
    assert out.status == "MANUAL_REVIEW" and "UNSUPPORTED" in out.error


def test_process_job_verifier_crash_is_automation_error(tmp_path):
    rid = _mk()
    f = tmp_path / "a.jpg"
    f.write_bytes(b"\xff\xd8\xff0")
    with get_conn() as conn:
        conn.execute("UPDATE attendance_photos SET temp_path = %s WHERE attendance_record_id = %s", (str(f), rid))
    out = process_job(verification.claim_next("w"), _Boom(), load_settings())
    assert out.status == "AUTOMATION_ERROR" and "chromium chết" in out.error


def test_manual_resolve_and_reverify():
    rid = _mk()
    verification.complete(verification.claim_next("w"), VerificationOutcome("UNABLE_TO_VERIFY", status_text="unable to verify"))
    assert [x["id"] for x in review.list_pending_review(ADMIN)].count(rid) == 1
    assert review.list_pending_review(555) is None
    assert review.reverify(555, rid, "thử lại") == "NOT_ADMIN"
    assert review.reverify(ADMIN, rid, "ab") == "BAD_INPUT"
    assert review.reverify(ADMIN, rid, "Timemark đã sửa lỗi") == "OK"
    assert review.reverify(ADMIN, rid, "lần nữa") == "ALREADY_QUEUED"
    assert _rec(rid)["verification_status"] == "PENDING"
    verification.complete(verification.claim_next("w"), _authentic())
    assert _rec(rid)["final_status"] == "VALID"  # xác minh lại thành công gỡ trạng thái chờ kiểm tra

    rid2 = _mk()
    verification.complete(verification.claim_next("w"), VerificationOutcome("VERIFIED_NOT_FOUND", status_text="not found"))
    assert review.resolve(ADMIN, rid2, "VALID", "") == "BAD_INPUT"
    assert review.resolve(555, rid2, "VALID", "đã gọi điện xác nhận") == "NOT_ADMIN"
    assert review.resolve(ADMIN, rid2, "VALID", "đã gọi điện xác nhận") == "OK"
    r = _rec(rid2)
    assert (r["final_status"], r["punctuality_status"], r["verification_status"]) == ("VALID", "ON_TIME", "VERIFIED_NOT_FOUND")
    assert review.resolve(ADMIN, rid2, "REJECTED", "đổi ý") == "NOT_PENDING"
    with get_conn() as conn:
        n = conn.execute("SELECT count(*) AS n FROM audit_logs WHERE action = 'ATTENDANCE_MANUAL_RESOLVE' AND entity_id = %s", (rid2,)).fetchone()["n"]
        m = conn.execute("SELECT reviewed_by, result_text FROM verification_logs WHERE attendance_record_id = %s AND provider = 'MANUAL_ADMIN'", (rid2,)).fetchone()
    assert n == 1 and m["reviewed_by"] and "gọi điện" in m["result_text"]


def _pending(code=-2307):
    return VerificationOutcome("UNABLE_TO_VERIFY", status_text="unable to verify", status_code=code, reason="PENDING_UPLOAD")


def _run_at_now(rid):
    with get_conn() as conn:
        mins = conn.execute(
            "SELECT EXTRACT(EPOCH FROM (run_after - now())) / 60 AS m FROM verification_jobs WHERE attendance_record_id = %s",
            (rid,),
        ).fetchone()["m"]
        conn.execute("UPDATE verification_jobs SET run_after = now() WHERE attendance_record_id = %s", (rid,))
    return float(mins)


def test_pending_upload_retries_every_10_min_max_5_then_review():
    rid = _mk()
    for n in range(5):
        job = verification.claim_next("w")
        assert job.record_id == rid and job.attempts == 1  # lượt chờ không tiêu hao attempts
        comp = verification.complete(job, _pending())
        assert (comp.kind, comp.reason, comp.first_retry) == ("RETRY", "PENDING_UPLOAD", n == 0)
        assert verification.claim_next("w") is None  # đang chờ 10 phút
        assert 9 < _run_at_now(rid) <= 10
        assert _rec(rid)["verification_status"] == "PENDING"
    job = verification.claim_next("w")  # lượt thứ 6 = sau 5 lần thử lại
    comp = verification.complete(job, _pending())
    r = _rec(rid)
    assert (comp.kind, comp.reason) == ("FINAL", "PENDING_UPLOAD")
    assert (r["verification_status"], r["final_status"]) == ("UNABLE_TO_VERIFY", "PENDING_REVIEW")
    assert _job_status(rid)["status"] == "DONE"


def test_pending_upload_then_authentic():
    rid = _mk()
    for _ in range(2):
        verification.complete(verification.claim_next("w"), _pending())
        _run_at_now(rid)
    verification.complete(verification.claim_next("w"), _authentic())
    r = _rec(rid)
    assert (r["verification_status"], r["final_status"]) == ("VERIFIED_AUTHENTIC", "VALID")
    with get_conn() as conn:
        n = conn.execute("SELECT pending_retries AS n FROM verification_jobs WHERE attendance_record_id = %s", (rid,)).fetchone()["n"]
    assert n == 2


def test_edited_photo_is_rejected_with_reason():
    rid = _mk()
    out = VerificationOutcome("VERIFIED_FAKE", status_text="unable to verify", status_code=-2300, reason="EDITED",
                              photo_code="LTDX2RRLMD6TRG", fields={"GPS": "18.691468,105.693124", "Device Model": "Redmi K60"})
    comp = verification.complete(verification.claim_next("w"), out)
    r = _rec(rid)
    assert (comp.kind, comp.reason) == ("FINAL", "EDITED")
    assert (r["verification_status"], r["final_status"], r["photo_taken_at"]) == ("VERIFIED_FAKE", "REJECTED", None)
    assert r["resubmit_allowed"] is True
    with get_conn() as conn:
        lg = conn.execute("SELECT parsed FROM verification_logs WHERE attendance_record_id = %s ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    assert lg["parsed"]["status_code"] == -2300 and lg["parsed"]["reason"] == "EDITED"


def test_placeholder_values_not_stored_on_photo():
    rid = _mk()
    out = _pending(code=-9999)
    out.reason = "UNKNOWN_CODE"
    out.fields = {"GPS": "No Data", "Device Model": "Unknown"}
    verification.complete(verification.claim_next("w"), out)
    with get_conn() as conn:
        p = conn.execute("SELECT device_model, gps_raw FROM attendance_photos WHERE attendance_record_id = %s", (rid,)).fetchone()
    assert p["device_model"] is None and p["gps_raw"] == "No Data"
    assert _rec(rid)["final_status"] == "PENDING_REVIEW"


# ------------------------------------------------------------------ Giai đoạn 5: đánh giá đúng giờ
OFFICE_LAT, OFFICE_LON = 18.693679106009718, 105.69232416307415


def _north(km):
    return OFFICE_LAT + math.degrees(km / 6371.0088), OFFICE_LON


def _complete_authentic(**kw):
    return verification.complete(verification.claim_next("w"), _authentic(**kw))


def test_office_is_admin_only_and_audited():
    assert offices.set_office(555, "x", OFFICE_LAT, OFFICE_LON) == "NOT_ADMIN"
    assert offices.set_office(ADMIN, "x", 0, 0) == "BAD_INPUT"
    assert offices.set_office(ADMIN, "x", 123, 10) == "BAD_INPUT"
    with get_conn() as conn:
        conn.execute("UPDATE office_locations SET is_active = FALSE")
    rid = _mk()
    lat, lon = _north(1)
    _complete_authentic(gps_state="OK", latitude=lat, longitude=lon)  # chưa có văn phòng -> kiểm tra thủ công
    r = _rec(rid)
    assert "NO_OFFICE_CONFIG" in r["flags"] and r["final_status"] == "PENDING_REVIEW" and r["punctuality_status"] == "MANUAL_REVIEW"
    assert offices.set_office(ADMIN, "Văn phòng", OFFICE_LAT, OFFICE_LON) == "OK"
    assert offices.set_office(ADMIN, "Văn phòng", OFFICE_LAT, OFFICE_LON) == "OK"  # thay thế, vẫn chỉ một văn phòng hoạt động
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM office_locations WHERE is_active").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM audit_logs WHERE action = 'OFFICE_SET'").fetchone()["n"] == 2
    assert offices.get_office()["latitude"] == pytest.approx(OFFICE_LAT)


def test_near_office_on_time_stores_distance():
    rid = _mk()
    lat, lon = 18.691468, 105.693124  # tọa độ ảnh mẫu, cách văn phòng ~0.25 km
    comp = _complete_authentic(gps_state="OK", latitude=lat, longitude=lon, capture_time=_cap(7, 49, 59))
    r = _rec(rid)
    assert (r["final_status"], r["punctuality_status"]) == ("VALID", "ON_TIME")
    assert float(r["distance_km"]) == pytest.approx(0.25, abs=0.02)
    assert comp.deadline.hour == 7 and comp.deadline.minute == 50


def test_exact_deadline_is_late():
    rid = _mk()
    comp = _complete_authentic(capture_time=_cap(7, 50, 0))
    r = _rec(rid)
    assert (r["final_status"], r["punctuality_status"]) == ("LATE", "LATE") and comp.punctuality == "LATE"


def test_far_morning_gets_later_deadline():
    r1, r2 = _mk(), _mk()
    lat, lon = _north(20)
    _complete_authentic(gps_state="OK", latitude=lat, longitude=lon, capture_time=_cap(9, 59, 59))
    lat, lon = _north(50)
    _complete_authentic(gps_state="OK", latitude=lat, longitude=lon, capture_time=_cap(11, 30, 0))
    assert _rec(r1)["final_status"] == "VALID" and _rec(r2)["final_status"] == "LATE"


def test_capture_from_previous_day_goes_to_manual_review():
    rid = _mk()
    _complete_authentic(capture_time=_cap(23, 50, day_offset=-1))
    r = _rec(rid)
    assert r["flags"] == ["CAPTURE_DATE_MISMATCH"] and r["final_status"] == "PENDING_REVIEW"
    assert r["punctuality_status"] == "MANUAL_REVIEW"


def test_capture_from_wrong_session_allows_resubmission():
    rid = _mk()
    with get_conn() as conn:
        conn.execute("UPDATE attendance_records SET photo_received_at = %s WHERE id = %s", (_cap(13, 5), rid))
    comp = _complete_authentic(capture_time=_cap(13, 0))
    r = _rec(rid)
    assert comp.reason == "SESSION_MISMATCH" and comp.resubmit_allowed
    assert r["flags"] == ["CAPTURE_SESSION_MISMATCH"] and r["final_status"] == "PENDING_REVIEW"
    assert r["resubmit_allowed"] is True


def test_reverify_recomputes_evaluation_flags():
    rid = _mk()
    _complete_authentic(capture_time=None)
    assert "NO_CAPTURE_TIME" in _rec(rid)["flags"]
    assert review.reverify(ADMIN, rid, "Timemark đã trả đủ dữ liệu") == "OK"
    _complete_authentic()
    r = _rec(rid)
    assert r["flags"] == [] and (r["final_status"], r["punctuality_status"]) == ("VALID", "ON_TIME")


def test_admin_decision_not_overridden_by_evaluation():
    rid = _mk()
    verification.complete(verification.claim_next("w"), VerificationOutcome("VERIFIED_NOT_FOUND", status_text="not found"))
    assert review.resolve(ADMIN, rid, "REJECTED", "ảnh không phải Timemark") == "OK"
    assert _rec(rid)["resubmit_allowed"] is True
    with get_conn() as conn:
        conn.execute("INSERT INTO verification_jobs (attendance_record_id) VALUES (%s)", (rid,))
    _complete_authentic()
    assert _rec(rid)["final_status"] == "REJECTED"  # quyết định đã chốt không bị đè


# ------------------------------------------------------------------ Status -2301
def _no_code():
    return VerificationOutcome("UNABLE_TO_VERIFY", status_text="unable to verify", status_code=-2301, reason="NO_VALID_CODE")


def test_no_valid_code_rejects_and_allows_resubmission():
    rid = _mk()
    comp = verification.complete(verification.claim_next("w"), _no_code())
    r = _rec(rid)
    assert (comp.kind, comp.reason, comp.resubmit_allowed) == ("FINAL", "NO_VALID_CODE", True)
    assert (r["verification_status"], r["final_status"], r["resubmit_allowed"]) == ("UNABLE_TO_VERIFY", "REJECTED", True)
    assert _job_status(rid)["status"] == "DONE"


def test_no_valid_code_on_last_attempt_disables_resubmission():
    rid = _mk()
    with get_conn() as conn:
        rec = conn.execute("SELECT user_id, attendance_date FROM attendance_records WHERE id = %s", (rid,)).fetchone()
        for i in range(2):  # 2 lần gửi trước đó đã bị thay thế => đây là lần thứ 3 (cuối)
            conn.execute(
                "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, telegram_message_id, "
                "photo_id, photo_received_at, superseded_at, final_status) VALUES (%s, %s, 'MORNING', -1, %s, 'x', now(), now(), 'REJECTED')",
                (rec["user_id"], rec["attendance_date"], 900000 + rid * 10 + i))
    comp = verification.complete(verification.claim_next("w"), _no_code())
    assert comp.resubmit_allowed is False
    r = _rec(rid)
    assert r["final_status"] == "REJECTED" and r["resubmit_allowed"] is False


# ------------------------------------------------------------------ ảnh xác minh nhưng sai ngày -> yêu cầu gửi lại
def test_date_mismatch_asks_to_resend():
    rid = _mk()
    comp = _complete_authentic(capture_time=_cap(23, 50, day_offset=-1))
    r = _rec(rid)
    assert (comp.reason, comp.resubmit_allowed) == ("DATE_MISMATCH", True)
    assert comp.capture_date == _last["d"] - timedelta(days=1) and comp.expected_date == _last["d"]
    assert (r["resubmit_allowed"], r["final_status"], r["flags"]) == (True, "PENDING_REVIEW", ["CAPTURE_DATE_MISMATCH"])
    assert r["verification_status"] == "VERIFIED_AUTHENTIC"


def test_date_mismatch_resubmission_capped_and_admin_resolve_closes_it():
    rid = _mk()
    with get_conn() as conn:
        rec = conn.execute("SELECT user_id, attendance_date FROM attendance_records WHERE id = %s", (rid,)).fetchone()
        for i in range(2):
            conn.execute(
                "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, telegram_message_id, "
                "photo_id, photo_received_at, superseded_at, final_status) VALUES (%s, %s, 'MORNING', -1, %s, 'x', now(), now(), 'REJECTED')",
                (rec["user_id"], rec["attendance_date"], 800000 + rid * 10 + i))
    comp = _complete_authentic(capture_time=_cap(9, 0, day_offset=-2))
    assert comp.reason == "DATE_MISMATCH" and comp.resubmit_allowed is False and _rec(rid)["resubmit_allowed"] is False

    rid2 = _mk()
    _complete_authentic(capture_time=_cap(23, 0, day_offset=-1))
    assert _rec(rid2)["resubmit_allowed"] is True
    assert review.resolve(ADMIN, rid2, "VALID", "đồng hồ điện thoại bị lệch, đã xác nhận") == "OK"
    assert _rec(rid2)["resubmit_allowed"] is False  # admin đã chốt: không còn gửi lại để thay thế


def test_admin_rejection_disables_resubmission_at_three_total_attempts():
    rid = _mk()
    with get_conn() as conn:
        rec = conn.execute("SELECT user_id, attendance_date FROM attendance_records WHERE id = %s", (rid,)).fetchone()
        for i in range(2):
            conn.execute(
                "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, telegram_message_id, "
                "photo_id, photo_received_at, superseded_at, final_status) VALUES (%s, %s, 'MORNING', -1, %s, 'x', now(), now(), 'REJECTED')",
                (rec["user_id"], rec["attendance_date"], 700000 + rid * 10 + i))
    verification.complete(verification.claim_next("w"), VerificationOutcome("VERIFIED_NOT_FOUND", status_text="not found"))
    assert review.resolve(ADMIN, rid, "REJECTED", "không xác minh được") == "OK"
    assert _rec(rid)["resubmit_allowed"] is False
