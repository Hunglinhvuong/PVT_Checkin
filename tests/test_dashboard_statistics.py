from datetime import date, datetime, timezone

from dashboard import data


def test_attendance_statistics_respects_approval_dates_and_workday_overrides(monkeypatch):
    users = [
        {"id": 1, "full_name": "An", "approved_at": datetime(2026, 10, 1, tzinfo=timezone.utc)},
        {"id": 2, "full_name": "Bình", "approved_at": datetime(2026, 10, 11, 17, tzinfo=timezone.utc)},
    ]
    overrides = [
        {"holiday_date": date(2026, 10, 10), "is_working_day": True},
        {"holiday_date": date(2026, 10, 11), "is_working_day": False},
    ]

    def read(sql, params=()):
        if "FROM attendance_records" in sql:
            return [{"user_id": 1, "sessions": 2, "on_time": 1, "late": 1}]
        if "FROM holiday_calendar" in sql:
            return overrides
        return users

    monkeypatch.setattr(data, "_read", read)

    stats = data.attendance_statistics(date(2026, 10, 9), date(2026, 10, 12), "Asia/Ho_Chi_Minh")

    assert stats == [
        {
            "full_name": "An",
            "working_days": 3,
            "work_sessions": 6,
            "on_time": 1,
            "punctuality_rate": 0.5,
            "late": 1,
            "missing": 4,
        },
        {
            "full_name": "Bình",
            "working_days": 1,
            "work_sessions": 2,
            "on_time": 0,
            "punctuality_rate": None,
            "late": 0,
            "missing": 2,
        },
    ]
