"""Truy vấn CHỈ ĐỌC cho dashboard (mọi truy vấn chạy trong transaction READ ONLY, tham số hóa). Không phụ thuộc Streamlit."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dashboard.util import like_pattern
from database.connection import get_conn
from services.workcalendar import is_working_day

SESSIONS = ("MORNING", "AFTERNOON")


def _read(sql: str, params: tuple | list = ()) -> list[dict]:
    with get_conn() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        return conn.execute(sql, params).fetchall()


def _day_bounds(d1: date, d2: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    return datetime.combine(d1, time.min, tzinfo=z), datetime.combine(d2 + timedelta(days=1), time.min, tzinfo=z)


def local_today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


# ---------------------------------------------------------------- lịch & tổng quan
def working_day_info(d: date) -> tuple[bool, str | None]:
    rows = _read("SELECT * FROM holiday_calendar WHERE holiday_date = %s", (d,))
    override = rows[0] if rows else None
    return is_working_day(d, override), (override["name"] if override else None)


def missing_users(d: date, session: str, tz: str) -> list[dict]:
    """Nhân viên đã duyệt (trước hết ngày d) chưa có bản ghi trong ngày/buổi."""
    _, end = _day_bounds(d, d, tz)
    return _read(
        "SELECT u.id, u.full_name, u.telegram_user_id FROM users u WHERE u.status = 'APPROVED' "
        "AND (u.approved_at IS NULL OR u.approved_at < %s) "
        "AND NOT EXISTS (SELECT 1 FROM attendance_records r WHERE r.user_id = u.id "
        "AND r.attendance_date = %s AND r.session = %s AND r.superseded_at IS NULL) ORDER BY u.full_name",
        (end, d, session),
    )


def overview(d: date, tz: str) -> dict:
    users = {r["status"]: r["n"] for r in _read("SELECT status, count(*) AS n FROM users GROUP BY status")}
    sessions: dict[str, dict[str, int]] = {s: {} for s in SESSIONS}
    for r in _read(
        "SELECT session, final_status, count(*) AS n FROM attendance_records WHERE attendance_date = %s AND superseded_at IS NULL "
        "GROUP BY session, final_status", (d,),
    ):
        sessions[r["session"]][r["final_status"]] = r["n"]
    working, _ = working_day_info(d)
    return {
        "users": users,
        "sessions": sessions,
        "working": working,
        "missing": {s: (len(missing_users(d, s, tz)) if working else 0) for s in SESSIONS},
        "pending_review": _read("SELECT count(*) AS n FROM attendance_records WHERE final_status = 'PENDING_REVIEW'")[0]["n"],
        "pending_verification": _read(
            "SELECT count(*) AS n FROM attendance_records WHERE verification_status IN ('PENDING', 'PROCESSING')")[0]["n"],
        "jobs": {r["status"]: r["n"] for r in _read(
            "SELECT status, count(*) AS n FROM verification_jobs WHERE status IN ('QUEUED', 'RUNNING') GROUP BY status")},
        "pending_upload": _read(
            "SELECT count(*) AS n FROM attendance_photos "
            "WHERE storage_backend = 'cloudinary' AND cloudinary_public_id IS NULL")[0]["n"],
    }


def daily_series(end: date, days: int = 14) -> list[dict]:
    return _read(
        "SELECT attendance_date, final_status, count(*) AS n FROM attendance_records "
        "WHERE attendance_date BETWEEN %s AND %s AND superseded_at IS NULL GROUP BY attendance_date, final_status ORDER BY attendance_date",
        (end - timedelta(days=days - 1), end),
    )


# ---------------------------------------------------------------- điểm danh
def users_basic() -> list[dict]:
    return _read("SELECT id, full_name, status, telegram_user_id FROM users ORDER BY full_name")


def attendance_rows(
    d1: date, d2: date, sessions: tuple = (), user_ids: tuple = (), finals: tuple = (), limit: int = 2000
) -> list[dict]:
    where, params = ["r.attendance_date BETWEEN %s AND %s", "r.superseded_at IS NULL"], [d1, d2]
    if sessions:
        where.append("r.session = ANY(%s)")
        params.append(list(sessions))
    if user_ids:
        where.append("r.user_id = ANY(%s)")
        params.append(list(user_ids))
    if finals:
        where.append("r.final_status = ANY(%s)")
        params.append(list(finals))
    params.append(limit)
    return _read(
        "SELECT r.id, r.attendance_date, r.session, u.full_name, r.photo_received_at, r.photo_taken_at, "
        "r.distance_km, r.verification_status, r.punctuality_status, r.final_status, r.flags "
        "FROM attendance_records r JOIN users u ON u.id = r.user_id WHERE " + " AND ".join(where) +
        " ORDER BY r.attendance_date DESC, r.session DESC, u.full_name LIMIT %s",
        params,
    )


# ---------------------------------------------------------------- xác minh ảnh
def review_queue(limit: int = 300) -> list[dict]:
    return _read(
        "SELECT r.id, r.attendance_date, r.session, u.full_name, r.verification_status, r.final_status, r.flags, "
        "r.created_at FROM attendance_records r JOIN users u ON u.id = r.user_id "
        "WHERE r.final_status = 'PENDING_REVIEW' OR r.verification_status IN ('PENDING', 'PROCESSING') "
        "ORDER BY r.created_at DESC LIMIT %s", (limit,),
    )


_DETAIL_SQL = (
    "SELECT r.*, u.full_name, u.telegram_user_id, p.id AS pid, p.cloudinary_public_id, p.cloudinary_url, p.sha256, "
    "p.photo_code, p.latitude, p.longitude, p.gps_raw, p.device_model, p.temp_path, p.upload_attempts, "
    "p.last_upload_error, p.file_ext, p.source, p.storage_backend "
    "FROM attendance_records r JOIN users u ON u.id = r.user_id "
    "LEFT JOIN attendance_photos p ON p.attendance_record_id = r.id WHERE r.id = %s"
)


def record_detail(record_id: int) -> dict | None:
    rows = _read(_DETAIL_SQL, (record_id,))
    return rows[0] if rows else None


def verification_logs(record_id: int) -> list[dict]:
    return _read(
        "SELECT id, provider, status, started_at, finished_at, error_message, screenshot_reference, result_text, "
        "reviewed_by, reviewed_at, parsed FROM verification_logs WHERE attendance_record_id = %s ORDER BY id DESC",
        (record_id,),
    )


def job_info(record_id: int) -> list[dict]:
    return _read(
        "SELECT status, attempts, max_attempts, pending_retries, run_after, last_error FROM verification_jobs "
        "WHERE attendance_record_id = %s ORDER BY id DESC LIMIT 3", (record_id,),
    )


# ---------------------------------------------------------------- nhân viên, lịch, văn phòng
def users_list(statuses: tuple = ()) -> list[dict]:
    where, params = "", []
    if statuses:
        where, params = "WHERE u.status = ANY(%s)", [list(statuses)]
    return _read(
        "SELECT u.id, u.telegram_user_id, u.full_name, u.status, u.created_at, u.approved_at, "
        "a.telegram_user_id AS approved_by_tg, u.registered_chat_id, "
        "(SELECT count(*) FROM attendance_records r WHERE r.user_id = u.id) AS n_records, "
        "(SELECT max(attendance_date) FROM attendance_records r WHERE r.user_id = u.id) AS last_date "
        "FROM users u LEFT JOIN admins a ON a.id = u.approved_by " + where +
        " ORDER BY CASE u.status WHEN 'PENDING' THEN 0 ELSE 1 END, u.full_name", params,
    )


def holidays_between(d1: date, d2: date) -> list[dict]:
    return _read("SELECT * FROM holiday_calendar WHERE holiday_date BETWEEN %s AND %s ORDER BY holiday_date", (d1, d2))


def active_office() -> dict | None:
    rows = _read("SELECT * FROM office_locations WHERE is_active LIMIT 1")
    return rows[0] if rows else None


def active_admins() -> list[dict]:
    return _read("SELECT id, telegram_user_id, role FROM admins WHERE status = 'ACTIVE' AND role IN ('SUPER_ADMIN', 'ADMIN') ORDER BY id")


# ---------------------------------------------------------------- tra cứu ảnh
def photo_search(
    d1: date, d2: date, session: str | None = None, user_ids: tuple = (), text: str = "", limit: int = 24
) -> list[dict]:
    where, params = ["r.attendance_date BETWEEN %s AND %s"], [d1, d2]
    if session:
        where.append("r.session = %s")
        params.append(session)
    if user_ids:
        where.append("r.user_id = ANY(%s)")
        params.append(list(user_ids))
    text = (text or "").strip()
    if text:
        where.append("(p.photo_code ILIKE %s OR p.sha256 ILIKE %s)")
        params += [like_pattern(text), like_pattern(text)]
    params.append(limit)
    return _read(
        "SELECT r.id AS record_id, r.attendance_date, r.session, r.final_status, r.photo_taken_at, u.full_name, "
        "p.cloudinary_public_id, p.photo_code, p.sha256, p.gps_raw, p.device_model, p.temp_path "
        "FROM attendance_photos p JOIN attendance_records r ON r.id = p.attendance_record_id "
        "JOIN users u ON u.id = r.user_id WHERE " + " AND ".join(where) +
        " ORDER BY r.attendance_date DESC, r.id DESC LIMIT %s", params,
    )


# ---------------------------------------------------------------- nhật ký & bất thường
def audit_actions() -> list[str]:
    return [r["action"] for r in _read("SELECT DISTINCT action FROM audit_logs ORDER BY action")]


def audit_rows(
    d1: date, d2: date, tz: str, actions: tuple = (), entity_type: str | None = None, text: str = "", limit: int = 500
) -> list[dict]:
    start, end = _day_bounds(d1, d2, tz)
    where, params = ["created_at >= %s AND created_at < %s"], [start, end]
    if actions:
        where.append("action = ANY(%s)")
        params.append(list(actions))
    if entity_type:
        where.append("entity_type = %s")
        params.append(entity_type)
    text = (text or "").strip()
    if text:
        where.append("(reason ILIKE %s OR action ILIKE %s)")
        params += [like_pattern(text), like_pattern(text)]
    params.append(limit)
    return _read(
        "SELECT id, created_at, actor_type, actor_telegram_id, action, entity_type, entity_id, old_value, new_value, reason "
        "FROM audit_logs WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT %s", params,
    )


def audit_entity_types() -> list[str]:
    return [r["entity_type"] for r in _read("SELECT DISTINCT entity_type FROM audit_logs ORDER BY entity_type")]


def anomalies() -> dict[str, list[dict]]:
    return {
        "flagged": _read(
            "SELECT r.id, r.attendance_date, r.session, u.full_name, r.verification_status, r.final_status, r.flags "
            "FROM attendance_records r JOIN users u ON u.id = r.user_id "
            "WHERE cardinality(r.flags) > 0 OR r.final_status = 'PENDING_REVIEW' ORDER BY r.id DESC LIMIT 300"),
        "jobs": _read(
            "SELECT id, attendance_record_id, status, attempts, pending_retries, run_after, locked_at, last_error "
            "FROM verification_jobs WHERE status = 'FAILED' "
            "OR (status = 'RUNNING' AND locked_at < now() - interval '10 minutes') "
            "OR (status = 'QUEUED' AND run_after < now() - interval '30 minutes') ORDER BY id DESC LIMIT 200"),
        "uploads": _read(
            "SELECT id, attendance_record_id, upload_attempts, last_upload_error FROM attendance_photos "
            "WHERE storage_backend = 'cloudinary' AND cloudinary_public_id IS NULL "
            "AND upload_attempts > 0 ORDER BY id DESC LIMIT 200"),
        "duplicate_hashes": _read(
            "SELECT sha256, count(*) AS n, array_agg(attendance_record_id ORDER BY attendance_record_id) AS record_ids "
            "FROM attendance_photos GROUP BY sha256 HAVING count(*) > 1 ORDER BY max(id) DESC LIMIT 200"),
    }
