#!/usr/bin/env bash
# End-to-end check of the bot API boundary with throwaway containers and a throwaway database
# (never production). Mirrors the server layout:
#   <run>-default   web (Caddy) + backend          ≈ finance-tracker_default
#   <run>-internal  backend (alias finance-backend) + the "bot" client  ≈ vav-internal
#
# Phase A — Caddy experiment: the real web image in front of an echo upstream shows what Caddy
#           forwards (X-Forwarded-For is always set and cannot be spoofed; Authorization → 403).
# Phase B — the real backend: token only from the internal network and only without proxy
#           headers, allowlist, Idempotency-Key (replay, 409, parallel), subscriptions.
#
# Usage: scripts/e2e_bot_api.sh <backend-image> <web-image>
# The random test token is generated here, passed to containers via env files and never printed.
set -euo pipefail

BACKEND_IMAGE="${1:?usage: e2e_bot_api.sh <backend-image> <web-image>}"
WEB_IMAGE="${2:?usage: e2e_bot_api.sh <backend-image> <web-image>}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
RUN="e2e-bot-$(date +%s)-$$"
NET_DEFAULT="${RUN}-default"
NET_INTERNAL="${RUN}-internal"
WORK="$(mktemp -d)"

log() { printf '[e2e] %s\n' "$*"; }

cleanup() {
  docker rm -f "${RUN}-echo" "${RUN}-web" "${RUN}-backend" >/dev/null 2>&1 || true
  docker network rm "${NET_DEFAULT}" "${NET_INTERNAL}" >/dev/null 2>&1 || true
  rm -rf "${WORK}"
}
trap cleanup EXIT

client() {
  # client <network> <mode>
  docker run --rm --network "$1" --env-file "${WORK}/client.env" \
    -v "${SCRIPTS}:/e2e:ro" --entrypoint python "${BACKEND_IMAGE}" /e2e/e2e_bot_client.py "$2"
}

wait_http() {
  # wait_http <container> <python check expression>
  local i
  for i in $(seq 1 40); do
    if docker exec "$1" python -c "$2" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  docker logs "$1" | tail -30
  log "timeout waiting for $1 (${i}s)"
  return 1
}

docker network create "${NET_DEFAULT}" >/dev/null
docker network create "${NET_INTERNAL}" >/dev/null
SUBNET="$(docker network inspect "${NET_INTERNAL}" --format '{{(index .IPAM.Config 0).Subnet}}')"
log "internal subnet ${SUBNET}"

umask 077
TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
printf 'E2E_TOKEN=%s\n' "${TOKEN}" > "${WORK}/client.env"
{
  echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  echo "ADMIN_USERNAME=admin"
  echo "ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  echo "USER1_USERNAME="
  echo "USER2_USERNAME="
  echo "DOMAIN=localhost"
  echo "TZ=Europe/Budapest"
  echo "BOT_API_TOKEN_SHA256=$(printf '%s' "${TOKEN}" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
  echo "BOT_API_USERNAME=admin"
  echo "BOT_API_ALLOWED_NETWORKS=${SUBNET}"
} > "${WORK}/backend.env"
unset TOKEN

# ---------------------------------------------------------------- phase A: Caddy experiment
log "phase A: Caddy in front of an echo upstream"
docker run -d --name "${RUN}-echo" --network "${NET_DEFAULT}" --network-alias backend \
  -v "${SCRIPTS}:/e2e:ro" --entrypoint python "${BACKEND_IMAGE}" /e2e/e2e_echo.py >/dev/null
docker run -d --name "${RUN}-web" --network "${NET_DEFAULT}" --network-alias web -e DOMAIN=localhost \
  "${WEB_IMAGE}" >/dev/null
wait_http "${RUN}-echo" "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/', timeout=2)"
sleep 2
client "${NET_DEFAULT}" caddy-echo
docker rm -f "${RUN}-echo" "${RUN}-web" >/dev/null

# ---------------------------------------------------------------- phase B: real backend
log "phase B: real backend with a throwaway database"
docker create --name "${RUN}-backend" --network "${NET_DEFAULT}" --network-alias backend \
  --env-file "${WORK}/backend.env" "${BACKEND_IMAGE}" >/dev/null
docker network connect --alias finance-backend "${NET_INTERNAL}" "${RUN}-backend"
docker start "${RUN}-backend" >/dev/null
wait_http "${RUN}-backend" \
  "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
docker run -d --name "${RUN}-web" --network "${NET_DEFAULT}" --network-alias web -e DOMAIN=localhost \
  "${WEB_IMAGE}" >/dev/null
sleep 2

client "${NET_INTERNAL}" bot
client "${NET_DEFAULT}" outside

if docker logs "${RUN}-backend" 2>&1 | grep -qF "$(sed -n 's/^E2E_TOKEN=//p' "${WORK}/client.env")"; then
  log "FAIL: the token appears in the backend logs"
  exit 1
fi
log "PASS: the token does not appear in the backend logs"
log "all e2e checks passed"
