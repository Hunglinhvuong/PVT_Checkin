import pandas as pd
import streamlit as st

from dashboard import auth, common
from services import admin_accounts

MSG = {
    "NOT_SUPER": "Chỉ SUPER_ADMIN mới có quyền này.",
    "BAD_INPUT": "Dữ liệu không hợp lệ.",
    "EXISTS": "Telegram ID này đã là admin đang hoạt động.",
    "NOT_FOUND": "Không tìm thấy admin.",
    "SELF": "Không thể tự khóa chính mình.",
    "LAST_SUPER": "Không thể khóa SUPER_ADMIN cuối cùng.",
}


def render() -> None:
    common.show_flash()
    st.title("🛡️ Quản trị admin")
    me = auth.current()
    if not me or me["role"] != "SUPER_ADMIN":
        st.warning("Chỉ SUPER_ADMIN đã đăng nhập mới xem được trang này.")
        return

    rows = admin_accounts.list_admins()
    df = pd.DataFrame(rows)
    common.localize(df, ["created_at"])
    st.dataframe(df.rename(columns={"id": "ID", "telegram_user_id": "Telegram ID", "role": "Vai trò",
                                    "status": "Trạng thái", "created_at": "Tạo lúc"}),
                 use_container_width=True, hide_index=True)
    st.caption("Admin mới phải nhắn `/start` riêng cho bot trước, nếu không sẽ không nhận được mã đăng nhập. "
               "VIEWER không có quyền sửa nên không đăng nhập được.")

    with st.form("admin_add"):
        c = st.columns(2)
        tg = c[0].text_input("Telegram User ID")
        role = c[1].selectbox("Vai trò", list(admin_accounts.ROLES), index=1)
        if st.form_submit_button("Thêm / mở lại admin"):
            try:
                tg_id = int(tg.strip())
            except ValueError:
                st.error("Telegram User ID phải là số.")
            else:
                with common.dashboard_source():
                    res = admin_accounts.add_admin(me["telegram_id"], tg_id, role)
                if res == "OK":
                    common.done("success", f"Đã thêm admin {tg_id} ({role}).")
                st.error(MSG.get(res, res))

    by_id = {r["id"]: r for r in rows}
    with st.form("admin_status"):
        aid = st.selectbox("Admin", list(by_id), format_func=lambda i: f"{by_id[i]['telegram_user_id']} · {by_id[i]['role']} · {by_id[i]['status']}")
        status = st.selectbox("Chuyển sang", ["DISABLED", "ACTIVE"])
        if st.form_submit_button("Cập nhật trạng thái"):
            with common.dashboard_source():
                res = admin_accounts.set_status(me["telegram_id"], aid, status)
            if res == "OK":
                common.done("success", "Đã cập nhật trạng thái admin.")
            st.error(MSG.get(res, res))
