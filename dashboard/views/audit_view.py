import json

import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import FINAL, SESSION, VERIFICATION, label, normalize_range


def _js(v) -> str:
    return "" if v is None else json.dumps(v, ensure_ascii=False, default=str)


def _audit_tab(tz: str, today) -> None:
    c = st.columns([2, 2, 2, 2])
    d1, d2 = normalize_range(c[0].date_input("Khoảng ngày", value=(today - pd.Timedelta(days=7), today), format="DD/MM/YYYY", key="audit_range"), today)
    actions = c[1].multiselect("Hành động", common.cached(data.audit_actions)())
    entity = c[2].selectbox("Đối tượng", [None, *common.cached(data.audit_entity_types)()], format_func=lambda e: "Tất cả" if e is None else e)
    text = c[3].text_input("Tìm trong lý do/hành động")
    rows = common.cached(data.audit_rows)(d1, d2, tz, tuple(actions), entity, text)
    st.caption(f"{len(rows)} dòng" + (" (đã giới hạn 500 dòng mới nhất)" if len(rows) >= 500 else ""))
    if not rows:
        return
    df = pd.DataFrame(rows)
    common.localize(df, ["created_at"])
    df["old_value"], df["new_value"] = df["old_value"].map(_js), df["new_value"].map(_js)
    df = df.rename(columns={
        "id": "ID", "created_at": "Lúc", "actor_type": "Nguồn", "actor_telegram_id": "Telegram ID", "action": "Hành động",
        "entity_type": "Đối tượng", "entity_id": "ID đối tượng", "old_value": "Trước", "new_value": "Sau", "reason": "Lý do"})
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("⬇️ Tải CSV", df.to_csv(index=False).encode("utf-8-sig"), file_name="nhat-ky.csv", mime="text/csv")


def _anomaly_tab() -> None:
    an = common.cached(data.anomalies)()

    st.subheader(f"Bản ghi có cờ / chờ kiểm tra ({len(an['flagged'])})")
    if an["flagged"]:
        df = pd.DataFrame(an["flagged"])
        df["flags"] = df["flags"].map(lambda f: ", ".join(f))
        df["attendance_date"] = pd.to_datetime(df["attendance_date"]).dt.strftime("%d/%m/%Y")
        common.label_col(df, "session", SESSION)
        common.label_col(df, "verification_status", VERIFICATION)
        common.label_col(df, "final_status", FINAL)
        st.dataframe(df.rename(columns={"id": "ID", "attendance_date": "Ngày", "session": "Buổi", "full_name": "Nhân viên",
                                        "verification_status": "Xác minh", "final_status": "Kết quả", "flags": "Cờ"}),
                     use_container_width=True, hide_index=True)

    st.subheader(f"Job xác minh lỗi / treo ({len(an['jobs'])})")
    if an["jobs"]:
        df = pd.DataFrame(an["jobs"])
        common.localize(df, ["run_after", "locked_at"])
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader(f"Ảnh upload Cloudinary lỗi ({len(an['uploads'])})")
    if an["uploads"]:
        st.dataframe(pd.DataFrame(an["uploads"]), use_container_width=True, hide_index=True)

    st.subheader(f"Ảnh trùng SHA-256 ({len(an['duplicate_hashes'])})")
    if an["duplicate_hashes"]:
        df = pd.DataFrame(an["duplicate_hashes"])
        df["record_ids"] = df["record_ids"].map(lambda ids: ", ".join(f"#{i}" for i in ids))
        st.dataframe(df, use_container_width=True, hide_index=True)


def render() -> None:
    common.show_flash()
    st.title("📜 Nhật ký")
    tz = common.tz()
    today = data.local_today(tz)
    tab_audit, tab_anom = st.tabs(["Nhật ký thao tác", "Trường hợp bất thường"])
    with tab_audit:
        _audit_tab(tz, today)
    with tab_anom:
        _anomaly_tab()
