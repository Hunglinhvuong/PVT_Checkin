"""Quy tắc ngày làm việc và xác định buổi. Hàm thuần, dễ test."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MORNING = "MORNING"
AFTERNOON = "AFTERNOON"
SESSION_LABEL = {MORNING: "buổi sáng", AFTERNOON: "buổi chiều"}


def to_local(dt: datetime, tz_name: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime phải có tzinfo")
    return dt.astimezone(ZoneInfo(tz_name))


def session_for(local_dt: datetime) -> str:
    """Trước 12:00 là sáng; từ 12:00:00 trở đi là chiều."""
    return MORNING if local_dt.hour < 12 else AFTERNOON


def is_working_day(d: date, override: dict | None) -> bool:
    """override là dòng holiday_calendar (nếu có): is_working_day=False là ngày nghỉ, True là ngày làm ngoại lệ."""
    if override is not None:
        return bool(override["is_working_day"])
    return d.weekday() < 5
