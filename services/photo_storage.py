"""Lưu ảnh tạm + upload Cloudinary (type=authenticated, không công khai, public_id cố định nên retry không tạo trùng)."""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path

from config.settings import Settings, validate_folder
from database.connection import get_conn
from repositories import attendance

log = logging.getLogger(__name__)
MAX_UPLOAD_ATTEMPTS = 5
_folder = "attendance"
_set_asset_folder = True
_backend = "cloudinary"
_local_photo_dir = Path("local/photos")
_local_photo_retention_days = 90


def set_folder(folder: str, set_asset_folder: bool = True) -> None:
    """Đặt thư mục Cloudinary cho các lần upload sau (ảnh đã upload giữ nguyên public_id đã lưu trong DB)."""
    global _folder, _set_asset_folder
    _folder, _set_asset_folder = validate_folder(folder), set_asset_folder


def configure(settings: Settings) -> None:
    global _backend, _local_photo_dir, _local_photo_retention_days
    _backend = settings.photo_storage_backend
    _local_photo_dir = Path(settings.local_photo_dir)
    _local_photo_retention_days = settings.local_photo_retention_days
    if _backend == "cloudinary":
        import cloudinary

        settings.require("cloudinary_cloud_name", "cloudinary_api_key", "cloudinary_api_secret")
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )
        set_folder(settings.cloudinary_folder, settings.cloudinary_set_asset_folder)


def incoming_photo_dir(settings: Settings) -> Path:
    """Trả về thư mục lưu trữ lâu dài hoặc thư mục tạm tùy backend."""
    return settings.local_photo_dir if settings.photo_storage_backend == "local" else settings.upload_tmp_dir


