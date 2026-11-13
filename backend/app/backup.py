"""SQLite backups via the online backup API (safe while the app is running, WAL included)."""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger("app.backup")

# The database and its copies are readable by the container user only (stage 3b: on the server
# ./data was 755 and the files 644, i.e. readable by every local account).
DIR_MODE = 0o700
FILE_MODE = 0o600


def sqlite_backup(source_path: str | Path, dest: Path) -> Path:
    """Copy ``source_path`` into ``dest`` with ``sqlite3.Connection.backup``."""
    dest.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(dest))
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()
    os.chmod(dest, FILE_MODE)
    return dest


def restrict_data_permissions(db_path: str | Path, backup_dir: str | Path) -> int:
    """Make the database directory, the backup directory and every file in them private to the
    current user (700/600). Only entries owned by this user are touched; errors are logged and
    never stop the app. Returns how many modes were changed."""
    changed = 0
    uid = os.getuid()
    directories = {Path(db_path).resolve().parent, Path(backup_dir).resolve()}
    for directory in sorted(directories):
        if not directory.is_dir():
            continue
        entries = [(directory, DIR_MODE)] + [
            (entry, DIR_MODE if entry.is_dir() else FILE_MODE)
            for entry in directory.iterdir()
            if entry.is_file() or (entry.is_dir() and entry.resolve() in directories)
        ]
        for path, mode in entries:
            try:
                info = path.stat()
                if info.st_uid != uid or info.st_mode & 0o777 == mode:
                    continue
                os.chmod(path, mode)
                changed += 1
            except OSError as exc:
                logger.warning(
                    "data_permissions_failed", extra={"path": str(path), "error": str(exc)}
                )
    if changed:
        logger.info("data_permissions_restricted", extra={"changed": changed})
    return changed


def prune_backups(backup_dir: Path, pattern: str, keep: int) -> list[Path]:
    """Keep the ``keep`` newest files matching ``pattern`` (by name, which embeds the date)."""
    backups = sorted(backup_dir.glob(pattern), key=lambda p: p.name, reverse=True)
    removed = backups[keep:]
    for old in removed:
        old.unlink(missing_ok=True)
    return removed
