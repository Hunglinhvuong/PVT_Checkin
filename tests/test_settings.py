import pytest

from config.settings import ConfigError, load_settings


def _clear(monkeypatch):
    for k in ("DATABASE_URL", "ADMIN_TELEGRAM_IDS", "DB_POOL_MIN", "DB_POOL_MAX", "BOT_TOKEN", "TELEGRAM_PROXY"):
        monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch, tmp_path):
    _clear(monkeypatch)
    s = load_settings(tmp_path / "none.env")
    assert s.timezone == "Asia/Ho_Chi_Minh"
    assert s.admin_telegram_ids == ()
    assert s.db_pool_min == 1


def test_telegram_proxy(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_PROXY", "http://proxy.example:8080")
    assert load_settings(tmp_path / "none.env").telegram_proxy == "http://proxy.example:8080"


def test_admin_ids_parse(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "123, 456")
    assert load_settings(tmp_path / "none.env").admin_telegram_ids == (123, 456)


def test_invalid_admin_ids(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "abc")
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "none.env")


def test_invalid_pool(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("DB_POOL_MIN", "5")
    monkeypatch.setenv("DB_POOL_MAX", "2")
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "none.env")


def test_require(monkeypatch, tmp_path):
    _clear(monkeypatch)
    s = load_settings(tmp_path / "none.env")
    with pytest.raises(ConfigError):
        s.require("bot_token")
