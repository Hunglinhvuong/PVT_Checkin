#!/usr/bin/env bash
# Sao lưu PostgreSQL (pg_dump -Fc) vào BACKUP_DIR và xóa bản cũ hơn BACKUP_KEEP_DAYS ngày.
# Chạy tay: ./deploy/backup.sh   (systemd timer chạy hằng ngày 02:30)
set -euo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

load_pg_env
BACKUP_DIR="$(abs_path "$(envval BACKUP_DIR backups)")"
KEEP_DAYS="$(envval BACKUP_KEEP_DAYS 14)"
[[ "$KEEP_DAYS" =~ ^[0-9]+$ && "$KEEP_DAYS" -ge 1 ]] || { echo "BACKUP_KEEP_DAYS không hợp lệ: $KEEP_DAYS" >&2; exit 2; }

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
TMP="$BACKUP_DIR/.attendance_${STAMP}.dump.partial"
OUT="$BACKUP_DIR/attendance_${STAMP}.dump"
trap 'rm -f "$TMP"' EXIT

pg_dump --format=custom --no-owner --no-privileges --file="$TMP"
pg_restore --list "$TMP" > /dev/null                      # file đọc được và có cấu trúc hợp lệ
[[ "$(stat -c %s "$TMP")" -gt 1024 ]] || { echo "Bản sao lưu quá nhỏ, nghi ngờ lỗi" >&2; exit 3; }
mv "$TMP" "$OUT"
find "$BACKUP_DIR" -maxdepth 1 -name 'attendance_*.dump' -mtime +"$KEEP_DAYS" -delete

echo "Sao lưu xong: $OUT ($(du -h "$OUT" | cut -f1)); giữ ${KEEP_DAYS} ngày; hiện có $(find "$BACKUP_DIR" -maxdepth 1 -name 'attendance_*.dump' | wc -l) bản"
