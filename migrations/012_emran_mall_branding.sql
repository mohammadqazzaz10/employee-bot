-- Emran Mall identity and Khreibat Al-Souq main branch.
BEGIN;
UPDATE web_app_settings SET setting_value = '"عمران مول | Emran Mall"'::jsonb, updated_at = CURRENT_TIMESTAMP WHERE setting_key = 'site_name';
INSERT INTO web_app_settings(setting_key, setting_value) VALUES ('site_name', '"عمران مول | Emran Mall"'::jsonb) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = CURRENT_TIMESTAMP;
UPDATE web_app_settings SET setting_value = '"الفرع الرئيسي – خريبة السوق"'::jsonb, updated_at = CURRENT_TIMESTAMP WHERE setting_key = 'site_tagline';
INSERT INTO web_app_settings(setting_key, setting_value) VALUES ('site_tagline', '"الفرع الرئيسي – خريبة السوق"'::jsonb) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = CURRENT_TIMESTAMP;
UPDATE web_app_settings SET setting_value = '"للمساعدة، تواصل مع مدير فرع عمران مول."'::jsonb, updated_at = CURRENT_TIMESTAMP WHERE setting_key = 'support_message';
INSERT INTO web_app_settings(setting_key, setting_value) VALUES ('support_message', '"للمساعدة، تواصل مع مدير فرع عمران مول."'::jsonb) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = CURRENT_TIMESTAMP;
UPDATE branches SET name = 'خريبة السوق' WHERE code = 'main';
COMMIT;
