import os

import pandas as pd
import streamlit as st

from dashboard import common, data
from dashboard.util import FINAL, PUNCTUALITY, SESSION, VERIFICATION, label
from services import review

MSG = {
    "NOT_ADMIN": "Bạn không có quyền admin.",
    "BAD_INPUT": "Thiếu lý do (tối thiểu 3 ký tự) hoặc dữ liệu không hợp lệ.",
    "NOT_FOUND": "Không tìm thấy bản ghi.",
    "NOT_PENDING": "Bản ghi không ở trạng thái chờ kiểm tra.",
    "NOT_ELIGIBLE": "Chỉ xác minh lại được bản ghi đang chờ kiểm tra và không đang xử lý.",
    "ALREADY_QUEUED": "Bản ghi đã có yêu cầu xác minh đang chờ.",
}


def _image(d: dict) -> None:
    url = common.photo_url(d["cloudinary_public_id"])
    if url:
        st.image(url, use_container_width=True)
        st.markdown(f"[Mở ảnh gốc]({url})")
    elif d["temp_path"] and os.path.exists(d["temp_path"]):
        st.image(d["temp_path"], use_container_width=True)
        st.caption("Ảnh lưu trên máy chủ." if d["storage_backend"] == "local"
                   else "Ảnh tạm trên máy chủ (chưa có trên Cloudinary).")
    else:
        st.warning("Không có ảnh để hiển thị (chưa upload Cloudinary hoặc chưa cấu hình xem ảnh).")


def _actions(d: dict) -> None:
    st.subheader("Xử lý ngoại lệ")
    if d["final_status"] != "PENDING_REVIEW":
        st.caption("Chỉ xử lý được bản ghi ở trạng thái 'Chờ kiểm tra'.")
        return
    admin = common.need_admin()
    if admin is None:
        return
    rid = d["id"]
    with st.form(f"resolve_{rid}"):
        decision = st.selectbox("Quyết định", ["VALID", "LATE", "REJECTED"], format_func=FINAL.get)
        reason = st.text_input("Lý do (bắt buộc)")
        if st.form_submit_button("Ghi nhận quyết định"):
            with common.dashboard_source():
                res = review.resolve(admin, rid, decision, reason)
            if res == "OK":
                common.done("success", f"Đã ghi nhận #{rid}: {FINAL[decision]}.")
            st.error(MSG.get(res, res))
    with st.form(f"reverify_{rid}"):
        reason2 = st.text_input("Lý do xác minh lại (bắt buộc)")
        if st.form_submit_button("Xác minh lại qua Timemark"):
            with common.dashboard_source():
                res = review.reverify(admin, rid, reason2)
            if res == "OK":
                common.done("success", f"Đã đưa #{rid} vào hàng đợi xác minh lại.")
            st.error(MSG.get(res, res))


def render() -> None:
    common.show_flash()
    st.title("🔍 Xác minh ảnh")
    queue = common.cached(data.review_queue)()
    st.caption(f"{len(queue)} bản ghi chờ kiểm tra hoặc đang xác minh")

    manual_id = st.number_input("Hoặc nhập ID bản ghi", min_value=0, value=0, step=1)
    options = [r["id"] for r in queue]
    by_id = {r["id"]: r for r in queue}

    def fmt(i: int) -> str:
        r = by_id[i]
        return (f"#{i} · {r['full_name']} · {r['attendance_date']:%d/%m} {SESSION[r['session']]} · "
                f"{label(VERIFICATION, r['verification_status'])} · {label(FINAL, r['final_status'])}")

    picked = st.selectbox("Bản ghi", options, format_func=fmt, index=0 if options else None, placeholder="Không có bản ghi")
    rid = int(manual_id) if manual_id else picked
    if not rid:
        return
    d = common.cached(data.record_detail)(rid)
    if d is None:
        st.error("Không tìm thấy bản ghi.")
        return

    left, right = st.columns([1, 1])
    with left:
        _image(d)
    with right:
        st.subheader(f"#{d['id']} · {d['full_name']}")
        info = {
            "Ngày / buổi": f"{d['attendance_date']:%d/%m/%Y} · {SESSION[d['session']]}",
            "Nhận lúc (Telegram)": common.fmt_dt(d["photo_received_at"]),
            "Chụp lúc (Timemark)": common.fmt_dt(d["photo_taken_at"]) or "—",
            "Cách văn phòng": f"{float(d['distance_km']):.3f} km" if d["distance_km"] is not None else "—",
            "Xác minh": label(VERIFICATION, d["verification_status"]),
            "Giờ giấc": label(PUNCTUALITY, d["punctuality_status"]),
            "Kết quả": label(FINAL, d["final_status"]),
            "Cờ": ", ".join(d["flags"]) or "—",
            "Gửi lại": (f"Đã bị thay bằng bản ghi #{d['superseded_by']}" if d["superseded_by"]
                        else ("Được phép gửi ảnh khác" if d["resubmit_allowed"] else "—")),
            "Photo Code": d["photo_code"] or "—",
            "GPS (trang Timemark)": d["gps_raw"] or "—",
            "Thiết bị": d["device_model"] or "—",
            "SHA-256": d["sha256"] or "—",
            "Lưu ảnh": ("Local" if d["storage_backend"] == "local" else
                        "Cloudinary" if d["cloudinary_public_id"] else
                        f"Chưa upload (lần thử {d['upload_attempts']}, {d['last_upload_error'] or '—'})"),
        }
        st.table(pd.Series(info, name="Giá trị"))

    jobs = common.cached(data.job_info)(rid)
    if jobs:
        st.caption("Job xác minh gần nhất: " + "; ".join(
            f"{j['status']} (lượt {j['attempts']}/{j['max_attempts']}, chờ Timemark {j['pending_retries']}/5)" for j in jobs[:1]))

    st.subheader("Lịch sử xác minh")
    logs = common.cached(data.verification_logs)(rid)
    if logs:
        df = pd.DataFrame(logs).drop(columns=["parsed", "result_text"])
        common.localize(df, ["started_at", "finished_at", "reviewed_at"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        for lg in logs[:5]:
            with st.expander(f"Chi tiết lượt #{lg['id']} · {lg['status']}"):
                if lg["result_text"]:
                    st.code(lg["result_text"])
                if lg["parsed"]:
                    st.json(lg["parsed"])
                shot = lg["screenshot_reference"]
                if shot and os.path.exists(shot):
                    st.image(shot)
    else:
        st.caption("Chưa có lượt xác minh nào.")
    _actions(d)
