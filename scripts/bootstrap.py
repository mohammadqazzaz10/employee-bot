"""Render pre-deploy bootstrap: initialize legacy tables, apply migrations, verify."""
from __future__ import annotations

import logging
import subprocess
import sys

from bot import initialize_database_tables

logger = logging.getLogger("employee_bot.bootstrap")


def main() -> int:
    if not initialize_database_tables():
        logger.critical("Base database initialization failed; deployment must stop.")
        return 1

    migrate = [sys.executable, "-m", "scripts.migrate"]
    check = [sys.executable, "-m", "scripts.migrate", "--check"]

    try:
        subprocess.run(migrate, check=True)
        subprocess.run(check, check=True)
    except subprocess.CalledProcessError as exc:
        logger.critical(
            "Database migration/bootstrap command failed with exit code %s.",
            exc.returncode,
        )
        return exc.returncode or 1

    logger.info("Database bootstrap and all migration checksum checks passed.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
