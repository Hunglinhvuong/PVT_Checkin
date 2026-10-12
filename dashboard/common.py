"""Tiện ích dùng chung cho các trang dashboard (Streamlit)."""
from __future__ import annotations

import contextlib
from datetime import datetime

import pandas as pd
import streamlit as st

from config.settings import ConfigError, Settings, load_settings
from dashboard.util import label
from database.connection import init_pool
from repositories import audit
from services import photo_storage


@st.cache_resource
def settings() -> Settings:
    return load_settings()


@st.cache_resource
def init_backend() -> bool:
    s = settings()
    init_pool(s)
    try:
        photo_storage.configure(s)  # để tạo URL có chữ ký xem ảnh Cloudinary
    except ConfigError:
        pass
    return True


def tz() -> str:
    return settings().timezone


def cached(fn):
    """Cache truy vấn đọc 10 giây (nút 'Làm mới' và mọi thao tác ghi sẽ xóa cache)."""
    return st.cache_data(ttl=10, show_spinner=False)(fn)


@contextlib.contextmanager
def dashboard_source():
    """Các thao tác ghi trong khối này được audit với actor_type='DASHBOARD'."""
    token = audit.SOURCE.set("DASHBOARD")
    try:
        yield
    finally:
        audit.SOURCE.reset(token)


def flash(kind: str, text: str) -> None:
    st.session_state.setdefault("_flash", []).append((kind, text))


def show_flash() -> None:
    for kind, text in st.session_state.pop("_flash", []):
        getattr(st, kind)(text)


def done(kind: str, text: str) -> None:
    """Báo kết quả thao tác ghi, xóa cache và vẽ lại trang."""
    flash(kind, text)
    st.cache_data.clear()
    st.rerun()


def need_admin() -> int | None:
    """Telegram ID của admin đã đăng nhập (được phép sửa); nếu chưa đăng nhập thì gợi ý và trả None."""
    from dashboard import auth  # import muộn để tránh vòng phụ thuộc

    admin = auth.current()
    if admin is None:
        st.caption("🔒 Chế độ chỉ xem. Admin đăng nhập ở thanh bên để thao tác.")
        return None
    return admin["telegram_id"]


def localize(df: pd.DataFrame, cols: list[str], fmt: str = "%d/%m/%Y %H:%M:%S") -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            s = pd.to_datetime(df[c], utc=True, errors="coerce").dt.tz_convert(tz())
            df[c] = s.dt.strftime(fmt).fillna("")
    return df


def fmt_dt(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M:%S") -> str:
    if value is None:
        return ""
    return pd.Timestamp(value).tz_convert(tz()).strftime(fmt) if value.tzinfo else value.strftime(fmt)


def photo_url(public_id: str | None) -> str | None:
    if not public_id:
        return None
    try:
        return photo_storage.signed_url(public_id)
    except Exception:  # noqa: BLE001 - thiếu cấu hình/thư viện Cloudinary
        return None


def label_col(df: pd.DataFrame, col: str, mapping: dict[str, str]) -> None:
    if col in df.columns:
        df[col] = df[col].map(lambda v: label(mapping, v))
