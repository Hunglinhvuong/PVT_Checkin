import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import photo_storage


class _Connection:
    def __init__(self):
        self.cleared = []

    def execute(self, query, params):
        self.cleared.append(params[0])


@pytest.fixture
def configure_local(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        photo_storage_backend="local",
        local_photo_dir=tmp_path / "photos",
        local_photo_retention_days=90,
    )
    monkeypatch.setattr(photo_storage, "_backend", "local")
    monkeypatch.setattr(photo_storage, "_local_photo_dir", settings.local_photo_dir)
    monkeypatch.setattr(photo_storage, "_local_photo_retention_days", settings.local_photo_retention_days)
    yield settings


def test_incoming_photo_uses_local_storage_when_enabled(configure_local):
    assert photo_storage.incoming_photo_dir(configure_local) == configure_local.local_photo_dir


def test_upload_photo_marks_local_without_cloudinary(configure_local, monkeypatch, tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    conn = object()
    marked = []
    row = {
        "id": 5, "temp_path": str(image), "file_ext": "jpg",
        "cloudinary_public_id": None, "storage_backend": "cloudinary",
    }
    monkeypatch.setattr(photo_storage, "get_conn", lambda: nullcontext(conn))
    monkeypatch.setattr(photo_storage.attendance, "get_photo_for_upload", lambda *_: row)
    monkeypatch.setattr(
        photo_storage.attendance, "mark_saved_locally", lambda _, photo_id, path: marked.append((photo_id, path))
    )

    assert photo_storage.upload_photo(5)
    assert len(marked) == 1 and marked[0][0] == 5
    archived = Path(marked[0][1])
    assert archived.parent == configure_local.local_photo_dir
    assert archived.read_bytes() == b"\xff\xd8\xff"
    assert not image.exists()


def test_cleanup_expired_local_removes_only_expired_files(configure_local, monkeypatch):
    root = configure_local.local_photo_dir
    root.mkdir()
    expired = root / "expired.jpg"
    fresh = root / "fresh.jpg"
    expired.write_bytes(b"expired")
    fresh.write_bytes(b"fresh")
    old = expired.stat().st_mtime - 100 * 24 * 60 * 60
    os.utime(expired, (old, old))
    conn = _Connection()

    @contextmanager
    def fake_get_conn():
        yield conn

    monkeypatch.setattr(photo_storage, "get_conn", fake_get_conn)

    assert photo_storage.cleanup_expired_local(retention_days=90) == 1
    assert not expired.exists()
    assert fresh.exists()
    assert conn.cleared == [str(expired)]


def test_cleanup_rejects_nonpositive_retention(configure_local, tmp_path):
    with pytest.raises(ValueError, match="retention_days"):
        photo_storage.cleanup_expired_local(tmp_path / "missing", retention_days=0)
