"""Kiểm tra TimemarkWebVerifier với trình duyệt GIẢ (không cần Playwright/mạng): các kịch bản lỗi và chặn truy cập."""
import tempfile
from pathlib import Path
from types import SimpleNamespace

from services.timemark_verifier import TimemarkWebVerifier
from tests.test_timemark_parse import HEADER, SAMPLE_AUTHENTIC

FIX = Path(__file__).parent / "fixtures"


class FakeTimeout(Exception):
    pass


class FakeError(Exception):
    pass


class FakeLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def wait_for(self, state=None, timeout=None):
        if self.page.no_input:
            raise FakeTimeout("không có input")

    def set_input_files(self, path):
        self.page.uploaded = path
        if self.page.upload_error:
            raise FakeError(self.page.upload_error)


class FakePage:
    def __init__(self, body, status=200, no_input=False, wait_timeout=False, upload_error=None, title="Batch Image Verification"):
        self.body, self.status, self.no_input = body, status, no_input
        self.wait_timeout, self.upload_error, self._title = wait_timeout, upload_error, title
        self.uploaded, self.shots = None, []

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, wait_until=None, timeout=None):
        return SimpleNamespace(status=self.status)

    def inner_text(self, selector, timeout=None):
        return self.body

    def title(self):
        return self._title

    def locator(self, selector):
        return FakeLocator(self)

    def wait_for_function(self, js, timeout=None, polling=None):
        if self.wait_timeout:
            raise FakeTimeout("hết thời gian chờ kết quả")

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path, full_page=False):
        Path(path).write_bytes(b"png")
        self.shots.append(path)

    def content(self):
        return "<html></html>"


class FakeContext:
    def __init__(self, page):
        self.page, self.closed = page, False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.ctx = FakeContext(page)

    def is_connected(self):
        return True

    def new_context(self, **kwargs):
        return self.ctx


def _run(page, debug_dir=None):
    shots = Path(tempfile.mkdtemp())
    v = TimemarkWebVerifier("https://verify.example/", result_timeout_s=10, screenshot_dir=shots)
    v._browser = FakeBrowser(page)
    v._PWTimeout, v._PWError = FakeTimeout, FakeError
    out = v.verify("/tmp/photo.jpg", tag="t", debug_dir=debug_dir)
    assert v._browser.ctx.closed, "context phải luôn được đóng"
    return out


def test_authentic_has_no_screenshot_and_uploads_file():
    page = FakePage(SAMPLE_AUTHENTIC)
    out = _run(page)
    assert out.status == "VERIFIED_AUTHENTIC" and out.photo_code == "YX3BEH29N42MKA" and out.screenshot is None
    assert page.uploaded == "/tmp/photo.jpg" and page.shots == []


def test_real_pending_and_edited_pages_keep_screenshot_for_diagnosis():
    out = _run(FakePage((FIX / "page_text_pending_2307.txt").read_text(encoding="utf-8")))
    assert (out.status, out.reason) == ("UNABLE_TO_VERIFY", "PENDING_UPLOAD") and Path(out.screenshot).exists()
    out = _run(FakePage((FIX / "page_text_edited_2300.txt").read_text(encoding="utf-8")))
    assert (out.status, out.reason) == ("VERIFIED_FAKE", "EDITED") and Path(out.screenshot).exists()


def test_timeout_waiting_for_result_is_automation_error():
    out = _run(FakePage(HEADER, wait_timeout=True))
    assert out.status == "AUTOMATION_ERROR" and out.error.startswith("TIMEOUT") and not out.blocked


def test_http_429_and_403_are_blocked_not_bypassed():
    for status in (429, 403, 503):
        out = _run(FakePage(HEADER, status=status))
        assert out.status == "MANUAL_REVIEW" and out.blocked and out.error.startswith("BLOCKED"), status


def test_captcha_page_is_blocked():
    out = _run(FakePage("Just a moment... Please verify you are human", title="Just a moment..."))
    assert out.status == "MANUAL_REVIEW" and out.blocked


def test_missing_file_input_is_ui_changed():
    out = _run(FakePage("Trang đã đổi giao diện", no_input=True))
    assert out.status == "AUTOMATION_ERROR" and out.error.startswith("UI_CHANGED") and "input" in out.error


def test_browser_error_during_upload():
    out = _run(FakePage(HEADER, upload_error="Target closed"))
    assert out.status == "AUTOMATION_ERROR" and out.error.startswith("BROWSER_ERROR") and "Target closed" in out.error


def test_result_missing_after_wait_is_not_guessed():
    out = _run(FakePage(HEADER))  # có mốc 'Clear All' nhưng không có trạng thái nào
    assert out.status == "AUTOMATION_ERROR" and out.error.startswith("UI_CHANGED")


def test_contradictory_statuses_are_not_guessed():
    out = _run(FakePage(HEADER + "Authentic\nFake\n"))
    assert out.status == "AUTOMATION_ERROR" and "mâu thuẫn" in out.error


def test_debug_dump_written():
    d = Path(tempfile.mkdtemp()) / "probe"
    _run(FakePage(SAMPLE_AUTHENTIC), debug_dir=d)
    assert (d / "page_text.txt").read_text(encoding="utf-8") == SAMPLE_AUTHENTIC
    assert (d / "page.html").exists() and (d / "page.png").exists()
