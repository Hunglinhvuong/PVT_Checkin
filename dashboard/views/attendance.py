import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import FINAL, PUNCTUALITY, SESSION, VERIFICATION, WEEKDAY, normalize_range

COLUMNS = {
    "id": "ID", "attendance_date": "Ngày", "session": "Buổi", "full_name": "Nhân viên",
    "photo_received_at": "Nhận lúc", "photo_taken_at": "Chụp lúc", "distance_km": "Cách VP (km)",
    "verification_status": "Xác minh", "punctuality_status": "Giờ giấc", "final_status": "Kết quả", "flags": "Cờ",
}


def render() -> None:
    common.show_flash()
    st.title("✅ Điểm danh")
    tz = common.tz()
    today = data.local_today(tz)
    tab_list, tab_missing = st.tabs(["Danh sách", "Thiếu điểm danh"])

    with tab_list:
        users = common.cached(data.users_basic)()
        names = {u["id"]: u["full_name"] for u in users}
        c = st.columns([2, 1, 2, 2])
        d1, d2 = normalize_range(c[0].date_input("Khoảng ngày", value=(today - pd.Timedelta(days=6), today), format="DD/MM/YYYY"), today)
        sessions = c[1].multiselect("Buổi", list(SESSION), format_func=SESSION.get)
        picked = c[2].multiselect("Nhân viên", list(names), format_func=names.get)
        finals = c[3].multiselect("Kết quả", list(FINAL), format_func=FINAL.get)

        rows = common.cached(data.attendance_rows)(d1, d2, tuple(sessions), tuple(picked), tuple(finals))
        st.caption(f"{len(rows)} bản ghi" + (" (đã giới hạn 2000 dòng đầu)" if len(rows) >= 2000 else ""))
        if rows:
            df = pd.DataFrame(rows)
            df["flags"] = df["flags"].map(lambda f: ", ".join(f) if f else "")
            df["distance_km"] = df["distance_km"].map(lambda v: None if v is None else float(v))
            df["attendance_date"] = pd.to_datetime(df["attendance_date"]).dt.strftime("%d/%m/%Y")
            common.localize(df, ["photo_received_at", "photo_taken_at"])
            common.label_col(df, "session", SESSION)
            common.label_col(df, "verification_status", VERIFICATION)
            common.label_col(df, "punctuality_status", PUNCTUALITY)
            common.label_col(df, "final_status", FINAL)
            df = df.rename(columns=COLUMNS)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button("⬇️ Tải CSV", df.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"diem-danh_{d1:%Y%m%d}_{d2:%Y%m%d}.csv", mime="text/csv")

    with tab_missing:
        c = st.columns(2)
        d = c[0].date_input("Ngày", value=today, format="DD/MM/YYYY", key="missing_date")
        session = c[1].selectbox("Buổi", list(SESSION), format_func=SESSION.get, key="missing_session")
        working, why = common.cached(data.working_day_info)(d)
        if not working:
            st.info(f"{WEEKDAY[d.weekday()]} {d:%d/%m/%Y} không phải ngày làm việc" + (f" ({why})." if why else "."))
        else:
            missing = common.cached(data.missing_users)(d, session, tz)
            st.metric("Số nhân viên chưa điểm danh", len(missing))
            if missing:
                st.dataframe(pd.DataFrame(missing).rename(columns={"full_name": "Nhân viên", "telegram_user_id": "Telegram ID"})
                             .drop(columns=["id"]), use_container_width=True, hide_index=True)
