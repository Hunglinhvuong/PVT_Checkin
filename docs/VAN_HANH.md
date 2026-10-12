# Triển khai & vận hành (Debian/Ubuntu)

Dịch vụ chạy **trực tiếp từ thư mục dự án** (`WorkingDirectory` = thư mục dự án, Python trong `.venv` của dự án); không sao chép mã nguồn đi nơi khác.

## 1. Chuẩn bị
- Debian 11+/Ubuntu 22.04+ (Python ≥ 3.9), có quyền `sudo`, máy ra được Internet (Telegram, verify.timemark.com; Cloudinary nếu chọn backend Cloudinary).
- PostgreSQL: dùng sẵn (từ xa/cục bộ) hoặc cài cục bộ bằng `sudo ./deploy/setup_postgres.sh` (in ra `DATABASE_URL`/`TEST_DATABASE_URL`).
- Đặt dự án ở đường dẫn không có khoảng trắng, vd `/home/<user>/telegram_attendance`.

## 2. Cài đặt
```bash
cd /đường/dẫn/telegram_attendance
sudo ./deploy/install.sh            # lần đầu: tạo .env từ .env.example rồi dừng
nano .env                           # điền DATABASE_URL, BOT_TOKEN, ADMIN_TELEGRAM_IDS, ATTENDANCE_CHAT_IDS...; cấu hình backend ảnh
sudo ./deploy/install.sh            # chạy lại: cài thư viện + Chromium, migrate, bật dịch vụ và timer
```
Mặc định ảnh được lưu trên Cloudinary. Máy chủ không kết nối được Cloudinary có thể chọn lưu trực tiếp trên đĩa:
```dotenv
PHOTO_STORAGE_BACKEND=local
LOCAL_PHOTO_DIR=/var/lib/telegram-attendance/photos
LOCAL_PHOTO_RETENTION_DAYS=90
```
`LOCAL_PHOTO_DIR` cần nằm trên đĩa bền vững, được người dùng chạy dịch vụ ghi/đọc; không đặt trong thư mục tạm hoặc thư mục dự án bị thay thế khi cập nhật. Ảnh local được giữ trong 90 ngày (khoảng 3 tháng), worker tự xóa ảnh hết hạn mỗi ngày. Khi dùng `local`, không cần điền `CLOUDINARY_*`. Sau khi sửa `.env`, chạy lại `sudo ./deploy/install.sh` để dịch vụ nhận cấu hình.
Tùy chọn: `--user TÊN` (người dùng chạy dịch vụ, mặc định là người gọi sudo), `--no-browser`, `--no-timers`.
Admin cần nhắn `/start` riêng cho bot nếu muốn nhận tin nhắn từ bot; đăng nhập dashboard không còn phụ thuộc bước này.

Được cài: `telegram-attendance-bot`, `-worker`, `-dashboard` (luôn tự khởi động lại), timer sao lưu 02:30 hằng ngày và timer kiểm tra sức khỏe 15 phút/lần.

### Chạy dashboard trên máy riêng
Bot và worker có thể tiếp tục chạy trên máy hiện tại; trên máy dashboard, lấy cùng phiên bản mã nguồn, tạo `.env` riêng rồi chỉ cài dịch vụ dashboard:
```bash
cp .env.example .env
# Sửa .env: DATABASE_URL trỏ tới PostgreSQL từ xa, DASHBOARD_HOST=0.0.0.0,
# DASHBOARD_PORT; thêm CLOUDINARY_* nếu PHOTO_STORAGE_BACKEND=cloudinary
sudo ./deploy/install.sh --dashboard-only
```
Chế độ này chỉ cài/bật `telegram-attendance-dashboard`, không cài Chromium, không migrate DB và không bật bot, worker, sao lưu hay timer kiểm tra sức khỏe. Schema phải được máy chạy bot/worker cập nhật bằng `sudo ./deploy/install.sh` trước khi chạy dashboard; chạy lại lệnh `--dashboard-only` sau mỗi lần cập nhật mã nguồn.

Tài khoản đăng nhập được cấu hình bằng `dashboard_users` trong Streamlit Secrets (xem mục dưới), gắn với Telegram ID của admin đang hoạt động. Dashboard không cần `BOT_TOKEN`, không gọi Telegram API và không gửi thông báo Telegram. Dùng Cloudinary để máy dashboard có thể tạo URL ảnh có chữ ký. Với `PHOTO_STORAGE_BACKEND=local`, phải mount thư mục ảnh dùng chung chỉ-đọc tại cùng đường dẫn mà `LOCAL_PHOTO_DIR` trỏ tới.

