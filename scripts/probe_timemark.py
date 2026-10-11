"""Thăm dò giao diện thực tế của trang xác minh Timemark bằng MỘT ảnh, để thu thập các trạng thái chưa quan sát.

  python -m scripts.probe_timemark /đường/dẫn/anh.jpg [--headed]

Ghi ra probe_out/<tên>_<giờ>/: page_text.txt (văn bản hiển thị), page.html, page.png và in kết quả phân tích.
Hãy chạy với: (1) ảnh Timemark thật, (2) ảnh bị chỉnh sửa, (3) ảnh thường không phải Timemark, (4) ảnh Timemark
rất cũ nếu có, rồi gửi lại page_text.txt + page.png của từng trường hợp để chốt các nhánh Fake/Not Found/Unable.
Chỉ dùng thủ công với số lượng nhỏ; không thay thế việc xin phép Timemark.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from config.settings import load_settings
from services.timemark_verifier import TimemarkWebVerifier


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1 or not Path(args[0]).is_file():
        print(__doc__)
        return 2
    s = load_settings()
    out = Path("probe_out") / f"{Path(args[0]).stem}_{int(time.time())}"
    with TimemarkWebVerifier(
        s.timemark_url, headless="--headed" not in sys.argv, result_timeout_s=s.timemark_result_timeout_s,
        screenshot_dir=out, no_sandbox=s.timemark_no_sandbox,
    ) as v:
        o = v.verify(args[0], tag="probe", debug_dir=out)
    print("status      :", o.status, f"({o.status_text})")
    print("error       :", o.error)
    print("fields      :", o.fields)
    print("capture_time:", o.capture_time)
    print("photo_code  :", o.photo_code)
    print("gps         :", o.gps_state, o.latitude, o.longitude)
    print("dữ liệu thô :", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
