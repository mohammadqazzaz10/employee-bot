import unittest
from web_app import app


class EmployeeWebRuntimeTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, COOKIE_SECURE=False, WEB_BOOTSTRAP_TOKEN="")

    def test_homepage_serves_portal(self):
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("رفيق العمل".encode("utf-8"), response.data)

    def test_setup_page_is_available_for_one_time_admin_provisioning(self):
        response = app.test_client().get("/setup")
        self.assertEqual(response.status_code, 200)
        self.assertIn("WEB_BOOTSTRAP_TOKEN".encode("utf-8"), response.data)

    def test_bootstrap_fails_closed_without_configured_secret(self):
        response = app.test_client().post(
            "/api/setup/bootstrap",
            json={"bootstrap_token": "anything", "username": "root", "full_name": "Admin",
                  "password": "a-very-long-test-password"},
        )
        self.assertEqual(response.status_code, 403)

    def test_login_rejects_empty_credentials_before_database_access(self):
        response = app.test_client().post("/api/auth/login", json={})
        self.assertEqual(response.status_code, 400)

    def test_api_routes_are_present(self):
        routes = {rule.rule for rule in app.url_map.iter_rules()}
        for route in (
            "/api/auth/login", "/api/auth/me", "/api/auth/logout",
            "/api/auth/activate", "/api/employee/summary",
            "/api/employee/request-types", "/api/employee/requests",
            "/api/employee/call-supervisor", "/api/admin/overview",
            "/api/admin/employees", "/api/admin/employees/<int:employee_id>/reset-password",
            "/api/admin/requests",
            "/api/admin/settings", "/api/admin/request-types",
        ):
            with self.subTest(route=route):
                self.assertIn(route, routes)

    def test_health_endpoint_is_not_a_fake_success_without_database(self):
        old = app.config["DATABASE_URL"]
        app.config["DATABASE_URL"] = ""
        try:
            response = app.test_client().get("/healthz")
            self.assertEqual(response.status_code, 503)
        finally:
            app.config["DATABASE_URL"] = old


if __name__ == "__main__":
    unittest.main()
