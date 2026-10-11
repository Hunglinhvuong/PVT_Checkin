"""Nạp các ngày nghỉ cố định theo dương lịch cho một năm.  Chạy: python -m scripts.seed_holidays 2027

CHỈ gồm ngày cố định (1/1, 30/4, 1/5, 2/9). Tết Nguyên đán, Giỗ Tổ Hùng Vương (âm lịch), ngày nghỉ bù
và ngày nghỉ hoán đổi do Chính phủ công bố từng năm => admin thêm bằng /holiday add (không tự suy đoán).
Không ghi đè dòng đã có.
"""
from __future__ import annotations

import sys
from datetime import date

import psycopg

from config.settings import load_settings

FIXED = [(1, 1, "Tết Dương lịch"), (4, 30, "Ngày Giải phóng miền Nam"), (5, 1, "Quốc tế Lao động"), (9, 2, "Quốc khánh")]


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Cú pháp: python -m scripts.seed_holidays <năm>", file=sys.stderr)
        return 2
    year = int(sys.argv[1])
    s = load_settings()
    if not s.database_url:
        print("[CẤU HÌNH] DATABASE_URL chưa được cấu hình", file=sys.stderr)
        return 1
    added = 0
    with psycopg.connect(s.database_url) as conn:
        for m, d, name in FIXED:
            added += conn.execute(
                "INSERT INTO holiday_calendar (holiday_date, name, is_working_day) VALUES (%s, %s, FALSE) "
                "ON CONFLICT (holiday_date) DO NOTHING",
                (date(year, m, d), name),
            ).rowcount
    print(f"Đã thêm {added}/{len(FIXED)} ngày nghỉ cố định cho năm {year}. Hãy thêm ngày âm lịch/nghỉ bù bằng /holiday add.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
