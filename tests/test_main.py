from types import SimpleNamespace

import main


def test_dashboard_start_does_not_run_migrations(monkeypatch):
    settings = SimpleNamespace(
        database_url="postgresql://db/attendance",
        log_level="INFO",
        log_dir=None,
        log_to_file=False,
        dashboard_host="0.0.0.0",
        dashboard_port=8501,
    )
    migrated = []
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main, "setup_logging", lambda *args: None)
    monkeypatch.setattr(main, "migrate", lambda url: migrated.append(url))
    monkeypatch.setattr(main.subprocess, "call", lambda command: 0)

    assert main.main(["dashboard"]) == 0
    assert migrated == []
