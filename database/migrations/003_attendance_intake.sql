-- Giai đoạn 3: tiếp nhận ảnh điểm danh + upload Cloudinary (có retry).
ALTER TABLE attendance_photos
    ADD COLUMN temp_path         TEXT,
    ADD COLUMN file_ext          TEXT NOT NULL DEFAULT 'jpg',
    ADD COLUMN file_size_bytes   INT,
    ADD COLUMN source            TEXT NOT NULL DEFAULT 'PHOTO' CHECK (source IN ('PHOTO', 'DOCUMENT')),
    ADD COLUMN upload_attempts   INT NOT NULL DEFAULT 0,
    ADD COLUMN last_upload_error TEXT,
    ADD COLUMN uploaded_at       TIMESTAMPTZ;

-- Mỗi bản ghi điểm danh chỉ có đúng một ảnh gốc (không bao giờ ghi đè).
CREATE UNIQUE INDEX uq_photo_one_per_record ON attendance_photos(attendance_record_id);
CREATE INDEX idx_photos_pending_upload ON attendance_photos(id) WHERE cloudinary_public_id IS NULL;

-- Cờ bất thường (vd DUPLICATE_PHOTO_HASH) để Giai đoạn 5 không tự động duyệt.
ALTER TABLE attendance_records ADD COLUMN flags TEXT[] NOT NULL DEFAULT '{}';
