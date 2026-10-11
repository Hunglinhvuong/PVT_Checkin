from datetime import date

from dashboard.util import FINAL, SESSION, label, like_pattern, normalize_range

D = date(2026, 10, 12)


def test_normalize_range_variants():
    assert normalize_range((date(2026, 10, 1), date(2026, 10, 5)), D) == (date(2026, 10, 1), date(2026, 10, 5))
    assert normalize_range((date(2026, 10, 5), date(2026, 10, 1)), D) == (date(2026, 10, 1), date(2026, 10, 5))
    assert normalize_range((date(2026, 10, 3),), D) == (date(2026, 10, 3), date(2026, 10, 3))
    assert normalize_range((), D) == (D, D)
    assert normalize_range(date(2026, 10, 4), D) == (date(2026, 10, 4), date(2026, 10, 4))


def test_like_pattern_escapes_wildcards():
    assert like_pattern("ab") == "%ab%"
    assert like_pattern("50%_\\") == "%50\\%\\_\\\\%"


def test_labels():
    assert label(FINAL, "LATE") == "Muộn" and label(SESSION, "MORNING") == "Sáng"
    assert label(FINAL, "UNKNOWN") == "UNKNOWN" and label(FINAL, None) == ""
