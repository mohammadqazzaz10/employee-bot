from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WebPushAndWorkTrackingContractTests(unittest.TestCase):
    def test_push_delivery_is_real_service_worker_push_not_page_polling(self):
        backend = (ROOT / "web_work_extensions.py").read_text(encoding="utf-8")
        worker = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        client = (ROOT / "web" / "notifications.js").read_text(encoding="utf-8")
        for fragment in ("web_push_subscriptions", "web_push_queue", "pywebpush", "VAPID_PRIVATE_KEY",
                         "@app.post(\"/api/push/subscriptions\")", "@app.get(\"/api/push/config\")"):
            self.assertIn(fragment, backend)
        self.assertIn('self.addEventListener("push"', worker)
        self.assertIn('pushManager.subscribe(', client)
        self.assertIn('applicationServerKey: decodeKey(config.public_key)', client)

    def test_task_workflow_is_branch_scoped_and_audited(self):
        backend = (ROOT / "web_work_extensions.py").read_text(encoding="utf-8")
        for fragment in ("@app.post(\"/api/admin/tasks\")", "@app.get(\"/api/work/tasks\")",
                         "@app.patch(\"/api/work/tasks/<int:assignment_id>\")",
                         "_branch_allowed(cur, g.current_user, branch_id)",
                         "work_task_events", "work_task_status_changed", "employee_ids"):
            self.assertIn(fragment, backend)

    def test_task_ui_is_loaded_and_has_manager_and_employee_views(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "work.js").read_text(encoding="utf-8")
        self.assertIn('<script src="/work.js" defer></script>', html)
        self.assertIn('nav.dataset.page = "workTasks"', js)
        self.assertIn('"/admin/tasks"', js)
        self.assertIn('"/work/tasks"', js)
        self.assertIn('data-task-history', js)

    def test_push_queue_and_task_tables_are_in_additive_migration(self):
        sql = (ROOT / "migrations" / "011_web_push_and_work_tasks.sql").read_text(encoding="utf-8").lower()
        for fragment in ("create table if not exists web_push_subscriptions",
                         "create table if not exists web_push_queue",
                         "after insert on web_notifications",
                         "create table if not exists work_tasks",
                         "create table if not exists work_task_assignments",
                         "create table if not exists work_task_events",
                         "commit;"):
            self.assertIn(fragment, sql)
        self.assertNotIn("drop table", sql)


if __name__ == "__main__":
    unittest.main()
