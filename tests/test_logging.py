import logging

from config.logging_config import RedactFilter


def _msg(text):
    rec = logging.LogRecord("t", logging.INFO, "", 0, text, (), None)
    RedactFilter().filter(rec)
    return rec.getMessage()


def test_redact_db_password():
    assert "secret" not in _msg("postgresql://u:secret@host/db")


def test_redact_bot_token():
    token = "123456789:" + "A" * 35
    assert token not in _msg(f"token={token}")
