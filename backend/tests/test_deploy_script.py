"""Dry run of deploy/deploy.sh with fake `docker` and `curl`: success, rollback, pruning."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "deploy.sh"

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Records calls. The tag being started is remembered on `compose up`; backend health depends on
# $FAKE_UNHEALTHY, web health (fake curl) on $FAKE_WEB_DOWN (space-separated tags).
echo "$*" >> "$FAKE_LOG"
case "$1" in
  login) cat > "$FAKE_DIR/token"; exit 0 ;;
  logout) exit 0 ;;
  network)
    if [ "$2" = "inspect" ]; then [ -f "$FAKE_DIR/network" ] && exit 0 || exit 1; fi
    if [ "$2" = "create" ]; then touch "$FAKE_DIR/network"; fi
    exit 0 ;;
  inspect)
    tag="${@: -1}"; tag="${tag#cid-backend-}"
    for bad in $FAKE_UNHEALTHY; do [ "$bad" = "$tag" ] && { echo unhealthy; exit 0; }; done
    echo healthy; exit 0 ;;
  image)
    if [ "$2" = "ls" ]; then printf '%s\n' $FAKE_IMAGES; fi
    exit 0 ;;
  compose)
    if [[ " $* " == *" up "* ]]; then echo "$IMAGE_TAG" > "$FAKE_DIR/running"; fi
    if [[ " $* " == *" ps "* ]]; then echo "cid-backend-${IMAGE_TAG}"; fi
    exit 0 ;;
esac
exit 0
"""

FAKE_CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
running="$(cat "$FAKE_DIR/running" 2>/dev/null)"
for bad in $FAKE_WEB_DOWN; do [ "$bad" = "$running" ] && exit 22; done
echo '{"status":"ok","db":"ok"}'
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


def _write_exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def _setup(tmp_path: Path) -> dict[str, str]:
    deploy_dir = tmp_path / "opt"
    deploy_dir.mkdir()
    (deploy_dir / "docker-compose.prod.yml").write_text("services: {}\n")
    (deploy_dir / ".env").write_text("SECRET_KEY=x\nDOMAIN=finance.example.com\n")
    env = {k: v for k, v in os.environ.items() if k != "DOMAIN"}
    return {
        **env,
        "DEPLOY_DIR": str(deploy_dir),
        "DOCKER": str(_write_exe(tmp_path / "docker", FAKE_DOCKER)),
        "CURL": str(_write_exe(tmp_path / "curl", FAKE_CURL)),
        "HEALTH_TIMEOUT": "2",
        "HEALTH_POLL_INTERVAL": "0",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_DIR": str(tmp_path),
        "FAKE_UNHEALTHY": "",
        "FAKE_WEB_DOWN": "",
        "FAKE_IMAGES": "",
    }


def _run(env: dict[str, str], tag: str, token: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DEPLOY), tag, "ghcr.io/owner/finance-tracker", "owner"],
        input=token,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _state(env: dict[str, str], name: str) -> str:
    return (Path(env["DEPLOY_DIR"]) / name).read_text().strip()


def _calls(env: dict[str, str]) -> str:
    return Path(env["FAKE_LOG"]).read_text()


def test_successful_deploy_records_tag_and_uses_stdin_token(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    result = _run(env, "sha-new", token="secret-token")
    assert result.returncode == 0, result.stderr
    assert _state(env, ".last_successful_tag") == "sha-new"
    calls = _calls(env)
    assert "login ghcr.io --username owner --password-stdin" in calls
    assert "logout ghcr.io" in calls
    assert "secret-token" not in calls  # token only via stdin
    assert (tmp_path / "token").read_text() == "secret-token"
    assert "pull --quiet backend web" in calls
    assert "up --detach --remove-orphans backend web" in calls
    assert "--project-name finance-tracker" in calls


def test_web_check_uses_domain_from_env_file(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-new").returncode == 0
    calls = _calls(env)
    assert "--resolve finance.example.com:443:127.0.0.1" in calls
    assert "https://finance.example.com/api/health" in calls


def test_creates_internal_network_only_when_missing(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-1").returncode == 0
    assert _run(env, "sha-2").returncode == 0
    assert _calls(env).count("network create vav-internal") == 1


def test_unhealthy_backend_rolls_back_both_services(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_UNHEALTHY"] = "sha-bad"
    result = _run(env, "sha-bad")
    assert result.returncode == 1
    assert "rolled back to sha-old" in result.stderr
    assert _state(env, ".last_successful_tag") == "sha-old"
    ups = [line for line in _calls(env).splitlines() if " up " in line]
    assert len(ups) == 3  # old, bad, rollback to old
    assert all("backend web" in line for line in ups)


def test_web_down_triggers_rollback(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_WEB_DOWN"] = "sha-bad"
    result = _run(env, "sha-bad")
    assert result.returncode == 1
    assert "rolled back to sha-old" in result.stderr
    assert (tmp_path / "running").read_text().strip() == "sha-old"


def test_unhealthy_without_previous_fails(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    env["FAKE_UNHEALTHY"] = "sha-first"
    result = _run(env, "sha-first")
    assert result.returncode == 1
    assert "no previous version" in result.stderr
    assert not (Path(env["DEPLOY_DIR"]) / ".last_successful_tag").exists()


def test_rollback_also_unhealthy(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_UNHEALTHY"] = "sha-bad sha-old"
    result = _run(env, "sha-bad")
    assert result.returncode == 1
    assert "unhealthy too" in result.stderr


def test_missing_env_file(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    (Path(env["DEPLOY_DIR"]) / ".env").unlink()
    result = _run(env, "sha-x")
    assert result.returncode == 1
    assert ".env" in result.stderr


def test_missing_domain(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    (Path(env["DEPLOY_DIR"]) / ".env").write_text("SECRET_KEY=x\n")
    result = _run(env, "sha-x")
    assert result.returncode == 1
    assert "DOMAIN" in result.stderr


def test_keeps_current_and_previous_tags(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_IMAGES"] = "sha-ancient sha-old sha-new latest"
    assert _run(env, "sha-new").returncode == 0
    assert _state(env, ".last_successful_tag") == "sha-new"
    assert _state(env, ".previous_successful_tag") == "sha-old"
    calls = _calls(env)
    for service in ("backend", "web"):
        image = f"ghcr.io/owner/finance-tracker-{service}"
        assert f"image rm {image}:sha-ancient" in calls
        assert f"image rm {image}:latest" in calls
        assert f"image rm {image}:sha-old" not in calls
        assert f"image rm {image}:sha-new" not in calls
    assert "image prune --force" in calls
    assert "volume" not in calls and "system prune" not in calls


def test_redeploy_same_tag_keeps_previous(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    assert _run(env, "sha-new").returncode == 0
    env["FAKE_IMAGES"] = "sha-old sha-new"
    assert _run(env, "sha-new").returncode == 0
    assert _state(env, ".previous_successful_tag") == "sha-old"
    assert "image rm ghcr.io/owner/finance-tracker-backend:sha-old" not in _calls(env)


def test_failed_deploy_does_not_prune(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_UNHEALTHY"] = "sha-bad"
    env["FAKE_IMAGES"] = "sha-old sha-bad"
    Path(env["FAKE_LOG"]).write_text("")
    assert _run(env, "sha-bad").returncode == 1
    assert "image rm" not in _calls(env)
