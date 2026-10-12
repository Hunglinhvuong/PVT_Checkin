#!/usr/bin/env bash
# Chạy toàn bộ test. Test tích hợp cần TEST_DATABASE_URL (CSDL riêng, bị XÓA SẠCH schema public).
# Chạy: ./deploy/run_tests.sh [tham số pytest]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "Chưa có .venv: chạy sudo ./deploy/install.sh trước (hoặc python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)" >&2; exit 2; }
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-$(envval TEST_DATABASE_URL "")}"
if [[ -z "$TEST_DATABASE_URL" ]]; then
  echo "CẢNH BÁO: chưa đặt TEST_DATABASE_URL, các test tích hợp (PostgreSQL) sẽ bị bỏ qua." >&2
fi
exec "$ROOT/.venv/bin/python" -m pytest -q "$@"
