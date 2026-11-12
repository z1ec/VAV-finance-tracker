"""Backup before Alembic migrations: only when migrations are pending, keep the newest 10."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


# ---------- 0002: expense source / idempotency key, subscriptions ----------


def _seed_0001(path: Path) -> None:
    command.upgrade(alembic_config(str(path)), "0001")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (1, 'admin', 'x', 'admin', '2026-09-10T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO categories (id, name, color, created_at) "
        "VALUES (1, 'Еда', '#ff0000', '2026-09-10T00:00:00+00:00')"
    )
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO expenses (id, amount, currency, rate_to_huf, amount_huf, rate_source,"
            " category_id, comment, occurred_at, created_by, created_at, updated_at)"
            " VALUES (?, 10.5, 'EUR', 390.12, 4096, 'api', 1, 'c', ?, 1, ?, ?)",
            (i, f"2026-10-0{i}T10:00:00+00:00", "2026-10-01T10:00:00+00:00", "x"),
        )
    conn.commit()
    conn.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {
            t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("users", "categories", "expenses", "exchange_rates", "budgets", "settings")
        }
    finally:
        conn.close()


def test_upgrade_0001_to_head_keeps_rows_and_backfills_source(tmp_path: Path) -> None:
    db = tmp_path / "expenses.db"
    _seed_0001(db)
    before = _counts(db)

    command.upgrade(alembic_config(str(db)), "head")

    assert _counts(db) == before
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        rows = conn.execute(
            "SELECT id, source, client_request_id, amount, amount_huf FROM expenses ORDER BY id"
        ).fetchall()
        assert rows == [(i, "web", None, 10.5, 4096) for i in range(1, 4)]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"subscriptions", "subscription_charges"} <= tables
        # Constraints are enforced: unknown source, duplicate key, old CHECKs still present.
        for statement in (
            "UPDATE expenses SET source = 'fax' WHERE id = 1",
            "UPDATE expenses SET currency = 'USD' WHERE id = 1",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)
        conn.execute("UPDATE expenses SET client_request_id = 'k' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE expenses SET client_request_id = 'k' WHERE id = 2")
        conn.execute(
            "INSERT INTO subscriptions (id, name, amount_type, amount, currency, category_id,"
            " period, next_payment_date, created_at, updated_at)"
            " VALUES (1, 's', 'fixed', 5, 'HUF', 1, 'monthly', '2026-11-01', 'x', 'x')"
        )
        conn.execute(
            "INSERT INTO subscription_charges (subscription_id, due_date, status, currency,"
            " created_at, updated_at) VALUES (1, '2026-11-01', 'pending', 'HUF', 'x', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subscription_charges (subscription_id, due_date, status, currency,"
                " created_at, updated_at) VALUES (1, '2026-11-01', 'pending', 'HUF', 'x', 'x')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subscriptions (name, amount_type, amount, currency, category_id,"
                " period, next_payment_date, created_at, updated_at)"
                " VALUES ('v', 'variable', 5, 'HUF', 1, 'monthly', '2026-11-01', 'x', 'x')"
            )
        conn.rollback()
    finally:
        conn.close()
    assert not has_pending_migrations(str(db))


def test_downgrade_0002_restores_the_0001_schema(tmp_path: Path) -> None:
    db = tmp_path / "expenses.db"
    _seed_0001(db)
    command.upgrade(alembic_config(str(db)), "head")
    command.downgrade(alembic_config(str(db)), "0001")
    conn = sqlite3.connect(db)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(expenses)")}
        assert not {"source", "client_request_id", "client_request_hash"} & columns
        assert conn.execute("SELECT count(*) FROM expenses").fetchone() == (3,)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "subscriptions" not in tables
    finally:
        conn.close()
    command.upgrade(alembic_config(str(db)), "head")  # and forward again


def test_models_match_migrations(tmp_path: Path) -> None:
    """Alembic autogenerate finds nothing to do on a fully migrated database."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.db import Base

    db = tmp_path / "expenses.db"
    command.upgrade(alembic_config(str(db)), "head")
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []
