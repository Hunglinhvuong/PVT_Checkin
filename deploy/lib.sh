# Hàm dùng chung cho các script trong deploy/ (được source, không chạy trực tiếp).
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

# envval KHÓA MẶC_ĐỊNH — đọc một khóa từ .env (không `source` để tránh lỗi với giá trị có khoảng trắng/ký tự đặc biệt)
envval() {
  local v
  v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  v="${v%$'\r'}"; v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  if [[ -n "$v" ]]; then printf '%s' "$v"; else printf '%s' "$2"; fi
}

abs_path() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s' "$ROOT/$1" ;; esac; }

# Đặt PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE từ DATABASE_URL (mật khẩu không xuất hiện trên dòng lệnh/ps)
load_pg_env() {
  local url
  url="$(envval DATABASE_URL "")"
  [[ -n "$url" ]] || { echo "Thiếu DATABASE_URL trong $ENV_FILE" >&2; return 2; }
  eval "$("$PY" - "$url" <<'PYEOF'
import shlex, sys
from urllib.parse import parse_qs, unquote, urlparse

u = urlparse(sys.argv[1])
env = {
    "PGHOST": u.hostname or "localhost",
    "PGPORT": str(u.port or 5432),
    "PGUSER": unquote(u.username or ""),
    "PGPASSWORD": unquote(u.password or ""),
    "PGDATABASE": (u.path or "/").lstrip("/"),
}
q = parse_qs(u.query)
if "sslmode" in q:
    env["PGSSLMODE"] = q["sslmode"][0]
for k, v in env.items():
    if v:
        print(f"export {k}={shlex.quote(v)}")
PYEOF
)"
}
