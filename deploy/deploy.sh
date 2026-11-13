#!/usr/bin/env bash
# Deploy one image tag of both services (backend + web), wait until they are healthy,
# roll both back to the previous tag otherwise.
#
# Usage (on the server, normally invoked by GitHub Actions over SSH):
#   printf '%s' "$GHCR_TOKEN" | ./deploy.sh <image_tag> [image_prefix] [registry_user]
#
#   image_prefix  default ghcr.io/z1ec/finance-tracker → <prefix>-backend, <prefix>-web
#
# The registry token is read from stdin (never from argv/env, so it does not appear in `ps`
# or logs); it is only used for `docker login` and the session is logged out on exit.
#
# Health: the backend container must report Docker `healthy`, and the public URL
# https://$DOMAIN/api/health must answer {"status":"ok"} through Caddy (resolved to 127.0.0.1).
#
# Image pull: every attempt runs under `timeout` (PULL_TIMEOUT) and is retried PULL_ATTEMPTS
# times; a stalled registry download fails the deploy before anything is changed (the bot's
# stage 2b incident, fixed there in PR #8).
#
# Database safety (a new image may migrate the database; the previous image cannot start on a
# newer schema — alembic does not know the revision): after the pull and before the new tag
# starts, the running backend backs its SQLite database up with the sqlite3 backup API
# (`docker exec … python -c …`; works with every image) and the alembic revision is remembered.
# If the new tag is not healthy and the revision changed, the backend is stopped, the migrated
# database (with -wal/-shm) is kept as expenses.db.failed-<tag>, the backup is restored and only
# then the previous tag starts. Every database file operation runs inside a backend container as
# its user (uid 10001): ./data on the server is not readable by the deploy user. A failed backup
# stops the deploy before anything changes. Nothing is ever deleted on failure.
#
# State files in DEPLOY_DIR: .last_successful_tag (current), .previous_successful_tag.
#
# Environment overrides (mostly for tests): DEPLOY_DIR, DOMAIN, HEALTH_TIMEOUT,
# HEALTH_POLL_INTERVAL, PULL_TIMEOUT, PULL_ATTEMPTS, PULL_RETRY_DELAY, PRE_DEPLOY_KEEP,
# DOCKER (docker binary), CURL (curl binary), REGISTRY, NETWORK.
set -euo pipefail

TAG="${1:?usage: deploy.sh <image_tag> [image_prefix] [registry_user]}"
IMAGE_PREFIX="${2:-${IMAGE_PREFIX:-ghcr.io/z1ec/finance-tracker}}"
REGISTRY_USER="${3:-${REGISTRY_USER:-}}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/finance-tracker}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
HEALTH_POLL_INTERVAL="${HEALTH_POLL_INTERVAL:-3}"
# A stalled registry download must not hang the deploy until the CI job is killed.
PULL_TIMEOUT="${PULL_TIMEOUT:-480}"
PULL_ATTEMPTS="${PULL_ATTEMPTS:-3}"
PULL_RETRY_DELAY="${PULL_RETRY_DELAY:-10}"
DOCKER="${DOCKER:-docker}"
CURL="${CURL:-curl}"
REGISTRY="${REGISTRY:-ghcr.io}"
NETWORK="${NETWORK:-vav-internal}"
PROJECT="finance-tracker"
COMPOSE_FILE="${DEPLOY_DIR}/docker-compose.prod.yml"
LAST_TAG_FILE="${DEPLOY_DIR}/.last_successful_tag"
PREVIOUS_TAG_FILE="${DEPLOY_DIR}/.previous_successful_tag"
SERVICES=(backend web)
DATA_UID=10001
# ./data on the host is /data in the backend container (docker-compose.prod.yml).
CONTAINER_DATA="/data"
DB_NAME="expenses.db"
# Pre-deploy backups kept after a successful deploy (nightly/pre-migrate ones rotate in the app).
PRE_DEPLOY_KEEP="${PRE_DEPLOY_KEEP:-10}"
BACKUP_NAME=""
BACKUP_REVISION=""

# Online backup + integrity check; prints the alembic revision ("none": no database yet).
# Python stdlib only, so it runs in every image of the backend.
BACKUP_PY='import os, sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
if not os.path.exists(src_path):
    print("none")
    sys.exit(0)
os.makedirs(os.path.dirname(dst_path), exist_ok=True)
src = sqlite3.connect(src_path)
try:
    row = src.execute("select version_num from alembic_version").fetchone()
