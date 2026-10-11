"""Hàng đợi xác minh (PostgreSQL, FOR UPDATE SKIP LOCKED) và ghi kết quả. Không phụ thuộc Playwright/Telegram."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, time

from database.connection import get_conn
from repositories import attendance as att_repo, audit, offices, verification as repo
from services import punctuality
from services.attendance import MAX_ATTEMPTS_PER_SESSION
from services.workcalendar import to_local
from services.timemark_verifier import VerificationOutcome

log = logging.getLogger(__name__)
STALE_MINUTES = 10
MAX_BACKOFF_MINUTES = 30
PENDING_UPLOAD_RETRY_MINUTES = 2  # Status -2307: thử lại mỗi 02 phút
PENDING_UPLOAD_MAX_RETRIES = 5  # tối đa 5 lần thử lại (tổng 6 lượt xác minh)
_PLACEHOLDERS = {"unknown", "--", "n/a", ""}


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    record_id: int
    attempts: int
    max_attempts: int
    log_id: int
    record: dict
    photo: dict
    pending_retries: int = 0


@dataclass(frozen=True)
class Completion:
    kind: str  # FINAL | RETRY
    verification_status: str
    final_status: str | None
    reason: str | None = None
    first_retry: bool = False  # lần đầu phải chờ Timemark nhận dữ liệu (để báo cho nhân viên một lần)
    deadline: time | None = None  # hạn điểm danh đã áp dụng (nếu đã đánh giá)
    punctuality: str | None = None
    resubmit_allowed: bool = False  # nhân viên được/còn lượt gửi lại ảnh (không có code hợp lệ, hoặc sai ngày chụp)
    capture_date: date | None = None  # chỉ có khi reason == DATE_MISMATCH: ngày chụp đọc được từ ảnh
    expected_date: date | None = None  # ngày điểm danh (theo thời điểm nhận ảnh)


def claim_next(worker_id: str) -> ClaimedJob | None:
    with get_conn() as conn:
        repo.requeue_stale(conn, STALE_MINUTES)
        while True:
            job = repo.lock_next_queued(conn)
            if job is None:
                return None
            if job["attempts"] >= job["max_attempts"]:  # đã tiêu hết lượt (vd worker crash lặp lại)
                _fail_exhausted(conn, job)
                continue
            repo.mark_running(conn, job["id"], worker_id)
            repo.set_record_verification(conn, job["attendance_record_id"], "PROCESSING")
            log_id = repo.insert_log(conn, job["attendance_record_id"], "PROCESSING")
            return ClaimedJob(
                job_id=job["id"],
                record_id=job["attendance_record_id"],
                attempts=job["attempts"] + 1,
                max_attempts=job["max_attempts"],
                log_id=log_id,
                record=repo.get_record(conn, job["attendance_record_id"]),
                photo=repo.get_photo_by_record(conn, job["attendance_record_id"]),
                pending_retries=job["pending_retries"],
            )


def _fail_exhausted(conn, job: dict) -> None:
    rid = job["attendance_record_id"]
    msg = "Vượt số lần thử xác minh (worker crash/treo nhiều lần)"
    record = repo.get_record(conn, rid, for_update=True)
    repo.finish_job(conn, job["id"], "FAILED", msg)
    log_id = repo.insert_log(conn, rid, "AUTOMATION_ERROR")
    repo.finish_log(conn, log_id, status="AUTOMATION_ERROR", result_text=None, screenshot=None, error=msg, parsed=None)
    final = "PENDING_REVIEW" if record["final_status"] in ("RECEIVED", "PENDING_REVIEW") else record["final_status"]
    repo.apply_final(conn, rid, verification_status="AUTOMATION_ERROR", final_status=final,
                     photo_taken_at=None, flags=list(record["flags"]))
    audit.add(conn, action="VERIFICATION_EXHAUSTED", entity_type="attendance_records", entity_id=rid,
              actor_type="SYSTEM", new_value={"verification_status": "AUTOMATION_ERROR"})


def _parsed_json(o: VerificationOutcome) -> dict:
    return {
        "status_text": o.status_text,
        "status_code": o.status_code,
        "reason": o.reason,
        "fields": o.fields,
        "gps_state": o.gps_state,
        "capture_time": o.capture_time.isoformat() if o.capture_time else None,
        "photo_code": o.photo_code,
    }


def _clean(value: str | None) -> str | None:
    return None if value is None or value.strip().lower() in _PLACEHOLDERS else value


def complete(job: ClaimedJob, outcome: VerificationOutcome, tz: str = "Asia/Ho_Chi_Minh") -> Completion:
    """Ghi kết quả một lượt xác minh và (nếu Authentic) đánh giá đúng giờ. Không sửa dữ liệu gốc (ảnh, thời điểm nhận)."""
    with get_conn() as conn:
        record = repo.get_record(conn, job.record_id, for_update=True)

        # Timemark chưa nhận dữ liệu ảnh (Status -2307): thử lại mỗi 10 phút, tối đa 5 lần, không tiêu hao attempts.
        if (outcome.status == "UNABLE_TO_VERIFY" and outcome.reason == "PENDING_UPLOAD"
                and job.pending_retries < PENDING_UPLOAD_MAX_RETRIES):
            n = job.pending_retries + 1
            note = f"Timemark chưa nhận dữ liệu ảnh (Status {outcome.status_code}); thử lại lần {n}/{PENDING_UPLOAD_MAX_RETRIES}"
            repo.finish_log(conn, job.log_id, status="UNABLE_TO_VERIFY", result_text=outcome.result_text,
                            screenshot=outcome.screenshot, error=note, parsed=_parsed_json(outcome))
            repo.requeue_job(conn, job.job_id, PENDING_UPLOAD_RETRY_MINUTES, note, pending_retry=True)
            repo.set_record_verification(conn, job.record_id, "PENDING")
            return Completion("RETRY", "PENDING", None, "PENDING_UPLOAD", first_retry=job.pending_retries == 0)

        if outcome.status == "AUTOMATION_ERROR" and job.attempts < job.max_attempts:
            repo.finish_log(conn, job.log_id, status="AUTOMATION_ERROR", result_text=outcome.result_text,
                            screenshot=outcome.screenshot, error=outcome.error, parsed=None)
            repo.requeue_job(conn, job.job_id, min(2 ** job.attempts, MAX_BACKOFF_MINUTES), outcome.error)
            repo.set_record_verification(conn, job.record_id, "PENDING")
            return Completion("RETRY", "PENDING", None, "AUTOMATION_ERROR")

        ev = None
        resubmit = None
        date_mismatch = False
        flags = list(record["flags"])
        if outcome.status == "VERIFIED_AUTHENTIC":
            office = offices.get_active(conn)
            ev = punctuality.evaluate(
                session=record["session"],
                attendance_date=record["attendance_date"],
                received_at=record["photo_received_at"],
                capture_time=outcome.capture_time,
                gps_state=outcome.gps_state,
                latitude=outcome.latitude,
                longitude=outcome.longitude,
                office=(office["latitude"], office["longitude"]) if office else None,
                tz=tz,
            )
            # Cờ đánh giá được tính lại mỗi lần; cờ từ tiếp nhận (vd ảnh trùng hash) giữ nguyên và chặn tự động duyệt.
            flags = [f for f in flags if f not in punctuality.EVAL_FLAGS]
            flags += [f for f in ev.flags if f not in flags]
            wanted = "PENDING_REVIEW" if flags else ev.final_status
            if "CAPTURE_DATE_MISMATCH" in ev.flags:  # đã xác minh Authentic nhưng ngày chụp sai: yêu cầu gửi lại
                date_mismatch = True
                resubmit = att_repo.count_attempts(
                    conn, record["user_id"], record["attendance_date"], record["session"]
                ) < MAX_ATTEMPTS_PER_SESSION
        elif outcome.status == "VERIFIED_FAKE":  # gồm cả ảnh bị chỉnh sửa (Status -2300)
            wanted = "REJECTED"
        elif outcome.status == "UNABLE_TO_VERIFY" and outcome.reason == "NO_VALID_CODE":  # Status -2301
            wanted = "REJECTED"  # không có code hợp lệ: nhân viên cần gửi ảnh khác
            resubmit = att_repo.count_attempts(
                conn, record["user_id"], record["attendance_date"], record["session"]
            ) < MAX_ATTEMPTS_PER_SESSION
        else:  # NOT_FOUND, UNABLE_TO_VERIFY (mã lạ/hết lượt chờ), AUTOMATION_ERROR hết lượt, MANUAL_REVIEW
            wanted = "PENDING_REVIEW"

        # Không đè quyết định đã chốt (VALID/LATE/REJECTED do admin hoặc lần xử lý trước)
        can_override = record["final_status"] in ("RECEIVED", "PENDING_REVIEW")
        final = wanted if can_override else record["final_status"]

        repo.apply_final(
            conn, job.record_id, verification_status=outcome.status, final_status=final,
            photo_taken_at=outcome.capture_time if outcome.status == "VERIFIED_AUTHENTIC" else None,
            flags=flags,
            punctuality_status=ev.punctuality if (ev and can_override) else None,
            distance_km=ev.distance_km if (ev and can_override) else None,
            resubmit_allowed=resubmit if (resubmit is not None and can_override) else None,
        )
        if job.photo:
            repo.update_photo_details(
                conn, job.photo["id"], code=outcome.photo_code, lat=outcome.latitude, lon=outcome.longitude,
                gps_raw=_clean(outcome.fields.get("GPS")), device=_clean(outcome.fields.get("Device Model")),
            )
        repo.finish_log(conn, job.log_id, status=outcome.status, result_text=outcome.result_text,
                        screenshot=outcome.screenshot, error=outcome.error, parsed=_parsed_json(outcome))
        repo.finish_job(conn, job.job_id, "FAILED" if outcome.status == "AUTOMATION_ERROR" else "DONE", outcome.error)
        audit.add(
            conn, action="VERIFICATION_COMPLETED", entity_type="attendance_records", entity_id=job.record_id,
            actor_type="SYSTEM",
            old_value={"final_status": record["final_status"]},
            new_value={
                "verification_status": outcome.status, "final_status": final, "flags": flags,
                "reason": outcome.reason, "status_code": outcome.status_code,
                **({"resubmit_allowed": resubmit} if resubmit is not None else {}),
                **({"punctuality": ev.punctuality, "distance_km": ev.distance_km,
                    "deadline": ev.deadline.isoformat() if ev.deadline else None, "evaluation": ev.reason} if ev else {}),
            },
        )
        return Completion(
            "FINAL", outcome.status, final, "DATE_MISMATCH" if date_mismatch else outcome.reason,
            deadline=ev.deadline if ev else None, punctuality=ev.punctuality if ev else None,
            resubmit_allowed=bool(resubmit and can_override),
            capture_date=to_local(outcome.capture_time, tz).date() if (date_mismatch and outcome.capture_time) else None,
            expected_date=record["attendance_date"] if date_mismatch else None,
        )
