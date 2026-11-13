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
# The fake database is a text file whose first line is "rev=<alembic revision>"; tags in
# $FAKE_MIGRATES rewrite it to rev=0003 (and leave a -wal file) when they start.
echo "$*" >> "$FAKE_LOG"
# emulate `exec <cid> <prog> -c …` and `compose run … --entrypoint <prog> backend -c …`
in_container() {
  local prog="" prev="" a
  for a in "$@"; do [ "$prev" = "--entrypoint" ] && prog="$a"; prev="$a"; done
  [ "$1" = "exec" ] && prog="$3"
  while [ "$1" != "-c" ]; do shift; done
  local script="$2"; shift 2
  if [ "$prog" = "sh" ]; then  # /data → $DATA_DIR, run with the real shell
    [ -f "$FAKE_DIR/restore_fails" ] && [[ "$script" == *"cp "* ]] && exit 1
    [ -f "$FAKE_DIR/prune_fails" ] && [[ "$script" == *"ls -1t"* ]] && exit 1
    bash -c "${script//\/data/$DATA_DIR}" "$@"; exit $?
  fi
  local src="${1/\/data/$DATA_DIR}"
  case "$script" in
    *src.backup*)
      [ -f "$FAKE_DIR/backup_fails" ] && { echo "disk full" >&2; exit 1; }
      [ -f "$src" ] || { echo none; exit 0; }
      local dst="${2/\/data/$DATA_DIR}"; mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"
      sed -n 's/^rev=//p' "$src" | head -1 ;;
    *mode=ro*)
      [ -f "$src" ] || { echo none; exit 0; }
      sed -n 's/^rev=//p' "$src" | head -1 ;;
  esac
  exit 0
}
case "$1" in
  login) cat > "$FAKE_DIR/token"; exit 0 ;;
  logout) exit 0 ;;
  exec)
    [ -f "$FAKE_DIR/not_running" ] && { echo "container is not running" >&2; exit 1; }
    in_container "$@" ;;
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
    if [[ " $* " == *" run "* ]]; then in_container "$@"; fi
    if [[ " $* " == *" stop "* ]]; then touch "$FAKE_DIR/not_running"; fi
    if [[ " $* " == *" ps "* ]] && [ ! -f "$FAKE_DIR/not_running" ]; then
      echo "cid-backend-${IMAGE_TAG}"
    fi
    if [[ " $* " == *" up "* ]]; then
      rm -f "$FAKE_DIR/not_running"
      echo "$IMAGE_TAG" > "$FAKE_DIR/running"
      for tag in $FAKE_MIGRATES; do
        if [ "$tag" = "$IMAGE_TAG" ] && [ -f "$DATA_DIR/expenses.db" ]; then
          printf 'rev=0003\nmigrated by %s\n' "$tag" > "$DATA_DIR/expenses.db"
          echo wal > "$DATA_DIR/expenses.db-wal"
        fi
      done
    fi
    if [[ " $* " == *" pull "* ]] && [ -f "$FAKE_DIR/pull_hangs" ]; then
      n=$(cat "$FAKE_DIR/pull_hangs"); echo $((n - 1)) > "$FAKE_DIR/pull_hangs"
      if [ "$n" -gt 0 ]; then sleep 30; fi
    fi
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


