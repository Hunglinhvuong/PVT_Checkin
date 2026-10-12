"""Kiểm tra tĩnh các file triển khai (không cần systemd/root)."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITS = ROOT / "deploy" / "systemd"
SCRIPTS = sorted((ROOT / "deploy").glob("*.sh"))


def test_unit_placeholders_are_known():
    for f in UNITS.iterdir():
        assert set(re.findall(r"@[A-Z_]+@", f.read_text(encoding="utf-8"))) <= {"@PROJECT_DIR@", "@SERVICE_USER@"}, f.name


def test_services_run_from_project_dir_with_project_venv():
    for name in ("bot", "worker", "dashboard"):
        t = (UNITS / f"{name}.service").read_text(encoding="utf-8")
        assert "WorkingDirectory=@PROJECT_DIR@" in t
        assert f"ExecStart=@PROJECT_DIR@/.venv/bin/python main.py {name}" in t
        assert "User=@SERVICE_USER@" in t and "Restart=always" in t and "StartLimitIntervalSec=0" in t
        assert "Environment=LOG_TO_FILE=false" in t  # nhiều tiến trình không cùng xoay vòng một file log
        assert "/opt/" not in t and "/usr/local/" not in t  # không sao chép mã nguồn đi nơi khác
    dashboard = (UNITS / "dashboard.service").read_text(encoding="utf-8")
    assert "After=network-online.target" in dashboard
    assert "postgresql.service" not in dashboard


def test_backup_failure_triggers_alert_and_timer_is_persistent():
    assert "OnFailure=telegram-attendance-alert@%n.service" in (UNITS / "backup.service").read_text(encoding="utf-8")
    assert "Persistent=true" in (UNITS / "backup.timer").read_text(encoding="utf-8")
    assert "doctor --alert" in (UNITS / "doctor.service").read_text(encoding="utf-8")


def test_install_script_maps_every_unit_template():
    install = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    for f in UNITS.iterdir():
        assert f"{f.name}:telegram-attendance-" in install, f.name
    assert '--dashboard-only' in install
    assert 'INSTALL_UNITS=("dashboard.service:telegram-attendance-dashboard.service")' in install


def test_scripts_are_valid_bash_and_executable():
    assert {p.name for p in SCRIPTS} >= {"install.sh", "uninstall.sh", "backup.sh", "restore.sh", "setup_postgres.sh", "run_tests.sh", "lib.sh"}
    for p in SCRIPTS:
        subprocess.run(["bash", "-n", str(p)], check=True)
        if p.name != "lib.sh":
            assert os.access(p, os.X_OK), f"{p.name} chưa có quyền thực thi"
            assert "set -euo pipefail" in p.read_text(encoding="utf-8"), p.name


def test_lib_parses_database_url_and_env_values_safely():
    env = ROOT / "tmp_test.env"
    env.write_text(
        'DATABASE_URL=postgresql://us%40er:p%3Ass%23w@db.local:5433/mydb?sslmode=require\n'
        'OFFICE_NAME=Văn phòng chính\nBACKUP_DIR="/var/bk dir"\n', encoding="utf-8")
    try:
        script = (
            f'ENV_FILE={env}; source {ROOT}/deploy/lib.sh; load_pg_env; '
            'echo "$PGUSER|$PGPASSWORD|$PGHOST|$PGPORT|$PGDATABASE|$PGSSLMODE"; '
            'echo "$(envval OFFICE_NAME x)|$(envval BACKUP_DIR x)|$(envval MISSING mặc-định)"'
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.splitlines()
    finally:
        env.unlink()
    assert out[0] == "us@er|p:ss#w|db.local|5433|mydb|require"
    assert out[1] == "Văn phòng chính|/var/bk dir|mặc-định"  # .env có khoảng trắng/dấu nháy không làm hỏng script
