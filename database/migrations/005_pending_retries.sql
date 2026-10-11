-- Giai đoạn 4b: đếm riêng số lần thử lại khi Timemark chưa nhận dữ liệu ảnh (Status -2307), không tiêu hao attempts.
ALTER TABLE verification_jobs ADD COLUMN pending_retries INT NOT NULL DEFAULT 0;