def _setup(tmp_path: Path, *, database: bool = True) -> dict[str, str]:
    deploy_dir = tmp_path / "opt"
    deploy_dir.mkdir()
    (deploy_dir / "docker-compose.prod.yml").write_text("services: {}\n")
    (deploy_dir / ".env").write_text("SECRET_KEY=x\nDOMAIN=finance.example.com\n")
    data_dir = deploy_dir / "data"
    data_dir.mkdir()
    if database:
        (data_dir / "expenses.db").write_text("rev=0002\nreal data\n")
    env = {k: v for k, v in os.environ.items() if k != "DOMAIN"}
    return {
        **env,
        "DEPLOY_DIR": str(deploy_dir),
        "DATA_DIR": str(data_dir),
        "FAKE_MIGRATES": "",
        "PULL_TIMEOUT": "1",
        "PULL_RETRY_DELAY": "0",
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
    assert "attempt 1/3, timeout 1s" in result.stdout
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


def _data(env: dict[str, str]) -> Path:
    return Path(env["DATA_DIR"])


def _backups(env: dict[str, str]) -> list[Path]:
    return sorted((_data(env) / "backups").glob("pre-deploy-*.db"))


# ---------- Stalled image pull (bot PR #8, ported in stage 3b) ----------

needs_timeout = pytest.mark.skipif(
    shutil.which("timeout") is None, reason="coreutils timeout required (CI and the server)"
)


@needs_timeout
def test_stalled_pull_is_retried(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    (tmp_path / "pull_hangs").write_text("1")  # the first pull stalls, the second works
    result = _run(env, "sha-new")
    assert result.returncode == 0, result.stderr
    assert "attempt 2/3" in result.stdout
    assert _state(env, ".last_successful_tag") == "sha-new"


@needs_timeout
def test_pull_that_never_finishes_fails_fast(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    (tmp_path / "pull_hangs").write_text("9")
    result = _run(env, "sha-new")
    assert result.returncode == 1
    assert "could not pull" in result.stderr
    assert "attempt 3/3" in result.stdout
    ups = [line for line in _calls(env).splitlines() if " up " in line]
    assert len(ups) == 1  # the running version was never touched
    assert _state(env, ".last_successful_tag") == "sha-old"
    assert (tmp_path / "running").read_text().strip() == "sha-old"


def test_failed_pull_is_retried(tmp_path: Path) -> None:
    """A pull that fails outright (registry error) is retried too, with or without `timeout`."""
    env = _setup(tmp_path)
    docker = Path(env["DOCKER"])
    docker.write_text(
        docker.read_text().replace(
            'echo "$*" >> "$FAKE_LOG"',
            'echo "$*" >> "$FAKE_LOG"\n'
            'if [[ " $* " == *" pull "* ]] && [ ! -f "$FAKE_DIR/pulled_once" ]; then '
            'touch "$FAKE_DIR/pulled_once"; exit 1; fi',
            1,
        )
    )
    result = _run(env, "sha-new")
    assert result.returncode == 0, result.stderr
    assert "attempt 2/3" in result.stdout


# ---------- Pre-deploy backup and restore after a failed migration ----------


def test_successful_deploy_backs_up_the_database_first(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    Path(env["FAKE_LOG"]).write_text("")
    result = _run(env, "sha-new")
    assert result.returncode == 0, result.stderr
    assert "alembic revision 0002" in result.stdout
    new_backups = [b for b in _backups(env) if b.name.startswith("pre-deploy-sha-new-")]
    assert len(new_backups) == 1
    assert new_backups[0].read_text() == "rev=0002\nreal data\n"
    lines = _calls(env).splitlines()
    backup_at = next(i for i, line in enumerate(lines) if line.startswith("exec "))
    up_at = next(i for i, line in enumerate(lines) if " up " in line)
    assert backup_at < up_at  # backed up before the new version started…
    assert lines[backup_at].startswith("exec cid-backend-sha-old python -c")  # …by the old one


def test_first_deploy_backs_up_with_a_one_off_container(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    (tmp_path / "not_running").write_text("")
    result = _run(env, "sha-first")
    assert result.returncode == 0, result.stderr
    assert "run --rm --no-deps -T --entrypoint python backend" in _calls(env)
    assert len(_backups(env)) == 1


def test_no_database_yet_skips_the_backup(tmp_path: Path) -> None:
    env = _setup(tmp_path, database=False)
    result = _run(env, "sha-first")
    assert result.returncode == 0, result.stderr
    assert "nothing to back up" in result.stdout
    assert _backups(env) == []


def test_failed_backup_stops_before_the_new_tag_starts(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    (tmp_path / "backup_fails").write_text("")
    result = _run(env, "sha-new")
    assert result.returncode == 1
    assert "pre-deploy backup failed" in result.stderr
    assert (tmp_path / "running").read_text().strip() == "sha-old"
    assert _state(env, ".last_successful_tag") == "sha-old"


def test_failed_migration_restores_the_backup_before_rollback(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_MIGRATES"] = "sha-bad"
    env["FAKE_UNHEALTHY"] = "sha-bad"
    result = _run(env, "sha-bad")
    assert result.returncode == 1
    assert "rolled back to sha-old" in result.stderr
    assert "alembic revision changed: 0002 -> 0003" in result.stdout
    data = _data(env)
    assert (data / "expenses.db").read_text() == "rev=0002\nreal data\n"  # restored
    kept = data / "expenses.db.failed-sha-bad"
    assert kept.read_text().startswith("rev=0003")  # migrated copy kept, nothing deleted
    assert (data / "expenses.db.failed-sha-bad-wal").exists()
    assert not (data / "expenses.db-wal").exists()
    calls = _calls(env)
    assert calls.index("stop backend") < calls.rindex(" up ")  # restored before the rollback
    assert (tmp_path / "running").read_text().strip() == "sha-old"
    assert _state(env, ".last_successful_tag") == "sha-old"


def test_unhealthy_without_migration_does_not_restore(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    (_data(env) / "expenses.db").write_text("rev=0002\nwritten after the backup\n")
    env["FAKE_UNHEALTHY"] = "sha-bad"
    result = _run(env, "sha-bad")
    assert result.returncode == 1
    assert "no restore needed" in result.stdout
    assert (_data(env) / "expenses.db").read_text() == "rev=0002\nwritten after the backup\n"
    assert not list(_data(env).glob("expenses.db.failed-*"))


def test_failed_restore_does_not_start_the_previous_tag(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    assert _run(env, "sha-old").returncode == 0
    env["FAKE_MIGRATES"] = "sha-bad"
    env["FAKE_UNHEALTHY"] = "sha-bad"
    (tmp_path / "restore_fails").write_text("")
    result = _run(env, "sha-bad")
    assert result.returncode == 1
    assert "restore of data/backups/pre-deploy-sha-bad-" in result.stderr
    assert "sha-old was not started" in result.stderr
    ups = [line for line in _calls(env).splitlines() if " up " in line]
    assert len(ups) == 2  # old, bad — never the old one on a migrated database


def test_pre_deploy_backups_are_rotated(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    env["PRE_DEPLOY_KEEP"] = "2"
    backups = _data(env) / "backups"
    backups.mkdir()
    for i in range(4):
        old = backups / f"pre-deploy-sha-{i}-2026010{i}T000000Z.db"
        old.write_text("old")
        os.utime(old, (1_700_000_000 + i, 1_700_000_000 + i))
    (backups / "expenses-2026-11-01.db").write_text("nightly")  # never touched
    assert _run(env, "sha-new").returncode == 0
    remaining = sorted(p.name for p in backups.glob("pre-deploy-*.db"))
    assert len(remaining) == 2
    assert any(name.startswith("pre-deploy-sha-new-") for name in remaining)
    assert (backups / "expenses-2026-11-01.db").exists()


def test_prune_failure_does_not_fail_the_deploy(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    (tmp_path / "prune_fails").write_text("")
    result = _run(env, "sha-new")
    assert result.returncode == 0, result.stderr
    assert "were not pruned" in result.stdout
