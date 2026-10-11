import math
from datetime import date, datetime, timedelta, timezone

import pytest

from services.geo import EARTH_RADIUS_KM, haversine_km
from services.punctuality import evaluate

TZ = "Asia/Ho_Chi_Minh"
VN = timezone(timedelta(hours=7))
OFFICE = (18.693679106009718, 105.69232416307415)
DAY = date(2026, 10, 12)


def north(km):
    return OFFICE[0] + math.degrees(km / EARTH_RADIUS_KM), OFFICE[1]


def cap(h, m, s=0, day=DAY, tz=VN):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=tz)


def ev(session, capture, *, gps="NONE", pos=None, office=OFFICE, received=None):
    lat, lon = pos if pos else (None, None)
    if received is None:
        received = (capture + timedelta(minutes=2)) if capture else cap(12, 0)
    return evaluate(session=session, attendance_date=DAY, received_at=received, capture_time=capture,
                    gps_state=gps, latitude=lat, longitude=lon, office=office, tz=TZ)


def test_haversine_sanity():
    assert haversine_km(*OFFICE, *north(10)) == pytest.approx(10, abs=1e-6)
    assert haversine_km(*OFFICE, *OFFICE) == 0


@pytest.mark.parametrize("h,m,s,expected", [(7, 49, 59, "ON_TIME"), (7, 50, 0, "LATE"), (7, 50, 1, "LATE"), (6, 0, 0, "ON_TIME")])
def test_morning_no_gps_deadline_0750_strict(h, m, s, expected):
    assert ev("MORNING", cap(h, m, s)).punctuality == expected


@pytest.mark.parametrize(
    "km,h,m,s,expected,deadline",
    [
        (0.25, 7, 49, 59, "ON_TIME", (7, 50)), (0.25, 7, 50, 0, "LATE", (7, 50)),
        (10.0, 7, 49, 59, "ON_TIME", (7, 50)),  # đúng 10 km vẫn thuộc nhóm ≤ 10 km
        (10.01, 7, 50, 0, "ON_TIME", (10, 0)), (20, 9, 59, 59, "ON_TIME", (10, 0)), (20, 10, 0, 0, "LATE", (10, 0)),
        (40.0, 9, 59, 59, "ON_TIME", (10, 0)),  # đúng 40 km vẫn thuộc nhóm ≤ 40 km
        (40.01, 10, 0, 0, "ON_TIME", (11, 30)), (50, 11, 29, 59, "ON_TIME", (11, 30)), (50, 11, 30, 0, "LATE", (11, 30)),
    ],
)
def test_morning_distance_tiers(km, h, m, s, expected, deadline):
    r = ev("MORNING", cap(h, m, s), gps="OK", pos=north(km))
    assert r.punctuality == expected and (r.deadline.hour, r.deadline.minute) == deadline
    assert r.distance_km == pytest.approx(km, abs=1e-3)
    assert r.final_status == ("VALID" if expected == "ON_TIME" else "LATE") and r.flags == ()


@pytest.mark.parametrize("km", [0.25, 20, 100])
@pytest.mark.parametrize("gps,h,m,s,expected", [("NONE", 13, 49, 59, "ON_TIME"), ("NONE", 13, 50, 0, "LATE")])
def test_afternoon_1350_regardless_of_distance(km, gps, h, m, s, expected):
    pos = north(km)
    assert ev("AFTERNOON", cap(h, m, s), gps="OK", pos=pos).punctuality == expected
    assert ev("AFTERNOON", cap(h, m, s), gps=gps).punctuality == expected


def test_afternoon_ignores_unreadable_gps():
    assert ev("AFTERNOON", cap(13, 0), gps="UNPARSEABLE").punctuality == "ON_TIME"


def test_timezone_conversion():
    # 08:00 GMT+8 = 07:00 giờ VN -> đúng giờ; 07:30 GMT+0 = 14:30 giờ VN -> lệch buổi sáng
    plus8 = timezone(timedelta(hours=8))
    assert ev("MORNING", cap(8, 0, tz=plus8)).punctuality == "ON_TIME"
    r = ev("MORNING", cap(7, 30, tz=timezone.utc), received=cap(7, 40))
    assert "CAPTURE_SESSION_MISMATCH" in r.flags and r.punctuality == "MANUAL_REVIEW"


def test_capture_on_other_day_goes_manual_never_reassigned():
    r = ev("MORNING", cap(23, 50, day=DAY - timedelta(days=1)), received=cap(7, 0))
    assert r.flags == ("CAPTURE_DATE_MISMATCH",) and r.final_status == "PENDING_REVIEW" and r.deadline is None


def test_capture_in_other_session_goes_manual():
    r = ev("AFTERNOON", cap(11, 59, 59), received=cap(12, 1))  # chụp lúc sáng, gửi lúc chiều
    assert r.flags == ("CAPTURE_SESSION_MISMATCH",) and r.punctuality == "MANUAL_REVIEW"


def test_capture_after_received_beyond_tolerance():
    assert "CAPTURE_AFTER_RECEIVED" in ev("MORNING", cap(7, 40), received=cap(7, 30)).flags
    assert ev("MORNING", cap(7, 40), received=cap(7, 36)).flags == ()  # lệch đồng hồ ≤ 5 phút


def test_missing_capture_time():
    r = ev("MORNING", None)
    assert r.flags == ("NO_CAPTURE_TIME",) and r.final_status == "PENDING_REVIEW"


@pytest.mark.parametrize("gps,pos", [("UNPARSEABLE", None), ("OK", (0.0, 0.0)), ("OK", (95.0, 10.0)), ("WEIRD", None)])
def test_morning_unreadable_coordinates_not_treated_as_no_coordinates(gps, pos):
    r = ev("MORNING", cap(7, 0), gps=gps, pos=pos)
    assert r.flags == ("GPS_UNREADABLE",) and r.punctuality == "MANUAL_REVIEW"


def test_morning_coordinates_without_office_config():
    r = ev("MORNING", cap(7, 0), gps="OK", pos=north(1), office=None)
    assert r.flags == ("NO_OFFICE_CONFIG",) and r.final_status == "PENDING_REVIEW"
    assert ev("MORNING", cap(7, 0), gps="NONE", office=None).punctuality == "ON_TIME"  # không tọa độ thì không cần văn phòng


def test_real_sample_photo_edited_sample_distance():
    # Tọa độ trong ảnh mẫu bị chỉnh sửa (18.691468,105.693124) cách văn phòng ~0.25 km
    assert haversine_km(*OFFICE, 18.691468, 105.693124) == pytest.approx(0.25, abs=0.02)
