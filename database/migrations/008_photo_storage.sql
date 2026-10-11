-- Backend ảnh đã xác nhận lưu (Cloudinary hoặc filesystem local).
ALTER TABLE attendance_photos
    ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'cloudinary'
    CHECK (storage_backend IN ('cloudinary', 'local'));
