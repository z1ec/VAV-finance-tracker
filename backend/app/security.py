"""Startup guard against default or weak secrets.

On a real domain the app refuses to start with an insecure configuration; on ``localhost``
it only warns. Messages name the offending settings but never include their values.
"""

import logging
import re

from app.config import (
    DEFAULT_PASSWORDS,
    DEFAULT_SECRET_KEY,
    MIN_PASSWORD_LENGTH,
    MIN_SECRET_KEY_LENGTH,
    Settings,
)

logger = logging.getLogger("app.security")

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


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
    problems.extend(find_bot_api_problems(cfg))
    return problems


def find_bot_api_problems(cfg: Settings) -> list[str]:
    """Validate the bot token settings (names only). An unset hash simply disables the token."""
    problems: list[str] = []
    try:
        networks = cfg.bot_networks
    except ValueError:
        problems.append("BOT_API_ALLOWED_NETWORKS contains an entry that is not a CIDR network")
        networks = []
    if any(not net.is_private for net in networks):
        problems.append("BOT_API_ALLOWED_NETWORKS must list private (internal) networks only")
    if not cfg.bot_api_token_sha256:
        return problems
    if not _SHA256_HEX.match(cfg.bot_api_token_sha256.strip()):
        problems.append("BOT_API_TOKEN_SHA256 must be a 64-char hex SHA-256 digest")
    if not cfg.bot_api_username:
        problems.append("BOT_API_USERNAME is required when BOT_API_TOKEN_SHA256 is set")
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
