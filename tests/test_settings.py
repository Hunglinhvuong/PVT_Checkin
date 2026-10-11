import pytest

from config.settings import ConfigError, load_settings


def _clear(monkeypatch):
    for k in (
        "DATABASE_URL", "ADMIN_TELEGRAM_IDS", "DB_POOL_MIN", "DB_POOL_MAX", "BOT_TOKEN", "TELEGRAM_PROXY",
        "PHOTO_STORAGE_BACKEND", "LOCAL_PHOTO_DIR", "LOCAL_PHOTO_RETENTION_DAYS",
    ):
        monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch, tmp_path):
    _clear(monkeypatch)
    s = load_settings(tmp_path / "none.env")
    assert s.timezone == "Asia/Ho_Chi_Minh"
    assert s.admin_telegram_ids == ()
    assert s.db_pool_min == 1
    assert s.photo_storage_backend == "cloudinary"
    assert s.local_photo_dir.name == "photos"
    assert s.local_photo_retention_days == 90


def test_local_photo_storage_settings(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("PHOTO_STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_PHOTO_DIR", "/var/lib/attendance/photos")
    monkeypatch.setenv("LOCAL_PHOTO_RETENTION_DAYS", "120")
    s = load_settings(tmp_path / "none.env")
    assert s.photo_storage_backend == "local"
    assert s.local_photo_dir.as_posix() == "/var/lib/attendance/photos"
    assert s.local_photo_retention_days == 120


@pytest.mark.parametrize(
    "name,value",
    [("PHOTO_STORAGE_BACKEND", "s3"), ("LOCAL_PHOTO_RETENTION_DAYS", "0")],
)
def test_invalid_photo_storage_settings(monkeypatch, tmp_path, name, value):
    _clear(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "none.env")


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
