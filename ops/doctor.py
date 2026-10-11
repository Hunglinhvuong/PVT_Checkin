"""Kiểm tra sức khỏe hệ thống.  python main.py doctor [--quick] [--alert]

--quick: bỏ qua kiểm tra chậm (Telegram, Cloudinary, trình duyệt, Timemark).
--alert: chế độ nhanh + gửi cảnh báo Telegram tới admin khi tình trạng thay đổi (chống spam: nhắc lại tối đa 6 giờ/lần).
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psycopg

from config.settings import ROOT_DIR, Settings
from ops.alerts import send_admins

log = logging.getLogger(__name__)
OK, WARN, FAIL = "OK", "WARN", "FAIL"
ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
SERVICES = ("telegram-attendance-bot", "telegram-attendance-worker", "telegram-attendance-dashboard")
BACKUP_WARN_H, BACKUP_FAIL_H = 36, 72
QUEUE_STALE_MIN = 30
REALERT_SECONDS = 6 * 3600
MIGRATIONS_DIR = ROOT_DIR / "database" / "migrations"


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    detail: str = ""


# ---------------------------------------------------------------- hàm thuần (có test)
def evaluate_backup_age(latest_mtime: float | None, now: float) -> Check:
    if latest_mtime is None:
        return Check("Sao lưu", WARN, "Chưa có bản sao lưu nào (chạy deploy/backup.sh)")
    hours = (now - latest_mtime) / 3600
    level = FAIL if hours > BACKUP_FAIL_H else WARN if hours > BACKUP_WARN_H else OK
    return Check("Sao lưu", level, f"bản gần nhất cách đây {hours:.1f} giờ")


def fingerprint(checks: list[Check]) -> str:
    bad = sorted(f"{c.name}:{c.level}" for c in checks if c.level != OK)
    return "|".join(bad) if bad else "OK"


def decide_alert(prev: dict | None, fp: str, now: float, cooldown_s: int = REALERT_SECONDS) -> tuple[bool, dict]:
    """Gửi khi: lần đầu mà có vấn đề; tình trạng đổi (kể cả hồi phục); hoặc vấn đề cũ kéo dài quá cooldown."""
    if prev is None:
        return fp != "OK", {"fp": fp, "at": now}
    if prev.get("fp") != fp:
        return True, {"fp": fp, "at": now}
    if fp != "OK" and now - prev.get("at", 0) >= cooldown_s:
        return True, {"fp": fp, "at": now}
    return False, prev


def format_report(checks: list[Check]) -> str:
    return "\n".join(f"{ICON[c.level]} {c.name}" + (f": {c.detail}" if c.detail else "") for c in checks)


def alert_text(checks: list[Check]) -> str:
    bad = [c for c in checks if c.level != OK]
    if not bad:
        return "✅ Hệ thống điểm danh đã hoạt động bình thường trở lại."
    return f"⚠️ Cảnh báo hệ thống điểm danh ({socket.gethostname()}):\n" + format_report(bad)


# ---------------------------------------------------------------- các kiểm tra
def _safe(name: str, fn) -> list[Check]:
    try:
        res = fn()
        return res if isinstance(res, list) else [res]
    except Exception as exc:  # noqa: BLE001
        return [Check(name, FAIL, f"{type(exc).__name__}: {str(exc)[:200]}")]


def check_config(s: Settings) -> Check:
    missing = [n for n, v in [
        ("DATABASE_URL", s.database_url), ("BOT_TOKEN", s.bot_token), ("ADMIN_TELEGRAM_IDS", s.admin_telegram_ids),
        ("CLOUDINARY_CLOUD_NAME", s.cloudinary_cloud_name), ("CLOUDINARY_API_KEY", s.cloudinary_api_key),
        ("CLOUDINARY_API_SECRET", s.cloudinary_api_secret)] if not v]
    if missing:
        return Check("Cấu hình .env", FAIL, "thiếu " + ", ".join(missing))
    if not s.attendance_chat_ids:
        return Check("Cấu hình .env", WARN, "chưa đặt ATTENDANCE_CHAT_IDS: bot trả lời ảnh ở mọi chat")
    return Check("Cấu hình .env", OK)


def check_database(s: Settings) -> list[Check]:
    out: list[Check] = []
    with psycopg.connect(s.database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1")
        out.append(Check("PostgreSQL", OK, "kết nối được"))

        exists = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
        files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")} if exists else set()
        pending = [f for f in files if f not in applied]
        out.append(Check("Migrations", WARN, f"chưa áp dụng: {', '.join(pending)} (bot/worker sẽ tự áp dụng khi khởi động)")
                   if pending else Check("Migrations", OK, f"{len(files)} bản đã áp dụng"))
        if pending and not exists:
            return out

        n_admin = conn.execute("SELECT count(*) FROM admins WHERE status = 'ACTIVE'").fetchone()[0]
        office = conn.execute("SELECT count(*) FROM office_locations WHERE is_active").fetchone()[0]
        out.append(Check("Admin", OK if n_admin else FAIL, f"{n_admin} admin hoạt động"))
        out.append(Check("Văn phòng", OK if office else WARN, "đã cấu hình" if office else "chưa cấu hình tọa độ văn phòng"))

        q = QUEUE_STALE_MIN
        queued = conn.execute("SELECT count(*) FROM verification_jobs WHERE status = 'QUEUED' AND run_after < now() - make_interval(mins => %s)", (q,)).fetchone()[0]
        running = conn.execute("SELECT count(*) FROM verification_jobs WHERE status = 'RUNNING' AND locked_at < now() - interval '10 minutes'").fetchone()[0]
        failed = conn.execute("SELECT count(*) FROM verification_jobs WHERE status = 'FAILED' AND updated_at > now() - interval '24 hours'").fetchone()[0]
        out.append(Check("Hàng đợi xác minh", WARN if (queued or running) else OK,
                         f"{queued} job chờ quá {q} phút, {running} job treo (worker có đang chạy?)" if (queued or running) else "bình thường"))
        out.append(Check("Job xác minh lỗi (24h)", WARN if failed else OK, f"{failed} job"))
        up = conn.execute("SELECT count(*) FROM attendance_photos WHERE cloudinary_public_id IS NULL AND upload_attempts >= 5").fetchone()[0]
        out.append(Check("Upload Cloudinary", WARN if up else OK, f"{up} ảnh upload lỗi quá 5 lần" if up else "không có ảnh kẹt"))
        pr = conn.execute("SELECT count(*) FROM attendance_records WHERE final_status = 'PENDING_REVIEW' AND superseded_at IS NULL").fetchone()[0]
        out.append(Check("Chờ admin kiểm tra", OK, f"{pr} bản ghi"))
    return out


def check_disk(s: Settings) -> Check:
    free_mb = shutil.disk_usage(ROOT_DIR).free / 1024 / 1024
    level = FAIL if free_mb < 100 else WARN if free_mb < 500 else OK
    return Check("Dung lượng đĩa", level, f"còn {free_mb:.0f} MB")


def check_backup(s: Settings) -> Check:
    files = sorted(Path(s.backup_dir).glob("attendance_*.dump")) if Path(s.backup_dir).is_dir() else []
    return evaluate_backup_age(max((f.stat().st_mtime for f in files), default=None), time.time())


def check_services() -> list[Check]:
    if not shutil.which("systemctl"):
        return [Check("Dịch vụ systemd", OK, "bỏ qua (không có systemctl)")]
    out = []
    for unit in SERVICES:
        state = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip() or "unknown"
        if state == "inactive" and subprocess.run(["systemctl", "cat", unit], capture_output=True).returncode != 0:
            continue  # chưa cài dịch vụ này (chạy tay)
        out.append(Check(f"Dịch vụ {unit.removeprefix('telegram-attendance-')}", OK if state == "active" else FAIL, state))
    return out or [Check("Dịch vụ systemd", OK, "chưa cài (chạy tay)")]


def _http(url: str, timeout: int = 10, proxy_url: str | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "attendance-doctor/1.0"})
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        if proxy_url
        else urllib.request
    )
    with opener.urlopen(req, timeout=timeout) as r:
        return r.read(200_000)


def check_telegram(s: Settings) -> Check:
    try:
        data = json.loads(_http(f"https://api.telegram.org/bot{s.bot_token}/getMe", proxy_url=s.telegram_proxy or None))
    except urllib.error.HTTPError as exc:
        return Check("Telegram", FAIL, f"HTTP {exc.code} (BOT_TOKEN sai?)")
    except Exception as exc:  # noqa: BLE001 - không đưa URL (chứa token) vào thông báo
        return Check("Telegram", FAIL, f"không kết nối được ({type(exc).__name__})")
    return Check("Telegram", OK, f"bot @{data['result'].get('username')}") if data.get("ok") else Check("Telegram", FAIL, "getMe không ok")


def check_cloudinary(s: Settings) -> Check:
    import cloudinary
    import cloudinary.api

    cloudinary.config(cloud_name=s.cloudinary_cloud_name, api_key=s.cloudinary_api_key,
                      api_secret=s.cloudinary_api_secret, secure=True)
    cloudinary.api.ping()
    return Check("Cloudinary", OK, f"cloud {s.cloudinary_cloud_name}, thư mục '{s.cloudinary_folder}'")


def check_timemark(s: Settings) -> Check:
    try:
        _http(s.timemark_url)
    except urllib.error.HTTPError as exc:
        return Check("Trang Timemark", WARN if exc.code in (403, 429) else FAIL, f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return Check("Trang Timemark", FAIL, f"không truy cập được ({type(exc).__name__})")
    return Check("Trang Timemark", OK, "truy cập được")


def check_playwright(s: Settings) -> Check:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True, args=["--no-sandbox"] if s.timemark_no_sandbox else []).close()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        hint = ("chạy: .venv/bin/playwright install chromium" if "Executable doesn't exist" in msg
                else "đặt TIMEMARK_NO_SANDBOX=true (sandbox bị chặn)" if "sandbox" in msg.lower()
                else "chạy: sudo .venv/bin/playwright install-deps chromium")
        return Check("Chromium (Playwright)", FAIL, hint)
    return Check("Chromium (Playwright)", OK, "khởi động được")


# ---------------------------------------------------------------- điều phối
def run_doctor(s: Settings, alert: bool = False, quick: bool = False) -> int:
    checks: list[Check] = [check_config(s)]
    checks += _safe("PostgreSQL", lambda: check_database(s)) if s.database_url else []
    checks += _safe("Dung lượng đĩa", lambda: check_disk(s))
    checks += _safe("Sao lưu", lambda: check_backup(s))
    checks += _safe("Dịch vụ systemd", check_services)
    if not quick:
        if s.bot_token:
            checks += _safe("Telegram", lambda: check_telegram(s))
        if s.cloudinary_cloud_name:
            checks += _safe("Cloudinary", lambda: check_cloudinary(s))
        checks += _safe("Trang Timemark", lambda: check_timemark(s))
        checks += _safe("Chromium (Playwright)", lambda: check_playwright(s))

    print(format_report(checks))
    if alert:
        state_file = ROOT_DIR / "tmp" / "doctor_state.json"
        try:
            prev = json.loads(state_file.read_text())
        except (OSError, ValueError):
            prev = None
        send, new_state = decide_alert(prev, fingerprint(checks), time.time())
        if send:
            send_admins(s, alert_text(checks))
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(new_state))
        except OSError as exc:
            log.warning("Không ghi được trạng thái cảnh báo: %s", exc)
    return 1 if any(c.level == FAIL for c in checks) else 0