except sqlite3.OperationalError:
    row = None
dst = sqlite3.connect(dst_path)
src.backup(dst)
check = dst.execute("pragma quick_check").fetchone()[0]
dst.close()
src.close()
os.chmod(dst_path, 0o600)
if check != "ok" or os.path.getsize(dst_path) == 0:
    sys.exit("backup integrity check failed: " + check)
print(row[0] if row else "unknown")'

# Read-only: the current alembic revision of the database.
REVISION_PY='import os, sqlite3, sys
if not os.path.exists(sys.argv[1]):
    print("none")
    sys.exit(0)
con = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
try:
    row = con.execute("select version_num from alembic_version").fetchone()
except sqlite3.OperationalError:
    row = None
print(row[0] if row else "unknown")'

# $1 = suffix (failed-<tag>), $2 = backup file name. Keeps the migrated files, restores the
# backup; prints the suffix used. Nothing is deleted.
# shellcheck disable=SC2016  # expanded by sh inside the container, not here
RESTORE_SH='set -eu
cd /data
test -s "backups/$2"
suffix="$1"
if [ -e "expenses.db.$suffix" ]; then suffix="$suffix-$(date -u +%Y%m%dT%H%M%SZ)"; fi
for ext in "" -wal -shm; do
  if [ -e "expenses.db$ext" ]; then mv "expenses.db$ext" "expenses.db.$suffix$ext"; fi
done
cp "backups/$2" expenses.db
echo "$suffix"'

# $1 = how many pre-deploy backups to keep (newest first).
# shellcheck disable=SC2016  # expanded by sh inside the container, not here
PRUNE_SH='cd /data/backups 2>/dev/null || exit 0
ls -1t pre-deploy-*.db 2>/dev/null | tail -n +"$(($1 + 1))" | while read -r f; do rm -f "$f"; done'

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

compose() {
  IMAGE_PREFIX="${IMAGE_PREFIX}" IMAGE_TAG="${CURRENT_TAG}" \
    "${DOCKER}" compose --project-name "${PROJECT}" --file "${COMPOSE_FILE}" "$@"
}

read_domain() {
  # DOMAIN is not a secret; take it from the environment or from the server's .env.
  if [ -z "${DOMAIN:-}" ]; then
    DOMAIN="$(sed -n 's/^DOMAIN=//p' "${DEPLOY_DIR}/.env" | tail -n 1 | tr -d "\"' \r")"
  fi
  [ -n "${DOMAIN}" ] || die "DOMAIN is not set in ${DEPLOY_DIR}/.env"
}

registry_login() {
  local token=""
  if [ ! -t 0 ]; then
    IFS= read -r token || true
  fi
  if [ -n "${token}" ]; then
    [ -n "${REGISTRY_USER}" ] || die "registry user is required when a token is provided"
    printf '%s' "${token}" | "${DOCKER}" login "${REGISTRY}" --username "${REGISTRY_USER}" \
      --password-stdin >/dev/null
    LOGGED_IN=1
    log "logged in to ${REGISTRY}"
  else
    log "no registry token on stdin; assuming the images are public or already pulled"
  fi
}

cleanup() {
  if [ "${LOGGED_IN:-0}" = "1" ]; then
    "${DOCKER}" logout "${REGISTRY}" >/dev/null 2>&1 || true
  fi
}

ensure_network() {
  if ! "${DOCKER}" network inspect "${NETWORK}" >/dev/null 2>&1; then
    log "creating docker network ${NETWORK}"
    "${DOCKER}" network create "${NETWORK}" >/dev/null
  fi
}

check_data_dir() {
  local owner
  mkdir -p "${DEPLOY_DIR}/data" 2>/dev/null || true
  owner="$(stat -c %u "${DEPLOY_DIR}/data" 2>/dev/null || stat -f %u "${DEPLOY_DIR}/data" 2>/dev/null \
    || echo unknown)"
  if [ "${owner}" != "${DATA_UID}" ]; then
    log "WARNING: ${DEPLOY_DIR}/data is owned by uid ${owner}, the backend runs as ${DATA_UID}"
  fi
}

