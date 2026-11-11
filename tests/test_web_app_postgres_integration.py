import os
import secrets
import unittest

import psycopg2
from web_app import app


@unittest.skipUnless(os.environ.get("CI") == "true" and os.environ.get("DATABASE_URL"),
                     "requires the isolated PostgreSQL database used by GitHub Actions")
class EmployeePortalPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {
            "DATABASE_URL": app.config["DATABASE_URL"],
            "DATABASE_SSLMODE": app.config["DATABASE_SSLMODE"],
            "COOKIE_SECURE": app.config["COOKIE_SECURE"],
            "WEB_BOOTSTRAP_TOKEN": app.config["WEB_BOOTSTRAP_TOKEN"],
        }
        app.config.update(
            DATABASE_URL=os.environ["DATABASE_URL"],
            DATABASE_SSLMODE="disable",
            COOKIE_SECURE=False,
            WEB_BOOTSTRAP_TOKEN=secrets.token_urlsafe(40),
        )
        cls.admin_client = app.test_client()
        cls.suffix = secrets.token_hex(4)
        cls.branch_ids = []
        cls.employee_ids = []

        response = cls.admin_client.post("/api/setup/bootstrap", json={
            "bootstrap_token": app.config["WEB_BOOTSTRAP_TOKEN"],
            "username": "webroot_" + cls.suffix,
            "full_name": "Web Integration Admin",
            "password": "Strong-Admin-Pass-2026!",
        })
        if response.status_code != 201:
            raise AssertionError("Unable to create isolated web super-admin for integration test: " + response.get_data(as_text=True))

        login = cls.admin_client.post("/api/auth/login", json={
            "username": "webroot_" + cls.suffix,
            "password": "Strong-Admin-Pass-2026!",
        })
        if login.status_code != 200:
            raise AssertionError("Web super-admin login failed: " + login.get_data(as_text=True))
        cls.admin_csrf = login.get_json()["csrf_token"]
        cls.admin_headers = {"X-CSRF-Token": cls.admin_csrf}

        branch_one = cls.admin_client.post("/api/admin/branches", headers=cls.admin_headers, json={
            "code": "webtest" + cls.suffix + "1",
            "name": "Web Test Branch 1 " + cls.suffix,
        })
        if branch_one.status_code != 201:
            raise AssertionError("Web branch creation failed: " + branch_one.get_data(as_text=True))
        cls.branch_ids.append(branch_one.get_json()["branch"]["id"])

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO branches(code,name,is_active) VALUES(%s,%s,TRUE) RETURNING id",
                    ("webtest" + cls.suffix + "2", "Web Test Branch 2 " + cls.suffix),
                )
                cls.branch_ids.append(cur.fetchone()[0])
            conn.commit()
        finally:
            conn.close()

        cls.employee_one = cls._create_employee(cls.branch_ids[0], "Employee One")
        cls.employee_two = cls._create_employee(cls.branch_ids[1], "Employee Two")

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO branch_request_types(branch_id,code,label,duration_minutes,is_active)
                       VALUES(%s,'webtest-break','استراحة اختبار',2,TRUE)""",
                    (cls.branch_ids[0],),
                )
            conn.commit()
        finally:
            conn.close()

        manager = cls.admin_client.post("/api/admin/users", headers=cls.admin_headers, json={
            "username": "webmgr_" + cls.suffix,
            "full_name": "Web Test Branch Manager",
            "role": "branch_manager",
            "branch_id": cls.branch_ids[0],
            "temporary_password": "Temporary-Manager-2026!",
        })
        if manager.status_code != 201:
            raise AssertionError("Unable to create web branch manager: " + manager.get_data(as_text=True))
        cls.manager_client = app.test_client()
        manager_login = cls.manager_client.post("/api/auth/login", json={
            "username": "webmgr_" + cls.suffix,
            "password": "Temporary-Manager-2026!",
        })
        if manager_login.status_code != 200:
            raise AssertionError("Web manager login failed: " + manager_login.get_data(as_text=True))
        cls.manager_csrf = manager_login.get_json()["csrf_token"]
        cls.manager_headers = {"X-CSRF-Token": cls.manager_csrf}
        cls.employee_client = app.test_client()
        activation = cls.admin_client.post(
            f"/api/admin/employees/{cls.employee_one['id']}/activation",
            headers=cls.admin_headers, json={},
        )
        if activation.status_code != 201:
            raise AssertionError("Employee activation code could not be issued: " + activation.get_data(as_text=True))
        activated = cls.employee_client.post("/api/auth/activate", json={
            "activation_code": activation.get_json()["activation_code"],
            "password": "Employee-Password-2026!",
        })
        if activated.status_code != 201:
            raise AssertionError("Employee activation failed: " + activated.get_data(as_text=True))
        employee_login = cls.employee_client.post("/api/auth/login", json={
            "employee_code": activated.get_json()["employee_code"],
            "password": "Employee-Password-2026!",
        })
        if employee_login.status_code != 200:
            raise AssertionError("Employee login failed: " + employee_login.get_data(as_text=True))
        cls.employee_csrf = employee_login.get_json()["csrf_token"]
        cls.employee_headers = {"X-CSRF-Token": cls.employee_csrf}

    @classmethod
    def _create_employee(cls, branch_id, label):
        phone = "07" + str(secrets.randbelow(90000000) + 10000000)
        response = cls.admin_client.post("/api/admin/employees", headers=cls.admin_headers, json={
            "full_name": label + " " + cls.suffix,
            "job_title": "Cashier",
            "phone_number": phone,
            "branch_id": branch_id,
        })
        if response.status_code != 201:
            raise AssertionError("Employee creation failed: " + response.get_data(as_text=True))
        employee = response.get_json()["employee"]
        cls.employee_ids.append(employee["id"])
        return employee

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.original.items():
            app.config[key] = value

    def test_employee_activation_requests_timer_return_and_manager_branch_scope(self):
        # Temporary manager credentials must not authorize normal operations.
        blocked = self.manager_client.get("/api/admin/overview")
        self.assertEqual(blocked.status_code, 403)
        changed = self.manager_client.post("/api/auth/change-password", headers=self.manager_headers, json={
            "current_password": "Temporary-Manager-2026!",
            "new_password": "Permanent-Manager-2026!",
        })
        self.assertEqual(changed.status_code, 200)

        # Branch manager can change only their own branch policy.
        settings = self.manager_client.get("/api/admin/settings")
        self.assertEqual(settings.status_code, 200)
        saved = self.manager_client.patch("/api/admin/settings", headers=self.manager_headers, json={
            "smoke_enabled": True, "smoke_duration_minutes": 8, "smoke_daily_limit": 4,
            "smoke_start_time": "00:00", "smoke_end_time": None, "smoke_gap_minutes": 0,
        })
        self.assertEqual(saved.status_code, 200)
        forbidden_settings = self.manager_client.get(
            "/api/admin/settings?branch_id=" + str(self.branch_ids[1])
        )
        self.assertEqual(forbidden_settings.status_code, 403)

        # Custom request types are audited and scoped to the selected branch.
        created_type = self.manager_client.post("/api/admin/request-types", headers=self.manager_headers, json={
            "label": "طلب مخصص", "duration_minutes": 7,
        })
        self.assertEqual(created_type.status_code, 201)
        type_id = created_type.get_json()["request_type"]["id"]
        changed_type = self.manager_client.patch(
            f"/api/admin/request-types/{type_id}", headers=self.manager_headers,
            json={"duration_minutes": 9, "is_active": False},
        )
        self.assertEqual(changed_type.status_code, 200)

        # The employee can see only their branch request types and submit a request.
        types = self.employee_client.get("/api/employee/request-types")
        self.assertEqual(types.status_code, 200)
        visible_codes = [row["code"] for row in types.get_json()["request_types"]]
        self.assertIn("webtest-break", visible_codes)
        self.assertIn("smoke", visible_codes)
        self.assertIn("break", visible_codes)
        created_request = self.employee_client.post("/api/employee/requests", headers=self.employee_headers, json={
            "request_type": "webtest-break",
        })
        self.assertEqual(created_request.status_code, 201)
        request_id = created_request.get_json()["request_id"]

        # A branch manager can decide the request; employee receives a durable timer.
        inbox = self.manager_client.get("/api/admin/requests?status=pending")
        self.assertEqual(inbox.status_code, 200)
        self.assertIn(request_id, [row["id"] for row in inbox.get_json()["requests"]])
        decision = self.manager_client.post(
            f"/api/admin/requests/{request_id}/decision",
            headers=self.manager_headers, json={"decision": "approved"},
        )
        self.assertEqual(decision.status_code, 200)
        summary = self.employee_client.get("/api/employee/summary").get_json()
        self.assertIsNotNone(summary["timer"])
        self.assertEqual(summary["timer"]["request_type"], "webtest-break")

        # Expiry is persisted before return acknowledgement; no second request can start early.
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE web_timers SET ends_at=CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE employee_id=%s",
                    (self.employee_one["id"],),
                )
            conn.commit()
        finally:
            conn.close()
        expired = self.employee_client.get("/api/employee/summary").get_json()
        self.assertIsNotNone(expired["timer"])
        self.assertIsNotNone(expired["timer"]["completed_at"])
        returned = self.employee_client.post("/api/employee/timer/return", headers=self.employee_headers, json={})
        self.assertEqual(returned.status_code, 200)

        # Smoke approval atomically records the daily counter and last-smoke timestamp.
        smoke_request = self.employee_client.post("/api/employee/requests", headers=self.employee_headers, json={
            "request_type": "smoke",
        })
        self.assertEqual(smoke_request.status_code, 201)
        smoke_id = smoke_request.get_json()["request_id"]
        smoke_approval = self.manager_client.post(
            f"/api/admin/requests/{smoke_id}/decision",
            headers=self.manager_headers, json={"decision": "approved"},
        )
        self.assertEqual(smoke_approval.status_code, 200)
        self.employee_client.get("/api/employee/summary")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count FROM daily_cigarettes WHERE employee_id=%s AND date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Amman')::date",
                            (self.employee_one["id"],))
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM cigarette_times WHERE employee_id=%s", (self.employee_one["id"],))
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("UPDATE web_timers SET ends_at=CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE employee_id=%s",
                            (self.employee_one["id"],))
            conn.commit()
        finally:
            conn.close()
        self.employee_client.get("/api/employee/summary")
        self.assertEqual(self.employee_client.post("/api/employee/timer/return", headers=self.employee_headers, json={}).status_code, 200)

        # Lunch is recorded once per Jordan-local work date and is also timed.
        lunch_request = self.employee_client.post("/api/employee/requests", headers=self.employee_headers, json={
            "request_type": "break",
        })
        self.assertEqual(lunch_request.status_code, 201)
        lunch_id = lunch_request.get_json()["request_id"]
        lunch_approval = self.manager_client.post(
            f"/api/admin/requests/{lunch_id}/decision",
            headers=self.manager_headers, json={"decision": "approved"},
        )
        self.assertEqual(lunch_approval.status_code, 200)
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT taken FROM lunch_breaks WHERE employee_id=%s AND date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Amman')::date",
                            (self.employee_one["id"],))
                self.assertTrue(cur.fetchone()[0])
                cur.execute("UPDATE web_timers SET ends_at=CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE employee_id=%s",
                            (self.employee_one["id"],))
            conn.commit()
        finally:
            conn.close()
        self.employee_client.get("/api/employee/summary")
        self.assertEqual(self.employee_client.post("/api/employee/timer/return", headers=self.employee_headers, json={}).status_code, 200)
        duplicate_lunch = self.employee_client.post("/api/employee/requests", headers=self.employee_headers, json={
            "request_type": "break",
        })
        self.assertEqual(duplicate_lunch.status_code, 409)

        # Branch managers cannot read or create records in another branch.
        manager_employees = self.manager_client.get("/api/admin/employees").get_json()["employees"]
        self.assertTrue(manager_employees)
        self.assertTrue(all(row["branch_name"].startswith("Web Test Branch 1") for row in manager_employees))
        cross_branch_create = self.manager_client.post("/api/admin/employees", headers=self.manager_headers, json={
            "full_name": "Unauthorized Cross Branch",
            "job_title": "Cashier",
            "phone_number": "07999999991",
            "branch_id": self.branch_ids[1],
        })
        self.assertEqual(cross_branch_create.status_code, 403)

        # Missing CSRF headers are rejected; notifications are scoped and readable.
        csrf_rejected = self.employee_client.post("/api/employee/call-supervisor", json={})
        self.assertEqual(csrf_rejected.status_code, 403)
        called = self.employee_client.post("/api/employee/call-supervisor", headers=self.employee_headers, json={
            "message": "اختبار استدعاء المشرف",
        })
        self.assertEqual(called.status_code, 201)
        manager_notifications = self.manager_client.get("/api/notifications").get_json()["notifications"]
        self.assertTrue(any(item["notification_type"] == "employee_call" for item in manager_notifications))

        # Super-admin can see employees across branches; logout revokes the session.
        all_employees = self.admin_client.get("/api/admin/employees").get_json()["employees"]
        self.assertGreaterEqual(len(all_employees), 2)
        logged_out = self.manager_client.post("/api/auth/logout", headers=self.manager_headers, json={})
        self.assertEqual(logged_out.status_code, 200)
        self.assertEqual(self.manager_client.get("/api/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
