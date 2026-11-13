# Plan

Roadmap: five stages. Briefs live in `docs/briefs/`.

## Stage 1 — move to Dubai, CI/CD, quality baseline, docs (2026-11-12)
- [x] DNS check (authoritative + public resolvers), AAAA absent
- [x] Read-only recon of `ru-tracker` and `dubai`, foreign-site baseline (200)
- [x] Freeze `ru-tracker`, sqlite backup, integrity, row counts, sha256
- [x] Stream DB + `.env` to dubai, verify sha256 / integrity / row counts
- [x] Bootstrap stack in Dubai (80, 443→8443, 8443), certificate, health on both ports — downtime 50 s
- [x] `pyproject.toml` (1.1.0), ruff + format, mypy, pytest-cov; all green
- [x] Startup guard for weak secrets (+ tests); weak prod passwords rotated in `.env`
- [x] Backup before pending migrations (+ tests); version in `/api/health`
- [x] Backend image (multi-stage, uid 10001, HEALTHCHECK), web image (Caddy 443, :8443 redirect)
- [x] `deploy/docker-compose.prod.yml`, `deploy.sh` (+ dry-run tests), `vav-internal`
- [x] CI (`ci.yml`) and CD (`deploy.yml`) workflows; PR CI green
- [x] Docs: CLAUDE.md, DEVLOG, PLAN, ARCHITECTURE, DEPLOYMENT, ADR 0001–0003, CHANGELOG, README, MANUAL_STEPS.ru
- [x] Dedicated CD key for `deploy@dubai` + `production` secrets + `DEPLOY_PLATFORM`
- [x] Switch bootstrap → CD (`down`, backup, `DEPLOY_ENABLED=true`, merge, Deploy success, §7 checks) — downtime 3 min 40 s
- [x] `python -m app.manage set-password`; new ADMIN/USER1 passwords active in the DB
- [x] `bootstrap-src` and override removed (backups and volumes kept)
- [ ] Owner: collect new passwords and delete the file; remove the old stack on ru-tracker after 2026-11-26

## Stage 2a — API for the bot, subscriptions, time zones (2026-11-12)
Brief: `docs/briefs/2026-11-12-stage2a-bot-api.md`.
- [x] Bot token: sha256 + compare_digest, `BOT_API_*` settings, startup validation
- [x] Token only from `BOT_API_ALLOWED_NETWORKS` without proxy headers; Caddy 403 on `Authorization`; experiment in ADR-0004
- [x] Endpoint allowlist + permission matrix test (every route × admin / user / token / anonymous)
- [x] `Idempotency-Key` (replay, 409, parallel), `expenses.client_request_id`
- [x] `expenses.source` (API, CSV, icons on the site)
- [x] Local-day boundaries (Europe/Budapest) everywhere, DST tests; frontend DST fix
- [x] Subscriptions + charges: model, migration 0002, job 06:00 local + on start, API, page, dashboard block
- [x] `docs/API_BOT.md`, `docs/openapi.json` + export script + CI check, ADR 0004–0007
- [x] e2e script (throwaway containers) in CI
- [x] Migration rehearsal on a copy of prod on the server (integrity ok, counts equal)
- [x] PR → CI → merge → Deploy success
- [x] Token issued on the server (hash → tracker `.env`, token → bot `.env`), tracker restarted
- [x] §8.3 checks from a temp container on `vav-internal`; public domain → 403; bot healthy
- [x] Stage 1 tails: `DEPLOY_USER`/`DEPLOY_PORT` → variables (deploy log readable); `finance-bootstrap` volumes/image removed
- [x] Owner: nothing required (token rotation procedure in `MANUAL_STEPS.ru.md`)

## Stage 2b — bot integration
_Placeholder._ The bot joins `vav-internal`, reads `FINANCE_API_URL` / `FINANCE_API_TOKEN` and
follows `docs/API_BOT.md` (+ `docs/openapi.json`).

## Stage 3a — widgets
_Placeholder._

## Stage 3b — security audit (2026-11-13/14)
Brief: `docs/briefs/2026-11-12-stage3b-security-audit.md` (run 1 of 2: tracker; run 2: bot).
Results: `docs/SECURITY.md`.
- [x] Scans: gitleaks (full history), pip-audit (runtime + dev), trivy (both images)
- [x] Dependencies: FastAPI 0.141.1 / Starlette 1.10.0, `python-multipart` and `pytest-asyncio` removed, full lock (`requirements.in` → `requirements.txt`), pytest 9.1.1
- [x] Token allowlist matched on the request path (FastAPI 0.14x routing change); `GET /api/stats/by-category` allowed for the token (+ matrix, API_BOT.md, openapi.json)
- [x] XSS: `icon` validated on the server, every API value escaped (`escapeHtml` with quotes), no inline scripts
- [x] CSV formula injection, input bounds, 64 KB body limit (Caddy + app), Origin/Sec-Fetch-Site check
- [x] Caddy security headers (CSP, frame-ancestors, HSTS, …), checked in headless Chrome and e2e
- [x] Cookies on logout, limiter memory bound, docs off in production, data/backups 700/600
- [x] Images: `apt-get upgrade`, no CA keys in the web image; compose hardening (read-only, cap_drop, no-new-privileges)
- [x] `deploy.sh`: pull timeout × 3 (bot PR #8), pre-deploy backup + restore after a failed migration (bot stage 3a); job limit 30 min; dry-run tests
- [x] CI job `security`, Dependabot, actions pinned by SHA, `persist-credentials: false`, weekly run
- [x] Read-only review of `dubai` and `ru-tracker`; recommendations and secret rotation in `MANUAL_STEPS.ru.md`
- [x] Issues #5 (session revocation), #6 (non-root Caddy), #7 (Caddy x/net CVE)
- [x] PR → CI → merge → Deploy success, production checks
- [ ] Owner: MANUAL_STEPS 3b.1–3b.7 (SSH password auth, `tmpuser`, key `restrict`, updates, leftovers, `ru-tracker`, GitHub protection)
- [ ] Run 2 in the bot repository (switch `/money` to `by-category`, section 5 cross-checks)