### Chạy trên Streamlit Community Cloud với Supabase
1. Đẩy phiên bản dự án lên GitHub, vào Streamlit Community Cloud, chọn repository/branch và file chính `dashboard/app.py`.
2. Trong phần **Secrets** của ứng dụng, đặt chuỗi kết nối PostgreSQL của Supabase (dùng chuỗi connection pooler nếu app không thể dùng kết nối trực tiếp), TLS và pool nhỏ:
   ```toml
   DATABASE_URL = "postgresql://USER:PASSWORD@HOST:6543/postgres?sslmode=require"
   DB_POOL_MIN = 1
   DB_POOL_MAX = 3
   TIMEZONE = "Asia/Ho_Chi_Minh"
   PHOTO_STORAGE_BACKEND = "cloudinary"
   CLOUDINARY_CLOUD_NAME = "..."
   CLOUDINARY_API_KEY = "..."
   CLOUDINARY_API_SECRET = "..."

   [[dashboard_users]]
   username = "admin"
   password_hash = "pbkdf2_sha256$..."
   telegram_id = 123456789
   ```
   Tạo hash mật khẩu bằng `.venv/bin/python -m scripts.hash_dashboard_password` (chạy ngay sau khi tạo môi trường Python; không ghi mật khẩu thuần vào Git). Thêm một `[[dashboard_users]]` cho mỗi tài khoản, mỗi tài khoản phải gắn với Telegram ID có vai trò ADMIN hoặc SUPER_ADMIN và trạng thái ACTIVE trong bảng `admins`.
3. Dùng địa chỉ pooler, port và username đúng như Supabase hiển thị trong **Connect**. `prepare_threshold=None` được bật cho tương thích với PgBouncer. Không dùng anon key hay `service_role` key; ứng dụng kết nối PostgreSQL bằng `DATABASE_URL`. Cập nhật `DATABASE_URL` trên máy bot/worker sang Supabase như vậy để tất cả tiến trình dùng chung DB. Cấu hình Cloudinary cần thiết để dashboard hiển thị ảnh đã upload.
4. Trước khi deploy bản dashboard có thay đổi schema, chạy migration từ máy bot/worker bằng `python main.py migrate` hoặc cập nhật dịch vụ ở máy đó trước. Dashboard trên Streamlit Cloud không tự chạy migration.

Secrets chỉ tồn tại trong phần cấu hình riêng của ứng dụng, không commit vào Git. Giới hạn quyền/network access của Supabase theo khả năng gói đang dùng, dùng TLS, giữ bí mật mật khẩu DB và xoay vòng nếu lộ. Mỗi lần deploy ứng dụng sẽ kết nối trực tiếp Supabase, nên đặt connection pool nhỏ để phù hợp giới hạn kết nối.

**Lưu ý quyền riêng tư:** dashboard vẫn cho xem ẩn danh theo lựa chọn hiện tại. Nếu app Streamlit được chia sẻ công khai, bất kỳ ai có URL đều có thể xem dữ liệu điểm danh, thông tin nhân viên, ảnh và tọa độ; URL ảnh có chữ ký cũng được gửi tới trình duyệt người xem. Chỉ bật chia sẻ công khai khi đã được phép; nếu không, giới hạn người xem bằng cài đặt chia sẻ/private của ứng dụng Streamlit.

## 3. Vận hành hằng ngày
| Việc | Lệnh |
|---|---|
| Trạng thái | `systemctl status telegram-attendance-{bot,worker,dashboard}` |
| Xem log trực tiếp | `journalctl -u telegram-attendance-worker -f` |
| Khởi động lại | `sudo systemctl restart telegram-attendance-bot` |
| Kiểm tra sức khỏe | `.venv/bin/python main.py doctor` (thêm `--quick` để bỏ qua kiểm tra mạng) |
| Sao lưu ngay | `./deploy/backup.sh` |
| Cập nhật phiên bản | `git pull && sudo ./deploy/install.sh` (an toàn chạy lại; tự migrate và khởi động lại) |
| Gỡ dịch vụ (giữ dữ liệu) | `sudo ./deploy/uninstall.sh` |

Dashboard: `http://<IP máy chủ>:8501`. Xem không cần đăng nhập; sửa cần tài khoản dashboard riêng. Chỉ mở trong LAN/VPN:
`sudo ufw allow from 192.168.0.0/16 to any port 8501`.

## 4. Sao lưu & khôi phục
- Tự động: `pg_dump -Fc` hằng ngày vào `BACKUP_DIR` (mặc định `backups/`, quyền 700), kiểm tra file đọc được, giữ `BACKUP_KEEP_DAYS` ngày (14). Lỗi sao lưu gửi cảnh báo Telegram cho admin.
- Nên chép thư mục `backups/` sang máy/ổ khác định kỳ (cùng máy với DB thì chưa phòng được hỏng ổ đĩa), và lưu riêng một bản `.env` ở nơi an toàn.
- Khôi phục: `./deploy/restore.sh backups/attendance_YYYYmmdd_HHMMSS.dump` — yêu cầu gõ tên CSDL để xác nhận, dừng dịch vụ, tự sao lưu DB hiện tại trước, khôi phục trong một transaction, migrate rồi bật lại dịch vụ.
- Với backend `cloudinary`, ảnh gốc nằm trên Cloudinary (thư mục `CLOUDINARY_FOLDER`), không nằm trong bản sao lưu DB; DB giữ `public_id` để tra cứu. Với backend `local`, ảnh nằm trong `LOCAL_PHOTO_DIR` và không nằm trong bản sao lưu DB; cần tự sao lưu thư mục này nếu muốn giữ ảnh.

