-- Giai đoạn 4: kết quả xác minh Timemark.
ALTER TABLE verification_logs ADD COLUMN parsed JSONB;               -- các trường đọc được từ trang kết quả
ALTER TABLE attendance_photos ADD COLUMN gps_raw TEXT, ADD COLUMN device_model TEXT;
CREATE INDEX idx_records_pending_review ON attendance_records(created_at) WHERE final_status = 'PENDING_REVIEW';
