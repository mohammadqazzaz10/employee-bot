from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "001_multibranch_foundation.sql"
INTEGRITY_MIGRATION = ROOT / "migrations" / "005_preproduction_integrity.sql"
TIMER_ACK_MIGRATION = ROOT / "migrations" / "006_timer_return_acknowledgements.sql"
RUNNER = ROOT / "scripts" / "migrate.py"


class MigrationContractTests(unittest.TestCase):
    def test_migration_is_additive_and_has_branch_entities(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        for fragment in (
            "create table if not exists branches",
            "insert into branches",
            "alter table employees",
            "branch_id",
            "create table if not exists admin_branch_roles",
            "create index if not exists idx_requests_branch_status_date",
            "commit;",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, sql)

    def test_existing_employee_records_are_backfilled(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("where branch_id is null", sql)
        self.assertIn("where r.employee_id = e.id", sql)

    def test_branch_roles_are_limited_to_supported_roles(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("'branch_manager'", sql)
        self.assertIn("'supervisor'", sql)
        self.assertIn("unique (telegram_id, branch_id)", sql)

    def test_integrity_migration_guards_active_phones_and_branch_settings(self):
        sql = INTEGRITY_MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("uq_employees_active_phone", sql)
        self.assertIn("uq_employees_active_telegram_id", sql)
        self.assertIn("duplicate active telegram employee ids", sql)
        self.assertIn("branch_settings_branch_id_fkey", sql)
        self.assertIn("timer_start_outbox", sql)
        self.assertIn("duplicate active employee phone numbers", sql)

    def test_timer_return_ack_migration_separates_expiry_from_return(self):
        sql = TIMER_ACK_MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("returned_at", sql)
        self.assertIn("idx_active_timers_unreturned", sql)

    def test_migration_runner_checks_checksums_and_serializes_execution(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("hashlib.sha256", source)
        self.assertIn("pg_advisory_lock", source)
        self.assertIn("schema_migrations", source)
        self.assertIn("--check", source)

    def test_legacy_inserts_receive_default_branch_context(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        self.assertIn("assign_default_employee_branch", sql)
        self.assertIn("trg_employees_default_branch", sql)
        self.assertIn("assign_request_employee_branch", sql)
        self.assertIn("trg_requests_employee_branch", sql)


if __name__ == "__main__":
    unittest.main()
