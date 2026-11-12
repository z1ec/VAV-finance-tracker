"""deploy/bot-token.sh: writes the token to the bot's .env and only its hash to the tracker's."""

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "bot-token.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("openssl") is None,
    reason="bash and openssl required",
)

FAKE_DOCKER = """#!/usr/bin/env bash
[ "$1 $2" = "network inspect" ] && [ "$3" = "vav-internal" ] && { echo "172.20.0.0/16,"; exit 0; }
exit 1
"""


def _env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def _setup(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    tracker = tmp_path / "tracker.env"
    bot = tmp_path / "bot.env"
    tracker.write_text("SECRET_KEY=keep-me\nADMIN_USERNAME=owner\nDOMAIN=example.org\n")
    bot.write_text("# bot settings\nTELEGRAM_TOKEN=bot-secret\nGEMINI_KEY=g")  # no final newline
    tracker.chmod(0o600)
    bot.chmod(0o600)
    docker = tmp_path / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    env = {
        **os.environ,
        "TRACKER_ENV": str(tracker),
        "BOT_ENV": str(bot),
        "DOCKER": str(docker),
    }
    return tracker, bot, env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def test_issues_token_and_hash_without_printing(tmp_path: Path) -> None:
    tracker, bot, env = _setup(tmp_path)
    tracker_inode = tracker.stat().st_ino
    result = _run(env)
    assert result.returncode == 0, result.stderr

    bot_env, tracker_env = _env(bot), _env(tracker)
    token = bot_env["FINANCE_API_TOKEN"]
    assert len(token) == 64
    assert bot_env["FINANCE_API_URL"] == "http://finance-backend:8000"
    assert tracker_env["BOT_API_TOKEN_SHA256"] == hashlib.sha256(token.encode()).hexdigest()
    assert tracker_env["BOT_API_USERNAME"] == "owner"
    assert tracker_env["BOT_API_ALLOWED_NETWORKS"] == "172.20.0.0/16"
    assert token not in tracker.read_text()
    assert token not in result.stdout + result.stderr
    assert tracker_env["BOT_API_TOKEN_SHA256"] not in result.stdout + result.stderr
    # other lines untouched, files rewritten in place with the same mode
    assert tracker_env["SECRET_KEY"] == "keep-me"
    assert bot_env["TELEGRAM_TOKEN"] == "bot-secret"
    assert bot_env["GEMINI_KEY"] == "g"
    assert bot.read_text().startswith("# bot settings\n")
    assert tracker.stat().st_ino == tracker_inode
    assert stat.S_IMODE(tracker.stat().st_mode) == 0o600
    assert stat.S_IMODE(bot.stat().st_mode) == 0o600


def test_rotation_replaces_keys(tmp_path: Path) -> None:
    tracker, bot, env = _setup(tmp_path)
    assert _run(env).returncode == 0
    first = _env(bot)["FINANCE_API_TOKEN"]
    assert _run(env).returncode == 0
    second = _env(bot)["FINANCE_API_TOKEN"]
    assert first != second
    assert bot.read_text().count("FINANCE_API_TOKEN=") == 1
    assert tracker.read_text().count("BOT_API_TOKEN_SHA256=") == 1
    assert _env(tracker)["BOT_API_TOKEN_SHA256"] == hashlib.sha256(second.encode()).hexdigest()


def test_fails_without_username_or_files(tmp_path: Path) -> None:
    tracker, bot, env = _setup(tmp_path)
    tracker.write_text("SECRET_KEY=x\n")
    result = _run(env)
    assert result.returncode != 0
    assert "ADMIN_USERNAME" in result.stderr
    assert "FINANCE_API_TOKEN" not in bot.read_text()

    result = _run({**env, "BOT_ENV": str(tmp_path / "absent.env")})
    assert result.returncode != 0
    assert "missing" in result.stderr


def test_overrides(tmp_path: Path) -> None:
    tracker, _, env = _setup(tmp_path)
    result = _run({**env, "BOT_USERNAME": "bot-admin", "ALLOWED_NETWORKS": "10.1.0.0/24"})
    assert result.returncode == 0, result.stderr
    assert _env(tracker)["BOT_API_USERNAME"] == "bot-admin"
    assert _env(tracker)["BOT_API_ALLOWED_NETWORKS"] == "10.1.0.0/24"
