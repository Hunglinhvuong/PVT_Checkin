import pytest

from services.registration import normalize_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Nguyễn   Văn  A ", "Nguyễn Văn A"),
        ("Nguye\u0302\u0303n Van A", "Nguyễn Van A"),  # NFC
        ("Trần Thị-Hoa", "Trần Thị-Hoa"),
        ("O'Neil", "O'Neil"),
    ],
)
def test_valid(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "A", "12345", "Nguyen <b>", "a" * 101, "Anh_Tuan", "😀😀", "/start"])
def test_invalid(raw):
    assert normalize_name(raw) is None
