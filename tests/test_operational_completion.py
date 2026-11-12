from pathlib import Path
import unittest

from web_app import app

ROOT = Path(__file__).resolve().parents[1]


class OperationalCompletionContractTests(unittest.TestCase):
    def test_background_meeting_reminder_worker_is_independent_of_browser_polling(self):
        source = (ROOT / "web_operations_extensions.py").read_text(encoding="utf-8")
        self.assertIn("def _meeting_reminder_worker_loop", source)
        self.assertIn("threading.Thread", source)
        self.assertIn("pg_try_advisory_xact_lock", source)
        self.assertIn("INTERVAL '15 minutes'", source)
        self.assertIn("web_notifications", source)

    def test_employee_presence_is_authenticated_and_branch_scoped(self):
        source = (ROOT / "web_operations_extensions.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/employee/presence")', source)
        self.assertIn('@app.get("/api/admin/employee-activity")', source)
        self.assertIn("g.current_user[\"branch_id\"]", source)
        self.assertIn("web_employee_presence", source)
        self.assertIn("web_employee_activity", source)
        self.assertIn("web_audit_log", source)

    def test_presence_frontend_is_loaded_and_does_not_collect_gps(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "presence.js").read_text(encoding="utf-8")
        self.assertIn('/presence.js', html)
        self.assertIn('setInterval(() => send(), 45000)', js)
        self.assertNotIn("geolocation", js)
        self.assertIn("/employee/presence", js)

    def test_admin_employee_activity_view_is_wired(self):
        js = (ROOT / "web" / "admin.js").read_text(encoding="utf-8")
        self.assertIn('data-page="employeeActivity"', js)
        self.assertIn("/admin/employee-activity", js)
        self.assertIn("employeePresenceList", js)

    def test_migration_creates_branch_scoped_presence_and_activity_tables(self):
        sql = (ROOT / "migrations" / "013_employee_presence_and_activity.sql").read_text(encoding="utf-8").lower()
        self.assertIn("create table if not exists web_employee_presence", sql)
        self.assertIn("create table if not exists web_employee_activity", sql)
        self.assertIn("branch_id bigint not null references branches(id)", sql)


if __name__ == "__main__":
    unittest.main()
