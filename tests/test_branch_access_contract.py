from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


class BranchAccessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BOT.read_text(encoding="utf-8")

    def test_admin_access_requires_super_admin_or_branch_role(self):
        self.assertIn("a.is_super_admin = TRUE OR EXISTS", self.source)
        self.assertIn("FROM admin_branch_roles r", self.source)
        self.assertIn("r.telegram_id = a.telegram_id", self.source)

    def test_branch_authorization_is_fail_closed(self):
        self.assertIn("def admin_can_access_branch(user_id, branch_id):", self.source)
        self.assertIn("if is_super_admin(user_id):", self.source)
        self.assertIn("role IN ('branch_manager', 'supervisor')", self.source)

    def test_employee_requests_notify_only_their_branch(self):
        self.assertGreaterEqual(
            self.source.count("await send_to_branch_admins(context, employee[\"branch_id\"], msg,"),
            4,
        )
        self.assertIn("def get_branch_admin_ids(branch_id):", self.source)

    def test_standard_and_custom_approvals_require_branch_access(self):
        self.assertIn(
            "if not admin_can_access_branch(query.from_user.id, emp.get(\"branch_id\")):",
            self.source,
        )
        self.assertIn("لا تملك صلاحية إدارة طلبات هذا الفرع", self.source)

    def test_employee_listing_is_branch_scoped_for_branch_admins(self):
        self.assertIn("def get_employees_for_admin(user_id):", self.source)
        self.assertIn("JOIN admin_branch_roles r ON r.branch_id=e.branch_id", self.source)
        self.assertIn("employees = get_employees_for_admin(user_id)", self.source)

    def test_global_employee_and_admin_management_is_super_admin_only(self):
        self.assertIn("إضافة الموظفين متاحة حالياً للمدير العام فقط", self.source)
        self.assertIn("إيقاف الموظفين متاح حالياً للمدير العام فقط", self.source)
        self.assertIn("عرض قائمة المديرين متاح للمدير العام فقط", self.source)


if __name__ == "__main__":
    unittest.main()
