from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


class RequestPersistenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BOT.read_text(encoding="utf-8")

    def test_request_creation_writes_pending_record_and_returns_id(self):
        function = self.source.split("def create_request", 1)[1].split(
            "\ndef decide_request", 1
        )[0]
        self.assertIn("INSERT INTO requests", function)
        self.assertIn("'pending'", function)
        self.assertIn("RETURNING id", function)

    def test_request_decision_is_atomic_and_single_use(self):
        function = self.source.split("def decide_request", 1)[1].split(
            "\n# --- دوال السجائر", 1
        )[0]
        self.assertIn("status = 'pending'", function)
        self.assertIn("RETURNING id", function)
        self.assertIn("decision not in {\"approved\", \"rejected\"}", function)

    def test_all_request_types_are_persisted_before_admin_notification(self):
        for handler_name, next_handler in (
            ("async def smoke_request", "\n# --- منطق الاستراحة"),
            ("async def break_request", "\n# --- المغادرات"),
            ("async def receive_leave_reason", "\nasync def vacation_request"),
            ("async def receive_vacation_reason", "\nasync def cancel"),
        ):
            handler = self.source.split(handler_name, 1)[1].split(next_handler, 1)[0]
            with self.subTest(handler=handler_name):
                self.assertIn("create_request(", handler)
                self.assertIn("request_id", handler)
                self.assertIn("approve_", handler)
                self.assertIn("reject_", handler)


if __name__ == "__main__":
    unittest.main()
