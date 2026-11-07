from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
MIGRATION = ROOT / "migrations" / "001_multibranch_foundation.sql"
DEPLOYMENT = ROOT / "DEPLOYMENT.md"


class RuntimeConfigurationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BOT.read_text(encoding="utf-8")
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.deployment = DEPLOYMENT.read_text(encoding="utf-8")

    def test_database_url_is_required_and_used(self):
        self.assertIn('os.environ.get("DATABASE_URL"', self.source)
        self.assertIn('if not DATABASE_URL:', self.source)
        self.assertIn("psycopg2.connect(", self.source)

    def test_webhook_uses_render_generated_external_url(self):
        self.assertIn('os.environ.get("RENDER_EXTERNAL_URL")', self.source)
        self.assertIn('.strip().rstrip("/")', self.source)

    def test_no_hardcoded_admin_or_employee_phone_remains(self):
        self.assertNotIn("ADMIN_IDS = [1465191277]", self.source)
        self.assertNotIn("+962786644106", self.source)

    def test_super_admin_is_bootstrapped_from_environment(self):
        self.assertIn("SUPER_ADMIN_TELEGRAM_ID", self.source)
        self.assertIn("INSERT INTO admins", self.source)
        self.assertIn("DO UPDATE SET is_super_admin = TRUE", self.source)

    def test_jordanian_local_phone_numbers_are_canonicalized(self):
        self.assertIn('digits_only.startswith("07")', self.source)
        self.assertIn('"962" + digits_only[1:]', self.source)
        self.assertIn("UPDATE employees", self.source)
        self.assertIn("regexp_replace(phone_number", self.source)

    def test_admin_management_reports_database_write_failures(self):
        add_admin = self.source.split("async def add_admin", 1)[1].split(
            "async def remove_admin", 1
        )[0]
        self.assertIn("if add_admin_to_db(", add_admin)
        self.assertIn("تعذرت إضافة المدير", add_admin)

    def test_database_failure_does_not_grant_admin_access(self):
        admin_function = self.source.split("def is_admin", 1)[1].split(
            "def is_super_admin", 1
        )[0]
        self.assertIn("return False", admin_function)

    def test_employee_removal_preserves_history(self):
        delete_function = self.source.split("def delete_employee_by_phone", 1)[1].split(
            "def create_request", 1
        )[0]
        self.assertIn("UPDATE employees", delete_function)
        self.assertIn("is_active=FALSE", delete_function)
        self.assertNotIn("DELETE FROM employees", delete_function)

    def test_trigger_functions_use_valid_dollar_quoting(self):
        self.assertEqual(self.migration.count("RETURNS TRIGGER AS $$"), 2)
        self.assertEqual(self.migration.count("$$ LANGUAGE plpgsql;"), 2)

    def test_database_bootstrap_precedes_token_and_runtime_requires_all_migrations(self):
        main = self.source.split("def main():", 1)[1]
        self.assertLess(main.index("if not DATABASE_URL:"), main.index("if not BOT_TOKEN:"))
        self.assertIn("verify_required_migrations()", main)
        self.assertIn("checksum_sha256", self.source)
        self.assertIn("hashlib.sha256", self.source)
        self.assertIn("006_timer_return_acknowledgements.sql", self.source)

    def test_webhook_mode_requires_telegram_secret_header(self):
        self.assertIn('WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET"', self.source)
        self.assertIn("secret_token=WEBHOOK_SECRET", self.source)
        self.assertIn("WEBHOOK_SECRET", self.deployment)

    def test_render_setup_documents_all_required_values(self):
        for key in (
            "TELEGRAM_BOT_TOKEN",
            "DATABASE_URL",
            "SUPER_ADMIN_TELEGRAM_ID",
            "RENDER_EXTERNAL_URL",
            "WEBHOOK_SECRET",
        ):
            with self.subTest(key=key):
                self.assertIn(key, self.deployment)


if __name__ == "__main__":
    unittest.main()
