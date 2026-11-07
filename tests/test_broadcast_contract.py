from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "broadcast.py"
BOT = ROOT / "bot.py"
MIGRATION = ROOT / "migrations" / "004_broadcast_acknowledgements.sql"


class BroadcastContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MODULE.read_text(encoding="utf-8")
        cls.bot = BOT.read_text(encoding="utf-8")
        cls.migration = MIGRATION.read_text(encoding="utf-8")

    def test_all_branches_are_super_admin_only(self):
        self.assertIn('if not bot.is_super_admin(user_id):', self.source)
        self.assertIn('if not bot.is_super_admin(q.from_user.id):', self.source)

    def test_branch_recipients_are_scoped(self):
        self.assertIn('AND branch_id=%s', self.source)
        self.assertIn('_resolve_branch(user_id, branch_id)', self.source)
        self.assertIn('_resolve_branch(q.from_user.id, int(audience))', self.source)

    def test_text_and_audio_broadcast_supported(self):
        for method in ('send_message', 'send_voice', 'send_audio'):
            self.assertIn(f'context.bot.{method}', self.source)

    def test_manager_must_choose_a_branch_explicitly(self):
        self.assertIn('اختر الفرع أو الجمهور', self.source)
        self.assertIn('for branch_id, name in _branches(user_id):', self.source)

    def test_individual_employee_selection_is_available(self):
        self.assertIn('EMPLOYEE_PAGE_SIZE = 8', self.source)
        self.assertIn('broadcast:employee:{telegram_id}', self.source)
        self.assertIn('broadcast:select_all', self.source)
        self.assertIn('broadcast:clear', self.source)
        self.assertIn('broadcast:continue', self.source)
        self.assertIn('broadcast_selection_callback', self.source)

    def test_selected_recipients_are_revalidated_before_sending(self):
        self.assertIn('pending.get("selected_ids", [])', self.source)
        self.assertIn('pending.get("recipients", [])', self.source)
        self.assertIn('not set(selected).issubset(set(allowed))', self.source)

    def test_acknowledgement_is_persisted_per_recipient(self):
        self.assertIn('broadcast_recipients', self.source)
        self.assertIn("delivery_status='acknowledged'", self.source)
        self.assertIn('acknowledged_at=CURRENT_TIMESTAMP', self.source)
        self.assertIn('UNIQUE (broadcast_id, telegram_id)', self.migration)

    def test_reminders_repeat_and_restore_after_restart(self):
        self.assertIn('REMINDER_INTERVAL_SECONDS = 60', self.source)
        self.assertIn('run_repeating', self.source)
        self.assertIn('restore_pending_broadcast_reminders', self.source)
        self.assertIn('await restore_pending_broadcast_reminders(application)', self.bot)
        self.assertIn('disable_notification=False', self.source)

    def test_confirmation_and_delivery_audit(self):
        self.assertIn('broadcast:send', self.source)
        self.assertIn('INSERT INTO broadcast_logs', self.source)
        self.assertIn('failed_count', self.source)

    def test_acknowledgement_handler_registered(self):
        self.assertIn('broadcast_ack_callback', self.source)
        self.assertIn('register_broadcast_handlers(application)', self.bot)


if __name__ == "__main__":
    unittest.main()
