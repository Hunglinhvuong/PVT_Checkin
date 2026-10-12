# Telegram Attendance — Giai đoạn 1 (nền tảng)

## Cài đặt
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # điền DATABASE_URL, ADMIN_TELEGRAM_IDS
```

## PostgreSQL (tạo DB)
```sql
CREATE USER attendance WITH PASSWORD 'change_me';
CREATE DATABASE attendance OWNER attendance;
CREATE DATABASE attendance_test OWNER attendance;
```

## Chạy
```bash
python main.py check     # kiểm tra kết nối
python main.py migrate   # áp dụng migrations
python main.py seed      # nạp admin từ ADMIN_TELEGRAM_IDS
python -m scripts.check_db
pytest                   # integration test chạy khi có TEST_DATABASE_URL (XÓA SẠCH schema public của DB test)
```

## Quy ước
- Migration mới: `database/migrations/NNN_ten.sql`; không sửa file đã áp dụng (checksum sẽ báo lỗi).
- Timestamp lưu UTC (`TIMESTAMPTZ`); chuyển `Asia/Ho_Chi_Minh` ở tầng ứng dụng.
- Backup: `pg_dump -Fc attendance > backup_$(date +%F).dump` (cron hằng ngày, chi tiết ở Giai đoạn 7).

## Giai đoạn 3 — Tiếp nhận điểm danh
- `.env`: thêm `CLOUDINARY_*` (bắt buộc), `ATTENDANCE_CHAT_IDS` (khuyến nghị: ID group điểm danh), `UPLOAD_TMP_DIR`.
- Ngày nghỉ: `python -m scripts.seed_holidays 2027` (chỉ ngày dương lịch cố định). Tết, Giỗ Tổ, nghỉ bù: admin dùng
  `/holiday add|work|del|list` trong Telegram.
- Ảnh upload lên Cloudinary dạng `authenticated` (không công khai); xem ảnh qua `photo_storage.signed_url()`.
- Ảnh tạm trong `UPLOAD_TMP_DIR` được giữ cho worker xác minh (Giai đoạn 4) và sẽ được dọn ở giai đoạn đó.
- Upload lỗi sẽ tự thử lại mỗi 5 phút (tối đa 5 lần).

## Giai đoạn 4 — Xác minh Timemark (Playwright)
```bash
pip install -r requirements.txt
playwright install --with-deps chromium   # Debian/Ubuntu/WSL; máy khác: playwright install chromium
python -m scripts.probe_timemark ảnh.jpg  # thăm dò giao diện thật (thêm --headed để xem trình duyệt)
python main.py worker                     # chạy worker (tiến trình riêng với bot)
```
- Chỉ một worker chạy cùng lúc (advisory lock) và nghỉ `TIMEMARK_MIN_INTERVAL_S` giữa các lượt.
- Trang chỉ nhận PNG/JPG/GIF ≤ 10MB; bot từ chối định dạng/kích thước khác (không chuyển đổi để không đổi ảnh gốc).
- Gặp CAPTCHA/chặn/429: worker KHÔNG vượt qua, chuyển ảnh sang `MANUAL_REVIEW` và tạm dừng 30 phút.
- Admin (chat riêng với bot): `/review`, `/resolve <id> VALID|LATE|REJECTED <lý do>`, `/reverify <id> <lý do>`.
- Ảnh tạm được xóa sau khi xác minh xong và ảnh đã có trên Cloudinary.
- Trước khi vận hành thường xuyên: xác nhận với Timemark rằng việc tự động hóa giao diện được phép.

### Mã Status của trang Timemark (đã quan sát)
| Hiển thị | Ý nghĩa | Xử lý |
|---|---|---|
| Authentic | ảnh hợp lệ | `VERIFIED_AUTHENTIC` |
| Unable to verify + `Status: -2307` | Timemark chưa nhận dữ liệu ảnh | thử lại mỗi 10 phút, tối đa 5 lần (không tính vào lượt lỗi), hết lượt → `PENDING_REVIEW` |
| Unable to verify + `Status: -2300` | ảnh đã bị chỉnh sửa | `VERIFIED_FAKE` → `REJECTED`, báo lý do và cho gửi ảnh thay thế nếu chưa quá giới hạn |
| Unable to verify + `Status: -2301` | ảnh không có code hợp lệ | `REJECTED`, báo lý do và cho gửi ảnh thay thế nếu chưa quá giới hạn |
| Unable to verify + mã khác | chưa rõ | `UNABLE_TO_VERIFY` → `PENDING_REVIEW` (admin xử lý) |
Đổi số lần/khoảng chờ ở `PENDING_UPLOAD_MAX_RETRIES` / `PENDING_UPLOAD_RETRY_MINUTES` (services/verification.py).

## Giai đoạn 5 — Quy tắc đúng giờ
Đánh giá theo **thời gian chụp** xác minh từ Timemark (giờ `Asia/Ho_Chi_Minh`); mốc nghiêm ngặt (đúng mốc = muộn).

| Buổi | Điều kiện | Hạn |
|---|---|---|
| Sáng | không có tọa độ hoặc ≤ 10 km | 07:50 |
| Sáng | > 10 km và ≤ 40 km | 10:00 |
| Sáng | > 40 km | 11:30 |
| Chiều | mọi khoảng cách | 13:50 |

Chuyển `PENDING_REVIEW` (không tự phán): thiếu thời gian chụp (`NO_CAPTURE_TIME`), ngày chụp ≠ ngày nhận (`CAPTURE_DATE_MISMATCH`),
buổi chụp ≠ buổi nhận (`CAPTURE_SESSION_MISMATCH`), chụp sau lúc nhận quá 5 phút (`CAPTURE_AFTER_RECEIVED`), buổi sáng có
tọa độ nhưng không đọc được/không tin cậy (`GPS_UNREADABLE`), chưa cấu hình văn phòng (`NO_OFFICE_CONFIG`), hoặc ảnh trùng hash.
Văn phòng: `OFFICE_*` trong `.env` (nạp lần đầu) hoặc `/office set <lat> <lon> [tên]` trong chat riêng với bot.

## Giai đoạn 6 — Dashboard (Streamlit)
```bash
pip install -r requirements.txt
python main.py dashboard          # mặc định 0.0.0.0:8501 (DASHBOARD_HOST / DASHBOARD_PORT)
```
- **Xem: không cần đăng nhập**. Khi đưa lên mạng, mọi người có thể xem tên, ảnh và tọa độ nhân viên; chỉ triển khai công khai nếu bạn chấp nhận việc này.
- **Sửa: tài khoản/mật khẩu riêng** được cấu hình trong Streamlit Secrets, gắn với Telegram ID admin đang hoạt động (vai trò ADMIN/SUPER_ADMIN). Không dùng BOT_TOKEN, không gọi Telegram API và không gửi thông báo Telegram từ dashboard. Phiên hết hạn sau 8 giờ.
- SUPER_ADMIN thêm/khóa admin ở trang "Quản trị admin"; để cấp tài khoản dashboard, thêm mục tương ứng vào `dashboard_users` trong Streamlit Secrets. Quyền vẫn được kiểm tra lại theo bảng `admins`, các thao tác được audit với nguồn `DASHBOARD`.
- Mọi truy vấn xem chạy trong transaction READ ONLY và tham số hóa; dữ liệu lấy trực tiếp từ PostgreSQL (không dùng CSV).
- Ảnh xem qua URL có chữ ký Cloudinary (loại `authenticated`); máy dashboard cần cấu hình `CLOUDINARY_*` và ra được Internet để tải ảnh.

Có thể cài riêng trên máy khác bằng `sudo ./deploy/install.sh --dashboard-only`; cấu hình `DATABASE_URL` trỏ tới PostgreSQL từ xa và `CLOUDINARY_*` nếu dùng Cloudinary. Schema phải được migrate trước trên máy bot/worker; dashboard không tự chạy migration khi khởi động. Hướng dẫn Streamlit Community Cloud và Supabase: [docs/VAN_HANH.md](docs/VAN_HANH.md).

## Bổ sung
- **Thư mục Cloudinary**: đặt `CLOUDINARY_FOLDER` trong `.env` (mặc định `attendance`). Ảnh mới lưu tại `<thư mục>/<YYYY-MM-DD>/<user>_<buổi>_<id>`
  (loại `authenticated`). Đổi thư mục chỉ ảnh upload sau đó theo thư mục mới; ảnh cũ giữ nguyên `public_id` đã lưu trong DB.
  `CLOUDINARY_SET_ASSET_FOLDER=true` (mặc định) đặt thêm `asset_folder` để ảnh hiện trong thư mục ở Media Library; nếu tài khoản
  Cloudinary ở chế độ "fixed folder" và upload báo lỗi `asset_folder` thì đặt `false`.
- **Ảnh xác minh Authentic nhưng sai ngày** (`CAPTURE_DATE_MISMATCH`): bot báo cả hai ngày và yêu cầu chụp lại bằng Timemark rồi gửi lại.
  **Sai buổi chụp** (`CAPTURE_SESSION_MISMATCH`) cũng cho phép gửi ảnh khác. Bản ghi để `PENDING_REVIEW` (admin vẫn `/resolve` được,
  vd khi đồng hồ điện thoại sai); nếu nhân viên gửi lại thì ảnh cũ được đánh dấu "bị thay thế" và chuyển `REJECTED`, lịch sử giữ nguyên.
- Mọi ảnh có trạng thái `REJECTED`, kể cả ảnh admin chủ động từ chối, có thể được thay thế. Giới hạn là 3 ảnh tổng cộng/buổi (kể cả ảnh
  đầu tiên); khi thay thế, ảnh cũ được giữ làm lịch sử và đánh dấu bị thay thế.

## Giai đoạn 7 — Triển khai, sao lưu, vận hành
Xem **docs/VAN_HANH.md**. Tóm tắt (Debian/Ubuntu, dịch vụ chạy trực tiếp từ thư mục dự án):
```bash
sudo ./deploy/install.sh        # lần đầu tạo .env; điền xong chạy lại để cài .venv, Chromium, migrate, dịch vụ systemd, timer
.venv/bin/python main.py doctor # kiểm tra sức khỏe
./deploy/backup.sh              # sao lưu; khôi phục: ./deploy/restore.sh <file.dump>
./deploy/run_tests.sh           # test (cần TEST_DATABASE_URL riêng)
```
