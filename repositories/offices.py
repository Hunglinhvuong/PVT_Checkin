from __future__ import annotations

from psycopg import Connection


def get_active(conn: Connection) -> dict | None:
    return conn.execute("SELECT * FROM office_locations WHERE is_active LIMIT 1").fetchone()


def replace_active(conn: Connection, name: str, lat: float, lon: float) -> dict:
    conn.execute("UPDATE office_locations SET is_active = FALSE WHERE is_active")
    return conn.execute(
        "INSERT INTO office_locations (name, latitude, longitude) VALUES (%s, %s, %s) RETURNING *",
        (name, lat, lon),
    ).fetchone()
