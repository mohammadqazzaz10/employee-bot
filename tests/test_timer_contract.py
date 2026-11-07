from datetime import timedelta
from pathlib import Path
import os
import subprocess
import sys
import unittest

import bot

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


class TimerContractTests(unittest.TestCase):
    def test_countdown_uses_one_repeating_job_not_one_job_per_second(self):
        self.assertIn("run_repeating(", SOURCE)
        self.assertIn("interval=5", SOURCE)
        self.assertNotIn("range(duration_seconds + 2)", SOURCE)

    def test_approved_timer_start_is_durable_and_retried(self):
        self.assertIn("timer_start_outbox", SOURCE)
        self.assertIn("returned_at IS NULL", SOURCE)
        self.assertIn("returned_at = CURRENT_TIMESTAMP", SOURCE)
        self.assertIn('"save_outbox"', SOURCE)
        self.assertIn('"outbox_list"', SOURCE)
        self.assertIn("process_pending_timer_starts", SOURCE)
        self.assertIn("interval=30", SOURCE)

    def test_timer_state_is_persisted_and_restored(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS active_timers", SOURCE)
        self.assertIn("async def restore_active_timers", SOURCE)
        self.assertIn(".post_init(restore_active_timers)", SOURCE)
        self.assertIn("WHERE completed = FALSE", SOURCE)

    def test_expiry_notification_has_return_confirmation_button(self):
        self.assertIn('callback_data=f"returned_{type_}_{user_id}_{msg_id}"', SOURCE)
        self.assertIn("Keep the timer active so the next tick retries", SOURCE)
        self.assertIn('_timer_db("clear_matching", user_id, type_, timer_message_id)', SOURCE)

    def test_employee_menu_buttons_are_wired_to_real_handlers(self):
        for label in (
            "🚬 طلب استراحة التدخين",
            "☕ طلب استراحة الغداء",
            "🚪 طلب مغادرة",
            "🌴 طلب إجازة",
            "ℹ️ المساعدة",
        ):
            with self.subTest(label=label):
                self.assertIn(label, SOURCE)
        self.assertIn('filters.Regex(r"^🚬 طلب استراحة التدخين$")', SOURCE)
        self.assertIn('filters.Regex(r"^☕ طلب استراحة الغداء$")', SOURCE)
        self.assertIn('filters.Regex(r"^🚪 طلب مغادرة$")', SOURCE)
        self.assertIn('filters.Regex(r"^🌴 طلب إجازة$")', SOURCE)

    def test_progress_bar_is_bounded_and_countdown_aware(self):
        self.assertEqual(len(bot.create_progress_bar(60, 60)), 12)
        self.assertEqual(len(bot.create_progress_bar(0, 60)), 12)
        self.assertEqual(bot.create_progress_bar(0, 60), "⬜" * 12)
        self.assertEqual(bot.create_progress_bar(60, 60), "🟩" * 12)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "PostgreSQL integration test requires DATABASE_URL")
class TimerPostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bot.initialize_database_tables():
            raise RuntimeError("Could not initialize database for timer integration test")
        subprocess.run(
            [sys.executable, "-m", "scripts.migrate"],
            cwd=ROOT,
            check=True,
        )

    def test_timer_can_be_saved_listed_completed_and_cleared(self):
        user_id = 987654321000
        message_id = 987654321
        start_at = bot.get_jordan_time()
        end_at = start_at + timedelta(minutes=5)

        self.assertTrue(
            bot._timer_db("save", user_id, message_id, "smoke", start_at, end_at)
        )
        rows = bot._timer_db("list")
        self.assertIsInstance(rows, list)
        row = next((item for item in rows if item[0] == user_id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row[1], message_id)
        self.assertEqual(row[2], "smoke")
        self.assertTrue(bot._timer_db("is_active", user_id))
        self.assertTrue(bot._timer_db("has_unreturned", user_id))

        self.assertTrue(bot._timer_db("complete", user_id))
        self.assertFalse(bot._timer_db("is_active", user_id))
        self.assertTrue(bot._timer_db("has_unreturned", user_id))

        # Reusing a user ID starts a fresh timer; explicit return clears it again.
        self.assertTrue(
            bot._timer_db("save", user_id, message_id + 1, "break", start_at, end_at)
        )
        self.assertFalse(bot._timer_db("clear_matching", user_id, "break", message_id))
        self.assertTrue(bot._timer_db("is_active", user_id))
        self.assertTrue(bot._timer_db("complete", user_id))
        self.assertFalse(bot._timer_db("is_active", user_id))
        # Expiry marks the timer complete before the employee presses Return.
        self.assertTrue(bot._timer_db("clear_matching", user_id, "break", message_id + 1))
        self.assertFalse(bot._timer_db("is_active", user_id))
        self.assertFalse(bot._timer_db("has_unreturned", user_id))


if __name__ == "__main__":
    unittest.main()
