"""Đăng nhập dashboard riêng và thanh bên. Người không đăng nhập vẫn xem được, chỉ không sửa được."""
from __future__ import annotations

import time

import streamlit as st

from dashboard import common
from dashboard.passwords import DashboardAuthConfigError, DashboardUser, parse_dashboard_users, verify_password
from services import dashboard_auth

SESSION_HOURS = 8
_KEY = "_auth"
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
_LOCKED_UNTIL = "_login_locked_until"
_LOGIN_ATTEMPTS = "_login_attempts"


def _users() -> tuple[DashboardUser, ...]:
    return parse_dashboard_users(st.secrets.get("dashboard_users", ()))


def current() -> dict | None:
    """Admin đang đăng nhập (kiểm tra lại hạn phiên và việc còn là admin trong CSDL mỗi lần vẽ trang)."""
    a = st.session_state.get(_KEY)
    if not a:
        return None
    try:
        user = next(
            (u for u in _users() if u.username == a["username"] and u.telegram_id == a["telegram_id"]),
            None,
        )
    except DashboardAuthConfigError as exc:
        st.session_state.pop(_KEY, None)
        st.error(f"Cấu hình đăng nhập dashboard không hợp lệ: {exc}")
        return None
    valid_session = (
        user is not None
        and user.password_hash == a["password_hash"]
        and time.time() - a["at"] <= SESSION_HOURS * 3600
    )
    editor = dashboard_auth.get_editor(a["telegram_id"]) if valid_session else None
    if editor is None:
        st.session_state.pop(_KEY, None)
        return None
    return {**a, "role": editor["role"]}


def logout() -> None:
    for k in (_KEY, _LOGIN_ATTEMPTS, _LOCKED_UNTIL):
        st.session_state.pop(k, None)
    st.rerun()


def _login(users: tuple[DashboardUser, ...]) -> None:
    locked_until = st.session_state.get(_LOCKED_UNTIL, 0)
    if time.time() < locked_until:
        st.error("Đăng nhập tạm khóa do nhập sai nhiều lần. Vui lòng thử lại sau 5 phút.")
        return

    with st.form("dashboard_login"):
        username = st.text_input("Tên đăng nhập")
        password = st.text_input("Mật khẩu", type="password")
        submitted = st.form_submit_button("Đăng nhập", type="primary")
    if not submitted:
        return

    account = next((u for u in users if u.username == username.strip()), None)
    if account is None or not verify_password(password, account.password_hash):
        attempts = st.session_state.get(_LOGIN_ATTEMPTS, 0) + 1
        st.session_state[_LOGIN_ATTEMPTS] = attempts
        if attempts >= MAX_LOGIN_ATTEMPTS:
            st.session_state[_LOCKED_UNTIL] = time.time() + LOCKOUT_SECONDS
            st.error("Đăng nhập tạm khóa do nhập sai nhiều lần. Vui lòng thử lại sau 5 phút.")
        else:
            st.error("Tên đăng nhập hoặc mật khẩu không đúng.")
        return

    editor = dashboard_auth.record_login(account.telegram_id)
    if editor is None:
        st.error("Tài khoản chưa được cấp quyền admin đang hoạt động.")
        return
    st.session_state[_KEY] = {
        "username": account.username,
        "password_hash": account.password_hash,
        "telegram_id": account.telegram_id,
        "role": editor["role"],
        "at": time.time(),
    }
    st.session_state.pop(_LOGIN_ATTEMPTS, None)
    st.session_state.pop(_LOCKED_UNTIL, None)
    st.cache_data.clear()
    st.rerun()


def sidebar() -> None:
    with st.sidebar:
        st.caption(f"Múi giờ: {common.tz()}")
        if st.button("🔄 Làm mới dữ liệu", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.divider()
        a = current()
        if a:
            st.success(f"Đã đăng nhập: {a['username']} ({a['role']})")
            if st.button("Đăng xuất", use_container_width=True):
                logout()
        else:
            st.caption("👁️ Chế độ chỉ xem (không cần đăng nhập).")
            with st.expander("🔐 Đăng nhập admin để sửa"):
                try:
                    users = _users()
                except DashboardAuthConfigError as exc:
                    st.error(f"Cấu hình đăng nhập dashboard không hợp lệ: {exc}")
                    return
                if not users:
                    st.error("Chưa cấu hình tài khoản dashboard_users trong Streamlit Secrets.")
                    return
                _login(users)
