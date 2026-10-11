from datetime import date, datetime, timezone

import pytest

from services.photo_storage import detect_image_ext
from services.workcalendar import AFTERNOON, MORNING, is_working_day, session_for, to_local

TZ = "Asia/Ho_Chi_Minh"


def _sess(utc):
    return session_for(to_local(utc, TZ))


def test_session_boundary_is_noon_local():
    assert _sess(datetime(2026, 10, 12, 4, 59, 59, tzinfo=timezone.utc)) == MORNING  # 11:59:59 VN
    assert _sess(datetime(2026, 10, 12, 5, 0, 0, tzinfo=timezone.utc)) == AFTERNOON  # 12:00:00 VN


def test_local_date_rolls_over_vs_utc():
    # 18:00 UTC ngày 12 = 01:00 ngày 13 giờ VN
    assert to_local(datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc), TZ).date() == date(2026, 10, 13)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        to_local(datetime(2026, 10, 12, 8, 0), TZ)


def test_weekdays_and_overrides():
    assert is_working_day(date(2026, 10, 12), None)  # thứ Hai
    assert not is_working_day(date(2026, 10, 10), None)  # thứ Bảy
    assert not is_working_day(date(2026, 10, 11), None)  # Chủ nhật
    assert not is_working_day(date(2026, 10, 12), {"is_working_day": False})  # lễ
    assert is_working_day(date(2026, 10, 10), {"is_working_day": True})  # làm bù


@pytest.mark.parametrize(
    "data,ext",
    [
        (b"\xff\xd8\xff\xe0" + b"0" * 20, "jpg"),
        (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "png"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 8, "webp"),
        (b"\x00\x00\x00\x18ftypheic" + b"0" * 8, "heic"),
        (b"%PDF-1.7", None),
        (b"", None),
    ],
)
def test_detect_image_ext(data, ext):
    assert detect_image_ext(data) == ext
