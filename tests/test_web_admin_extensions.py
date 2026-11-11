from pathlib import Path
import unittest

from web_app import app

ROOT = Path(__file__).resolve().parents[1]


class WebAdminExtensionContractTests(unittest.TestCase):
    def test_admin_lifecycle_broadcast_meeting_and_site_settings_routes_exist(self):
        routes = {(rule.rule, method) for rule in app.url_map.iter_rules() for method in rule.methods}
        expected = [
            ("/api/admin/employees/<int:employee_id>/status", "PATCH"),
            ("/api/admin/branches/<int:branch_id>", "PATCH"),
            ("/api/admin/announcements", "POST"),
            ("/api/admin/meetings", "GET"),
            ("/api/admin/meetings", "POST"),
            ("/api/admin/meetings/<int:meeting_id>", "PATCH"),
            ("/api/admin/site-settings", "GET"),
            ("/api/admin/site-settings", "PATCH"),
            ("/api/site-info", "GET"),
            ("/api/system/process-meeting-reminders", "GET"),
        ]
        for route in expected:
            with self.subTest(route=route):
                self.assertIn(route, routes)

    def test_new_admin_actions_require_authentication(self):
        client = app.test_client()
        for path, method in [
            ("/api/admin/announcements", "post"),
            ("/api/admin/meetings", "get"),
            ("/api/admin/site-settings", "get"),
            ("/api/admin/employees/1/status", "patch"),
            ("/api/system/process-meeting-reminders", "get"),
        ]:
            response = getattr(client, method)(path, json={} if method in {"post", "patch"} else None)
            self.assertEqual(response.status_code, 401, path)

    def test_migrations_and_frontend_include_new_controls(self):
        migration = (ROOT / "migrations" / "008_web_admin_controls.sql").read_text(encoding="utf-8")
        settings_migration = (ROOT / "migrations" / "009_web_site_settings.sql").read_text(encoding="utf-8")
        js = (ROOT / "web" / "admin.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS web_meetings", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS web_app_settings", settings_migration)
        for fragment in ("/admin/announcements", "/admin/meetings", "/admin/site-settings",
                         "/admin/employees/", "process-meeting-reminders"):
            self.assertIn(fragment, js)
        self.assertIn('/admin.js', html)


    def test_admin_extension_is_registered_before_direct_server_start(self):
        source = (ROOT / "web_app.py").read_text(encoding="utf-8")
        register = source.index("import web_admin_extensions")
        server_start = source.index('if __name__ == "__main__":')
        self.assertLess(register, server_start)
        self.assertIn('sys.modules.setdefault("web_app", sys.modules[__name__])', source)

    def test_notification_poll_works_with_httponly_session_cookie(self):
        source = (ROOT / "web" / "admin.js").read_text(encoding="utf-8")
        self.assertNotIn('document.cookie.includes("employee_session=")', source)
        self.assertIn('fetch("/api/notifications",{credentials:"same-origin",cache:"no-store"})', source)
        self.assertIn('localStorage.setItem("employee-sound-enabled",String(soundEnabled))', source)

    def test_realtime_notifications_poll_sound_toast_and_service_worker_actions(self):
        js = (ROOT / "web" / "notifications.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("window.setInterval(pollNotifications, 15000)", js)
        self.assertIn("showToast(item)", js)
        self.assertIn("playAlertTone()", js)
        self.assertIn("registration.showNotification", js)
        self.assertIn("aria-live", js)
        self.assertIn('/notifications.js', html)
        self.assertIn('self.addEventListener("notificationclick"', sw)
        self.assertIn("OPEN_NOTIFICATIONS", sw)

if __name__ == "__main__":
    unittest.main()
