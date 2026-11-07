import os
import unittest
from pathlib import Path

import psycopg2

import bot


@unittest.skipUnless(
    os.environ.get("DATABASE_URL"),
    "Set DATABASE_URL to run PostgreSQL integration tests",
)
class PostgreSQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bot.initialize_database_tables():
            raise RuntimeError("Application failed to initialize its PostgreSQL schema")

    def test_schema_admin_bootstrap_request_lifecycle_and_soft_delete(self):
        expected_tables = {
            "employees",
            "requests",
            "daily_cigarettes",
            "admins",
            "lunch_breaks",
            "cigarette_times",
            "absences",
        }
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                actual_tables = {row[0] for row in cur.fetchall()}
                self.assertTrue(expected_tables.issubset(actual_tables))

                cur.execute(
                    "SELECT is_super_admin FROM admins WHERE telegram_id = %s",
                    (bot.SUPER_ADMIN_TELEGRAM_ID,),
                )
                self.assertEqual(cur.fetchone(), (True,))
        finally:
            conn.close()

        phone = "+962781234567"
        employee_id = bot.save_employee(None, phone, "CI Test Employee")
        self.assertIsNotNone(employee_id)
        self.assertTrue(bot.verify_employee("0781234567"))
        employee = bot.get_employee_by_phone("0781234567")
        self.assertIsNotNone(employee)

        request_id = bot.create_request(employee_id, "smoke", "integration test")
        self.assertIsNotNone(request_id)
        self.assertTrue(
            bot.decide_request(
                request_id,
                employee_id,
                "smoke",
                "approved",
                bot.SUPER_ADMIN_TELEGRAM_ID,
            )
        )
        self.assertFalse(
            bot.decide_request(
                request_id,
                employee_id,
                "smoke",
                "rejected",
                bot.SUPER_ADMIN_TELEGRAM_ID,
            )
        )

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, decided_by FROM requests WHERE id = %s",
                    (request_id,),
                )
                self.assertEqual(
                    cur.fetchone(),
                    ("approved", bot.SUPER_ADMIN_TELEGRAM_ID),
                )
        finally:
            conn.close()

        self.assertTrue(bot.delete_employee_by_phone(phone))
        self.assertFalse(bot.verify_employee(phone))
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM requests WHERE id = %s", (request_id,))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()

        # Exercise the actual multi-branch SQL migration on the disposable CI database.
        migration_sql = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "001_multibranch_foundation.sql"
        ).read_text(encoding="utf-8")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(migration_sql)
                cur.execute(
                    "SELECT branch_id FROM employees WHERE id = %s",
                    (employee_id,),
                )
                self.assertIsNotNone(cur.fetchone()[0])
            conn.commit()
        finally:
            conn.close()

        # Legacy INSERT statements must inherit the default branch via the trigger.
        second_employee_id = bot.save_employee(
            None, "+962789876543", "Branch Trigger Employee"
        )
        self.assertIsNotNone(second_employee_id)
        second_request_id = bot.create_request(
            second_employee_id, "break", "branch trigger integration test"
        )
        self.assertIsNotNone(second_request_id)

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT e.branch_id, r.branch_id "
                    "FROM employees e JOIN requests r ON r.employee_id = e.id "
                    "WHERE e.id = %s AND r.id = %s",
                    (second_employee_id, second_request_id),
                )
                employee_branch, request_branch = cur.fetchone()
                self.assertIsNotNone(employee_branch)
                self.assertEqual(employee_branch, request_branch)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
