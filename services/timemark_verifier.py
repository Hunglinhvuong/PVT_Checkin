"""Xác minh ảnh qua giao diện web chính thức https://verify.timemark.com/ bằng Playwright.

Nguyên tắc:
- KHÔNG vượt CAPTCHA/chặn bot, KHÔNG gọi API nội bộ: chỉ thao tác như người dùng (upload ảnh, đọc kết quả hiển thị).
- Không dựa vào class CSS (dễ đổi): đọc văn bản hiển thị (innerText) của kết quả, hỗ trợ cả giao diện cũ và mới.
- Không đoán: không đọc được trạng thái rõ ràng => AUTOMATION_ERROR, không bao giờ coi là Authentic.
- Có thể thay bằng API chính thức: chỉ cần một lớp khác cài đặt Verifier.verify().

Đã quan sát giao diện cũ với các nhãn Location, GPS, Device Model, Software Provider, Capture Time và Photo Code;
giao diện mới hiển thị trạng thái trong gallery, mã "Code -2300" và các nhãn Taken, Location, Photo Code, Device.
"Unable to verify" kèm mã -2307 nghĩa là server chưa nhận dữ liệu ảnh; -2300 là ảnh bị chỉnh sửa.
Mã -2301 chưa có mẫu giao diện thật; mã lạ => UNABLE_TO_VERIFY, chuyển admin, không đoán.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

RESULT_MARKER = "Clear All"
LABELS = ("Location", "GPS", "Device Model", "Software Provider", "Capture Time", "Photo Code")
STATUS_BY_TEXT = {
    "authentic": "VERIFIED_AUTHENTIC",
    "fake": "VERIFIED_FAKE",
    "not found": "VERIFIED_NOT_FOUND",
    "unable to verify": "UNABLE_TO_VERIFY",
}
_CODE_STATUS_RE = re.compile(r"^\s*(?:Status|Code)\s*:?\s*(-?\d+)\b", re.I | re.M)
PENDING_UPLOAD_CODES = {-2307}  # Timemark chưa nhận dữ liệu ảnh => thử lại sau
EDITED_CODES = {-2300}  # ảnh đã bị chỉnh sửa
NO_CODE_CODES = {-2301}  # ảnh không có code hợp lệ => nhân viên cần gửi ảnh khác
_STATUS_RE = re.compile(r"\b(authentic|fake|not found|unable to verify)\b", re.I)  # \b: "authenticity" không khớp
_PHOTO_GALLERY_RE = re.compile(r"\bPhotos\s*\d+\b", re.I)
_PHOTO_POSITION_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
_BLOCK_RE = re.compile(
    r"captcha|verify you are human|are you a robot|just a moment|access denied|too many requests|"
    r"rate limit|unusual traffic",
    re.I,
)
_WAIT_JS = r"""() => {
  const t = document.body ? document.body.innerText : '';
  const status = /\b(authentic|fake|not found|unable to verify)\b/i;
  const i = t.indexOf('__MARKER__');
  if (i >= 0 && status.test(t.slice(i))) return true;
  const gallery = /\bPhotos\s*\d+\b/i.exec(t);
  if (!gallery) return false;
  const afterGallery = t.slice(gallery.index + gallery[0].length);
  const position = /\b\d+\s*\/\s*\d+\b/.exec(afterGallery);
  if (!position) return false;
  return status.test(afterGallery.slice(position.index + position[0].length));
}""".replace("__MARKER__", RESULT_MARKER)

_NO_GPS = {"", "no data", "unknown", "n/a", "na", "-", "--", "none", "null"}
_GPS_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;/ ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_TIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})\s*(?:GMT|UTC)?\s*([+-])(\d{1,2})(?::?(\d{2}))?"
)
_HUMAN_TIME_RE = re.compile(
    r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"\s*(AM|PM)?\s*(?:GMT|UTC)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?",
    re.I,
)
_CODE_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")


class UiChangedError(Exception):
    """Giao diện không như kỳ vọng (không thấy input, kết quả mâu thuẫn...)."""


class BlockedError(Exception):
    """Trang chặn/yêu cầu CAPTCHA: dừng, chuyển xác minh thủ công, KHÔNG vượt qua."""


@dataclass
class VerificationOutcome:
    status: str  # VERIFIED_* | UNABLE_TO_VERIFY | AUTOMATION_ERROR | MANUAL_REVIEW
    status_text: str | None = None
    result_text: str | None = None
    fields: dict[str, str | None] = field(default_factory=dict)
    capture_time: datetime | None = None
    photo_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    gps_state: str = "NONE"  # NONE | OK | UNPARSEABLE
    screenshot: str | None = None
    error: str | None = None
    blocked: bool = False
    status_code: int | None = None  # mã "Status: -23xx" hoặc "Code -23xx" của trang
    reason: str | None = None  # PENDING_UPLOAD | EDITED | NO_VALID_CODE | UNKNOWN_CODE | None


@dataclass
class ParsedResult:
    status: str
    status_text: str
    fields: dict[str, str | None]
    capture_time: datetime | None
    photo_code: str | None
    gps_state: str
    latitude: float | None
    longitude: float | None
    result_text: str
    status_code: int | None
    reason: str | None


class Verifier(Protocol):
    def verify(self, image_path: str, *, tag: str, debug_dir: Path | None = None) -> VerificationOutcome: ...


# ---------------------------------------------------------------- parsing (hàm thuần, có test)
def parse_capture_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _TIME_RE.search(raw)
    if m:
        y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
        sign = 1 if m.group(7) == "+" else -1
        off = timedelta(hours=int(m.group(8)), minutes=int(m.group(9) or 0))
    else:
        m = _HUMAN_TIME_RE.search(raw)
        if not m:
            return None
        try:
            mo = datetime.strptime(m.group(1).title(), "%b").month
        except ValueError:
            return None
        d, y, h, mi = (int(m.group(i)) for i in (2, 3, 4, 5))
        s = int(m.group(6) or 0)
        meridian = (m.group(7) or "").upper()
        if meridian:
            if not 1 <= h <= 12:
                return None
            h = h % 12 + (12 if meridian == "PM" else 0)
        sign = 1 if m.group(8) == "+" else -1
        off = timedelta(hours=int(m.group(9)), minutes=int(m.group(10) or 0))
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone(sign * off))
    except ValueError:
        return None


def parse_gps(raw: str | None) -> tuple[str, float | None, float | None]:
    """NONE: trang nói không có tọa độ. OK: đọc được cặp thập phân hợp lệ. UNPARSEABLE: có nội dung nhưng không đọc chắc chắn."""
    if raw is None or raw.strip().lower() in _NO_GPS:
        return "NONE", None, None
    m = _GPS_RE.match(raw)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return "OK", lat, lon
    return "UNPARSEABLE", None, None


def classify_status(word: str, code: int | None) -> tuple[str, str | None]:
    """Trang dùng chung nhãn "Unable to verify" cho nhiều tình huống; phân biệt bằng mã Status."""
    base = STATUS_BY_TEXT[word]
    if base != "UNABLE_TO_VERIFY":
        return base, None
    if code in PENDING_UPLOAD_CODES:
        return "UNABLE_TO_VERIFY", "PENDING_UPLOAD"
    if code in EDITED_CODES:
        return "VERIFIED_FAKE", "EDITED"
    if code in NO_CODE_CODES:
        return "UNABLE_TO_VERIFY", "NO_VALID_CODE"
    return "UNABLE_TO_VERIFY", "UNKNOWN_CODE"


def parse_result(text: str) -> ParsedResult | None:
    """None: kết quả chưa hiện. UiChangedError: không nhận diện được giao diện hoặc trạng thái mâu thuẫn."""
    idx = text.find(RESULT_MARKER)
    gallery_layout = idx < 0
    if idx >= 0:
        region = text[idx + len(RESULT_MARKER):]
        matches = list(_STATUS_RE.finditer(region))
        found = {m.group(1).lower() for m in matches}
        if not found:
            return None
        if len(found) > 1:
            raise UiChangedError(f"Nhiều trạng thái mâu thuẫn: {sorted(found)}")
        word = found.pop()
    else:
        gallery = _PHOTO_GALLERY_RE.search(text)
        if not gallery:
            raise UiChangedError(f"Không thấy mốc '{RESULT_MARKER}' hoặc danh sách ảnh")
        after_gallery = text[gallery.end():]
        position = _PHOTO_POSITION_RE.search(after_gallery)
        if not position:
            return None
        region = after_gallery[position.end():]
        code_marker = _CODE_STATUS_RE.search(region)
        status_region = region[:code_marker.start()] if code_marker else region
        matches = list(_STATUS_RE.finditer(status_region))
        if not matches:
            return None
        found = {m.group(1).lower() for m in matches}
        if len(found) > 1:
            raise UiChangedError(f"Nhiều trạng thái mâu thuẫn: {sorted(found)}")
        word = matches[-1].group(1).lower()
        region = region[matches[-1].start():]

    m_code = _CODE_STATUS_RE.search(region)
    status_code = int(m_code.group(1)) if m_code else None
    status, reason = classify_status(word, status_code)

    lines = [ln.strip() for ln in region.splitlines() if ln.strip()]
    low_labels = {lb.lower() for lb in LABELS}
    fields: dict[str, str | None] = {}
    for label in LABELS:
        pos = [i for i, ln in enumerate(lines) if ln.lower() == label.lower()]
        value = None
        if pos:
            i = pos[-1]  # nhãn "Location" xuất hiện cả ở ô tóm tắt lẫn lưới chi tiết; lưới chi tiết nằm sau
            if i + 1 < len(lines) and lines[i + 1].lower() not in low_labels:
                value = lines[i + 1]
        fields[label] = value

    if gallery_layout:
        taken = [i for i, ln in enumerate(lines) if ln.lower() == "taken"]
        if taken:
            start = taken[-1] + 1
            end = next(
                (i for i in range(start, len(lines)) if lines[i].lower() in {"location", "photo code", "device", "file"}),
                len(lines),
            )
            fields["Capture Time"] = "\n".join(lines[start:end]) or None

        locations = [i for i, ln in enumerate(lines) if ln.lower() == "location"]
        if locations:
            start = locations[-1] + 1
            end = next(
                (i for i in range(start, len(lines)) if lines[i].lower() in {"photo code", "device", "file"}),
                len(lines),
            )
            for value in lines[start:end]:
                gps_state, _, _ = parse_gps(value)
                if gps_state == "OK":
                    fields["GPS"] = value
                    break
                if fields["Location"] is None:
                    fields["Location"] = value

        devices = [i for i, ln in enumerate(lines) if ln.lower() == "device"]
        if devices:
            i = devices[-1]
            if i + 1 < len(lines) and lines[i + 1].lower() not in {"file", "photo code"}:
                fields["Device Model"] = lines[i + 1]

    code = fields["Photo Code"]
    gps_state, lat, lon = parse_gps(fields["GPS"])
    return ParsedResult(
        status=status,
        status_text=word,
        fields=fields,
        capture_time=parse_capture_time(fields["Capture Time"]),
        photo_code=code if code and _CODE_RE.match(code) else None,
        gps_state=gps_state,
        latitude=lat,
        longitude=lon,
        result_text=region.strip()[:4000],
        status_code=status_code,
        reason=reason,
    )


# ---------------------------------------------------------------- Playwright
class TimemarkWebVerifier:
    def __init__(self, url: str, *, headless: bool = True, result_timeout_s: int = 60,
                 screenshot_dir: Path | None = None, no_sandbox: bool = False) -> None:
        self.url = url
        self.headless = headless
        self.no_sandbox = no_sandbox
        self.timeout_ms = result_timeout_s * 1000
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._pw = None
        self._browser = None

    def __enter__(self) -> "TimemarkWebVerifier":
        from playwright.sync_api import Error, TimeoutError, sync_playwright

        self._PWError, self._PWTimeout = Error, TimeoutError
        self._pw = sync_playwright().start()
        self._launch()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _launch(self) -> None:
        self._browser = self._pw.chromium.launch(
            headless=self.headless, args=["--no-sandbox"] if self.no_sandbox else [])

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._browser = self._pw = None

    def _ensure_browser(self) -> None:
        if self._browser is None or not self._browser.is_connected():
            log.warning("Trình duyệt đã đóng/crash, khởi động lại")
            self._launch()

    def _shot(self, page, tag: str) -> str | None:
        if not self.screenshot_dir:
            return None
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = self.screenshot_dir / f"{tag}_{int(time.time())}.png"
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Không chụp được màn hình: %s", exc)
            return None

    @staticmethod
    def _dump(page, debug_dir: Path | None) -> None:
        if not debug_dir:
            return
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / "page_text.txt").write_text(page.inner_text("body"), encoding="utf-8")
            (debug_dir / "page.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(debug_dir / "page.png"), full_page=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Không dump được trang: %s", exc)

    def _raise_if_blocked(self, page) -> None:
        try:
            body = page.inner_text("body", timeout=3000)
            title = page.title()
        except Exception:  # noqa: BLE001
            return
        if _BLOCK_RE.search(title) or _BLOCK_RE.search(body[:3000]):
            raise BlockedError("Trang hiển thị cơ chế chặn/CAPTCHA/giới hạn truy cập")

    @staticmethod
    def _stable_text(page, rounds: int = 4) -> str:
        """Đọc lại tới khi văn bản không đổi giữa 2 lần liên tiếp (tránh đọc trạng thái trung gian)."""
        prev = page.inner_text("body")
        for _ in range(rounds):
            page.wait_for_timeout(1500)
            cur = page.inner_text("body")
            if cur == prev:
                return cur
            prev = cur
        return prev

    def verify(self, image_path: str, *, tag: str, debug_dir: Path | None = None) -> VerificationOutcome:
        self._ensure_browser()
        ctx = self._browser.new_context(locale="en-US", viewport={"width": 1280, "height": 1000})
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            resp = page.goto(self.url, wait_until="domcontentloaded", timeout=30_000)
            if resp is not None and resp.status in (403, 429, 503):
                raise BlockedError(f"HTTP {resp.status}")
            self._raise_if_blocked(page)

            file_input = page.locator("input[type='file']").first
            try:
                file_input.wait_for(state="attached", timeout=20_000)
            except self._PWTimeout:
                self._raise_if_blocked(page)
                raise UiChangedError("Không thấy input[type=file] trên trang")
            file_input.set_input_files(image_path)

            try:
                page.wait_for_function(_WAIT_JS, timeout=self.timeout_ms, polling=1000)
            except self._PWTimeout:
                self._raise_if_blocked(page)
                raise
            text = self._stable_text(page)
            self._dump(page, debug_dir)
            parsed = parse_result(text)
            if parsed is None:
                raise UiChangedError("Kết quả biến mất sau khi đã hiện")
            shot = None if parsed.status == "VERIFIED_AUTHENTIC" else self._shot(page, tag)
            return VerificationOutcome(
                status=parsed.status, status_text=parsed.status_text, result_text=parsed.result_text,
                fields=parsed.fields, capture_time=parsed.capture_time, photo_code=parsed.photo_code,
                latitude=parsed.latitude, longitude=parsed.longitude, gps_state=parsed.gps_state, screenshot=shot,
                status_code=parsed.status_code, reason=parsed.reason,
            )
        except BlockedError as exc:
            self._dump(page, debug_dir)
            return VerificationOutcome("MANUAL_REVIEW", error=f"BLOCKED: {exc}", screenshot=self._shot(page, tag), blocked=True)
        except self._PWTimeout as exc:
            self._dump(page, debug_dir)
            return VerificationOutcome("AUTOMATION_ERROR", error=f"TIMEOUT: {str(exc)[:300]}", screenshot=self._shot(page, tag))
        except UiChangedError as exc:
            self._dump(page, debug_dir)
            return VerificationOutcome("AUTOMATION_ERROR", error=f"UI_CHANGED: {exc}", screenshot=self._shot(page, tag))
        except self._PWError as exc:
            return VerificationOutcome("AUTOMATION_ERROR", error=f"BROWSER_ERROR: {str(exc)[:300]}", screenshot=self._shot(page, tag))
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
