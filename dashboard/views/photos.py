import os

import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import FINAL, SESSION, label, normalize_range


def render() -> None:
    common.show_flash()
    st.title("🖼️ Tra cứu ảnh")
    tz = common.tz()
    today = data.local_today(tz)
    users = common.cached(data.users_basic)()
    names = {u["id"]: u["full_name"] for u in users}

    c = st.columns([2, 1, 2, 2])
    d1, d2 = normalize_range(c[0].date_input("Khoảng ngày", value=(today - pd.Timedelta(days=6), today), format="DD/MM/YYYY"), today)
    session = c[1].selectbox("Buổi", [None, *SESSION], format_func=lambda s: "Tất cả" if s is None else SESSION[s])
    picked = c[2].multiselect("Nhân viên", list(names), format_func=names.get)
    text = c[3].text_input("Photo Code / SHA-256")

    rows = common.cached(data.photo_search)(d1, d2, session, tuple(picked), text)
    st.caption(f"Hiển thị tối đa 24 ảnh mới nhất · tìm thấy {len(rows)}")
    if not rows:
        return

    cols = st.columns(4)
    for i, r in enumerate(rows):
        with cols[i % 4]:
            url = common.photo_url(r["cloudinary_public_id"])
            if url:
                st.image(url, use_container_width=True)
            elif r["temp_path"] and os.path.exists(r["temp_path"]):
                st.image(r["temp_path"], use_container_width=True)
            else:
                st.caption("(chưa có ảnh)")
            st.markdown(
                f"**{r['full_name']}**  \n{r['attendance_date']:%d/%m/%Y} · {SESSION[r['session']]} · {label(FINAL, r['final_status'])}  \n"
                f"`{r['photo_code'] or '—'}` · bản ghi #{r['record_id']}" + (f"  \n[Mở ảnh gốc]({url})" if url else "")
            )
