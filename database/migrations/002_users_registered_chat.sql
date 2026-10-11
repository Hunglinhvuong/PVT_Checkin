-- Giai đoạn 2: lưu chat nơi người dùng đăng ký để thông báo kết quả duyệt đúng nơi.
ALTER TABLE users ADD COLUMN registered_chat_id BIGINT;
