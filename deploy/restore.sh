#!/usr/bin/env bash
# Khôi phục PostgreSQL từ bản sao lưu: ./deploy/restore.sh backups/attendance_YYYYmmdd_HHMMSS.dump [--yes]
# Dừng dịch vụ, sao lưu DB hiện tại (phòng hờ), khôi phục trong một transaction, migrate, khởi động lại dịch vụ.
set -euo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FILE="${1:-}"; ASSUME_YES=0
[[ "${2:-}" == "--yes" ]] && ASSUME_YES=1
[[ -n "$FILE" && -f "$FILE" ]] || { echo "Cú pháp: $0 <file.dump> [--yes]" >&2; exit 2; }
pg_restore --list "$FILE" > /dev/null || { echo "File sao lưu không hợp lệ: $FILE" >&2; exit 2; }

load_pg_env
echo "SẼ GHI ĐÈ cơ sở dữ liệu '$PGDATABASE' trên $PGHOST bằng: $FILE"
if [[ $ASSUME_YES -ne 1 ]]; then
  read -r -p "Gõ đúng tên CSDL ($PGDATABASE) để xác nhận: " ANSWER
  [[ "$ANSWER" == "$PGDATABASE" ]] || { echo "Đã hủy."; exit 1; }
fi

SUDO=""; [[ $EUID -eq 0 ]] || SUDO="sudo"
UNITS=(telegram-attendance-bot telegram-attendance-worker telegram-attendance-dashboard)
HAVE_UNITS=0
if command -v systemctl >/dev/null && systemctl cat "${UNITS[0]}" >/dev/null 2>&1; then
  HAVE_UNITS=1
  echo "Dừng dịch vụ..."; $SUDO systemctl stop "${UNITS[@]}"
fi

echo "Sao lưu DB hiện tại trước khi ghi đè..."
"$ROOT/deploy/backup.sh"

echo "Khôi phục..."
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error -d "$PGDATABASE" "$FILE"
"$PY" "$ROOT/main.py" migrate

if [[ $HAVE_UNITS -eq 1 ]]; then
  echo "Khởi động dịch vụ..."; $SUDO systemctl start "${UNITS[@]}"
fi
echo "Khôi phục xong."