def detect_image_ext(data: bytes) -> str | None:
    """Nhận diện theo magic bytes, không tin mime/tên file do client gửi."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
        return "heic"
    return None


def save_temp(tmp_dir: Path, chat_id: int, message_id: int, ext: str, data: bytes) -> str:
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tmp_dir, 0o700)
    except OSError:
        pass
    path = tmp_dir / f"{chat_id}_{message_id}_{uuid.uuid4().hex[:8]}.{ext}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return str(path)


def delete_temp(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Không xóa được file tạm %s: %s", path, exc)


def build_public_id(row: dict) -> str:
    return f"{_folder}/{row['attendance_date'].isoformat()}/{row['user_id']}_{row['session']}_{row['attendance_record_id']}"


def _upload_options(public_id: str) -> dict:
    opts = {
        "public_id": public_id,
        "type": "authenticated",  # không công khai
        "resource_type": "image",
        "overwrite": False,  # public_id đã tồn tại thì trả về tài nguyên cũ, không tạo bản trùng
        "unique_filename": False,
        "use_filename": False,
        "timeout": 30,
    }
    if _set_asset_folder and "/" in public_id:
        opts["asset_folder"] = public_id.rsplit("/", 1)[0]  # hiện trong thư mục ở Media Library (dynamic folder mode)
    return opts


def _upload(path: str, public_id: str) -> dict:
    import cloudinary.uploader

    return cloudinary.uploader.upload(path, **_upload_options(public_id))


def signed_url(public_id: str) -> str:
    """URL có chữ ký để xem ảnh authenticated (dùng cho dashboard, Giai đoạn 6)."""
    import cloudinary.utils

    url, _ = cloudinary.utils.cloudinary_url(
        public_id, type="authenticated", resource_type="image", sign_url=True, secure=True
    )
    return url


def upload_photo(photo_id: int) -> bool:
    """Upload ảnh đã tiếp nhận. Idempotent: gọi lại an toàn. Giữ file tạm cho worker xác minh (Giai đoạn 4)."""
    with get_conn() as conn:
        row = attendance.get_photo_for_upload(conn, photo_id)
    if row is None:
        return False
    if row["cloudinary_public_id"]:
        return True
    path = row["temp_path"]
    if row.get("storage_backend") == "local":
        if path and Path(path).exists():
            return True
        log.error("Ảnh local %s không còn file tại %s", photo_id, path)
        return False
    if not path or not Path(path).exists():
        with get_conn() as conn:
            attendance.mark_upload_failed(conn, photo_id, "file tạm không tồn tại")
        log.error("Ảnh %s: file tạm không tồn tại, không thể upload", photo_id)
        return False
    if _backend == "local":
        local_path = Path(path)
        archived_path = None
        try:
            if not local_path.resolve().is_relative_to(_local_photo_dir.resolve()):
                archived_path = save_temp(
                    _local_photo_dir, 0, photo_id, row["file_ext"], local_path.read_bytes()
                )
            stored_path = archived_path or path
            with get_conn() as conn:
                attendance.mark_saved_locally(conn, photo_id, stored_path)
        except Exception as exc:  # noqa: BLE001 - ghi local cũng cần được thử lại và báo lỗi
            if archived_path:
                delete_temp(archived_path)
            with get_conn() as conn:
                attendance.mark_upload_failed(conn, photo_id, f"{type(exc).__name__}: {exc}")
            log.error("Không lưu được ảnh %s trên local: %s", photo_id, exc)
            return False
        if archived_path:
            delete_temp(path)
        log.info("Đã lưu ảnh %s tại local: %s", photo_id, stored_path)
        return True
    try:
        res = _upload(path, build_public_id(row))
    except Exception as exc:  # noqa: BLE001 - lỗi mạng/Cloudinary đủ loại
        with get_conn() as conn:
            attendance.mark_upload_failed(conn, photo_id, f"{type(exc).__name__}: {exc}")
        log.warning("Upload Cloudinary lỗi (ảnh %s): %s", photo_id, exc)
        return False
    with get_conn() as conn:
        attendance.mark_uploaded(conn, photo_id, res["public_id"], res["secure_url"])
    log.info("Đã upload ảnh %s -> %s", photo_id, res["public_id"])
    cleanup_temp_if_done(photo_id)
    return True


def retry_pending(limit: int = 10) -> int:
    """Thử lại các ảnh chưa upload được. Trả về số ảnh upload thành công."""
    with get_conn() as conn:
        ids = attendance.list_pending_uploads(conn, limit, MAX_UPLOAD_ATTEMPTS)
    return sum(1 for i in ids if upload_photo(i))


def cleanup_temp_if_done(photo_id: int) -> bool:
    """Xóa file tạm Cloudinary sau xác minh; ảnh local được giữ đến khi hết hạn."""
    with get_conn() as conn:
        row = attendance.get_photo_with_status(conn, photo_id)
        if (not row or not row["temp_path"] or not row["cloudinary_public_id"]
                or row["storage_backend"] == "local"):
            return False
        if row["verification_status"] in ("PENDING", "PROCESSING"):
            return False
        attendance.clear_temp_path(conn, photo_id)
    delete_temp(row["temp_path"])
    return True


def cleanup_expired_local(photo_dir: Path | None = None, retention_days: int | None = None) -> int:
    """Xóa file local quá hạn và bỏ đường dẫn đã hết hiệu lực trong PostgreSQL."""
    root = Path(photo_dir or _local_photo_dir)
    days = retention_days if retention_days is not None else _local_photo_retention_days
    if days < 1:
        raise ValueError("retention_days phải >= 1")
    if not root.exists():
        return 0

    cutoff_time = time.time() - timedelta(days=days).total_seconds()
    expired: list[Path] = []
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_mtime < cutoff_time:
                expired.append(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("Không kiểm tra được tuổi file ảnh %s: %s", path, exc)

    removed = 0
    with get_conn() as conn:
        for path in expired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("Không xóa được ảnh local quá hạn %s: %s", path, exc)
                continue
            attendance.clear_local_path(conn, str(path))
            removed += 1
    if removed:
        log.info("Đã xóa %d ảnh local quá hạn (giữ %d ngày)", removed, days)
    return removed


def fetch_from_cloudinary(photo: dict, tmp_dir: Path, max_bytes: int = 10 * 1024 * 1024) -> str:
    """Tải lại ảnh gốc từ Cloudinary khi file tạm đã mất. Bắt buộc khớp SHA-256 để đảm bảo đúng ảnh."""
    import hashlib
    import urllib.request

    if not photo.get("cloudinary_public_id"):
        raise ValueError("Ảnh chưa có trên Cloudinary")
    with urllib.request.urlopen(signed_url(photo["cloudinary_public_id"]), timeout=30) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Ảnh tải về vượt giới hạn kích thước")
    if hashlib.sha256(data).hexdigest() != photo["sha256"]:
        raise ValueError("SHA-256 ảnh tải về không khớp bản gốc")
    ext = detect_image_ext(data)
    if ext is None:
        raise ValueError("Dữ liệu tải về không phải ảnh")
    path = save_temp(tmp_dir, 0, photo["id"], ext, data)
    with get_conn() as conn:
        attendance.set_temp_path(conn, photo["id"], path)
    return path
