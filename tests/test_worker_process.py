"""process_job: các nhánh không cần DB/trình duyệt thật."""
import tempfile
from pathlib import Path
from types import SimpleNamespace

from services import verification
from services.timemark_verifier import VerificationOutcome
from workers.verification_worker import process_job


def _job(photo):
    return verification.ClaimedJob(job_id=1, record_id=5, attempts=1, max_attempts=3, log_id=1, record={}, photo=photo)


def _settings():
    return SimpleNamespace(upload_tmp_dir=Path(tempfile.mkdtemp()))


class Boom:
    def verify(self, path, *, tag, debug_dir=None):
        raise RuntimeError("chromium chết")


class Ok:
    def __init__(self):
        self.path = None

    def verify(self, path, *, tag, debug_dir=None):
        self.path = path
        return VerificationOutcome("VERIFIED_AUTHENTIC", status_text="authentic")


def _photo(ext="jpg", size=1000, path=None):
    return {"id": 1, "file_ext": ext, "file_size_bytes": size, "temp_path": path, "cloudinary_public_id": None, "sha256": "0" * 64}


def test_unsupported_formats_and_oversize_go_manual_without_calling_verifier():
    for photo in (_photo("heic"), _photo("webp"), _photo("jpg", 10 * 1024 * 1024 + 1)):
        out = process_job(_job(photo), Boom(), _settings())
        assert out.status == "MANUAL_REVIEW" and "UNSUPPORTED" in out.error


def test_missing_file_and_no_cloudinary_copy_goes_manual():
    out = process_job(_job(_photo(path="/không/tồn/tại.jpg")), Boom(), _settings())
    assert out.status == "MANUAL_REVIEW" and out.error.startswith("NO_IMAGE_FILE")


def test_verifier_crash_becomes_automation_error():
    f = Path(tempfile.mkdtemp()) / "a.jpg"
    f.write_bytes(b"\xff\xd8\xff0")
    out = process_job(_job(_photo(path=str(f))), Boom(), _settings())
    assert out.status == "AUTOMATION_ERROR" and "chromium chết" in out.error


def test_verifier_receives_the_stored_file():
    f = Path(tempfile.mkdtemp()) / "a.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    v = Ok()
    out = process_job(_job(_photo("png", path=str(f))), v, _settings())
    assert out.status == "VERIFIED_AUTHENTIC" and v.path == str(f)
