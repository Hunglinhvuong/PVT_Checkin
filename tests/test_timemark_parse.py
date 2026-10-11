"""Test phân tích kết quả. SAMPLE_AUTHENTIC dựng theo ảnh chụp màn hình thật (ca Authentic).
Các mẫu Fake / Not Found / Unable là TỔNG HỢP theo tên trạng thái trong spec, chưa phải giao diện quan sát thật."""
from datetime import timedelta

import pytest

from services.timemark_verifier import UiChangedError, parse_capture_time, parse_gps, parse_result

HEADER = """verify-result
Upload multiple photos to verify their authenticity, metadata, and location using our AI analysis.
Click or drag images to upload
Support multiple image selection. PNG, JPG, GIF up to 10MB
Clear All
"""

SAMPLE_AUTHENTIC = HEADER + """REAL
Authentic
Time & Date
Location
Location permission was not granted
Image Analysis
Location
Unknown
GPS
No Data
Device Model
Redmi K60
Software Provider
Timemark
Capture Time
2026-10-10 08:11:53 GMT+7
Photo Code
YX3BEH29N42MKA
"""


def test_authentic_sample():
    r = parse_result(SAMPLE_AUTHENTIC)
    assert r.status == "VERIFIED_AUTHENTIC"
    assert r.fields["Location"] == "Unknown"  # lấy ô chi tiết, không phải câu ở ô tóm tắt
    assert r.fields["Device Model"] == "Redmi K60" and r.fields["Software Provider"] == "Timemark"
    assert r.photo_code == "YX3BEH29N42MKA"
    assert r.gps_state == "NONE" and r.latitude is None
    ct = r.capture_time
    assert (ct.year, ct.month, ct.day, ct.hour, ct.minute, ct.second) == (2026, 10, 10, 8, 11, 53)
    assert ct.utcoffset() == timedelta(hours=7)


def test_header_word_authenticity_is_not_status():
    assert parse_result(HEADER) is None  # có mốc nhưng chưa có kết quả


def test_missing_marker_is_ui_change():
    with pytest.raises(UiChangedError):
        parse_result("404 Not Found")  # trang lỗi của chính site không được hiểu thành VERIFIED_NOT_FOUND


def test_contradictory_statuses_not_guessed():
    with pytest.raises(UiChangedError):
        parse_result(HEADER + "Authentic\nFake\n")


@pytest.mark.parametrize(
    "word,status",
    [("Fake", "VERIFIED_FAKE"), ("Not Found", "VERIFIED_NOT_FOUND"), ("Unable to Verify", "UNABLE_TO_VERIFY")],
)
def test_other_statuses_synthetic(word, status):
    r = parse_result(HEADER + f"{word}\nPhoto Code\nABC123XYZ9\n")
    assert r.status == status and r.capture_time is None and r.photo_code == "ABC123XYZ9"


def test_label_followed_by_label_gives_none():
    r = parse_result(HEADER + "Authentic\nGPS\nCapture Time\n2026-10-10 08:11:53 GMT+7\n")
    assert r.fields["GPS"] is None and r.capture_time is not None


@pytest.mark.parametrize(
    "raw,state",
    [(None, "NONE"), ("No Data", "NONE"), ("Unknown", "NONE"), ("21.028511, 105.804817", "OK"),
     ("21.028511 105.804817", "OK"), ("21°01'42\"N 105°48'17\"E", "UNPARSEABLE"), ("999, 999", "UNPARSEABLE")],
)
def test_gps(raw, state):
    st, lat, lon = parse_gps(raw)
    assert st == state and ((lat is not None) == (state == "OK"))


def test_capture_time_variants():
    assert parse_capture_time("2026-10-10 08:11:53 GMT+05:30").utcoffset() == timedelta(hours=5, minutes=30)
    assert parse_capture_time("2026-10-10 08:11:53 GMT-8").utcoffset() == timedelta(hours=-8)
    assert parse_capture_time("2026-13-40 08:11:53 GMT+7") is None
    assert parse_capture_time("không phải thời gian") is None
    assert parse_capture_time(None) is None