compose_pull() {
  if command -v timeout >/dev/null 2>&1; then
    timeout --kill-after=10 "${PULL_TIMEOUT}" env IMAGE_PREFIX="${IMAGE_PREFIX}" \
      IMAGE_TAG="${CURRENT_TAG}" "${DOCKER}" compose --project-name "${PROJECT}" \
      --file "${COMPOSE_FILE}" pull --quiet "${SERVICES[@]}"
  else
    compose pull --quiet "${SERVICES[@]}"
  fi
}

pull_images() {
  local attempt=1
  while true; do
    log "pulling ${IMAGE_PREFIX}-{backend,web}:${CURRENT_TAG} (attempt ${attempt}/${PULL_ATTEMPTS}, timeout ${PULL_TIMEOUT}s)"
    if compose_pull; then
      return 0
    fi
    if [ "${attempt}" -ge "${PULL_ATTEMPTS}" ]; then
      return 1
    fi
    attempt=$((attempt + 1))
    sleep "${PULL_RETRY_DELAY}"
  done
}

pull_tag() {
  CURRENT_TAG="$1"
  pull_images || die "could not pull ${IMAGE_PREFIX}-{backend,web}:${CURRENT_TAG} after ${PULL_ATTEMPTS} attempts"
}

up_tag() {
  CURRENT_TAG="$1"
  log "starting ${CURRENT_TAG}"
  compose up --detach --remove-orphans "${SERVICES[@]}"
}

start_tag() {
  pull_tag "$1"
  up_tag "$1"
}

# in_backend <tag> <program> <args…>: run in the running backend container, or in a one-off
# container of image tag <tag> when it is not running (as the container user in both cases).
in_backend() {
  local tag="$1" program="$2" container
  shift 2
  CURRENT_TAG="${tag}"
  container="$(compose ps --quiet backend 2>/dev/null || true)"
  if [ -n "${container}" ]; then
    "${DOCKER}" exec "${container}" "${program}" "$@"
  else
    compose run --rm --no-deps -T --entrypoint "${program}" backend "$@"
  fi
}

backup_database() {
  # Called after the pull and before the new tag starts: the old version is still running.
  local tag="$1" stamp out
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_NAME="pre-deploy-${TAG}-${stamp}.db"
  log "pre-deploy backup of ${DB_NAME} (running version: ${tag})"
  if ! out="$(in_backend "${tag}" python -c "${BACKUP_PY}" "${CONTAINER_DATA}/${DB_NAME}" \
      "${CONTAINER_DATA}/backups/${BACKUP_NAME}")"; then
    die "pre-deploy backup failed; ${TAG} was not started"
  fi
  BACKUP_REVISION="$(printf '%s\n' "${out}" | tail -n 1)"
  case "${BACKUP_REVISION}" in
    none)
      BACKUP_NAME=""
      log "no database yet: nothing to back up"
      return 0 ;;
    "") die "pre-deploy backup printed no revision; ${TAG} was not started" ;;
  esac
  log "pre-deploy backup: data/backups/${BACKUP_NAME} (alembic revision ${BACKUP_REVISION})"
}

restore_if_migrated() {
  # The new tag failed. If it changed the schema, the previous tag cannot run on this
  # database: stop the backend, keep the migrated files, restore the pre-deploy backup.
  local previous="$1" revision kept
  [ -n "${BACKUP_NAME}" ] || return 0
  CURRENT_TAG="${TAG}"
  log "stopping the backend of ${TAG} before checking the database"
  compose stop backend || true
  revision="$(in_backend "${previous}" python -c "${REVISION_PY}" "${CONTAINER_DATA}/${DB_NAME}" \
    | tail -n 1 || true)"
  if [ "${revision}" = "${BACKUP_REVISION}" ]; then
    log "alembic revision unchanged (${revision}): no restore needed"
    return 0
  fi
  log "alembic revision changed: ${BACKUP_REVISION} -> ${revision:-unreadable}"
  if ! kept="$(in_backend "${previous}" sh -c "${RESTORE_SH}" sh "failed-${TAG}" "${BACKUP_NAME}" \
      | tail -n 1)"; then
    die "restore of data/backups/${BACKUP_NAME} failed; ${previous} was not started (see DEPLOYMENT.md)"
  fi
  log "restored data/backups/${BACKUP_NAME}; the migrated database is kept as ${DB_NAME}.${kept}"
}

prune_pre_deploy_backups() {
  # Runs in the new (healthy) backend; a failure here never fails the deploy.
  in_backend "${TAG}" sh -c "${PRUNE_SH}" sh "${PRE_DEPLOY_KEEP}" \
    || log "warning: old pre-deploy backups were not pruned"
}

