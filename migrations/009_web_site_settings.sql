-- Global portal branding and operational preferences, editable by super-admin only.
BEGIN;
CREATE TABLE IF NOT EXISTS web_app_settings (
    setting_key VARCHAR(64) PRIMARY KEY,
    setting_value JSONB NOT NULL,
    updated_by BIGINT REFERENCES web_users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO web_app_settings(setting_key,setting_value) VALUES
 ('site_name','"رفيق العمل"'::jsonb),
 ('site_tagline','"منظومة إدارة الموظفين"'::jsonb),
 ('support_message','"تواصل مع مدير فرعك عند الحاجة."'::jsonb),
 ('maintenance_notice','""'::jsonb)
ON CONFLICT(setting_key) DO NOTHING;
COMMIT;
