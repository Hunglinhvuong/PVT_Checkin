"""Worker xác minh Timemark: tiến trình độc lập với bot (lỗi/timeout trình duyệt không làm treo bot).
Chỉ một worker chạy tại một thời điểm (advisory lock) + nghỉ tối thiểu giữa các lượt để không gửi dồn dập."""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

import psycopg

from config.settings import ConfigError, Settings
from database.connection import close_pool, init_pool
from services import notifier, photo_storage, verification
from services.timemark_verifier import TimemarkWebVerifier, VerificationOutcome, Verifier

log = logging.getLogger(__name__)
WORKER_LOCK_ID = 726_002
SUPPORTED_EXT = {"jpg", "jpeg", "png", "gif"}
MAX_PAGE_BYTES = 10 * 1024 * 1024
BLOCK_COOLDOWN_S = 30 * 60
POLL_S = 3


def _manual(reason: str) -> VerificationOutcome:
    return VerificationOutcome("MANUAL_REVIEW", error=reason)


def process_job(job: verification.ClaimedJob, verifier: Verifier, settings: Settings) -> VerificationOutcome:
    photo = job.photo or {}
    ext = (photo.get("file_ext") or "").lower()
    size = photo.get("file_size_bytes") or 0
    if ext not in SUPPORTED_EXT or size > MAX_PAGE_BYTES:
        return _manual(f"UNSUPPORTED_FOR_TIMEMARK_PAGE: định dạng={ext!r}, kích thước={size}")

    path = photo.get("temp_path")
    if not (path and Path(path).exists()):
        try:
            path = photo_storage.fetch_from_cloudinary(photo, settings.upload_tmp_dir)
        except Exception as exc:  # noqa: BLE001
            log.warning("Không lấy được file ảnh %s: %s", photo.get("id"), exc)
            return _manual(f"NO_IMAGE_FILE: {type(exc).__name__}: {exc}"[:500])

    try:
        return verifier.verify(path, tag=f"rec{job.record_id}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Lỗi không lường trước khi xác minh bản ghi %s", job.record_id)
        return VerificationOutcome("AUTOMATION_ERROR", error=f"{type(exc).__name__}: {exc}"[:500])


def run_worker(settings: Settings) -> int:
    init_pool(settings)
    try:
        photo_storage.configure(settings)
    except ConfigError:
        log.warning("Chưa cấu hình Cloudinary: không thể tải lại ảnh khi mất file tạm")

    lock_conn = psycopg.connect(settings.database_url, autocommit=True)
    try:
        if not lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (WORKER_LOCK_ID,)).fetchone()[0]:
            log.error("Đã có worker khác đang chạy, thoát.")
            return 1

        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        cooldown_until = 0.0
        log.info("Worker %s bắt đầu", worker_id)

        with TimemarkWebVerifier(
            settings.timemark_url, headless=settings.timemark_headless,
            result_timeout_s=settings.timemark_result_timeout_s, screenshot_dir=settings.screenshot_dir,
            no_sandbox=settings.timemark_no_sandbox,
        ) as verifier:
            while not stop.is_set():
                if time.time() < cooldown_until:
                    stop.wait(10)
                    continue
                try:
                    job = verification.claim_next(worker_id)
                    if job is None:
                        stop.wait(POLL_S)
                        continue
                    outcome = process_job(job, verifier, settings)
                    comp = verification.complete(job, outcome, settings.timezone)
                    log.info("Bản ghi %s: %s (lượt %d/%d, %s)", job.record_id, outcome.status, job.attempts,
                             job.max_attempts, comp.kind)
                    if comp.kind == "FINAL":
                        if job.photo:
                            photo_storage.cleanup_temp_if_done(job.photo["id"])
                        notifier.reply(
                            settings.bot_token, job.record["telegram_chat_id"], job.record["telegram_message_id"],
                            notifier.message_for(
                                comp.verification_status, comp.final_status, comp.reason, comp.deadline,
                                comp.resubmit_allowed, comp.capture_date, comp.expected_date,
                            ),
                        )
                    elif comp.reason == "PENDING_UPLOAD" and comp.first_retry:
                        notifier.reply(
                            settings.bot_token, job.record["telegram_chat_id"], job.record["telegram_message_id"],
                            notifier.PENDING_UPLOAD_NOTICE,
                        )
                    if outcome.blocked:
                        cooldown_until = time.time() + BLOCK_COOLDOWN_S
                        log.error("Trang Timemark chặn/yêu cầu CAPTCHA: tạm dừng %d phút, các ảnh chuyển xác minh thủ công",
                                  BLOCK_COOLDOWN_S // 60)
                    stop.wait(settings.timemark_min_interval_s)
                except Exception:  # noqa: BLE001
                    log.exception("Lỗi vòng lặp worker")
                    stop.wait(10)
        log.info("Worker dừng")
        return 0
    finally:
        lock_conn.close()
        close_pool()
