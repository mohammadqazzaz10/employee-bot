from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "001_multibranch_foundation.sql"


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


if __name__ == "__main__":
    unittest.main()
