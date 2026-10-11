"""Logging ra console + file xoay vòng, che bí mật phổ biến."""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r"(bot)?\d{6,}:[A-Za-z0-9_-]{30,}"), "***BOT_TOKEN***"),
    (re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"), r"\1***\2"),
    (re.compile(r"(api_secret[=:]\s*)\S+", re.I), r"\1***"),
]


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat, repl in _SECRET_PATTERNS:
            msg = pat.sub(repl, msg)
        record.msg, record.args = msg, ()
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
            )
        )
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        root.addHandler(h)
    for noisy in ("psycopg.pool", "httpx", "httpcore"):  # httpx log URL chứa bot token
        logging.getLogger(noisy).setLevel(logging.WARNING)
