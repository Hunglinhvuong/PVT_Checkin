"""Xử lý ngoại lệ của admin: danh sách chờ kiểm tra, quyết định thủ công, xác minh lại."""
from __future__ import annotations

from database.connection import get_conn
from repositories import admins, audit

DECISIONS = {"VALID": "ON_TIME", "LATE": "LATE", "REJECTED": None}  # decision -> punctuality_status (None: giữ nguyên)


def _admin(conn, tg_id: int) -> dict | None:
    a = admins.get_active(conn, tg_id)
    return a if a and a["role"] in ("SUPER_ADMIN", "ADMIN") else None


def list_pending_review(admin_telegram_id: int, limit: int = 15) -> list[dict] | None:
    with get_conn() as conn:
        if not _admin(conn, admin_telegram_id):
            return None
        return conn.execute(
            "SELECT r.id, r.attendance_date, r.session, r.verification_status, r.flags, u.full_name, "
            "(SELECT v.error_message FROM verification_logs v WHERE v.attendance_record_id = r.id "
            " AND v.error_message IS NOT NULL ORDER BY v.id DESC LIMIT 1) AS last_error "
            "FROM attendance_records r JOIN users u ON u.id = r.user_id "
            "WHERE r.final_status = 'PENDING_REVIEW' ORDER BY r.created_at LIMIT %s",
            (limit,),
        ).fetchall()


def resolve(admin_telegram_id: int, record_id: int, decision: str, reason: str) -> str:
    """NOT_ADMIN | BAD_INPUT | NOT_FOUND | NOT_PENDING | OK. Bắt buộc có lý do; chỉ áp dụng cho bản ghi PENDING_REVIEW."""
    decision = decision.upper()
    reason = (reason or "").strip()
    with get_conn() as conn:
        admin = _admin(conn, admin_telegram_id)
        if not admin:
            return "NOT_ADMIN"
        if decision not in DECISIONS or len(reason) < 3:
            return "BAD_INPUT"
        rec = conn.execute("SELECT * FROM attendance_records WHERE id = %s FOR UPDATE", (record_id,)).fetchone()
        if rec is None:
            return "NOT_FOUND"
        if rec["final_status"] != "PENDING_REVIEW":
            return "NOT_PENDING"
        punct = DECISIONS[decision] or rec["punctuality_status"]
        conn.execute(
            "UPDATE attendance_records SET final_status = %s, punctuality_status = %s, resubmit_allowed = FALSE, "
            "processed_at = now() WHERE id = %s",
            (decision, punct, record_id),
        )
        conn.execute(
            "INSERT INTO verification_logs (attendance_record_id, provider, status, result_text, finished_at, "
            "reviewed_by, reviewed_at) VALUES (%s, 'MANUAL_ADMIN', %s, %s, now(), %s, now())",
            (record_id, f"MANUAL_{decision}", reason, admin["id"]),
        )
        audit.add(
            conn, action="ATTENDANCE_MANUAL_RESOLVE", entity_type="attendance_records", entity_id=record_id,
            actor_telegram_id=admin_telegram_id, actor_type="ADMIN",
            old_value={"final_status": rec["final_status"], "punctuality_status": rec["punctuality_status"]},
            new_value={"final_status": decision, "punctuality_status": punct}, reason=reason,
        )
        return "OK"


def reverify(admin_telegram_id: int, record_id: int, reason: str) -> str:
    """NOT_ADMIN | BAD_INPUT | NOT_FOUND | NOT_ELIGIBLE | ALREADY_QUEUED | OK"""
    reason = (reason or "").strip()
    with get_conn() as conn:
        admin = _admin(conn, admin_telegram_id)
        if not admin:
            return "NOT_ADMIN"
        if len(reason) < 3:
            return "BAD_INPUT"
        rec = conn.execute("SELECT * FROM attendance_records WHERE id = %s FOR UPDATE", (record_id,)).fetchone()
        if rec is None:
            return "NOT_FOUND"
        if rec["final_status"] != "PENDING_REVIEW" or rec["verification_status"] == "PROCESSING":
            return "NOT_ELIGIBLE"
        job = conn.execute(
            "INSERT INTO verification_jobs (attendance_record_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id",
            (record_id,),
        ).fetchone()
        if job is None:
            return "ALREADY_QUEUED"
        conn.execute("UPDATE attendance_records SET verification_status = 'PENDING' WHERE id = %s", (record_id,))
        audit.add(
            conn, action="VERIFICATION_REQUEUED", entity_type="attendance_records", entity_id=record_id,
            actor_telegram_id=admin_telegram_id, actor_type="ADMIN",
            old_value={"verification_status": rec["verification_status"]}, reason=reason,
        )
        return "OK"
