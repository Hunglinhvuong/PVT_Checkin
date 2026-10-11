"""Đọc cấu hình từ biến môi trường (.env). Không hard-code bí mật."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} phải là số nguyên, nhận được: {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} phải là true/false, nhận được: {raw!r}")


def _float_opt(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} phải là số thực, nhận được: {raw!r}") from exc


def _int_list(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    try:
        return tuple(int(p.strip()) for p in raw.split(",") if p.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} phải là danh sách số nguyên phân tách bằng dấu phẩy") from exc


_FOLDER_RE = re.compile(r"^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*$")


def validate_folder(raw: str | None, default: str = "attendance") -> str:
    """Thư mục Cloudinary: chữ/số/_/-, phân cấp bằng '/'. Bỏ '/' đầu cuối; rỗng thì dùng mặc định."""
    folder = (raw or "").strip().strip("/") or default
    if len(folder) > 120 or not _FOLDER_RE.match(folder):
        raise ConfigError(f"CLOUDINARY_FOLDER không hợp lệ: {raw!r} (chỉ chữ/số/_/-, phân cấp bằng '/')")
    return folder


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default).strip() or default)
    return p if p.is_absolute() else ROOT_DIR / p


@dataclass(frozen=True)
class Settings:
    database_url: str
    test_database_url: str
    db_pool_min: int
    db_pool_max: int
    bot_token: str
    telegram_proxy: str
    admin_telegram_ids: tuple[int, ...]
    attendance_chat_ids: tuple[int, ...]
    cloudinary_cloud_name: str
    cloudinary_api_key: str
    cloudinary_api_secret: str
    cloudinary_folder: str
    cloudinary_set_asset_folder: bool
    photo_storage_backend: str
    local_photo_dir: Path
    local_photo_retention_days: int
    upload_tmp_dir: Path
    timemark_url: str
    timemark_headless: bool
    timemark_result_timeout_s: int
    timemark_min_interval_s: int
    timemark_no_sandbox: bool
    screenshot_dir: Path
    dashboard_host: str
    dashboard_port: int
    office_name: str
    office_latitude: float | None
    office_longitude: float | None
    timezone: str
    log_level: str
    log_dir: Path
    log_to_file: bool
    backup_dir: Path
    backup_keep_days: int

    def require(self, *names: str) -> None:
        """Gọi ở nơi cần bí mật cụ thể, vd settings.require('bot_token')."""
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise ConfigError("Thiếu cấu hình bắt buộc: " + ", ".join(m.upper() for m in missing))


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or ROOT_DIR / ".env", override=False)
    s = Settings(
        database_url=os.getenv("DATABASE_URL", "").strip(),
        test_database_url=os.getenv("TEST_DATABASE_URL", "").strip(),
        db_pool_min=_int("DB_POOL_MIN", 1),
        db_pool_max=_int("DB_POOL_MAX", 10),
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip(),
        admin_telegram_ids=_int_list("ADMIN_TELEGRAM_IDS"),
        attendance_chat_ids=_int_list("ATTENDANCE_CHAT_IDS"),
        cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "").strip(),
        cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY", "").strip(),
        cloudinary_api_secret=os.getenv("CLOUDINARY_API_SECRET", "").strip(),
        cloudinary_folder=validate_folder(os.getenv("CLOUDINARY_FOLDER", "attendance")),
        cloudinary_set_asset_folder=_bool("CLOUDINARY_SET_ASSET_FOLDER", True),
        photo_storage_backend=os.getenv("PHOTO_STORAGE_BACKEND", "cloudinary").strip().lower() or "cloudinary",
        local_photo_dir=_path("LOCAL_PHOTO_DIR", "local/photos"),
        local_photo_retention_days=_int("LOCAL_PHOTO_RETENTION_DAYS", 90),
        upload_tmp_dir=_path("UPLOAD_TMP_DIR", "tmp/photos"),
        timemark_url=os.getenv("TIMEMARK_URL", "https://verify.timemark.com/").strip(),
        timemark_headless=_bool("TIMEMARK_HEADLESS", True),
        timemark_result_timeout_s=_int("TIMEMARK_RESULT_TIMEOUT_S", 60),
        timemark_min_interval_s=_int("TIMEMARK_MIN_INTERVAL_S", 5),
        timemark_no_sandbox=_bool("TIMEMARK_NO_SANDBOX", False),
        screenshot_dir=_path("SCREENSHOT_DIR", "screenshots"),
        dashboard_host=os.getenv("DASHBOARD_HOST", "0.0.0.0").strip() or "0.0.0.0",
        dashboard_port=_int("DASHBOARD_PORT", 8501),
        office_name=os.getenv("OFFICE_NAME", "Văn phòng").strip() or "Văn phòng",
        office_latitude=_float_opt("OFFICE_LATITUDE"),
        office_longitude=_float_opt("OFFICE_LONGITUDE"),
        timezone=os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_dir=_path("LOG_DIR", "logs"),
        log_to_file=_bool("LOG_TO_FILE", True),
        backup_dir=_path("BACKUP_DIR", "backups"),
        backup_keep_days=_int("BACKUP_KEEP_DAYS", 14),
    )
    if s.db_pool_min < 1 or s.db_pool_max < s.db_pool_min:
        raise ConfigError("DB_POOL_MIN >= 1 và DB_POOL_MAX >= DB_POOL_MIN")
    if s.photo_storage_backend not in {"cloudinary", "local"}:
        raise ConfigError("PHOTO_STORAGE_BACKEND phải là cloudinary hoặc local")
    if s.local_photo_retention_days < 1:
        raise ConfigError("LOCAL_PHOTO_RETENTION_DAYS phải >= 1")
    if (s.office_latitude is None) != (s.office_longitude is None):
        raise ConfigError("OFFICE_LATITUDE và OFFICE_LONGITUDE phải đặt cùng nhau")
    if s.office_latitude is not None and not (-90 <= s.office_latitude <= 90 and -180 <= s.office_longitude <= 180):
        raise ConfigError("Tọa độ văn phòng ngoài phạm vi hợp lệ")
    if s.backup_keep_days < 1:
        raise ConfigError("BACKUP_KEEP_DAYS phải >= 1")
    if s.timemark_result_timeout_s < 10 or s.timemark_min_interval_s < 1:
        raise ConfigError("TIMEMARK_RESULT_TIMEOUT_S >= 10 và TIMEMARK_MIN_INTERVAL_S >= 1")
    return s
