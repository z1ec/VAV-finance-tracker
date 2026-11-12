#!/usr/bin/env bash
# Issue or rotate the Momentum bot's service token for the tracker API (ADR-0004).
# The token is generated here and never printed; only key names are shown.
#
# Usage on the server (as root, both .env files must be writable):
#   /opt/finance-tracker/bot-token.sh
# then restart the tracker backend (docs/MANUAL_STEPS.ru.md) — and the bot once it reads FINANCE_*.
#
# Writes (replacing earlier values of the same keys, every other line untouched):
#   TRACKER_ENV  BOT_API_TOKEN_SHA256 (hex SHA-256 of the token), BOT_API_USERNAME,
#                BOT_API_ALLOWED_NETWORKS (subnet(s) of the Docker network NETWORK)
#   BOT_ENV      FINANCE_API_URL, FINANCE_API_TOKEN
# Files are rewritten in place (same inode), so owner and mode stay as they were.
#
# Environment overrides: TRACKER_ENV, BOT_ENV, NETWORK, API_URL, BOT_USERNAME (default:
# ADMIN_USERNAME from TRACKER_ENV), ALLOWED_NETWORKS (default: from `docker network inspect`),
# DOCKER (docker binary, for tests).
set -euo pipefail

TRACKER_ENV="${TRACKER_ENV:-/opt/finance-tracker/.env}"
BOT_ENV="${BOT_ENV:-/opt/momentum/.env}"
NETWORK="${NETWORK:-vav-internal}"
API_URL="${API_URL:-http://finance-backend:8000}"
DOCKER="${DOCKER:-docker}"

log() { printf '[bot-token] %s\n' "$*"; }
die() { printf '[bot-token] ERROR: %s\n' "$*" >&2; exit 1; }

for file in "${TRACKER_ENV}" "${BOT_ENV}"; do
  [ -f "${file}" ] || die "missing ${file}"
  [ -w "${file}" ] || die "${file} is not writable (run as root)"
done

read_key() {
  # read_key <file> <KEY> → last value, quotes and CR stripped
  sed -n "s/^$2=//p" "$1" | tail -n 1 | tr -d "\"' \r"
}

ALLOWED_NETWORKS="${ALLOWED_NETWORKS:-$("${DOCKER}" network inspect "${NETWORK}" \
  --format '{{range .IPAM.Config}}{{.Subnet}},{{end}}' | sed 's/,$//')}"
[ -n "${ALLOWED_NETWORKS}" ] || die "no subnet found for docker network ${NETWORK}"

BOT_USERNAME="${BOT_USERNAME:-$(read_key "${TRACKER_ENV}" ADMIN_USERNAME)}"
[ -n "${BOT_USERNAME}" ] || die "BOT_USERNAME is empty and ADMIN_USERNAME is not set in ${TRACKER_ENV}"

sha256_hex() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
  else shasum -a 256 | cut -d' ' -f1; fi
}

umask 077
TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

set_keys() {
  # set_keys <file> KEY=VALUE... : drop old lines of these keys, append the new ones, same inode
  local file="$1" pattern="" pair
  shift
  for pair in "$@"; do pattern="${pattern:+${pattern}|}${pair%%=*}"; done
  grep -v -E "^(${pattern})=" "${file}" > "${TMP}" || true
  if [ -s "${TMP}" ] && [ -n "$(tail -c 1 "${TMP}")" ]; then printf '\n' >> "${TMP}"; fi
  printf '%s\n' "$@" >> "${TMP}"
  cat "${TMP}" > "${file}"
  : > "${TMP}"
}

TOKEN="$(openssl rand -hex 32)"
[ "${#TOKEN}" -eq 64 ] || die "token generation failed"
HASH="$(printf '%s' "${TOKEN}" | sha256_hex)"

set_keys "${BOT_ENV}" "FINANCE_API_URL=${API_URL}" "FINANCE_API_TOKEN=${TOKEN}"
set_keys "${TRACKER_ENV}" "BOT_API_TOKEN_SHA256=${HASH}" "BOT_API_USERNAME=${BOT_USERNAME}" \
  "BOT_API_ALLOWED_NETWORKS=${ALLOWED_NETWORKS}"
unset TOKEN HASH

log "${BOT_ENV}: FINANCE_API_URL, FINANCE_API_TOKEN written"
log "${TRACKER_ENV}: BOT_API_TOKEN_SHA256, BOT_API_USERNAME, BOT_API_ALLOWED_NETWORKS=${ALLOWED_NETWORKS} written"
log "restart the tracker backend to apply; the previous token (if any) stops working then"
