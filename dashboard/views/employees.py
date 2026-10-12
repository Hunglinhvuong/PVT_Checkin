import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import USER_STATUS
from services import registration, users_admin

MSG = {
    "NOT_ADMIN": "Bạn không có quyền admin.",
    "BAD_INPUT": "Thiếu lý do (tối thiểu 3 ký tự).",
    "NOT_FOUND": "Không tìm thấy nhân viên.",
    "NOT_ALLOWED": "Không thể chuyển sang trạng thái này từ trạng thái hiện tại.",
    "ALREADY": "Đăng ký đã được xử lý trước đó.",
}
TARGETS = {"APPROVED": {"DISABLED"}, "DISABLED": {"APPROVED"}, "REJECTED": {"APPROVED"}}


def _decide(admin: int, user: dict, approve: bool) -> None:
    with common.dashboard_source():
        res = registration.decide(admin, user["id"], approve)
    if res.kind == "OK":
        common.done("success", f"Đã {'duyệt' if approve else 'từ chối'} {user['full_name']}.")
    st.error(MSG.get(res.kind, res.kind))


def render() -> None:
    common.show_flash()
    st.title("👥 Nhân viên")
    statuses = st.multiselect("Trạng thái", list(USER_STATUS), format_func=USER_STATUS.get)
    rows = common.cached(data.users_list)(tuple(statuses))
    st.caption(f"{len(rows)} nhân viên")
    if not rows:
        return

    df = pd.DataFrame(rows)
    common.localize(df, ["created_at", "approved_at"])
    df["last_date"] = pd.to_datetime(df["last_date"]).dt.strftime("%d/%m/%Y").fillna("")
    common.label_col(df, "status", USER_STATUS)
    st.dataframe(
        df.drop(columns=["registered_chat_id"]).rename(columns={
            "id": "ID", "telegram_user_id": "Telegram ID", "full_name": "Họ tên", "status": "Trạng thái",
            "created_at": "Đăng ký lúc", "approved_at": "Duyệt lúc", "approved_by_tg": "Duyệt bởi (Telegram ID)",
            "n_records": "Số lượt điểm danh", "last_date": "Lần điểm danh gần nhất"}),
        use_container_width=True, hide_index=True)

    st.subheader("Thao tác")
    by_id = {r["id"]: r for r in rows}
    uid = st.selectbox("Nhân viên", list(by_id), format_func=lambda i: f"{by_id[i]['full_name']} · {USER_STATUS[by_id[i]['status']]}")
    user = by_id[uid]
    admin = common.need_admin()
    if admin is None:
        return

    if user["status"] == "PENDING":
        c = st.columns(2)
        if c[0].button("✅ Duyệt", type="primary", key=f"ap{uid}"):
            _decide(admin, user, True)
        if c[1].button("❌ Từ chối", key=f"rj{uid}"):
            _decide(admin, user, False)
    elif user["status"] in TARGETS:
        with st.form(f"status_{uid}"):
            target = st.selectbox("Chuyển sang", sorted(TARGETS[user["status"]]), format_func=USER_STATUS.get)
            reason = st.text_input("Lý do (bắt buộc)")
            if st.form_submit_button("Cập nhật trạng thái"):
                with common.dashboard_source():
                    res = users_admin.change_status(admin, uid, target, reason)
                if res == "OK":
                    common.done("success", f"Đã chuyển {user['full_name']} sang {USER_STATUS[target]}.")
                st.error(MSG.get(res, res))
