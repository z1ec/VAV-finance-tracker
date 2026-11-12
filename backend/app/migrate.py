"""Programmatic ``alembic upgrade head`` with a safety backup when migrations are pending."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.backup import prune_backups, sqlite_backup
from app.config import settings

logger = logging.getLogger("app.migrate")

BACKEND_DIR = Path(__file__).resolve().parent.parent
PRE_MIGRATE_PATTERN = "pre-migrate-*.db"


def alembic_config(db_path: str) -> AlembicConfig:
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.attributes["configure_logger"] = False
    return cfg


def has_pending_migrations(db_path: str) -> bool:
    """True if the database is not at the latest Alembic head."""
    heads = set(ScriptDirectory.from_config(alembic_config(db_path)).get_heads())
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            current = set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()
    return current != heads


def backup_before_migrations(
    db_path: str,
    backup_dir: str | Path,
    keep: int,
    now: datetime | None = None,
) -> Path | None:
    """Back up an existing database if migrations are pending; keep the ``keep`` newest copies."""
    if not Path(db_path).is_file() or not has_pending_migrations(db_path):
        return None
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(backup_dir)
    dest = sqlite_backup(db_path, directory / f"pre-migrate-{stamp}.db")
    prune_backups(directory, PRE_MIGRATE_PATTERN, keep)
    logger.info("pre_migrate_backup_created", extra={"path": str(dest)})
    return dest


def run_migrations(db_path: str | None = None) -> None:
    path = db_path or settings.db_path
    backup_before_migrations(path, settings.backup_dir, settings.pre_migrate_backup_keep)
    command.upgrade(alembic_config(path), "head")
