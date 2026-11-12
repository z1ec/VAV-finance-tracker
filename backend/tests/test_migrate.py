"""Backup before Alembic migrations: only when migrations are pending, keep the newest 10."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command

from app.migrate import alembic_config, backup_before_migrations, has_pending_migrations


def _make_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO marker (note) VALUES ('keep me')")
    conn.commit()
    conn.close()


def test_backup_created_when_migrations_pending(tmp_path: Path) -> None:
    db = tmp_path / "expenses.db"
    _make_legacy_db(db)
    backups = tmp_path / "backups"
    now = datetime(2026, 11, 12, 11, 30, tzinfo=UTC)

    assert has_pending_migrations(str(db))
    dest = backup_before_migrations(str(db), backups, keep=10, now=now)

    assert dest == backups / "pre-migrate-20261112T113000Z.db"
    copy = sqlite3.connect(dest)
    assert copy.execute("SELECT note FROM marker").fetchone() == ("keep me",)
    copy.close()


def test_no_backup_when_up_to_date(tmp_path: Path) -> None:
    db = tmp_path / "expenses.db"
    command.upgrade(alembic_config(str(db)), "head")
    assert not has_pending_migrations(str(db))
    assert backup_before_migrations(str(db), tmp_path / "backups", keep=10) is None
    assert not (tmp_path / "backups").exists()


def test_no_backup_for_missing_database(tmp_path: Path) -> None:
    assert backup_before_migrations(str(tmp_path / "absent.db"), tmp_path / "b", keep=10) is None


def test_keeps_only_newest_backups(tmp_path: Path) -> None:
    db = tmp_path / "expenses.db"
    _make_legacy_db(db)
    backups = tmp_path / "backups"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(12):
        backup_before_migrations(str(db), backups, keep=10, now=start + timedelta(hours=i))
    names = sorted(p.name for p in backups.glob("pre-migrate-*.db"))
    assert len(names) == 10
    assert names[0] == "pre-migrate-20260101T020000Z.db"  # two oldest pruned
    assert names[-1] == "pre-migrate-20260101T110000Z.db"
