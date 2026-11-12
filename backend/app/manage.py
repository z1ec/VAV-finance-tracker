"""Management commands, run inside the backend container.

    python -m app.manage set-password --from-env ADMIN USER1
        Set the password of the users configured as ADMIN_USERNAME/USER1_USERNAME to the current
        ADMIN_PASSWORD/USER1_PASSWORD from the environment (.env).
    printf '%s' "$NEW_PASSWORD" | python -m app.manage set-password --username alice
        Set the password of one user, read from stdin (never from argv).

Passwords in .env are only used when a user is created for the first time (seed); changing them
in .env alone does not change existing users — this command does. Secrets are never printed.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import DEFAULT_PASSWORDS, MIN_PASSWORD_LENGTH, Settings, settings
from app.db import SessionLocal
from app.models import User

ENV_PREFIXES = ("ADMIN", "USER1", "USER2")


class CommandError(Exception):
    """User-facing error; the message never contains secret values."""


def validate_password(password: str) -> None:
    if password in DEFAULT_PASSWORDS or len(password) < MIN_PASSWORD_LENGTH:
        raise CommandError(
            f"password is a default value or shorter than {MIN_PASSWORD_LENGTH} chars"
        )


def set_password(db: Session, username: str, password: str) -> None:
    validate_password(password)
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        raise CommandError(f"user '{username}' not found")
    user.password_hash = hash_password(password)


def credentials_from_env(cfg: Settings, prefix: str) -> tuple[str, str]:
    key = prefix.upper()
    if key not in ENV_PREFIXES:
        raise CommandError(f"unknown prefix '{prefix}', expected one of {', '.join(ENV_PREFIXES)}")
    username: str = getattr(cfg, f"{key.lower()}_username")
    password: str = getattr(cfg, f"{key.lower()}_password")
    if not username or not password:
        raise CommandError(f"{key}_USERNAME / {key}_PASSWORD are not set")
    return username, password


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.manage", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("set-password", help="set user passwords (from .env or stdin)")
    group = sp.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--from-env",
        nargs="+",
        metavar="PREFIX",
        help="ADMIN, USER1 and/or USER2: use <PREFIX>_USERNAME and <PREFIX>_PASSWORD",
    )
    group.add_argument("--username", help="user to update; the password is read from stdin")
    return parser


def main(argv: Sequence[str] | None = None, cfg: Settings = settings) -> int:
    args = _build_parser().parse_args(argv)
    if args.from_env:
        pairs = [credentials_from_env(cfg, prefix) for prefix in args.from_env]
    else:
        password = sys.stdin.readline().rstrip("\r\n")
        pairs = [(args.username, password)]

    db = SessionLocal()
    try:
        for username, password in pairs:
            set_password(db, username, password)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for username, _ in pairs:
        print(f"password updated for user '{username}'")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
