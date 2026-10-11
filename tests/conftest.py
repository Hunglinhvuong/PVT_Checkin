"""An toàn cho test tích hợp: chúng XÓA SẠCH schema public của TEST_DATABASE_URL."""
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from dotenv import load_dotenv


def pytest_configure(config):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url:
        return
    name = urlparse(url).path.lstrip("/")
    if "test" not in name.lower() or url == os.getenv("DATABASE_URL", ""):
        pytest.exit(
            "TEST_DATABASE_URL phải trỏ tới CSDL riêng cho test (tên chứa 'test' và khác DATABASE_URL): "
            "các test tích hợp XÓA SẠCH schema public.", returncode=2)
