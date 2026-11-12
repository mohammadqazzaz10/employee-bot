-- Global portal branding and operational preferences, editable by super-admin only.
BEGIN;
CREATE TABLE IF NOT EXISTS web_app_settings (
    setting_key VARCHAR(64) PRIMARY KEY,
    setting_value JSONB NOT NULL,
    updated_by BIGINT REFERENCES web_users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO web_app_settings(setting_key,setting_value) VALUES
 ('site_name','"عمران مول | Emran Mall"'::jsonb),
 ('site_tagline','"الفرع الرئيسي – خريبة السوق"'::jsonb),
 ('support_message','"للمساعدة، تواصل مع مدير فرع عمران مول."'::jsonb),
 ('maintenance_notice','""'::jsonb)
ON CONFLICT(setting_key) DO NOTHING;
COMMIT;
