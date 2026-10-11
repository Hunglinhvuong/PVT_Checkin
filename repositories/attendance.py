from __future__ import annotations

from datetime import date, datetime

from psycopg import Connection


def exists_for_session(conn: Connection, user_id: int, d: date, session: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM attendance_records WHERE user_id = %s AND attendance_date = %s AND session = %s "
        "AND superseded_at IS NULL",
        (user_id, d, session),
    ).fetchone() is not None


def insert_record(
    conn: Connection, *, user_id: int, d: date, session: str, chat_id: int, message_id: int,
    photo_id: str, received_at: datetime,
) -> dict | None:
    """ON CONFLICT DO NOTHING bao phủ cả (user, ngày, buổi) lẫn (chat, message). None nếu trùng."""
    return conn.execute(
        "INSERT INTO attendance_records (user_id, attendance_date, session, telegram_chat_id, "
        "telegram_message_id, photo_id, photo_received_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING RETURNING *",
        (user_id, d, session, chat_id, message_id, photo_id, received_at),
    ).fetchone()


def insert_photo(
    conn: Connection, *, record_id: int, sha256: str, temp_path: str, file_ext: str, size: int, source: str
) -> dict:
    return conn.execute(
        "INSERT INTO attendance_photos (attendance_record_id, sha256, temp_path, file_ext, file_size_bytes, source) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (record_id, sha256, temp_path, file_ext, size, source),
    ).fetchone()


def find_other_with_hash(conn: Connection, sha256: str, record_id: int) -> dict | None:
    return conn.execute(
        "SELECT attendance_record_id FROM attendance_photos WHERE sha256 = %s AND attendance_record_id <> %s LIMIT 1",
        (sha256, record_id),
    ).fetchone()


def add_flag(conn: Connection, record_id: int, flag: str) -> None:
    conn.execute(
        "UPDATE attendance_records SET flags = array_append(flags, %s), final_status = 'PENDING_REVIEW' WHERE id = %s",
        (flag, record_id),
    )


def create_verification_job(conn: Connection, record_id: int) -> None:
    conn.execute(
        "INSERT INTO verification_jobs (attendance_record_id) VALUES (%s) ON CONFLICT DO NOTHING", (record_id,)
    )


def get_photo_for_upload(conn: Connection, photo_id: int) -> dict | None:
    return conn.execute(
        "SELECT p.*, r.attendance_date, r.session, r.user_id FROM attendance_photos p "
        "JOIN attendance_records r ON r.id = p.attendance_record_id WHERE p.id = %s",
        (photo_id,),
    ).fetchone()


def mark_uploaded(conn: Connection, photo_id: int, public_id: str, url: str) -> None:
    conn.execute(
        "UPDATE attendance_photos SET cloudinary_public_id = %s, cloudinary_url = %s, uploaded_at = now(), "
        "last_upload_error = NULL WHERE id = %s",
        (public_id, url, photo_id),
    )


def mark_upload_failed(conn: Connection, photo_id: int, error: str) -> None:
    conn.execute(
        "UPDATE attendance_photos SET upload_attempts = upload_attempts + 1, last_upload_error = %s WHERE id = %s",
        (error[:500], photo_id),
    )


def list_pending_uploads(conn: Connection, limit: int, max_attempts: int) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM attendance_photos WHERE cloudinary_public_id IS NULL AND upload_attempts < %s "
        "ORDER BY id LIMIT %s",
        (max_attempts, limit),
    ).fetchall()
    return [r["id"] for r in rows]


def get_photo_with_status(conn: Connection, photo_id: int) -> dict | None:
    return conn.execute(
        "SELECT p.*, r.verification_status FROM attendance_photos p "
        "JOIN attendance_records r ON r.id = p.attendance_record_id WHERE p.id = %s",
        (photo_id,),
    ).fetchone()


def clear_temp_path(conn: Connection, photo_id: int) -> None:
    conn.execute("UPDATE attendance_photos SET temp_path = NULL WHERE id = %s", (photo_id,))


def set_temp_path(conn: Connection, photo_id: int, path: str) -> None:
    conn.execute("UPDATE attendance_photos SET temp_path = %s WHERE id = %s", (path, photo_id))


def get_active_for_session(conn: Connection, user_id: int, d: date, session: str) -> dict | None:
    return conn.execute(
        "SELECT id, resubmit_allowed FROM attendance_records "
        "WHERE user_id = %s AND attendance_date = %s AND session = %s AND superseded_at IS NULL",
        (user_id, d, session),
    ).fetchone()


def count_attempts(conn: Connection, user_id: int, d: date, session: str) -> int:
    """Số lần đã gửi ảnh trong ngày/buổi (gồm cả bản ghi đã bị thay thế)."""
    return conn.execute(
        "SELECT count(*) AS n FROM attendance_records WHERE user_id = %s AND attendance_date = %s AND session = %s",
        (user_id, d, session),
    ).fetchone()["n"]


def lock_record(conn: Connection, record_id: int) -> dict | None:
    return conn.execute("SELECT * FROM attendance_records WHERE id = %s FOR UPDATE", (record_id,)).fetchone()


def mark_superseded(conn: Connection, record_id: int) -> None:
    conn.execute(
        "UPDATE attendance_records SET superseded_at = now(), resubmit_allowed = FALSE, "
        "final_status = CASE WHEN final_status = 'PENDING_REVIEW' THEN 'REJECTED' ELSE final_status END WHERE id = %s",
        (record_id,),
    )


def link_superseded(conn: Connection, old_id: int, new_id: int) -> None:
    conn.execute("UPDATE attendance_records SET superseded_by = %s WHERE id = %s", (new_id, old_id))
