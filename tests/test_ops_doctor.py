from config.settings import load_settings
from ops.doctor import (
    FAIL, OK, WARN, Check, alert_text, check_telegram, decide_alert, evaluate_backup_age,
    fingerprint, format_report,
)

H = 3600


def test_backup_age_levels():
    now = 1_000_000.0
    assert evaluate_backup_age(None, now).level == WARN
    assert evaluate_backup_age(now - 2 * H, now).level == OK
    assert evaluate_backup_age(now - 40 * H, now).level == WARN
    assert evaluate_backup_age(now - 80 * H, now).level == FAIL


def test_fingerprint_ignores_ok_and_is_order_independent():
    a = [Check("A", OK), Check("B", WARN), Check("C", FAIL)]
    assert fingerprint(a) == fingerprint(list(reversed(a))) == "B:WARN|C:FAIL"
    assert fingerprint([Check("A", OK)]) == "OK"


def test_alert_dedup_and_recovery():
    t = 1_000_000.0
    send, st = decide_alert(None, "OK", t)
    assert not send  # lần đầu mà khỏe: im lặng
    send, st = decide_alert(None, "DB:FAIL", t)
    assert send and st["fp"] == "DB:FAIL"
    send, st2 = decide_alert(st, "DB:FAIL", t + 600)
    assert not send and st2 == st  # cùng vấn đề, chưa tới hạn nhắc lại
    send, st3 = decide_alert(st, "DB:FAIL", t + 7 * H)
    assert send and st3["at"] == t + 7 * H  # nhắc lại sau 6 giờ
    send, st4 = decide_alert(st3, "DB:FAIL|Q:WARN", t + 7 * H + 60)
    assert send  # thêm vấn đề mới
    send, st5 = decide_alert(st4, "OK", t + 8 * H)
    assert send and st5["fp"] == "OK"  # hồi phục: báo một lần
    assert not decide_alert(st5, "OK", t + 9 * H)[0]


def test_texts():
    checks = [Check("PostgreSQL", FAIL, "không kết nối được"), Check("Đĩa", OK, "còn 9000 MB")]
    assert "❌ PostgreSQL: không kết nối được" in format_report(checks)
    t = alert_text(checks)
    assert "PostgreSQL" in t and "Đĩa" not in t  # chỉ liệt kê mục có vấn đề
    assert "bình thường trở lại" in alert_text([Check("A", OK)])


def test_telegram_check_uses_configured_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_PROXY", "http://proxy.example:8080")
    settings = load_settings(tmp_path / "none.env")
    calls = []
    monkeypatch.setattr(
        "ops.doctor._http",
        lambda url, proxy_url=None: calls.append((url, proxy_url))
        or b'{"ok":true,"result":{"username":"test"}}',
    )

    assert check_telegram(settings).level == OK
    assert calls == [("https://api.telegram.org/bottest-token/getMe", "http://proxy.example:8080")]
