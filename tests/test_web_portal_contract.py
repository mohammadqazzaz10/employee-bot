from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class EmployeePortalContractTests(unittest.TestCase):
    def test_pwa_manifest_is_arabic_rtl_and_installable(self):
        manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["lang"], "ar")
        self.assertEqual(manifest["dir"], "rtl")
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(manifest["icons"])

    def test_portal_contains_employee_activation_and_manager_views(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('lang="ar" dir="rtl"', html)
        for element_id in ("employeeCode", "password", "loginForm", "activationForm",
                           "requestTypes", "managerRequests", "employeesTable", "notificationList"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)

    def test_client_does_not_store_credentials_in_browser_storage(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)
        self.assertIn('credentials: "same-origin"', js)
        self.assertIn('X-CSRF-Token', js)

    def test_service_worker_never_caches_api_responses(self):
        sw = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertIn('url.pathname.startsWith("/api/")', sw)

    def test_backend_has_hashed_sessions_csrf_and_secure_cookie_controls(self):
        source = (ROOT / "web_app.py").read_text(encoding="utf-8")
        for fragment in ("generate_password_hash", "check_password_hash", "token_hash",
                         "csrf_hash", "httponly=True", "secure=app.config[\"COOKIE_SECURE\"]",
                         "samesite=\"Lax\"", "MAX_LOGIN_FAILURES", "WEB_BOOTSTRAP_TOKEN"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_backend_uses_session_identity_and_branch_scope(self):
        source = (ROOT / "web_app.py").read_text(encoding="utf-8")
        self.assertIn("def _branch_allowed", source)
        self.assertIn("def auth_required", source)
        self.assertIn("WHERE id=%s AND status='pending'", source)
        self.assertIn("WHERE employee_id=%s", source)
        self.assertNotIn("request.json.get(\"employee_id\")", source)

    def test_no_mock_employee_actions_are_left_in_client(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("سيتم تفعيل هذه الخدمة", js)
        self.assertIn('api("/employee/requests"', js)
        self.assertIn('api("/admin/requests/"', js)
        self.assertIn('api("/employee/call-supervisor"', js)
        self.assertIn('api("/admin/settings"', js)
        self.assertIn('api("/admin/request-types"', js)
        self.assertIn('id="branchSettingsForm"', (WEB / "index.html").read_text(encoding="utf-8"))


    def test_live_portal_sync_refreshes_without_rebuilding_the_timer(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.setInterval(syncPortalData, 3000)", js)
        self.assertIn('document.addEventListener("visibilitychange"', js)
        self.assertIn('activePage === "page-manageRequests"', js)
        self.assertIn('if (timerKey === renderedTimerKey) return;', js)
        self.assertIn("lastRequestTypesRefresh", js)

    def test_live_sync_keeps_manager_filter_and_limits_background_refresh(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('await loadManagerRequests($("requestFilter")?.value || "pending", { silent: true });', js)
        self.assertIn('await loadAdminDashboard({ loadRequests: false });', js)
        self.assertIn("if (!currentUser || document.hidden || liveSyncBusy || portalMutationBusy) return;", js)

    def test_notifications_are_checked_frequently_without_page_reload(self):
        js = (WEB / "notifications.js").read_text(encoding="utf-8")
        self.assertIn("window.setInterval(pollNotifications, 15000)", js)
        self.assertNotIn("window.location.reload", js)

    def test_authenticated_sse_refreshes_dashboard_and_notifications(self):
        backend = (ROOT / "web_app.py").read_text(encoding="utf-8")
        client = (WEB / "notifications.js").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/events/notifications")', backend)
        self.assertIn('@app.get("/api/events/notifications")\n@auth_required', backend)
        self.assertIn('LISTEN {}', backend)
        self.assertIn('new EventSource("/api/events/notifications"', client)
        self.assertIn('window.dispatchEvent(new Event("employee-portal:refresh"))', client)
        self.assertIn('window.addEventListener("employee-portal:refresh"', app)

    def test_new_employee_codes_are_database_sequential_not_random(self):
        source = (ROOT / "web_app.py").read_text(encoding="utf-8")
        migration = (ROOT / "migrations" / "007_employee_web_portal.sql").read_text(encoding="utf-8")
        self.assertIn("INSERT INTO employees(phone_number,full_name,job_title,branch_id,is_active)", source)
        self.assertNotIn('secrets.token_hex(4)', source)
        self.assertIn("NEW.employee_code := 'EMP' || LPAD(NEW.id::TEXT, 5, '0')", migration)
        self.assertIn("trg_employees_assign_web_code", migration)

    def test_employee_portal_identity_notifications_and_live_timer_ui(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        js = (WEB / "app.js").read_text(encoding="utf-8")
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        for element_id in ("employeeIdentityCard", "identityName", "identityCode",
                           "headerNotifications", "headerNotificationBadge", "timerProgress",
                           "timerProgressText"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html)
        self.assertIn('Math.floor(seconds / 3600)', js)
        self.assertIn('window.confirm("هل تريد إرسال طلب "', js)
        self.assertIn('headerNotificationBadge', js)
        self.assertIn('.employee-identity-card', css)
        self.assertIn('.timer-progress-track', css)

    def test_frontend_and_backend_are_connected_by_same_origin_api(self):
        source = (ROOT / "web_app.py").read_text(encoding="utf-8")
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/auth/login")', source)
        self.assertIn('@app.get("/api/auth/me")', source)
        self.assertIn('fetch("/api" + path', js)


if __name__ == "__main__":
    unittest.main()
