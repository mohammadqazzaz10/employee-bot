from pathlib import Path
import unittest

from request_types import _save_new, _update_type, get_employee_buttons, register_request_type_handlers

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
MODULE = (ROOT / "request_types.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "migrations" / "002_configurable_request_types.sql").read_text(encoding="utf-8")


class ConfigurableRequestTypeContractTests(unittest.TestCase):
    def test_toilet_and_prayer_defaults_are_ten_minutes(self):
        self.assertIn("('bathroom', '🚻 استئذان الذهاب إلى الحمام')", MIGRATION)
        self.assertIn("('prayer', '🕌 استئذان الذهاب إلى الصلاة')", MIGRATION)
        self.assertIn("SELECT b.id, defaults.code, defaults.label, 10", MIGRATION)

    def test_types_are_branch_scoped_and_audited(self):
        self.assertIn("branch_id BIGINT NOT NULL REFERENCES branches(id)", MIGRATION)
        self.assertIn("branch_request_types_audit", MIGRATION)
        self.assertIn("branch_id=%s AND id=%s", MODULE)
        self.assertIn("branch_id, request_type_id, action, changed_by, new_value", MODULE)

    def test_manager_can_create_edit_and_disable_request_buttons(self):
        self.assertIn('CommandHandler("request_types", request_types_command)', MODULE)
        self.assertIn("➕ إنشاء زر استئذان", MODULE)
        self.assertIn("duration_minutes", MODULE)
        self.assertIn("active=not current[4]", MODULE)
        self.assertIn("register_request_type_handlers(application)", BOT)

    def test_custom_requests_start_timer_only_after_approval(self):
        self.assertIn('callback_data=f"approve_{request_type}_{request_id}_{update.effective_user.id}"', MODULE)
        self.assertIn("elif custom_request_type:", BOT)
        self.assertIn("await start_timer(context, target_id, timer_minutes, type_)", BOT)
        self.assertIn("not _can_manage(query.from_user.id, emp.get('branch_id'))", BOT)

    def test_custom_button_uses_employee_branch_and_database_name(self):
        self.assertIn('employee = bot.get_employee_by_telegram_id(update.effective_user.id)', MODULE)
        self.assertIn('employee["branch_id"]', MODULE)
        self.assertIn('employee.get("full_name") or "موظف"', MODULE)
        self.assertIn("get_employee_buttons(employee[\"branch_id\"])", BOT)

    def test_timer_displays_custom_request_name(self):
        self.assertIn("get_request_type_by_code(employee.get(\"branch_id\"), type_)", BOT)
        self.assertIn("custom[2] if custom else \"الاستئذان\"", BOT)

    def test_new_request_types_have_bounded_durations(self):
        self.assertIn("1 <= int(raw) <= 240", MODULE)
        self.assertIn("duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 1 AND 240)", MIGRATION)


if __name__ == "__main__":
    unittest.main()
