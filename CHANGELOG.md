# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.1.0] — 2026-11-12

### Added
- Startup guard: on a real `DOMAIN` the backend refuses to start with an empty/default/short
  `SECRET_KEY` or a default/short user password (setting names only, never values); warns on
  `localhost`.
- Management command `python -m app.manage set-password` (`--from-env ADMIN USER1` or
  `--username NAME` with the password on stdin); `.env` passwords are only used when a user is
  first created.
- Automatic `pre-migrate-<UTC>.db` backup when Alembic migrations are pending (newest 10 kept).
- `/api/health` reports `version` and `git_sha`; both are logged on startup.
- Production images in GHCR: `finance-tracker-backend` (non-root uid 10001, HEALTHCHECK) and
  `finance-tracker-web` (Caddy with the frontend baked in), one `sha-<7>` tag for both.
- CI (ruff, ruff format, mypy, pytest with coverage, actionlint, shellcheck, deploy.sh dry run,
  image build + smoke tests) and CD over SSH with health check and automatic rollback.
- Backend joins the external Docker network `vav-internal` (alias `finance-backend`) for the bot.
- Docs: CLAUDE.md, DEVLOG, PLAN, ARCHITECTURE, DEPLOYMENT, ADR 0001–0003, MANUAL_STEPS.ru.

### Changed
- Hosting moved from `ru-tracker` to the shared `dubai` server.
- Main address is `https://finance.fomin-vladimir.ru` (443); `:8443` permanently redirects to it.
- `backend/pyproject.toml` with project metadata and tool configs; dev tools in
  `requirements-dev.txt`; whole backend formatted with ruff.
- Root `docker-compose.yml` is the local development stack (`http://localhost:8080`).
- Exchange-rate fetch failures are logged before falling back.

### Removed
- `Caddyfile` (8443-only), `Caddyfile.behind-proxy`, `docker-compose.behind-proxy.yml`
  (replaced by `web/Caddyfile` and `deploy/docker-compose.prod.yml`).

## [1.0.0] — 2026-09-16

### Added
- Multi-currency expense tracking (HUF/EUR/RUB, base HUF) with rates frozen per expense.
- Daily exchange rates (currency-api, open.er-api fallback), manual rates with priority.
- Dashboard (summary, by category, by currency, top expenses), monthly budgets per category.
- Roles `admin` / `user`, bcrypt passwords, cookie sessions, login rate limit.
- Soft delete and restore, CSV export, category archive.
- SQLite (WAL) with Alembic, nightly backups (14 kept), Caddy with automatic HTTPS on 8443,
  behind-proxy variant.
- 18 tests (currency logic, permissions).