## 5. Giám sát
`doctor` kiểm tra: `.env`, PostgreSQL + migration, hàng đợi xác minh kẹt/treo, upload Cloudinary lỗi, đĩa, tuổi bản sao lưu, trạng thái dịch vụ; (không `--quick`) Telegram, Cloudinary, trang Timemark, Chromium. Timer chạy `doctor --alert` và nhắn admin khi tình trạng đổi (nhắc lại tối đa 6 giờ/lần, báo khi hồi phục).

## 6. Chạy test
```bash
./deploy/run_tests.sh          # cần TEST_DATABASE_URL (DB riêng, tên chứa "test"; test XÓA SẠCH schema public của DB đó)
```
Không có `TEST_DATABASE_URL` thì các test tích hợp bị bỏ qua; `tests/conftest.py` từ chối chạy nếu DB test trùng DB thật.

## 7. Xử lý sự cố
| Triệu chứng | Nguyên nhân thường gặp → xử lý |
|---|---|
| Bot không trả lời / log `telegram.error.TimedOut` khi khởi động | `journalctl -u telegram-attendance-bot`; kiểm tra `BOT_TOKEN`; nếu máy cần proxy, đặt `TELEGRAM_PROXY=http://127.0.0.1:<cổng-proxy>` trong `.env` rồi `sudo systemctl restart telegram-attendance-bot`; xác nhận proxy cho phép HTTPS tới `api.telegram.org`. Bot sẽ retry liên tục khi Telegram/proxy tạm thời mất kết nối; 2 bản bot cùng chạy (lỗi 409 Conflict) → tắt bản thừa |
| Ảnh kẹt "đang chờ xác minh" | worker không chạy (`systemctl status ...-worker`); Chromium lỗi → `doctor`; Timemark chặn → worker tạm dừng 30 phút, ảnh chuyển `MANUAL_REVIEW` |
| Worker báo sandbox/Chromium | Ubuntu 24.04+: đặt `TIMEMARK_NO_SANDBOX=true`; thiếu thư viện: `sudo .venv/bin/playwright install-deps chromium` |
| Trang Timemark đổi giao diện | log `UI_CHANGED`, ảnh chụp ở `screenshots/`; chạy `python -m scripts.probe_timemark ảnh.jpg --headed` để lấy dữ liệu và cập nhật bộ phân tích |
| Ảnh không lên Cloudinary | dashboard → Nhật ký → Bất thường; kiểm tra `CLOUDINARY_*`; lỗi `asset_folder` → `CLOUDINARY_SET_ASSET_FOLDER=false`; tự thử lại 5 lần. Máy không ra được Cloudinary: đặt `PHOTO_STORAGE_BACKEND=local` và cấu hình `LOCAL_PHOTO_DIR` |
| Không đăng nhập được dashboard | kiểm tra `dashboard_users` trong Streamlit Secrets: username, password hash hợp lệ và Telegram ID có quyền ADMIN/SUPER_ADMIN đang ACTIVE |
| Mất điện/sập máy | dịch vụ tự bật lại khi khởi động; job đang chạy dở được khôi phục sau 10 phút; timer sao lưu có `Persistent=true` |
| Cần khôi phục dữ liệu | mục 4 |

## 8. Bảo mật
- `.env` quyền 600; không đưa vào Git (đã có trong `.gitignore`). Cloudinary loại `authenticated`, ảnh chỉ xem qua URL có chữ ký từ dashboard.
- Dashboard hiển thị tên, ảnh, tọa độ cho người chưa đăng nhập; nếu triển khai online công khai, bảo đảm đã chấp nhận rủi ro chia sẻ dữ liệu này hoặc bật chế độ riêng tư của Streamlit.
- Dịch vụ chạy bằng người dùng thường (không root), `NoNewPrivileges`, `PrivateTmp`.
- Trước khi vận hành thường xuyên: xác nhận với Timemark rằng việc tự động hóa giao diện xác minh được phép.

## 9. Nghiệm thu (spec mục 14)
Chạy `./deploy/run_tests.sh` rồi kiểm tay trên nhóm thử: đăng ký → duyệt → gửi ảnh buổi sáng/chiều → gửi ảnh trùng → ảnh muộn → ngày nghỉ → tắt worker giữa chừng → `doctor` → sao lưu & khôi phục vào DB thử → dashboard (xem ẩn danh, sửa sau đăng nhập).
