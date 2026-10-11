"""Integration test (cần TEST_DATABASE_URL, XÓA schema public của DB test)."""
import dataclasses
import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from config.settings import load_settings
from database.connection import close_pool, get_conn, init_pool
from database.migrate import migrate
from services import registration as reg

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL chưa đặt")

CHAT = -1001


@pytest.fixture(scope="module", autouse=True)
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(URL)
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("INSERT INTO admins (telegram_user_id, role) VALUES (9001, 'ADMIN'), (9002, 'ADMIN'), (9003, 'VIEWER')")
    init_pool(dataclasses.replace(load_settings(), database_url=URL))
    yield
    close_pool()


def _register(tg_id, name="Nguyễn Văn A", chat=CHAT):
    assert reg.start(tg_id, chat).kind == "NEW"
    return reg.submit_name(tg_id, chat, name)


def test_no_state_ignored():
    assert reg.submit_name(111, CHAT, "Ai Đó").kind == "NO_STATE"


def test_invalid_keeps_state():
    reg.start(112, CHAT)
    assert reg.submit_name(112, CHAT, "1").kind == "INVALID"
    assert reg.submit_name(112, CHAT, "Lê Văn B").kind == "CREATED"


def test_states_are_isolated_per_user_and_chat():
    reg.start(201, CHAT)
    reg.start(202, CHAT)
    assert reg.submit_name(202, CHAT, "Người Hai").user["full_name"] == "Người Hai"
    assert reg.submit_name(201, -999, "Sai Chat").kind == "NO_STATE"  # chat khác
    assert reg.submit_name(201, CHAT, "Người Một").user["full_name"] == "Người Một"
    with get_conn() as conn:
        names = {r["telegram_user_id"]: r["full_name"] for r in conn.execute(
            "SELECT telegram_user_id, full_name FROM users WHERE telegram_user_id IN (201, 202)")}
    assert names == {201: "Người Một", 202: "Người Hai"}


def test_double_submit_creates_one_user():
    reg.start(301, CHAT)
    with ThreadPoolExecutor(4) as ex:
        kinds = sorted(r.kind for r in ex.map(lambda _: reg.submit_name(301, CHAT, "Một Người"), range(4)))
    assert kinds.count("CREATED") == 1
    assert set(kinds) <= {"CREATED", "NO_STATE", "EXISTS"}
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users WHERE telegram_user_id = 301").fetchone()["n"] == 1


def test_start_status_messages():
    uid = _register(401).user["id"]
    assert reg.start(401, CHAT).kind == "PENDING"
    assert reg.decide(9001, uid, True).kind == "OK"
    assert reg.start(401, CHAT).kind == "APPROVED"
    uid2 = _register(402).user["id"]
    reg.decide(9001, uid2, False)
    assert reg.start(402, CHAT).kind == "REJECTED"


def test_only_admin_can_decide():
    uid = _register(501).user["id"]
    assert reg.decide(555, uid, True).kind == "NOT_ADMIN"
    assert reg.decide(9003, uid, True).kind == "NOT_ADMIN"  # VIEWER
    assert reg.list_pending(555) is None
    assert any(u["id"] == uid for u in reg.list_pending(9001))


def test_concurrent_decisions_only_one_wins_and_audited():
    uid = _register(601).user["id"]
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda a: reg.decide(a, uid, True), (9001, 9002)))
    assert sorted(r.kind for r in res) == ["ALREADY", "OK"]
    with get_conn() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM audit_logs WHERE entity_id = %s AND action = 'USER_APPROVED'", (uid,)
        ).fetchone()["n"]
        u = conn.execute("SELECT status, approved_by, approved_at FROM users WHERE id = %s", (uid,)).fetchone()
    assert n == 1 and u["status"] == "APPROVED" and u["approved_by"] and u["approved_at"]
