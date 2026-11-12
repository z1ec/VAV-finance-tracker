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
# State files in DEPLOY_DIR: .last_successful_tag (current), .previous_successful_tag.
#
# Environment overrides (mostly for tests): DEPLOY_DIR, DOMAIN, HEALTH_TIMEOUT,
# HEALTH_POLL_INTERVAL, DOCKER (docker binary), CURL (curl binary), REGISTRY, NETWORK.
set -euo pipefail

TAG="${1:?usage: deploy.sh <image_tag> [image_prefix] [registry_user]}"
IMAGE_PREFIX="${2:-${IMAGE_PREFIX:-ghcr.io/z1ec/finance-tracker}}"
REGISTRY_USER="${3:-${REGISTRY_USER:-}}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/finance-tracker}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
HEALTH_POLL_INTERVAL="${HEALTH_POLL_INTERVAL:-3}"
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

start_tag() {
  CURRENT_TAG="$1"
  log "pulling ${IMAGE_PREFIX}-{backend,web}:${CURRENT_TAG}"
  compose pull --quiet "${SERVICES[@]}"
  log "starting ${CURRENT_TAG}"
  compose up --detach --remove-orphans "${SERVICES[@]}"
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

  start_tag "${TAG}"
  if wait_healthy; then
    record_success "${TAG}" "${previous}"
    log "deployed ${TAG}"
    local keep_previous="${previous}"
    if [ "${previous}" = "${TAG}" ] && [ -f "${PREVIOUS_TAG_FILE}" ]; then
      keep_previous="$(cat "${PREVIOUS_TAG_FILE}")"
    fi
    prune_images "${TAG}" "${keep_previous}"
    return 0
  fi

  compose logs --tail 50 "${SERVICES[@]}" || true
  if [ -n "${previous}" ] && [ "${previous}" != "${TAG}" ]; then
    log "rolling back to ${previous}"
    start_tag "${previous}"
    if wait_healthy; then
      die "deploy of ${TAG} failed health check; rolled back to ${previous}"
    fi
    die "deploy of ${TAG} failed and rollback to ${previous} is unhealthy too"
  fi
  die "deploy of ${TAG} failed health check and there is no previous version to roll back to"
}

main "$@"
