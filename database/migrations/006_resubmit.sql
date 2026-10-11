-- Giai đoạn 4c: cho phép gửi lại ảnh khi ảnh bị từ chối vì không có code hợp lệ (Timemark Status -2301).
-- Bản ghi cũ KHÔNG bị ghi đè/xóa: được đánh dấu "bị thay thế" và giữ nguyên toàn bộ lịch sử (ảnh, log xác minh).
ALTER TABLE attendance_records
    ADD COLUMN resubmit_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN superseded_at    TIMESTAMPTZ,
    ADD COLUMN superseded_by    BIGINT REFERENCES attendance_records(id);

-- Ràng buộc duy nhất chỉ áp dụng cho bản ghi đang có hiệu lực (chưa bị thay thế).
ALTER TABLE attendance_records DROP CONSTRAINT uq_attendance_user_date_session;
CREATE UNIQUE INDEX uq_attendance_user_date_session_active
    ON attendance_records(user_id, attendance_date, session) WHERE superseded_at IS NULL;
