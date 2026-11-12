"""SQLite backups via the online backup API (safe while the app is running, WAL included)."""

import sqlite3
from pathlib import Path


def sqlite_backup(source_path: str | Path, dest: Path) -> Path:
    """Copy ``source_path`` into ``dest`` with ``sqlite3.Connection.backup``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(dest))
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()
    return dest


def prune_backups(backup_dir: Path, pattern: str, keep: int) -> list[Path]:
    """Keep the ``keep`` newest files matching ``pattern`` (by name, which embeds the date)."""
    backups = sorted(backup_dir.glob(pattern), key=lambda p: p.name, reverse=True)
    removed = backups[keep:]
    for old in removed:
        old.unlink(missing_ok=True)
    return removed
