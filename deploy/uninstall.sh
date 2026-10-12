#!/usr/bin/env bash
# Gỡ dịch vụ systemd (GIỮ NGUYÊN mã nguồn, .env, .venv, CSDL và bản sao lưu). Chạy: sudo ./deploy/uninstall.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Cần chạy bằng sudo." >&2; exit 1; }
NAMES=(telegram-attendance-bot.service telegram-attendance-worker.service telegram-attendance-dashboard.service
       telegram-attendance-backup.timer telegram-attendance-doctor.timer
       telegram-attendance-backup.service telegram-attendance-doctor.service "telegram-attendance-alert@.service")
for u in "${NAMES[@]}"; do
  systemctl disable --now "$u" 2>/dev/null || true
  rm -f "/etc/systemd/system/$u"
done
systemctl daemon-reload
echo "Đã gỡ các dịch vụ."
