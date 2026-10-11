"""Đăng nhập admin (mã một lần qua Telegram) và thanh bên. Người không đăng nhập vẫn xem được, chỉ không sửa được."""
from __future__ import annotations

import time

import streamlit as st

from dashboard import common
from services import dashboard_auth, notifier

SESSION_HOURS = 8
_KEY = "_auth"
_GENERIC = "Nếu ID là admin đã nhắn /start riêng cho bot, mã đã được gửi qua Telegram."


def current() -> dict | None:
    """Admin đang đăng nhập (kiểm tra lại hạn phiên và việc còn là admin trong CSDL mỗi lần vẽ trang)."""
    a = st.session_state.get(_KEY)
    if not a:
        return None
    editor = dashboard_auth.get_editor(a["telegram_id"]) if time.time() - a["at"] <= SESSION_HOURS * 3600 else None
    if editor is None:
        st.session_state.pop(_KEY, None)
        return None
    return {**a, "role": editor["role"]}


def logout() -> None:
    for k in (_KEY, "_login_step", "_login_tg"):
        st.session_state.pop(k, None)
    st.rerun()


def _ask_code() -> None:
    with st.form("login_ask"):
        raw = st.text_input("Telegram User ID")
        if st.form_submit_button("Gửi mã qua Telegram"):
            try:
                tg = int(raw.strip())
                if tg <= 0:
                    raise ValueError
            except ValueError:
                st.error("Telegram User ID phải là số.")
                return
            s = common.settings()
            if not s.bot_token:
                st.error("Chưa cấu hình BOT_TOKEN nên không gửi được mã.")
                return
            res = dashboard_auth.issue_code(tg)
            if res.kind == "RATE_LIMITED":
                st.error("Yêu cầu quá nhiều lần, vui lòng thử lại sau ít phút.")
                return
            if res.code:
                notifier.reply(
                    s.bot_token, tg, None,
                    f"🔐 Mã đăng nhập dashboard: {res.code}\nHiệu lực {dashboard_auth.CODE_TTL_MINUTES} phút. "
                    "Nếu không phải bạn yêu cầu, hãy bỏ qua tin nhắn này.",
                )
            st.session_state["_login_step"], st.session_state["_login_tg"] = "code", tg
            st.rerun()


def _enter_code() -> None:
    tg = st.session_state["_login_tg"]
    st.caption(_GENERIC)
    with st.form("login_code"):
        code = st.text_input("Mã 6 số", max_chars=6)
        c = st.columns(2)
        ok = c[0].form_submit_button("Đăng nhập", type="primary")
        back = c[1].form_submit_button("Quay lại")
    if back:
        st.session_state.pop("_login_step", None)
        st.rerun()
    if ok:
        res = dashboard_auth.verify_code(tg, code)
        if res.kind == "OK":
            st.session_state[_KEY] = {"telegram_id": tg, "role": res.admin["role"], "at": time.time()}
            for k in ("_login_step", "_login_tg"):
                st.session_state.pop(k, None)
            st.cache_data.clear()
            st.rerun()
        elif res.kind == "LOCKED":
            st.error("Nhập sai quá số lần cho phép. Hãy yêu cầu mã mới.")
        elif res.kind == "INVALID":
            st.error("Mã không đúng.")
        else:
            st.error("Mã không hợp lệ hoặc đã hết hạn. Hãy yêu cầu mã mới.")


def sidebar() -> None:
    with st.sidebar:
        st.caption(f"Múi giờ: {common.tz()}")
        if st.button("🔄 Làm mới dữ liệu", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.divider()
        a = current()
        if a:
            st.success(f"Đã đăng nhập: {a['telegram_id']} ({a['role']})")
            if st.button("Đăng xuất", use_container_width=True):
                logout()
        else:
            st.caption("👁️ Chế độ chỉ xem (không cần đăng nhập).")
            with st.expander("🔐 Đăng nhập admin để sửa"):
                if st.session_state.get("_login_step") == "code":
                    _enter_code()
                else:
                    _ask_code()
