"""Hàm thuần dùng chung cho dashboard (không phụ thuộc Streamlit/DB)."""
from __future__ import annotations

from datetime import date

SESSION = {"MORNING": "Sáng", "AFTERNOON": "Chiều"}
FINAL = {
    "VALID": "Hợp lệ", "LATE": "Muộn", "REJECTED": "Từ chối",
    "PENDING_REVIEW": "Chờ kiểm tra", "RECEIVED": "Chờ đánh giá",
}
VERIFICATION = {
    "PENDING": "Chờ xác minh", "PROCESSING": "Đang xác minh", "VERIFIED_AUTHENTIC": "Authentic",
    "VERIFIED_FAKE": "Fake/Bị chỉnh sửa", "VERIFIED_NOT_FOUND": "Not Found", "UNABLE_TO_VERIFY": "Không xác minh được",
    "AUTOMATION_ERROR": "Lỗi tự động hóa", "MANUAL_REVIEW": "Cần kiểm tra thủ công",
}
PUNCTUALITY = {"NOT_EVALUATED": "Chưa đánh giá", "ON_TIME": "Đúng giờ", "LATE": "Muộn", "MANUAL_REVIEW": "Kiểm tra thủ công"}
USER_STATUS = {"PENDING": "Chờ duyệt", "APPROVED": "Đã duyệt", "REJECTED": "Từ chối", "DISABLED": "Đã khóa"}
WEEKDAY = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]


def label(mapping: dict[str, str], key: str | None) -> str:
    return mapping.get(key, key) if key else ""


def normalize_range(value, default: date) -> tuple[date, date]:
    """st.date_input(range) có thể trả về 0/1/2 phần tử khi người dùng đang chọn dở."""
    if isinstance(value, (tuple, list)):
        if len(value) >= 2:
            a, b = value[0], value[1]
        elif len(value) == 1:
            a = b = value[0]
        else:
            a = b = default
    else:
        a = b = value or default
    return (a, b) if a <= b else (b, a)


def like_pattern(q: str) -> str:
    """Escape ký tự đặc biệt của LIKE (\\ % _) rồi bọc %...% để tìm chuỗi con."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
