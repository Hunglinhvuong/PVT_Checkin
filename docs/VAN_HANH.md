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
Mỗi admin nhắn `/start` **riêng** cho bot một lần (để nhận thông báo và mã đăng nhập dashboard).

Được cài: `telegram-attendance-bot`, `-worker`, `-dashboard` (luôn tự khởi động lại), timer sao lưu 02:30 hằng ngày và timer kiểm tra sức khỏe 15 phút/lần.

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

Dashboard: `http://<IP máy chủ>:8501`. Xem không cần đăng nhập; sửa cần admin đăng nhập (mã gửi qua Telegram). Chỉ mở trong LAN/VPN:
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
| Không nhận được mã đăng nhập dashboard | admin chưa nhắn `/start` riêng cho bot; sai Telegram ID |
| Mất điện/sập máy | dịch vụ tự bật lại khi khởi động; job đang chạy dở được khôi phục sau 10 phút; timer sao lưu có `Persistent=true` |
| Cần khôi phục dữ liệu | mục 4 |

## 8. Bảo mật
- `.env` quyền 600; không đưa vào Git (đã có trong `.gitignore`). Cloudinary loại `authenticated`, ảnh chỉ xem qua URL có chữ ký từ dashboard.
- Dashboard hiển thị tên, ảnh, tọa độ cho mọi người truy cập được: chỉ mở trong mạng nội bộ.
- Dịch vụ chạy bằng người dùng thường (không root), `NoNewPrivileges`, `PrivateTmp`.
- Trước khi vận hành thường xuyên: xác nhận với Timemark rằng việc tự động hóa giao diện xác minh được phép.

## 9. Nghiệm thu (spec mục 14)
Chạy `./deploy/run_tests.sh` rồi kiểm tay trên nhóm thử: đăng ký → duyệt → gửi ảnh buổi sáng/chiều → gửi ảnh trùng → ảnh muộn → ngày nghỉ → tắt worker giữa chừng → `doctor` → sao lưu & khôi phục vào DB thử → dashboard (xem ẩn danh, sửa sau đăng nhập).
