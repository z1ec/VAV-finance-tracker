"""Startup guard: refuse default/weak secrets on a real domain, only warn on localhost."""

import logging

import pytest

from app.config import Settings
from app.security import InsecureConfigError, check_secrets, find_insecure_settings

STRONG_KEY = "k" * 48
STRONG_PASSWORD = "correct-horse-battery"


def make_settings(**overrides: str) -> Settings:
    values = {
        "secret_key": STRONG_KEY,
        "admin_username": "admin",
        "admin_password": STRONG_PASSWORD,
        "user1_username": "user1",
        "user1_password": STRONG_PASSWORD,
        "user2_username": "user2",
        "user2_password": STRONG_PASSWORD,
        "domain": "finance.example.com",
    }
    values.update(overrides)
    # model_validate skips env/.env sources, so the test sees exactly these values.
    return Settings.model_validate(values)


def test_strong_production_settings_pass() -> None:
    check_secrets(make_settings())


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"secret_key": ""}, "SECRET_KEY"),
        ({"secret_key": "dev-secret-key-change-me"}, "SECRET_KEY"),
        ({"secret_key": "short-but-not-default"}, "SECRET_KEY"),
        ({"admin_password": "admin"}, "ADMIN_PASSWORD"),
        ({"user1_password": "user1"}, "USER1_PASSWORD"),
        ({"user2_password": "user2"}, "USER2_PASSWORD"),
        ({"user1_password": "elevenchars"}, "USER1_PASSWORD"),
    ],
)
def test_production_refuses_insecure_values(overrides: dict[str, str], expected: str) -> None:
    cfg = make_settings(**overrides)
    with pytest.raises(InsecureConfigError) as excinfo:
        check_secrets(cfg)
    message = str(excinfo.value)
    assert expected in message
    for value in overrides.values():
        if value:
            assert value not in message  # never leak the secret itself


def test_unconfigured_user_is_ignored() -> None:
    cfg = make_settings(user2_username="", user2_password="")
    assert find_insecure_settings(cfg) == []


def test_error_lists_every_problem_without_values() -> None:
    cfg = make_settings(secret_key="tiny", admin_password="admin", user1_password="pw")
    with pytest.raises(InsecureConfigError) as excinfo:
        check_secrets(cfg)
    message = str(excinfo.value)
    for name in ("SECRET_KEY", "ADMIN_PASSWORD", "USER1_PASSWORD"):
        assert name in message
    assert "tiny" not in message and "admin," not in message


def test_localhost_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    cfg = make_settings(domain="localhost", secret_key="", admin_password="admin")
    with caplog.at_level(logging.WARNING, logger="app.security"):
        check_secrets(cfg)  # does not raise
    problems = [r.problem for r in caplog.records if hasattr(r, "problem")]
    assert any("SECRET_KEY" in p for p in problems)
    assert any("ADMIN_PASSWORD" in p for p in problems)


# ---------- Bot API settings ----------

GOOD_HASH = "a" * 64


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"bot_api_token_sha256": "not-hex"}, "BOT_API_TOKEN_SHA256"),
        ({"bot_api_token_sha256": "a" * 63}, "BOT_API_TOKEN_SHA256"),
        ({"bot_api_token_sha256": GOOD_HASH, "bot_api_username": ""}, "BOT_API_USERNAME"),
        ({"bot_api_allowed_networks": "172.20.0.0/16,nonsense"}, "BOT_API_ALLOWED_NETWORKS"),
        ({"bot_api_allowed_networks": "0.0.0.0/0"}, "BOT_API_ALLOWED_NETWORKS"),
        ({"bot_api_allowed_networks": "81.85.92.119/32"}, "BOT_API_ALLOWED_NETWORKS"),
    ],
)
def test_bad_bot_settings_are_rejected(overrides: dict[str, str], expected: str) -> None:
    values = {"bot_api_username": "admin"} | overrides
    problems = find_insecure_settings(make_settings(**values))
    assert any(expected in p for p in problems), problems
    with pytest.raises(InsecureConfigError):
        check_secrets(make_settings(**values))


def test_good_bot_settings_pass() -> None:
    check_secrets(
        make_settings(
            bot_api_token_sha256=GOOD_HASH.upper(),
            bot_api_username="admin",
            bot_api_allowed_networks="172.20.0.0/16, 10.0.0.0/8",
        )
    )
    # No hash → token disabled; networks alone are fine.
    check_secrets(make_settings(bot_api_allowed_networks="172.20.0.0/16"))


def test_bot_problems_never_contain_the_hash() -> None:
    problems = find_insecure_settings(make_settings(bot_api_token_sha256="zz" + GOOD_HASH))
    assert problems and all(GOOD_HASH not in p for p in problems)
