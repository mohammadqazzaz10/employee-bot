from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


class SecurityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BOT.read_text(encoding="utf-8")

    def test_contact_is_authorized_before_employee_record_is_saved(self):
        handler = self.source.split("async def handle_contact", 1)[1].split(
            "\ndef create_progress_bar", 1
        )[0]
        self.assertLess(handler.index("registered_employee = get_employee_by_phone(phone)"),
                        handler.index("save_employee(contact.user_id, phone, registered_name)"))
        self.assertIn("if not registered_employee", handler)

    def test_approval_callback_handler_does_not_consume_other_feature_callbacks(self):
        self.assertIn(
            'CallbackQueryHandler(button_callback, pattern=r"^(approve|reject|returned)_")',
            self.source,
        )

    def test_callback_actions_are_validated(self):
        handler = self.source.split("async def button_callback", 1)[1].split(
            "\nasync def my_id_command", 1
        )[0]
        self.assertIn('data[0] not in {"approve", "reject", "returned"}', handler)
        self.assertIn('type_ not in {"smoke", "break", "leave", "vacation"}', handler)

    def test_only_target_employee_can_confirm_return(self):
        handler = self.source.split("async def button_callback", 1)[1].split(
            "\nasync def my_id_command", 1
        )[0]
        self.assertIn("if query.from_user.id != target_id:", handler)

    def test_only_admin_can_approve_or_reject(self):
        handler = self.source.split("async def button_callback", 1)[1].split(
            "\nasync def my_id_command", 1
        )[0]
        self.assertIn("if not is_admin(query.from_user.id):", handler)
        self.assertLess(handler.index("if not is_admin(query.from_user.id):"),
                        handler.index("if action == \"approve\":"))

    def test_missing_employee_is_handled_without_indexing_none(self):
        handler = self.source.split("async def button_callback", 1)[1].split(
            "\nasync def my_id_command", 1
        )[0]
        self.assertIn("if not emp:", handler)


if __name__ == "__main__":
    unittest.main()
