from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RenderDeploymentContractTests(unittest.TestCase):
    def test_render_blueprint_uses_background_worker_and_managed_postgres(self):
        blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("type: worker", blueprint)
        self.assertIn("runtime: python", blueprint)
        self.assertIn("buildCommand: python -m pip install .", blueprint)
        self.assertIn("preDeployCommand: python -m scripts.bootstrap", blueprint)
        self.assertIn("startCommand: python bot.py", blueprint)
        self.assertIn("fromDatabase:", blueprint)
        self.assertIn("property: connectionString", blueprint)
        self.assertIn("sync: false", blueprint)
        self.assertNotIn("TELEGRAM_BOT_TOKEN: ", blueprint)
        self.assertNotIn("DATABASE_URL: ", blueprint)

    def test_render_and_ci_use_the_same_supported_python_version(self):
        version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        workflow = (ROOT / ".github" / "workflows" / "python-checks.yml").read_text(encoding="utf-8")
        self.assertEqual(version, "3.11.11")
        self.assertIn('python-version-file: ".python-version"', workflow)

    def test_predeploy_bootstrap_initializes_then_applies_and_checks_migrations(self):
        bootstrap = (ROOT / "scripts" / "bootstrap.py").read_text(encoding="utf-8")
        self.assertIn("initialize_database_tables()", bootstrap)
        self.assertIn('"scripts.migrate"', bootstrap)
        self.assertIn('"--check"', bootstrap)
        self.assertIn("subprocess.run(migrate, check=True)", bootstrap)
        self.assertIn("subprocess.run(check, check=True)", bootstrap)


if __name__ == "__main__":
    unittest.main()
