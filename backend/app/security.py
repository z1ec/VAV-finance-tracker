"""Startup guard against default or weak secrets.

On a real domain the app refuses to start with an insecure configuration; on ``localhost``
it only warns. Messages name the offending settings but never include their values.
"""

import logging

from app.config import (
    DEFAULT_PASSWORDS,
    DEFAULT_SECRET_KEY,
    MIN_PASSWORD_LENGTH,
    MIN_SECRET_KEY_LENGTH,
    Settings,
)

logger = logging.getLogger("app.security")


class InsecureConfigError(RuntimeError):
    """Raised when production settings contain default or weak secrets."""


def find_insecure_settings(cfg: Settings) -> list[str]:
    """Return human-readable problems (setting names and rules only, never values)."""
    problems: list[str] = []
    key = cfg.secret_key
    if not key or key == DEFAULT_SECRET_KEY or len(key) < MIN_SECRET_KEY_LENGTH:
        problems.append(
            f"SECRET_KEY is empty, the default value or shorter than {MIN_SECRET_KEY_LENGTH} chars"
        )
    users = (
        ("ADMIN", cfg.admin_username, cfg.admin_password),
        ("USER1", cfg.user1_username, cfg.user1_password),
        ("USER2", cfg.user2_username, cfg.user2_password),
    )
    for prefix, username, password in users:
        if not username or not password:
            continue  # user is not configured and will not be seeded
        if password in DEFAULT_PASSWORDS or len(password) < MIN_PASSWORD_LENGTH:
            problems.append(
                f"{prefix}_PASSWORD is a default value or shorter than {MIN_PASSWORD_LENGTH} chars"
            )
    return problems


def check_secrets(cfg: Settings) -> None:
    """Fail on a real domain, warn on localhost."""
    problems = find_insecure_settings(cfg)
    if not problems:
        return
    if cfg.is_local:
        for problem in problems:
            logger.warning("insecure_setting", extra={"problem": problem})
        return
    raise InsecureConfigError(
        "Refusing to start with insecure secrets (DOMAIN is not localhost): "
        + "; ".join(problems)
        + ". Set strong values in .env."
    )
