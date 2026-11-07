from pathlib import Path
import unittest

from datetime import time

from branch_settings import _parse, LABELS, NUMERIC, TIMES, TOGGLES
from bot import is_within_time_window

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
SETTINGS_SOURCE = (ROOT / "branch_settings.py").read_text(encoding="utf-8")


class BranchSettingsContractTests(unittest.TestCase):
    def test_policy_is_persisted_per_branch_with_audit_log(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS branch_settings", BOT_SOURCE)
        self.assertIn("CREATE TABLE IF NOT EXISTS branch_settings_audit", BOT_SOURCE)
        self.assertIn("branch_id BIGINT PRIMARY KEY", BOT_SOURCE)
        self.assertIn("changed_by BIGINT NOT NULL", BOT_SOURCE)

    def test_admin_settings_panel_is_registered(self):
        self.assertIn('CommandHandler("settings", settings_command)', SETTINGS_SOURCE)
        self.assertIn('pattern=r"^branchcfg:"', SETTINGS_SOURCE)
        self.assertIn("register_settings_handlers(application)", BOT_SOURCE)
        self.assertIn("def _resolve_branch", SETTINGS_SOURCE)
        self.assertIn("assign_branch_admin", SETTINGS_SOURCE)
        self.assertIn('"supervisor"', SETTINGS_SOURCE)
        self.assertIn('"branch_manager"', SETTINGS_SOURCE)
        self.assertIn('CommandHandler("remove_branch_admin", remove_branch_admin_command)', SETTINGS_SOURCE)
        self.assertIn('CommandHandler("add_branch", add_branch_command)', SETTINGS_SOURCE)
        self.assertIn('CommandHandler("list_branches", list_branches_command)', SETTINGS_SOURCE)
        self.assertIn("def create_branch(code, name, actor_id)", SETTINGS_SOURCE)

    def test_smoking_policy_is_read_from_branch_settings(self):
        self.assertIn("settings['smoke_daily_limit']", BOT_SOURCE)
        self.assertIn("settings['smoke_duration_minutes']", BOT_SOURCE)
        self.assertIn("settings['smoke_gap_minutes']", BOT_SOURCE)
        self.assertIn("settings['smoke_start_time']", BOT_SOURCE)
        self.assertIn("settings['smoke_enabled']", BOT_SOURCE)

    def test_lunch_policy_is_read_from_branch_settings(self):
        self.assertIn("settings['lunch_enabled']", BOT_SOURCE)
        decision = BOT_SOURCE.split("def decide_request", 1)[1].split(
            "\\n# --- دوال السجائر", 1
        )[0]
        self.assertIn("lunch_duration_minutes", decision)
        self.assertIn("timer_duration = smoke_duration if request_type == \"smoke\" else lunch_duration", decision)
        self.assertIn("settings['lunch_start_time']", BOT_SOURCE)
        self.assertIn("settings['lunch_end_time']", BOT_SOURCE)

    def test_numeric_values_are_bounded(self):
        self.assertEqual(_parse("smoke_daily_limit", "0"), 0)
        self.assertEqual(_parse("smoke_daily_limit", "30"), 30)
        self.assertEqual(_parse("smoke_duration_minutes", "6"), 6)
        with self.assertRaises(ValueError):
            _parse("smoke_daily_limit", "31")
        with self.assertRaises(ValueError):
            _parse("lunch_duration_minutes", "0")

    def test_times_and_optional_end_times_are_validated(self):
        self.assertEqual(_parse("smoke_start_time", "10:00").strftime("%H:%M"), "10:00")
        self.assertIsNone(_parse("smoke_end_time", "off"))
        with self.assertRaises(ValueError):
            _parse("lunch_start_time", "25:90")

    def test_time_windows_support_overnight_and_open_ended_ranges(self):
        self.assertTrue(is_within_time_window(time(23, 0), time(22, 0), time(6, 0)))
        self.assertTrue(is_within_time_window(time(5, 59), time(22, 0), time(6, 0)))
        self.assertFalse(is_within_time_window(time(12, 0), time(22, 0), time(6, 0)))
        self.assertTrue(is_within_time_window(time(11, 0), time(10, 0), None))
        self.assertFalse(is_within_time_window(time(9, 0), time(10, 0), None))
        self.assertTrue(is_within_time_window(time(12, 0), None, None))

    def test_toggle_values_are_explicit(self):
        self.assertTrue(_parse("smoke_enabled", "نعم"))
        self.assertFalse(_parse("lunch_enabled", "لا"))
        with self.assertRaises(ValueError):
            _parse("smoke_enabled", "maybe")


if __name__ == "__main__":
    unittest.main()
