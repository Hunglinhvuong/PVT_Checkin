-- Giai đoạn 6b: đăng nhập admin cho dashboard bằng mã một lần gửi qua Telegram (không lưu mật khẩu).
CREATE TABLE dashboard_login_codes (
    id               BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    salt             TEXT,
    code_hash        TEXT,                      -- NULL: yêu cầu từ ID không phải admin (chỉ để tính giới hạn tần suất)
    expires_at       TIMESTAMPTZ NOT NULL,
    attempts         INT NOT NULL DEFAULT 0,
    used_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_login_codes_user ON dashboard_login_codes(telegram_user_id, created_at DESC);
CREATE INDEX idx_login_codes_created ON dashboard_login_codes(created_at);
