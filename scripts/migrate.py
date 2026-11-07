"""Apply ordered SQL migrations with checksums and a PostgreSQL advisory lock.

Usage:
    python -m scripts.migrate
    python -m scripts.migrate --check
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
LOCK_KEY = 7319052026
logger = logging.getLogger("employee_bot.migrations")


def migration_files():
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Apply employee-bot PostgreSQL migrations.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check migration checksums and report unapplied migrations without applying them.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "").strip()
    sslmode = os.environ.get("DATABASE_SSLMODE", "prefer").strip() or "prefer"
    if not database_url:
        parser.error("DATABASE_URL must be configured in the environment or local .env file.")

    files = migration_files()
    if not files:
        logger.error("No numbered SQL migrations found in %s", MIGRATIONS_DIR)
        return 1

    conn = None
    locked = False
    try:
        conn = psycopg2.connect(
            database_url,
            sslmode=sslmode,
            connect_timeout=10,
            application_name="employee-bot-migrations",
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            locked = True
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            ledger_exists = bool(cur.fetchone()[0])
            if not ledger_exists and args.check:
                logger.error(
                    "Migration ledger does not exist; migrations 001–006 are not recorded as applied."
                )
                return 3
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(120) PRIMARY KEY,
                    checksum_sha256 CHAR(64) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("SELECT version, checksum_sha256 FROM schema_migrations")
            applied = dict(cur.fetchall())

            pending = []
            for path in files:
                content = path.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                version = path.name
                if version in applied:
                    if applied[version] != checksum:
                        logger.error(
                            "Applied migration %s has changed. Do not edit applied migrations; "
                            "create a new numbered migration.",
                            version,
                        )
                        return 2
                    continue
                pending.append((path, content.decode("utf-8"), checksum))

            if args.check:
                if pending:
                    logger.info("Unapplied migrations: %s", ", ".join(p[0].name for p in pending))
                    return 3
                logger.info("All migrations are applied and checksums match.")
                return 0

            for path, sql, checksum in pending:
                logger.info("Applying %s", path.name)
                # Each migration owns its transaction and must be safe to retry.
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations(version, checksum_sha256) VALUES (%s, %s)",
                    (path.name, checksum),
                )
                logger.info("Applied %s", path.name)

            logger.info("Migration check complete; applied %d migration(s).", len(pending))
            return 0
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Database migration failed. No secret values are logged.")
        return 1
    finally:
        if conn:
            if locked:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                except Exception:
                    logger.exception("Could not release migration advisory lock")
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
