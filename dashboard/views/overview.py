import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import FINAL, SESSION, WEEKDAY


def render() -> None:
    common.show_flash()
    st.title("📊 Tổng quan")
    tz = common.tz()
    d = st.date_input("Ngày", value=data.local_today(tz), format="DD/MM/YYYY")

    ov = common.cached(data.overview)(d, tz)
    if not ov["working"]:
        _, why = common.cached(data.working_day_info)(d)
        st.info(f"{WEEKDAY[d.weekday()]} {d:%d/%m/%Y} không phải ngày làm việc" + (f" ({why})." if why else "."))

    c = st.columns(4)
    c[0].metric("Nhân viên đã duyệt", ov["users"].get("APPROVED", 0))
    c[1].metric("Đăng ký chờ duyệt", ov["users"].get("PENDING", 0))
    c[2].metric("Bản ghi chờ kiểm tra", ov["pending_review"])
    c[3].metric("Đang chờ xác minh", ov["pending_verification"])

    for session in ("MORNING", "AFTERNOON"):
        st.subheader(f"Buổi {SESSION[session].lower()} · {d:%d/%m/%Y}")
        by = ov["sessions"][session]
        cols = st.columns(6)
        for col, key in zip(cols, ("VALID", "LATE", "REJECTED", "PENDING_REVIEW", "RECEIVED")):
            col.metric(FINAL[key], by.get(key, 0))
        cols[5].metric("Thiếu điểm danh", ov["missing"][session] if ov["working"] else "—")
    st.caption("Thiếu điểm danh = nhân viên đã duyệt chưa có ảnh trong buổi (buổi đang diễn ra sẽ còn thay đổi).")

    st.subheader("14 ngày gần nhất")
    series = common.cached(data.daily_series)(d, 14)
    if series:
        df = pd.DataFrame(series)
        wide = df.pivot_table(index="attendance_date", columns="final_status", values="n", aggfunc="sum", fill_value=0)
        wide.columns = [FINAL.get(c, c) for c in wide.columns]
        st.bar_chart(wide)
    else:
        st.caption("Chưa có dữ liệu điểm danh.")

    st.subheader("Hệ thống xác minh")
    j = ov["jobs"]
    c = st.columns(3)
    c[0].metric("Job đang chờ", j.get("QUEUED", 0))
    c[1].metric("Job đang chạy", j.get("RUNNING", 0))
    c[2].metric("Ảnh chưa upload Cloudinary", ov["pending_upload"])
