"""Entry point.  Lệnh: migrate | check | seed | bot | worker | dashboard | doctor | alert"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from config.logging_config import setup_logging
from config.settings import ConfigError, load_settings
from database.bootstrap import seed_admins, seed_office
from database.connection import check_connection
from database.migrate import migrate

NO_DB_COMMANDS = ("alert", "doctor")  # tự xử lý khi thiếu/hỏng DB


def _seed(s) -> None:
    seed_admins(s.database_url, s.admin_telegram_ids)
    if s.office_latitude is not None:
        seed_office(s.database_url, s.office_name, s.office_latitude, s.office_longitude)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hệ thống điểm danh Telegram")
    parser.add_argument("command", choices=["migrate", "check", "seed", "bot", "worker", "dashboard", "doctor", "alert"])
    parser.add_argument("rest", nargs="*", help="alert: nội dung cảnh báo")
    parser.add_argument("--alert", action="store_true", help="doctor: gửi cảnh báo Telegram khi có thay đổi/vấn đề (chế độ nhanh)")
    parser.add_argument("--quick", action="store_true", help="doctor: bỏ qua các kiểm tra chậm (mạng, trình duyệt)")
    args = parser.parse_args(argv)

    try:
        s = load_settings()
        setup_logging(s.log_level, s.log_dir if s.log_to_file else None)
        log = logging.getLogger("main")
        if args.command not in NO_DB_COMMANDS and not s.database_url:
            raise ConfigError("DATABASE_URL chưa được cấu hình")

        if args.command == "check":
            info = check_connection(s)
            log.info("Kết nối OK: %s", info["version"])
        elif args.command == "migrate":
            applied = migrate(s.database_url)
            log.info("Đã áp dụng: %s", applied or "không có migration mới")
        elif args.command == "seed":
            _seed(s)
        elif args.command == "bot":
            s.require("bot_token", "cloudinary_cloud_name", "cloudinary_api_key", "cloudinary_api_secret")
            if not s.admin_telegram_ids:
                raise ConfigError("ADMIN_TELEGRAM_IDS chưa được cấu hình (cần ít nhất một admin)")
            migrate(s.database_url)
            _seed(s)
            from bot.app import run_bot  # import muộn: lệnh khác không cần python-telegram-bot

            run_bot(s)
        elif args.command == "worker":
            migrate(s.database_url)
            from workers.verification_worker import run_worker  # import muộn: cần playwright

            return run_worker(s)
        elif args.command == "dashboard":
            migrate(s.database_url)
            app = Path(__file__).resolve().parent / "dashboard" / "app.py"
            return subprocess.call([
                sys.executable, "-m", "streamlit", "run", str(app),
                "--server.address", s.dashboard_host, "--server.port", str(s.dashboard_port),
                "--server.headless", "true", "--browser.gatherUsageStats", "false",
            ])
        elif args.command == "doctor":
            from ops.doctor import run_doctor

            return run_doctor(s, alert=args.alert, quick=args.quick or args.alert)
        elif args.command == "alert":
            from ops.alerts import send_admins

            return 0 if send_admins(s, " ".join(args.rest) or "Cảnh báo từ hệ thống điểm danh") else 1
        return 0
    except ConfigError as exc:
        print(f"[CẤU HÌNH] {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("main").exception("Lỗi: %s", exc)
    return 1


if __name__ == "__main__":
    sys.exit(main())
