import os
import unittest
from pathlib import Path

import psycopg2

import bot
from branch_settings import create_branch
from request_types import _save_new, _update_type, get_request_type


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

        # Apply branch isolation before exercising request decisions: the
        # production authorization query intentionally fails closed without it.
        migration_sql = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "001_multibranch_foundation.sql"
        ).read_text(encoding="utf-8")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(migration_sql)
            conn.commit()
        finally:
            conn.close()

        # The timer outbox migration must exist before an approval can commit.
        integrity_sql = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "005_preproduction_integrity.sql"
        ).read_text(encoding="utf-8")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(integrity_sql)
            conn.commit()
        finally:
            conn.close()

        timer_ack_sql = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "006_timer_return_acknowledgements.sql"
        ).read_text(encoding="utf-8")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(timer_ack_sql)
            conn.commit()
        finally:
            conn.close()

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM branches WHERE code='main'")
                main_branch_id = cur.fetchone()[0]
        finally:
            conn.close()

        phone = "+962781234567"
        employee_id = bot.save_employee_in_branch(
            phone, "CI Test Employee", "Cashier", main_branch_id,
            bot.SUPER_ADMIN_TELEGRAM_ID,
        )
        self.assertIsNotNone(employee_id)
        self.assertTrue(bot.verify_employee("0781234567"))
        employee = bot.get_employee_by_phone("0781234567")
        self.assertIsNotNone(employee)

        # Telegram linking must claim only the pre-registered row and be single-account.
        linked_id = bot.save_employee(555000111, "0781234567", "CI Test Employee")
        self.assertEqual(linked_id, employee_id)
        self.assertIsNone(bot.save_employee(555000222, "0781234567", "Other Account"))
        self.assertIsNone(bot.save_employee(555000111, "+962780000001", "Unregistered Number"))
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM employees WHERE id=%s", (employee_id,))
                self.assertEqual(cur.fetchone(), (555000111,))
        finally:
            conn.close()

        employee_settings = bot.get_branch_settings(employee["branch_id"])
        self.assertIsNotNone(employee_settings)
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE branch_settings SET smoke_start_time='00:00', smoke_gap_minutes=0 "
                    "WHERE branch_id=%s",
                    (employee["branch_id"],),
                )
            conn.commit()
        finally:
            conn.close()
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
        self.assertEqual(bot.get_smoke_count_db(employee_id), 1)
        self.assertIsNotNone(bot.get_last_cigarette_time(employee_id))
        timer_outbox = bot._timer_db("outbox_for_request", request_id)
        self.assertEqual(timer_outbox, (555000111, "smoke", employee_settings["smoke_duration_minutes"]))
        timer_start = bot.get_jordan_time()
        self.assertTrue(
            bot._timer_db(
                "save_outbox", 555000111, 777777, "smoke",
                timer_start, timer_start + __import__("datetime").timedelta(minutes=6),
                request_id,
            )
        )
        self.assertIsNone(bot._timer_db("outbox_for_request", request_id))
        self.assertTrue(bot._timer_db("is_active", 555000111))
        self.assertTrue(bot._timer_db("clear_matching", 555000111, "smoke", 777777))
        self.assertFalse(bot._timer_db("has_unreturned", 555000111))
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
        replacement_id = bot.save_employee_in_branch(
            "+962780000999", "Replacement Employee", "Cashier",
            main_branch_id, bot.SUPER_ADMIN_TELEGRAM_ID,
        )
        self.assertIsNotNone(replacement_id)
        self.assertIsNone(
            bot.save_employee(555000111, "+962780000999", "Replacement Employee")
        )
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM requests WHERE id = %s", (request_id,))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()

        # Apply configurable request types after the branch foundation and verify defaults.
        request_types_sql = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "002_configurable_request_types.sql"
        ).read_text(encoding="utf-8")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(request_types_sql)
                cur.execute("""
                    SELECT code, label, duration_minutes
                    FROM branch_request_types
                    WHERE branch_id = (SELECT id FROM branches WHERE code='main')
                    ORDER BY code
                """)
                defaults = cur.fetchall()
                self.assertEqual(len(defaults), 2)
                self.assertEqual({row[0] for row in defaults}, {"bathroom", "prayer"})
                self.assertTrue(all(row[2] == 10 for row in defaults))
                cur.execute("""
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_name='active_timers' AND column_name='timer_type'
                """)
                self.assertEqual(cur.fetchone()[0], 40)
            conn.commit()
        finally:
            conn.close()

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM branches WHERE code='main'")
                main_branch_id = cur.fetchone()[0]
        finally:
            conn.close()

        custom_type_id = _save_new(
            main_branch_id, "CI Custom Request", 7, bot.SUPER_ADMIN_TELEGRAM_ID
        )
        self.assertIsNotNone(custom_type_id)
        custom_type = get_request_type(main_branch_id, custom_type_id)
        self.assertEqual(custom_type[2:4], ("CI Custom Request", 7))
        self.assertTrue(
            _update_type(
                main_branch_id, custom_type_id, bot.SUPER_ADMIN_TELEGRAM_ID,
                duration=9,
            )
        )
        self.assertEqual(get_request_type(main_branch_id, custom_type_id)[3], 9)

        second_employee_id = bot.save_employee_in_branch(
            "+962789876543", "Branch Trigger Employee", "Stock Clerk",
            main_branch_id, bot.SUPER_ADMIN_TELEGRAM_ID,
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

        # Verify that a branch manager can see/decide only within assigned branches.
        branch_admin_id = 987654321
        self.assertTrue(bot.add_admin_to_db(branch_admin_id, bot.SUPER_ADMIN_TELEGRAM_ID))
        main_employee_id = bot.save_employee_in_branch(
            "+962780001111", "Main Branch Employee", "Cashier",
            main_branch_id, bot.SUPER_ADMIN_TELEGRAM_ID,
        )
        second_employee_id = bot.save_employee_in_branch(
            "+962780002222", "Second Branch Employee", "Supervisor",
            main_branch_id, bot.SUPER_ADMIN_TELEGRAM_ID,
        )
        self.assertIsNotNone(main_employee_id)
        self.assertIsNotNone(second_employee_id)

        second_branch_id = create_branch(
            "second", "Second Branch", bot.SUPER_ADMIN_TELEGRAM_ID
        )
        self.assertIsNotNone(second_branch_id)
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM branch_request_types WHERE branch_id=%s",
                    (second_branch_id,),
                )
                self.assertEqual(cur.fetchone()[0], 2)
                cur.execute(
                    "SELECT COUNT(*) FROM branch_settings WHERE branch_id=%s",
                    (second_branch_id,),
                )
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute(
                    "UPDATE employees SET branch_id=%s WHERE id=%s",
                    (second_branch_id, second_employee_id),
                )
                cur.execute(
                    """INSERT INTO admin_branch_roles
                       (telegram_id, branch_id, role, granted_by)
                       VALUES (%s, %s, 'branch_manager', %s)""",
                    (branch_admin_id, main_branch_id, bot.SUPER_ADMIN_TELEGRAM_ID),
                )
            conn.commit()
        finally:
            conn.close()

        self.assertTrue(bot.is_admin(branch_admin_id))
        self.assertTrue(bot.admin_can_access_branch(branch_admin_id, main_branch_id))
        self.assertFalse(bot.admin_can_access_branch(branch_admin_id, second_branch_id))

        # Branch managers may onboard employees only into their assigned branch.
        scoped_employee_id = bot.save_employee_in_branch(
            "+962780003333", "Scoped Branch Employee", "Cashier", main_branch_id, branch_admin_id
        )
        self.assertIsNotNone(scoped_employee_id)
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT full_name, job_title, branch_id FROM employees WHERE id=%s",
                    (scoped_employee_id,),
                )
                self.assertEqual(cur.fetchone(), ("Scoped Branch Employee", "Cashier", main_branch_id))
        finally:
            conn.close()
        self.assertIsNone(
            bot.save_employee_in_branch(
                "+962780004444", "Unauthorized Branch Employee", "Supervisor", second_branch_id, branch_admin_id
            )
        )
        visible_employee_ids = {
            row["id"] for row in bot.get_employees_for_admin(branch_admin_id)
        }
        self.assertIn(main_employee_id, visible_employee_ids)
        self.assertNotIn(second_employee_id, visible_employee_ids)

        second_branch_request = bot.create_request(
            second_employee_id, "leave", "branch isolation test"
        )
        self.assertIsNotNone(second_branch_request)
        self.assertFalse(
            bot.decide_request(
                second_branch_request, second_employee_id, "leave",
                "approved", branch_admin_id,
            )
        )
        self.assertTrue(
            bot.decide_request(
                second_branch_request, second_employee_id, "leave",
                "approved", bot.SUPER_ADMIN_TELEGRAM_ID,
            )
        )

        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM admin_branch_roles WHERE telegram_id=%s AND branch_id=%s",
                    (branch_admin_id, main_branch_id),
                )
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(bot.is_admin(branch_admin_id))

        # Apply the remaining production migrations and verify they are repeatable.
        for migration_name in (
            "003_broadcast_logs.sql",
            "004_broadcast_acknowledgements.sql",
            "005_preproduction_integrity.sql",
            "006_timer_return_acknowledgements.sql",
        ):
            migration_sql = (
                Path(__file__).resolve().parents[1] / "migrations" / migration_name
            ).read_text(encoding="utf-8")
            conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
            try:
                with conn.cursor() as cur:
                    cur.execute(migration_sql)
                conn.commit()
            finally:
                conn.close()

        migration_005 = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "005_preproduction_integrity.sql"
        ).read_text(encoding="utf-8")
        conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="disable")
        try:
            with conn.cursor() as cur:
                cur.execute(migration_005)
                cur.execute("""
                    SELECT 1 FROM pg_indexes
                    WHERE indexname='uq_employees_active_phone'
                """)
                self.assertIsNotNone(cur.fetchone())
                cur.execute("""
                    SELECT 1 FROM pg_indexes
                    WHERE indexname='uq_employees_active_telegram_id'
                """)
                self.assertIsNotNone(cur.fetchone())
                cur.execute("""
                    SELECT 1 FROM pg_constraint
                    WHERE conname='branch_settings_branch_id_fkey'
                """)
                self.assertIsNotNone(cur.fetchone())
                cur.execute(
                    "SELECT COUNT(*) FROM branch_settings WHERE branch_id=%s",
                    (second_branch_id,),
                )
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT to_regclass('public.broadcast_recipients')")
                self.assertIsNotNone(cur.fetchone()[0])
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
