#!/usr/bin/env bash
# Cài đặt/cập nhật dịch vụ systemd CHẠY TRỰC TIẾP TỪ THƯ MỤC DỰ ÁN (không sao chép mã nguồn đi nơi khác).
# Debian/Ubuntu:  sudo ./deploy/install.sh [--user TÊN] [--no-browser] [--no-timers]
# Chạy lại an toàn: cập nhật thư viện + file unit rồi khởi động lại dịch vụ (dùng sau khi `git pull`).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER=""; NO_BROWSER=0; NO_TIMERS=0
die() { echo "LỖI: $*" >&2; exit 1; }
say() { echo -e "\n==> $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) RUN_USER="${2:-}"; shift 2 ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --no-timers) NO_TIMERS=1; shift ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) die "Tham số không hợp lệ: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Cần quyền root: sudo $0"
command -v systemctl >/dev/null || die "Không tìm thấy systemd"
[[ ! "$ROOT" =~ [^A-Za-z0-9_./@:+-] ]] || die "Đường dẫn dự án chỉ được chứa chữ/số và _ . / @ : + - : $ROOT"

RUN_USER="${RUN_USER:-${SUDO_USER:-}}"
[[ -n "$RUN_USER" && "$RUN_USER" != "root" ]] || RUN_USER="$(stat -c %U "$ROOT")"
[[ "$RUN_USER" != "root" ]] || die "Không chạy dịch vụ bằng root. Dùng --user TÊN_NGƯỜI_DÙNG"
id "$RUN_USER" >/dev/null 2>&1 || die "Người dùng không tồn tại: $RUN_USER"
run_as() { sudo -u "$RUN_USER" -H "$@"; }
echo "Thư mục dự án: $ROOT"; echo "Người dùng chạy dịch vụ: $RUN_USER"

say "Cài gói hệ thống"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql-client ca-certificates curl
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "Cần Python >= 3.9 (Debian 11+/Ubuntu 22.04+)"

say "Thư mục chạy và .env"
for d in logs tmp tmp/photos screenshots backups; do install -d -o "$RUN_USER" -m 0750 "$ROOT/$d"; done
chmod 700 "$ROOT/tmp" "$ROOT/backups"
if [[ ! -f "$ROOT/.env" ]]; then
  install -o "$RUN_USER" -m 0600 "$ROOT/.env.example" "$ROOT/.env"
  die "Đã tạo $ROOT/.env từ .env.example. Hãy điền DATABASE_URL, BOT_TOKEN, ADMIN_TELEGRAM_IDS, CLOUDINARY_* rồi chạy lại."
fi
chown "$RUN_USER" "$ROOT/.env"; chmod 600 "$ROOT/.env"

say "Môi trường Python (.venv trong thư mục dự án)"
[[ -x "$ROOT/.venv/bin/python" ]] || run_as python3 -m venv "$ROOT/.venv"
run_as "$ROOT/.venv/bin/pip" install --quiet --upgrade pip wheel
run_as "$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"

if [[ $NO_BROWSER -eq 0 ]]; then
  say "Trình duyệt Chromium cho Playwright"
  "$ROOT/.venv/bin/playwright" install-deps chromium
  run_as "$ROOT/.venv/bin/playwright" install chromium
fi

say "Kiểm tra kết nối CSDL và áp dụng migration"
(cd "$ROOT" && run_as "$ROOT/.venv/bin/python" main.py migrate) || die "Không migrate được: kiểm tra DATABASE_URL trong .env và PostgreSQL đã chạy chưa"

say "Cài file systemd"
UNITS=(
  "bot.service:telegram-attendance-bot.service"
  "worker.service:telegram-attendance-worker.service"
  "dashboard.service:telegram-attendance-dashboard.service"
  "alert@.service:telegram-attendance-alert@.service"
  "backup.service:telegram-attendance-backup.service"
  "backup.timer:telegram-attendance-backup.timer"
  "doctor.service:telegram-attendance-doctor.service"
  "doctor.timer:telegram-attendance-doctor.timer"
)
for pair in "${UNITS[@]}"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  sed -e "s|@PROJECT_DIR@|$ROOT|g" -e "s|@SERVICE_USER@|$RUN_USER|g" "$ROOT/deploy/systemd/$src" > "/etc/systemd/system/$dst"
  chmod 644 "/etc/systemd/system/$dst"
done
chmod +x "$ROOT"/deploy/*.sh
systemctl daemon-reload

say "Bật và (khởi động lại) dịch vụ"
systemctl enable telegram-attendance-bot telegram-attendance-worker telegram-attendance-dashboard
systemctl restart telegram-attendance-bot telegram-attendance-worker telegram-attendance-dashboard
if [[ $NO_TIMERS -eq 0 ]]; then
  systemctl enable --now telegram-attendance-backup.timer telegram-attendance-doctor.timer
fi

sleep 4
systemctl --no-pager --lines=0 status telegram-attendance-bot telegram-attendance-worker telegram-attendance-dashboard || true
cat <<MSG

Xong. Lệnh thường dùng:
  journalctl -u telegram-attendance-bot -f          # xem log (thay bot bằng worker / dashboard)
  sudo systemctl restart telegram-attendance-bot    # khởi động lại
  $ROOT/.venv/bin/python main.py doctor             # kiểm tra sức khỏe
  $ROOT/deploy/backup.sh                            # sao lưu ngay
Dashboard: http://<IP máy chủ>:8501   (xem docs/VAN_HANH.md)
MSG
