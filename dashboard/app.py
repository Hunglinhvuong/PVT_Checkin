"""Dashboard quản trị (Streamlit).  Chạy: python main.py dashboard   (hoặc streamlit run dashboard/app.py)
Xem: không cần đăng nhập.  Sửa: admin đăng nhập bằng mã gửi qua Telegram."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Điểm danh Telegram", page_icon="🗓️", layout="wide")

from dashboard import auth, common  # noqa: E402
from dashboard.views import (  # noqa: E402
    admins_view, attendance, audit_view, calendar_view, employees, overview, photos, verification,
)

common.init_backend()
auth.sidebar()

pages = [
    st.Page(overview.render, title="Tổng quan", icon="📊", url_path="tong-quan", default=True),
    st.Page(attendance.render, title="Điểm danh", icon="✅", url_path="diem-danh"),
    st.Page(verification.render, title="Xác minh ảnh", icon="🔍", url_path="xac-minh-anh"),
    st.Page(employees.render, title="Nhân viên", icon="👥", url_path="nhan-vien"),
    st.Page(calendar_view.render, title="Lịch làm việc", icon="📅", url_path="lich-lam-viec"),
    st.Page(photos.render, title="Tra cứu ảnh", icon="🖼️", url_path="tra-cuu-anh"),
    st.Page(audit_view.render, title="Nhật ký", icon="📜", url_path="nhat-ky"),
]
me = auth.current()
if me and me["role"] == "SUPER_ADMIN":
    pages.append(st.Page(admins_view.render, title="Quản trị admin", icon="🛡️", url_path="quan-tri-admin"))
st.navigation(pages).run()
