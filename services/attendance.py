"""Tiếp nhận điểm danh: kiểm tra quyền/ngày/buổi, chống ghi trùng bằng transaction + unique constraint."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from database.connection import get_conn
from repositories import attendance, audit, holidays, users
from services.workcalendar import is_working_day, session_for, to_local

# 1 lần gửi + tối đa 2 lần gửi lại (chỉ khi ảnh trước bị từ chối vì không có code hợp lệ, Timemark Status -2301)
MAX_ATTEMPTS_PER_SESSION = 3


@dataclass(frozen=True)
class PhotoIntake:
    telegram_user_id: int
    chat_id: int
    message_id: int
    file_unique_id: str
    received_at: datetime  # msg.date (UTC) - thời điểm Telegram nhận ảnh
    sha256: str
    temp_path: str
    file_ext: str
    file_size: int
    source: str  # PHOTO | DOCUMENT


@dataclass(frozen=True)
class Eligibility:
    # NOT_REGISTERED | PENDING | REJECTED | DISABLED | NON_WORKING_DAY | DUPLICATE | ATTEMPTS_EXCEEDED | OK
    kind: str
    user: dict | None = None
    work_date: date | None = None
    session: str | None = None
    holiday_name: str | None = None
    supersedes: int | None = None  # id bản ghi bị từ chối (-2301) sẽ được thay thế bởi ảnh gửi lại


@dataclass(frozen=True)
class IntakeResult:
    kind: str  # như Eligibility
    user: dict | None = None
    work_date: date | None = None
    session: str | None = None
    holiday_name: str | None = None
    record: dict | None = None
    photo: dict | None = None
    flagged: bool = False
    superseded_id: int | None = None


class _Duplicate(Exception):
    pass


def _check(conn, telegram_user_id: int, received_at: datetime, tz: str) -> Eligibility:
    user = users.get_by_telegram_id(conn, telegram_user_id)
    if user is None:
        return Eligibility("NOT_REGISTERED")
    if user["status"] != "APPROVED":
        return Eligibility(user["status"], user)
    local = to_local(received_at, tz)
    d, session = local.date(), session_for(local)
    override = holidays.get(conn, d)
    if not is_working_day(d, override):
        return Eligibility("NON_WORKING_DAY", user, d, session, override["name"] if override else None)
    active = attendance.get_active_for_session(conn, user["id"], d, session)
    if active is None:
        return Eligibility("OK", user, d, session)
    if active["resubmit_allowed"]:
        if attendance.count_attempts(conn, user["id"], d, session) >= MAX_ATTEMPTS_PER_SESSION:
            return Eligibility("ATTEMPTS_EXCEEDED", user, d, session)
        return Eligibility("OK", user, d, session, supersedes=active["id"])
    return Eligibility("DUPLICATE", user, d, session)


def precheck(telegram_user_id: int, received_at: datetime, tz: str) -> Eligibility:
    """Kiểm tra rẻ trước khi tải ảnh."""
    with get_conn() as conn:
        return _check(conn, telegram_user_id, received_at, tz)


def record_intake(intake: PhotoIntake, tz: str) -> IntakeResult:
    """Một transaction: kiểm tra lại -> (đánh dấu bản ghi cũ bị thay thế nếu gửi lại) -> tạo bản ghi (unique chặn trùng)
    -> ảnh -> job xác minh -> audit. Bản ghi/ảnh/log cũ không bị xóa hay ghi đè."""
    with get_conn() as conn:
        el = _check(conn, intake.telegram_user_id, intake.received_at, tz)
        if el.kind != "OK":
            return IntakeResult(el.kind, el.user, el.work_date, el.session, el.holiday_name)

        try:
            with conn.transaction():  # savepoint: nếu thua cuộc đua thì hoàn tác cả việc đánh dấu thay thế
                if el.supersedes:
                    old = attendance.lock_record(conn, el.supersedes)
                    if old is None or old["superseded_at"] is not None or not old["resubmit_allowed"]:
                        raise _Duplicate
                    attendance.mark_superseded(conn, old["id"])  # phải trước khi chèn bản ghi mới (unique một phần)

                record = attendance.insert_record(
                    conn,
                    user_id=el.user["id"],
                    d=el.work_date,
                    session=el.session,
                    chat_id=intake.chat_id,
                    message_id=intake.message_id,
                    photo_id=intake.file_unique_id,
                    received_at=intake.received_at,
                )
                if record is None:  # thua cuộc đua với tin nhắn khác của cùng người/buổi (hoặc tin nhắn đã xử lý)
                    raise _Duplicate

                if el.supersedes:
                    attendance.link_superseded(conn, el.supersedes, record["id"])
                    audit.add(
                        conn, action="ATTENDANCE_SUPERSEDED", entity_type="attendance_records", entity_id=el.supersedes,
                        actor_telegram_id=intake.telegram_user_id, actor_type="USER",
                        new_value={"superseded_by": record["id"]}, reason="Gửi lại ảnh sau khi ảnh trước không có code hợp lệ",
                    )

                flagged = attendance.find_other_with_hash(conn, intake.sha256, record["id"]) is not None
                photo = attendance.insert_photo(
                    conn,
                    record_id=record["id"],
                    sha256=intake.sha256,
                    temp_path=intake.temp_path,
                    file_ext=intake.file_ext,
                    size=intake.file_size,
                    source=intake.source,
                )
                if flagged:
                    attendance.add_flag(conn, record["id"], "DUPLICATE_PHOTO_HASH")
                attendance.create_verification_job(conn, record["id"])
                audit.add(
                    conn,
                    action="ATTENDANCE_RECEIVED",
                    entity_type="attendance_records",
                    entity_id=record["id"],
                    actor_telegram_id=intake.telegram_user_id,
                    actor_type="USER",
                    new_value={
                        "date": el.work_date.isoformat(),
                        "session": el.session,
                        "sha256": intake.sha256,
                        "flags": ["DUPLICATE_PHOTO_HASH"] if flagged else [],
                        "resubmission": bool(el.supersedes),
                    },
                )
        except _Duplicate:
            return IntakeResult("DUPLICATE", el.user, el.work_date, el.session)
        return IntakeResult("OK", el.user, el.work_date, el.session, None, record, photo, flagged, el.supersedes)
