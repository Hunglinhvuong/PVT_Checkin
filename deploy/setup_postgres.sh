#!/usr/bin/env bash
# (Tùy chọn) Cài PostgreSQL cục bộ và tạo role + CSDL: attendance, attendance_test. Chạy: sudo ./deploy/setup_postgres.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Cần chạy bằng sudo." >&2; exit 1; }
DB_USER="${DB_USER:-attendance}"; DB_NAME="${DB_NAME:-attendance}"; TEST_DB="${TEST_DB:-attendance_test}"
for n in "$DB_USER" "$DB_NAME" "$TEST_DB"; do [[ "$n" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "Tên không hợp lệ: $n" >&2; exit 2; }; done

export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get install -y postgresql postgresql-client openssl
systemctl enable --now postgresql

PASS=""
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  PASS="$(openssl rand -hex 16)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$PASS'"
fi
for db in "$DB_NAME" "$TEST_DB"; do
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || sudo -u postgres createdb -O "$DB_USER" "$db"
done

echo
if [[ -n "$PASS" ]]; then
  echo "Thêm vào .env (mật khẩu chỉ hiện một lần):"
  echo "DATABASE_URL=postgresql://$DB_USER:$PASS@localhost:5432/$DB_NAME"
  echo "TEST_DATABASE_URL=postgresql://$DB_USER:$PASS@localhost:5432/$TEST_DB"
else
  echo "Role '$DB_USER' đã tồn tại: giữ nguyên mật khẩu. CSDL '$DB_NAME' và '$TEST_DB' đã sẵn sàng."
fi
