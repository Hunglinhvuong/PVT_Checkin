from __future__ import annotations

from datetime import date

from psycopg import Connection


def get(conn: Connection, d: date) -> dict | None:
    return conn.execute("SELECT * FROM holiday_calendar WHERE holiday_date = %s", (d,)).fetchone()


def upsert(conn: Connection, d: date, name: str, is_working_day: bool) -> dict:
    return conn.execute(
        "INSERT INTO holiday_calendar (holiday_date, name, is_working_day) VALUES (%s, %s, %s) "
        "ON CONFLICT (holiday_date) DO UPDATE SET name = EXCLUDED.name, is_working_day = EXCLUDED.is_working_day "
        "RETURNING *",
        (d, name, is_working_day),
    ).fetchone()


def delete(conn: Connection, d: date) -> dict | None:
    return conn.execute("DELETE FROM holiday_calendar WHERE holiday_date = %s RETURNING *", (d,)).fetchone()


def list_year(conn: Connection, year: int) -> list[dict]:
    return conn.execute(
        "SELECT * FROM holiday_calendar WHERE holiday_date >= %s AND holiday_date < %s ORDER BY holiday_date",
        (date(year, 1, 1), date(year + 1, 1, 1)),
    ).fetchall()
