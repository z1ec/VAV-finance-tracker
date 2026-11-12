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
- [ ] Dedicated CD key for `deploy@dubai` + `production` secrets + `DEPLOY_PLATFORM` (blocked: owner permission)
- [ ] Switch bootstrap → CD (backup, `down`, `DEPLOY_ENABLED=true`, merge, verify), remove `bootstrap-src`
- [ ] New passwords active in the DB (owner)

## Stage 2a — API for the bot and subscriptions
_Placeholder: brief to come (`docs/briefs/`)._ Backend reachable from the bot as
`http://finance-backend:8000` on `vav-internal`; service-to-service auth; subscription model.

## Stage 2b — bot integration
_Placeholder._

## Stage 3a — widgets
_Placeholder._

## Stage 3b — security audit
_Placeholder._
