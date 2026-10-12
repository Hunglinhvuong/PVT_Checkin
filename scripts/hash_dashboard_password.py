"""Tạo giá trị password_hash để đặt trong Streamlit Secrets."""
from __future__ import annotations

import getpass

from dashboard.passwords import hash_password


def main() -> int:
    password = getpass.getpass("Mật khẩu dashboard: ")
    confirmation = getpass.getpass("Nhập lại mật khẩu: ")
    if password != confirmation:
        print("Mật khẩu nhập lại không khớp.")
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
