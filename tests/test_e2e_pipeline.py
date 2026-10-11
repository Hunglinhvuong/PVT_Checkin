"""Luồng đầu-cuối (DB thật, Telegram/Cloudinary/Timemark giả): nhận ảnh -> upload -> xác minh -> đánh giá -> dọn file tạm."""
import dataclasses
import os
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from config.settings import load_settings
from database.connection import close_pool, get_conn, init_pool
from database.migrate import migrate
from services import attendance as att
from services import photo_storage, verification
from services.notifier import message_for
from services.timemark_verifier import VerificationOutcome
from workers.verification_worker import process_job

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")
TZ = "Asia/Ho_Chi_Minh"
VN = timezone(timedelta(hours=7))
_n = count(0)
_msg = count(1)


@pytest.fixture(scope="module", autouse=True)
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        aid = c.execute("INSERT INTO admins (telegram_user_id, role) VALUES (1, 'ADMIN') RETURNING id").fetchone()[0]
        c.execute("INSERT INTO users (telegram_user_id, full_name, status, approved_at, approved_by) VALUES (101, 'NV E2E', 'APPROVED', now(), %s)", (aid,))
    init_pool(dataclasses.replace(load_settings(), database_url=URL))
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean_queue():
    with get_conn() as conn:
        conn.execute("DELETE FROM verification_jobs")


def _workday():
    n = next(_n)
    return datetime(2026, 11, 16, tzinfo=timezone.utc) + timedelta(days=(n // 5) * 7 + n % 5)  # chỉ thứ Hai-Sáu


def _send(tmp_path, day, hour=7, minute=30, sha=None):
    """Nhân viên gửi ảnh lúc hour:minute giờ VN; trả về (kết quả tiếp nhận, đường dẫn file tạm)."""
    mid = next(_msg)
    f = tmp_path / f"p{mid}.jpg"
    f.write_bytes(b"\xff\xd8\xff" + b"0" * 10)
    at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=VN)
    res = att.record_intake(att.PhotoIntake(101, -100, mid, f"u{mid}", at, sha or f"{mid:064x}", str(f), "jpg", 13, "PHOTO"), TZ)
    return res, f, at


class Fake:
    def __init__(self, outcome):
        self.outcome = outcome

    def verify(self, path, *, tag, debug_dir=None):
        return self.outcome


def _verify(res, outcome, tmp_path):
    job = verification.claim_next("e2e")
    assert job.record_id == res.record["id"]
    out = process_job(job, Fake(outcome), SimpleNamespace(upload_tmp_dir=tmp_path))
    return job, verification.complete(job, out)


def _authentic(at, **kw):
    return VerificationOutcome("VERIFIED_AUTHENTIC", status_text="authentic", capture_time=at - timedelta(seconds=30),
                               photo_code="YX3BEH29N42MKA", gps_state="NONE", fields={"GPS": "No Data"}, **kw)


def _rec(rid):
    with get_conn() as conn:
        return conn.execute("SELECT r.*, p.temp_path FROM attendance_records r JOIN attendance_photos p ON p.attendance_record_id = r.id WHERE r.id = %s", (rid,)).fetchone()


def test_on_time_photo_end_to_end_and_temp_file_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(photo_storage, "_upload", lambda path, pid: {"public_id": pid, "secure_url": "https://x/" + pid})
    res, f, at = _send(tmp_path, _workday(), 7, 30)
    assert res.kind == "OK" and photo_storage.upload_photo(res.photo["id"]) is True
    assert f.exists()  # xác minh chưa xong: giữ file tạm
    job, comp = _verify(res, _authentic(at), tmp_path)
    assert (comp.final_status, comp.punctuality) == ("VALID", "ON_TIME")
    assert photo_storage.cleanup_temp_if_done(res.photo["id"]) is True
    assert not f.exists() and _rec(res.record["id"])["temp_path"] is None
    assert message_for(comp.verification_status, comp.final_status, comp.reason, comp.deadline).startswith("✅ Điểm danh hợp lệ")


def test_late_photo_is_marked_late_with_deadline_in_message(monkeypatch, tmp_path):
    res, f, at = _send(tmp_path, _workday(), 8, 5)
    _, comp = _verify(res, _authentic(at), tmp_path)
    assert (comp.final_status, comp.punctuality) == ("LATE", "LATE")
    assert "07:50" in message_for(comp.verification_status, comp.final_status, comp.reason, comp.deadline)


def test_edited_photo_rejected_and_cannot_be_replaced(tmp_path):
    day = _workday()
    res, f, at = _send(tmp_path, day, 7, 20)
    out = VerificationOutcome("VERIFIED_FAKE", status_text="unable to verify", status_code=-2300, reason="EDITED")
    _, comp = _verify(res, out, tmp_path)
    assert comp.final_status == "REJECTED" and "chỉnh sửa" in message_for(comp.verification_status, comp.final_status, comp.reason)
    again, _, _ = _send(tmp_path, day, 7, 40)
    assert again.kind == "DUPLICATE"  # ảnh bị chỉnh sửa không được thay bằng ảnh khác


def test_no_code_then_resend_succeeds_keeping_history(tmp_path):
    day = _workday()
    first, _, at1 = _send(tmp_path, day, 7, 10)
    out = VerificationOutcome("UNABLE_TO_VERIFY", status_text="unable to verify", status_code=-2301, reason="NO_VALID_CODE")
    _, comp = _verify(first, out, tmp_path)
    assert message_for(comp.verification_status, comp.final_status, comp.reason, None, comp.resubmit_allowed) == "❌ Ảnh không có code hợp lệ, cần gửi ảnh khác."

    second, _, at2 = _send(tmp_path, day, 7, 25)
    assert second.kind == "OK" and second.superseded_id == first.record["id"]
    _, comp2 = _verify(second, _authentic(at2), tmp_path)
    assert comp2.final_status == "VALID"
    assert _rec(first.record["id"])["superseded_by"] == second.record["id"]


def test_timemark_not_ready_retries_then_succeeds(tmp_path):
    res, f, at = _send(tmp_path, _workday(), 7, 15)
    pending = VerificationOutcome("UNABLE_TO_VERIFY", status_text="unable to verify", status_code=-2307, reason="PENDING_UPLOAD")
    for i in range(2):
        _, comp = _verify(res, pending, tmp_path)
        assert comp.kind == "RETRY" and comp.first_retry == (i == 0)
        with get_conn() as conn:
            conn.execute("UPDATE verification_jobs SET run_after = now()")
    _, comp = _verify(res, _authentic(at), tmp_path)
    assert comp.kind == "FINAL" and comp.final_status == "VALID"
    assert f.exists()  # chưa lên Cloudinary nên file tạm vẫn được giữ
