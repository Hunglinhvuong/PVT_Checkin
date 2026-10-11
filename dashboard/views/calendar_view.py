from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import WEEKDAY
from services import holidays, offices
from services.workcalendar import is_working_day

MSG = {"NOT_ADMIN": "Bạn không có quyền admin.", "NOT_FOUND": "Không có cấu hình cho ngày này.", "BAD_INPUT": "Dữ liệu không hợp lệ."}


def _preview(start: date, days: int = 21) -> pd.DataFrame:
    end = start + timedelta(days=days - 1)
    overrides = {h["holiday_date"]: h for h in common.cached(data.holidays_between)(start, end)}
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        o = overrides.get(d)
        rows.append({
            "Ngày": f"{d:%d/%m/%Y}", "Thứ": WEEKDAY[d.weekday()],
            "Trạng thái": "Làm việc" if is_working_day(d, o) else "Nghỉ", "Ghi chú": o["name"] if o else "",
        })
    return pd.DataFrame(rows)


def render() -> None:
    common.show_flash()
    st.title("📅 Lịch làm việc")
    tz = common.tz()
    today = data.local_today(tz)

    st.subheader("21 ngày tới")
    st.dataframe(_preview(today), use_container_width=True, hide_index=True)

    st.subheader("Ngày nghỉ / ngày làm ngoại lệ")
    year = st.number_input("Năm", min_value=2020, max_value=2100, value=today.year, step=1)
    rows = common.cached(data.holidays_between)(date(int(year), 1, 1), date(int(year), 12, 31))
    if rows:
        df = pd.DataFrame(rows)[["holiday_date", "name", "is_working_day"]]
        df["holiday_date"] = pd.to_datetime(df["holiday_date"]).dt.strftime("%d/%m/%Y")
        df["is_working_day"] = df["is_working_day"].map({True: "Ngày làm việc (ngoại lệ)", False: "Nghỉ"})
        st.dataframe(df.rename(columns={"holiday_date": "Ngày", "name": "Tên / ghi chú", "is_working_day": "Loại"}),
                     use_container_width=True, hide_index=True)
    else:
        st.caption(f"Chưa có cấu hình cho năm {int(year)}. Ngày lễ âm lịch (Tết, Giỗ Tổ) và nghỉ bù cần admin nhập theo thông báo chính thức.")

    admin = common.need_admin()
    if admin is not None:
        with st.form("holiday_set"):
            c = st.columns(3)
            d = c[0].date_input("Ngày", value=today, format="DD/MM/YYYY")
            kind = c[1].selectbox("Loại", ["Nghỉ", "Ngày làm việc (ngoại lệ)"])
            name = c[2].text_input("Tên / ghi chú")
            if st.form_submit_button("Lưu"):
                if not name.strip():
                    st.error("Thiếu tên/ghi chú.")
                else:
                    with common.dashboard_source():
                        ok = holidays.set_day(admin, d, " ".join(name.split())[:100], kind != "Nghỉ")
                    if ok:
                        common.done("success", f"Đã lưu {d:%d/%m/%Y}.")
                    st.error(MSG["NOT_ADMIN"])
        with st.form("holiday_del"):
            d2 = st.date_input("Xóa cấu hình ngày", value=today, format="DD/MM/YYYY", key="del_date")
            if st.form_submit_button("Xóa"):
                with common.dashboard_source():
                    res = holidays.remove_day(admin, d2)
                if res == "OK":
                    common.done("success", f"Đã xóa cấu hình {d2:%d/%m/%Y}.")
                st.error(MSG.get(res, res))

    st.subheader("Tọa độ văn phòng")
    office = common.cached(data.active_office)()
    if office:
        st.write(f"**{office['name']}**: {office['latitude']}, {office['longitude']}")
        st.map(pd.DataFrame({"lat": [office["latitude"]], "lon": [office["longitude"]]}), zoom=15)
    else:
        st.warning("Chưa cấu hình văn phòng: ảnh buổi sáng có tọa độ sẽ chuyển sang kiểm tra thủ công.")
    if admin is not None:
        with st.form("office_set"):
            c = st.columns(3)
            lat = c[0].text_input("Vĩ độ", value=str(office["latitude"]) if office else "")
            lon = c[1].text_input("Kinh độ", value=str(office["longitude"]) if office else "")
            oname = c[2].text_input("Tên", value=office["name"] if office else "Văn phòng")
            if st.form_submit_button("Cập nhật văn phòng"):
                try:
                    la, lo = float(lat.strip().rstrip(",")), float(lon.strip().rstrip(","))
                except ValueError:
                    st.error("Tọa độ không hợp lệ.")
                else:
                    with common.dashboard_source():
                        res = offices.set_office(admin, oname, la, lo)
                    if res == "OK":
                        common.done("success", "Đã cập nhật tọa độ văn phòng.")
                    st.error(MSG.get(res, "Tọa độ ngoài phạm vi hợp lệ."))
