-- Giai đoạn 1: schema nền tảng. Mọi timestamp là TIMESTAMPTZ (lưu UTC, hiển thị Asia/Ho_Chi_Minh ở tầng ứng dụng).

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------- admins ----------
CREATE TABLE admins (
    id               BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL UNIQUE,
    role             TEXT NOT NULL DEFAULT 'ADMIN' CHECK (role IN ('SUPER_ADMIN', 'ADMIN', 'VIEWER')),
    status           TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- users ----------
CREATE TABLE users (
    id               BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL UNIQUE,
    full_name        TEXT NOT NULL CHECK (length(btrim(full_name)) BETWEEN 2 AND 100),
    status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'DISABLED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at      TIMESTAMPTZ,
    approved_by      BIGINT REFERENCES admins(id),
    CHECK (status <> 'APPROVED' OR (approved_at IS NOT NULL AND approved_by IS NOT NULL))
);
CREATE INDEX idx_users_status ON users(status);
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- conversation_states (state machine theo user + chat, thay biến dùng chung) ----------
CREATE TABLE conversation_states (
    telegram_user_id BIGINT NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    state            TEXT NOT NULL,
    data             JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (telegram_user_id, telegram_chat_id)
);
CREATE TRIGGER trg_conv_updated BEFORE UPDATE ON conversation_states FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- holiday_calendar ----------
-- is_working_day = FALSE: ngày nghỉ (lễ/Tết). is_working_day = TRUE: ngày làm bù/ngoại lệ (vd thứ Bảy làm việc).
CREATE TABLE holiday_calendar (
    id             BIGSERIAL PRIMARY KEY,
    holiday_date   DATE NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    is_working_day BOOLEAN NOT NULL DEFAULT FALSE,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- office_locations ----------
CREATE TABLE office_locations (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    latitude   DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude  DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Chỉ cho phép đúng một văn phòng đang hoạt động (đơn giản hóa V1)
CREATE UNIQUE INDEX uq_office_one_active ON office_locations ((TRUE)) WHERE is_active;

-- ---------- attendance_records ----------
CREATE TABLE attendance_records (
    id                   BIGSERIAL PRIMARY KEY,
    user_id              BIGINT NOT NULL REFERENCES users(id),
    attendance_date      DATE NOT NULL,
    session              TEXT NOT NULL CHECK (session IN ('MORNING', 'AFTERNOON')),
    telegram_chat_id     BIGINT NOT NULL,
    telegram_message_id  BIGINT NOT NULL,
    photo_id             TEXT NOT NULL,                 -- Telegram file_unique_id
    photo_taken_at       TIMESTAMPTZ,                   -- thời gian chụp đã xác minh từ Timemark
    photo_received_at    TIMESTAMPTZ NOT NULL,          -- thời điểm nhận trên Telegram
    processed_at         TIMESTAMPTZ,
    distance_km          NUMERIC(8, 3),
    verification_status  TEXT NOT NULL DEFAULT 'PENDING' CHECK (verification_status IN
        ('PENDING', 'PROCESSING', 'VERIFIED_AUTHENTIC', 'VERIFIED_FAKE', 'VERIFIED_NOT_FOUND',
         'UNABLE_TO_VERIFY', 'AUTOMATION_ERROR', 'MANUAL_REVIEW')),
    punctuality_status   TEXT NOT NULL DEFAULT 'NOT_EVALUATED' CHECK (punctuality_status IN
        ('NOT_EVALUATED', 'ON_TIME', 'LATE', 'MANUAL_REVIEW')),
    final_status         TEXT NOT NULL DEFAULT 'RECEIVED' CHECK (final_status IN
        ('RECEIVED', 'VALID', 'LATE', 'REJECTED', 'PENDING_REVIEW')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_attendance_user_date_session UNIQUE (user_id, attendance_date, session),
    CONSTRAINT uq_attendance_tg_message UNIQUE (telegram_chat_id, telegram_message_id)
);
CREATE INDEX idx_attendance_date_session ON attendance_records(attendance_date, session);
CREATE INDEX idx_attendance_verification ON attendance_records(verification_status);
CREATE INDEX idx_attendance_final ON attendance_records(final_status);
CREATE TRIGGER trg_attendance_updated BEFORE UPDATE ON attendance_records FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- attendance_photos ----------
CREATE TABLE attendance_photos (
    id                    BIGSERIAL PRIMARY KEY,
    attendance_record_id  BIGINT NOT NULL REFERENCES attendance_records(id) ON DELETE RESTRICT,
    cloudinary_public_id  TEXT UNIQUE,
    cloudinary_url        TEXT,
    sha256                CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    photo_code            TEXT,
    latitude              DOUBLE PRECISION CHECK (latitude BETWEEN -90 AND 90),
    longitude             DOUBLE PRECISION CHECK (longitude BETWEEN -180 AND 180),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_photos_record ON attendance_photos(attendance_record_id);
CREATE INDEX idx_photos_sha256 ON attendance_photos(sha256);   -- không UNIQUE: để phát hiện & xử lý ảnh trùng

-- ---------- verification_jobs (hàng đợi trong PostgreSQL, dùng FOR UPDATE SKIP LOCKED) ----------
CREATE TABLE verification_jobs (
    id                    BIGSERIAL PRIMARY KEY,
    attendance_record_id  BIGINT NOT NULL REFERENCES attendance_records(id) ON DELETE RESTRICT,
    status                TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')),
    attempts              INT NOT NULL DEFAULT 0,
    max_attempts          INT NOT NULL DEFAULT 3,
    run_after             TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by             TEXT,
    locked_at             TIMESTAMPTZ,
    last_error            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Mỗi bản ghi chỉ có tối đa một job đang hoạt động
CREATE UNIQUE INDEX uq_job_active_per_record ON verification_jobs(attendance_record_id)
    WHERE status IN ('QUEUED', 'RUNNING');
CREATE INDEX idx_jobs_pickup ON verification_jobs(run_after) WHERE status = 'QUEUED';
CREATE TRIGGER trg_jobs_updated BEFORE UPDATE ON verification_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- verification_logs ----------
CREATE TABLE verification_logs (
    id                    BIGSERIAL PRIMARY KEY,
    attendance_record_id  BIGINT NOT NULL REFERENCES attendance_records(id) ON DELETE RESTRICT,
    provider              TEXT NOT NULL DEFAULT 'TIMEMARK_WEB',
    status                TEXT NOT NULL,
    result_text           TEXT,
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    screenshot_reference  TEXT,
    error_message         TEXT,
    reviewed_by           BIGINT REFERENCES admins(id),
    reviewed_at           TIMESTAMPTZ
);
CREATE INDEX idx_vlogs_record ON verification_logs(attendance_record_id);

-- ---------- audit_logs (chỉ thêm, không sửa/xóa) ----------
CREATE TABLE audit_logs (
    id                 BIGSERIAL PRIMARY KEY,
    actor_telegram_id  BIGINT,
    actor_type         TEXT NOT NULL DEFAULT 'ADMIN' CHECK (actor_type IN ('ADMIN', 'USER', 'SYSTEM', 'DASHBOARD')),
    action             TEXT NOT NULL,
    entity_type        TEXT NOT NULL,
    entity_id          BIGINT,
    old_value          JSONB,
    new_value          JSONB,
    reason             TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_created ON audit_logs(created_at DESC);

CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs là append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
