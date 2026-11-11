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

    def test_portal_is_rtl_and_has_employee_login(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('lang="ar" dir="rtl"', html)
        self.assertIn('id="employeeCode"', html)
        self.assertIn('id="password"', html)
        self.assertIn('id="loginForm"', html)

    def test_client_does_not_store_credentials_in_browser_storage(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)
        self.assertIn('credentials: "same-origin"', js)

    def test_service_worker_never_caches_api_responses(self):
        sw = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertIn('url.pathname.startsWith("/api/")', sw)

    def test_readme_marks_api_as_required_before_production(self):
        readme = (WEB / "README.md").read_text(encoding="utf-8")
        self.assertIn("not production-authentication-ready", readme)
        self.assertIn("Do not publish", readme)


if __name__ == "__main__":
    unittest.main()
