# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.2.0] — 2026-11-12

### Added
- **API for the Momentum bot** (`docs/API_BOT.md`, `docs/openapi.json`): service token
  `Authorization: Bearer` (only its SHA-256 in `BOT_API_TOKEN_SHA256`), accepted only from
  `BOT_API_ALLOWED_NETWORKS` (the `vav-internal` subnet) and without proxy headers, endpoint
  allowlist (everything else → 403), acts as `BOT_API_USERNAME`; failed tokens share the login
  rate limit and are never logged.
- `Idempotency-Key` (UUID) on `POST /api/expenses` and on paying a subscription charge: replay →
  200 with the same record, same key with other content → 409; required for the bot.
- `expenses.source` (`web | telegram | subscription`): in the API, the CSV export and as a
  🤖 / 🔁 icon next to the expense on the site.
- **Subscriptions and recurring payments**: fixed amounts are recorded automatically (daily at
  06:00 Europe/Budapest and on start, catching up missed dates), variable amounts wait for the
  bot or the site to enter the amount; `anchor_day` keeps the 31st / 29 February.
  API: list, create, update, deactivate, upcoming, monthly summary, pay, skip.
- Site: page **«Подписки»** (list, add/edit, «Ждут суммы», total per month) and the dashboard
  block **«Ближайшие списания (7 дней)»** with the monthly subscriptions total.
- `scripts/export_openapi.py`; CI fails when `docs/openapi.json` is stale.
- `scripts/e2e_bot_api.sh`: throwaway-container e2e of the bot boundary, including an
  experiment that shows what Caddy forwards; runs in CI.
- Startup guard validates the bot settings (hash format, user, private networks only).
- Migration `0002` (back-fills `source = web`); ADR 0004–0007.

### Changed
- **Bare dates are local days in Europe/Budapest** for every filter and stat (list, export,
  summary, by-category, by-currency, top, by-month, budget); "this month" is the local month.
  Previously these were UTC days, so expenses between 00:00 and 01:00/02:00 local time fell
  into the previous day or month.
- `occurred_at` is stored normalised to UTC; the bot must send an offset (422 otherwise).
- Caddy answers 403 to any request with an `Authorization` header; uvicorn runs with
  `--no-proxy-headers`.
- The site's forms send an `Idempotency-Key` (no duplicate on a double click).
- Deploy workflow: `DEPLOY_USER` / `DEPLOY_PORT` are environment variables, not secrets (readable
  deploy logs).
- Invalid date query parameters answer 422 instead of 500.

### Fixed
- Date-time input near the October DST switch (e.g. 25 Oct 01:30) was saved one hour late.

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
