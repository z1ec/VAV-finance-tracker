import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.currency import refresh_rates_for_date
from app.db import SessionLocal

logger = logging.getLogger("scheduler")

scheduler = BackgroundScheduler(timezone="UTC")


def refresh_rates_job() -> None:
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        rates = refresh_rates_for_date(db, today)
        logger.info("rates_refreshed", extra={"rates": {k: str(v) for k, v in rates.items()}})
    except Exception:
        logger.exception("rates_refresh_failed")
    finally:
        db.close()


def backup_job() -> None:
    try:
        backup_dir = Path(settings.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).date().isoformat()
        dest = backup_dir / f"expenses-{today}.db"

        source = sqlite3.connect(settings.db_path)
        target = sqlite3.connect(str(dest))
        with target:
            source.backup(target)
        target.close()
        source.close()

        logger.info("backup_created", extra={"path": str(dest)})
        _prune_old_backups(backup_dir)
    except Exception:
        logger.exception("backup_failed")


def _prune_old_backups(backup_dir: Path) -> None:
    backups = sorted(backup_dir.glob("expenses-*.db"), key=lambda p: p.name, reverse=True)
    for old in backups[settings.backup_keep:]:
        old.unlink(missing_ok=True)


def start_scheduler() -> None:
    scheduler.add_job(refresh_rates_job, CronTrigger(hour=4, minute=0), id="refresh_rates", replace_existing=True)
    scheduler.add_job(backup_job, CronTrigger(hour=3, minute=0), id="backup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