# ---- Mẫu THẬT do người dùng thu thập bằng trang verify.timemark.com ----
from pathlib import Path  # noqa: E402

from services.notifier import PENDING_UPLOAD_NOTICE, message_for  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def test_real_pending_upload_2307():
    r = parse_result((FIX / "page_text_pending_2307.txt").read_text(encoding="utf-8"))
    assert (r.status, r.reason, r.status_code) == ("UNABLE_TO_VERIFY", "PENDING_UPLOAD", -2307)
    assert r.capture_time is None and r.photo_code is None and r.gps_state == "NONE"
    assert r.fields["Location"] == "Unknown"


def test_real_edited_photo_2300():
    r = parse_result((FIX / "page_text_edited_2300.txt").read_text(encoding="utf-8"))
    assert (r.status, r.reason, r.status_code) == ("VERIFIED_FAKE", "EDITED", -2300)
    assert r.photo_code == "LTDX2RRLMD6TRG" and r.fields["Device Model"] == "Redmi K60"
    assert r.fields["Location"] == "Nghe An"
    assert (r.gps_state, r.latitude, r.longitude) == ("OK", 18.691468, 105.693124)
    ct = r.capture_time  # "14:46:42GMT+7" (không có khoảng trắng trước GMT)
    assert (ct.hour, ct.minute, ct.second) == (14, 46, 42) and ct.utcoffset() == timedelta(hours=7)


def test_unknown_unable_code_is_not_guessed():
    r = parse_result(HEADER + "Unable to verify\nStatus: -9999\n")
    assert (r.status, r.reason) == ("UNABLE_TO_VERIFY", "UNKNOWN_CODE")
    assert parse_result(HEADER + "Unable to verify\n").reason == "UNKNOWN_CODE"


def test_notifier_messages():
    assert "chỉnh sửa" in message_for("VERIFIED_FAKE", "REJECTED", "EDITED")
    assert "chưa nhận" in message_for("UNABLE_TO_VERIFY", "PENDING_REVIEW", "PENDING_UPLOAD")
    assert message_for("VERIFIED_AUTHENTIC", "RECEIVED").startswith("✅")
    assert message_for("VERIFIED_AUTHENTIC", "PENDING_REVIEW").startswith("⚠️")
    assert "Timemark" in PENDING_UPLOAD_NOTICE and "10 phút" in PENDING_UPLOAD_NOTICE


def test_status_2301_no_valid_code_synthetic():
    # Mẫu TỔNG HỢP theo bố cục của ca -2307 (chưa có trang thật cho -2301)
    r = parse_result(HEADER + "N/A\nUnable to verify\nStatus: -2301\nLocation\nUnknown\nGPS\nNo Data\nPhoto Code\n--\n")
    assert (r.status, r.reason, r.status_code) == ("UNABLE_TO_VERIFY", "NO_VALID_CODE", -2301)
    assert r.photo_code is None and r.gps_state == "NONE"


def test_notifier_no_valid_code_message():
    assert message_for("UNABLE_TO_VERIFY", "REJECTED", "NO_VALID_CODE") == "❌ Ảnh không có code hợp lệ, cần gửi ảnh khác."
    out = message_for("UNABLE_TO_VERIFY", "REJECTED", "NO_VALID_CODE", None, False)
    assert "hết số lần" in out and "cần gửi ảnh khác" not in out


def test_notifier_date_mismatch_messages():
    from datetime import date

    msg = message_for("VERIFIED_AUTHENTIC", "PENDING_REVIEW", "DATE_MISMATCH", None, True, date(2026, 10, 9), date(2026, 10, 12))
    assert "09/10/2026" in msg and "12/10/2026" in msg and "gửi lại" in msg and "hết số lần" not in msg
    out = message_for("VERIFIED_AUTHENTIC", "PENDING_REVIEW", "DATE_MISMATCH", None, False, date(2026, 10, 9), date(2026, 10, 12))
    assert "hết số lần" in out and "Vui lòng chụp lại" not in out
