"""Quy tắc đúng giờ (spec mục 4). Hàm thuần, không đụng DB.

- Đánh giá theo THỜI GIAN CHỤP đã xác minh từ Timemark; thời điểm nhận ở Telegram chỉ dùng để xác định ngày/buổi.
- Mốc nghiêm ngặt: đúng 07:50:00 / 10:00:00 / 11:30:00 / 13:50:00 là muộn.
- Bất thường (lệch ngày, lệch buổi, chụp sau lúc nhận, thiếu thời gian/tọa độ không đọc được...) => kiểm tra thủ công,
  không tự gán ảnh của ngày khác sang hôm nay.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from services.geo import haversine_km
from services.workcalendar import AFTERNOON, MORNING, session_for, to_local

DEADLINE_MORNING_NEAR = time(7, 50)  # không có tọa độ hoặc ≤ 10 km
DEADLINE_MORNING_MID = time(10, 0)  # > 10 km và ≤ 40 km
DEADLINE_MORNING_FAR = time(11, 30)  # > 40 km
DEADLINE_AFTERNOON = time(13, 50)  # không phụ thuộc khoảng cách
NEAR_KM = 10.0
MID_KM = 40.0
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)  # ảnh "chụp sau khi nhận" quá mức này là bất thường

# Cờ do bước đánh giá sinh ra (được tính lại mỗi lần xác minh); cờ khác (vd DUPLICATE_PHOTO_HASH) được giữ nguyên.
EVAL_FLAGS = frozenset({
    "NO_CAPTURE_TIME", "GPS_UNREADABLE", "CAPTURE_DATE_MISMATCH", "CAPTURE_SESSION_MISMATCH",
    "CAPTURE_AFTER_RECEIVED", "NO_OFFICE_CONFIG",
})


@dataclass(frozen=True)
class Evaluation:
    punctuality: str  # ON_TIME | LATE | MANUAL_REVIEW
    final_status: str  # VALID | LATE | PENDING_REVIEW
    distance_km: float | None
    deadline: time | None
    flags: tuple[str, ...]
    reason: str


def _valid_coords(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None and lon is not None
        and -90 <= lat <= 90 and -180 <= lon <= 180
        and not (lat == 0 and lon == 0)  # (0,0) là giá trị mặc định lỗi, không đủ tin cậy
    )


def _manual(reason: str, flags: list[str], distance: float | None) -> Evaluation:
    return Evaluation("MANUAL_REVIEW", "PENDING_REVIEW", distance, None, tuple(flags), reason)


def evaluate(
    *,
    session: str,
    attendance_date: date,
    received_at: datetime,
    capture_time: datetime | None,
    gps_state: str,
    latitude: float | None,
    longitude: float | None,
    office: tuple[float, float] | None,
    tz: str,
) -> Evaluation:
    coords_ok = gps_state == "OK" and _valid_coords(latitude, longitude)
    distance = None
    if coords_ok and office is not None:
        distance = round(haversine_km(office[0], office[1], latitude, longitude), 3)

    if capture_time is None:
        return _manual("Không đọc được thời gian chụp", ["NO_CAPTURE_TIME"], distance)

    local = to_local(capture_time, tz)
    anomalies: list[str] = []
    if local.date() != attendance_date:
        anomalies.append("CAPTURE_DATE_MISMATCH")
    elif session_for(local) != session:
        anomalies.append("CAPTURE_SESSION_MISMATCH")
    if capture_time > received_at + CLOCK_SKEW_TOLERANCE:
        anomalies.append("CAPTURE_AFTER_RECEIVED")
    if anomalies:
        return _manual(
            f"Thời gian chụp {local:%d/%m %H:%M:%S} lệch so với ngày/buổi tiếp nhận ({attendance_date:%d/%m}, {session})",
            anomalies, distance,
        )

    if session == AFTERNOON:
        deadline = DEADLINE_AFTERNOON
    elif session == MORNING:
        if gps_state == "NONE":
            deadline = DEADLINE_MORNING_NEAR
        else:  # có tọa độ (OK) hoặc có nhưng không đọc được (UNPARSEABLE/khác): không tự coi là "không có tọa độ"
            if not coords_ok:
                return _manual("Ảnh có tọa độ nhưng không đọc được/không đủ tin cậy", ["GPS_UNREADABLE"], None)
            if office is None:
                return _manual("Chưa cấu hình tọa độ văn phòng", ["NO_OFFICE_CONFIG"], None)
            if distance <= NEAR_KM:
                deadline = DEADLINE_MORNING_NEAR
            elif distance <= MID_KM:
                deadline = DEADLINE_MORNING_MID
            else:
                deadline = DEADLINE_MORNING_FAR
    else:
        raise ValueError(f"session không hợp lệ: {session}")

    if local.time() < deadline:  # nghiêm ngặt: đúng mốc là muộn
        return Evaluation("ON_TIME", "VALID", distance, deadline, (), f"Chụp {local:%H:%M:%S} < hạn {deadline:%H:%M}")
    return Evaluation("LATE", "LATE", distance, deadline, (), f"Chụp {local:%H:%M:%S} ≥ hạn {deadline:%H:%M}")