backend_status() {
  local container
  container="$(compose ps --quiet backend || true)"
  [ -n "${container}" ] || { echo missing; return; }
  "${DOCKER}" inspect --format '{{.State.Health.Status}}' "${container}" 2>/dev/null || echo unknown
}

web_ok() {
  "${CURL}" --silent --show-error --fail --max-time 5 \
    --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/api/health" 2>/dev/null \
    | grep -q '"status":"ok"'
}

wait_healthy() {
  local waited=0 status
  while [ "${waited}" -le "${HEALTH_TIMEOUT}" ]; do
    status="$(backend_status)"
    case "${status}" in
      unhealthy) log "backend reported unhealthy"; return 1 ;;
      healthy)
        if web_ok; then
          log "backend healthy and https://${DOMAIN}/api/health ok after ${waited}s"
          return 0
        fi
        ;;
    esac
    sleep "${HEALTH_POLL_INTERVAL}"
    waited=$((waited + HEALTH_POLL_INTERVAL))
    if [ "${HEALTH_POLL_INTERVAL}" = "0" ]; then
      waited=$((waited + 1))
    fi
  done
  log "not healthy within ${HEALTH_TIMEOUT}s (backend: ${status:-unknown})"
  return 1
}

prune_images() {
  # Keep only the running tag and the rollback candidate; the VPS disk is small.
  local keep_current="$1" keep_previous="$2" service image tag
  for service in "${SERVICES[@]}"; do
    image="${IMAGE_PREFIX}-${service}"
    "${DOCKER}" image ls "${image}" --format '{{.Tag}}' | while read -r tag; do
      if [ -z "${tag}" ] || [ "${tag}" = "${keep_current}" ] || [ "${tag}" = "${keep_previous}" ]
      then
        continue
      fi
      "${DOCKER}" image rm "${image}:${tag}" >/dev/null 2>&1 || true
    done
  done
  # Dangling layers only (never volumes, networks or other projects' tagged images).
  "${DOCKER}" image prune --force >/dev/null 2>&1 || true
  log "pruned old images (kept ${keep_current}${keep_previous:+ and ${keep_previous}})"
}

record_success() {
  local tag="$1" previous="$2"
  if [ -n "${previous}" ] && [ "${previous}" != "${tag}" ]; then
    printf '%s\n' "${previous}" > "${PREVIOUS_TAG_FILE}"
  fi
  printf '%s\n' "${tag}" > "${LAST_TAG_FILE}"
}

main() {
  [ -f "${COMPOSE_FILE}" ] || die "missing ${COMPOSE_FILE}"
  [ -f "${DEPLOY_DIR}/.env" ] || die "missing ${DEPLOY_DIR}/.env (secrets live only on the server)"
  read_domain
  trap cleanup EXIT
  registry_login
  ensure_network
  check_data_dir

  local previous=""
  if [ -f "${LAST_TAG_FILE}" ]; then
    previous="$(cat "${LAST_TAG_FILE}")"
  fi
  log "deploying ${TAG} (last successful: ${previous:-none})"

  pull_tag "${TAG}"
  backup_database "${previous:-${TAG}}"
  up_tag "${TAG}"
  if wait_healthy; then
    record_success "${TAG}" "${previous}"
    log "deployed ${TAG}"
    local keep_previous="${previous}"
    if [ "${previous}" = "${TAG}" ] && [ -f "${PREVIOUS_TAG_FILE}" ]; then
      keep_previous="$(cat "${PREVIOUS_TAG_FILE}")"
    fi
    prune_images "${TAG}" "${keep_previous}"
    prune_pre_deploy_backups
    return 0
  fi

  CURRENT_TAG="${TAG}"
  compose logs --tail 50 "${SERVICES[@]}" || true
  if [ -n "${previous}" ] && [ "${previous}" != "${TAG}" ]; then
    log "rolling back to ${previous}"
    restore_if_migrated "${previous}"
    start_tag "${previous}"
    if wait_healthy; then
      die "deploy of ${TAG} failed health check; rolled back to ${previous}"
    fi
    die "deploy of ${TAG} failed and rollback to ${previous} is unhealthy too"
  fi
  die "deploy of ${TAG} failed health check and there is no previous version to roll back to"
}

main "$@"
