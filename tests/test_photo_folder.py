from datetime import date

import pytest

from config.settings import ConfigError, validate_folder
from services import photo_storage as ps

ROW = {"attendance_date": date(2026, 10, 12), "user_id": 7, "session": "MORNING", "attendance_record_id": 42}


@pytest.fixture(autouse=True)
def reset():
    yield
    ps.set_folder("attendance")


def test_default_public_id_unchanged():
    assert ps.build_public_id(ROW) == "attendance/2026-10-12/7_MORNING_42"


def test_public_id_uses_configured_folder_and_asset_folder():
    ps.set_folder("chamcong/2026")
    pid = ps.build_public_id(ROW)
    assert pid == "chamcong/2026/2026-10-12/7_MORNING_42"
    o = ps._upload_options(pid)
    assert o["asset_folder"] == "chamcong/2026/2026-10-12"
    assert (o["type"], o["overwrite"], o["unique_filename"]) == ("authenticated", False, False)


def test_asset_folder_can_be_disabled():
    ps.set_folder("x", set_asset_folder=False)
    assert "asset_folder" not in ps._upload_options(ps.build_public_id(ROW))


@pytest.mark.parametrize("raw,expected", [("attendance", "attendance"), ("/cham-cong/2026/", "cham-cong/2026"), ("", "attendance"), (None, "attendance")])
def test_validate_folder_ok(raw, expected):
    assert validate_folder(raw) == expected


@pytest.mark.parametrize("raw", ["a b", "cham.cong", "../x", "a//b", "chấm công", "a" * 121])
def test_validate_folder_rejects(raw):
    with pytest.raises(ConfigError):
        validate_folder(raw)
    with pytest.raises(ConfigError):
        ps.set_folder(raw)
