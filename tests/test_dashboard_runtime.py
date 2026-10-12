import os

from dashboard.runtime import apply_streamlit_secrets


def test_streamlit_secrets_populate_missing_environment(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    apply_streamlit_secrets({"DATABASE_URL": "postgresql://db.example/attendance", "dashboard_users": [{"username": "a"}]})
    assert os.environ["DATABASE_URL"] == "postgresql://db.example/attendance"


def test_streamlit_secrets_replace_empty_environment_value(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    apply_streamlit_secrets({"DATABASE_URL": "postgresql://db.example/attendance"})
    assert os.environ["DATABASE_URL"] == "postgresql://db.example/attendance"


def test_streamlit_secrets_do_not_override_nonempty_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env.example/attendance")
    apply_streamlit_secrets({"DATABASE_URL": "postgresql://secrets.example/attendance"})
    assert os.environ["DATABASE_URL"] == "postgresql://env.example/attendance"
