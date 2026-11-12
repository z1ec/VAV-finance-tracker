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
- [ ] PR → CI → merge → Deploy success
- [ ] Token issued on the server (hash → tracker `.env`, token → bot `.env`), tracker restarted
- [ ] §8.3 checks from a temp container on `vav-internal`; public domain → 403; bot healthy
- [ ] Stage 1 tails: `DEPLOY_USER`/`DEPLOY_PORT` → variables; `finance-bootstrap` volumes/image removed
- [ ] Owner: nothing required (token rotation procedure in `MANUAL_STEPS.ru.md`)

## Stage 2b — bot integration
_Placeholder._ The bot joins `vav-internal`, reads `FINANCE_API_URL` / `FINANCE_API_TOKEN` and
follows `docs/API_BOT.md` (+ `docs/openapi.json`).

## Stage 3a — widgets
_Placeholder._

## Stage 3b — security audit
_Placeholder._
