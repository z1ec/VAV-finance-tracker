# ADR-0001: Stack — FastAPI, SQLite, vanilla frontend, Caddy

- Status: accepted (recorded retroactively)
- Date: 2026-09-13 (written 2026-11-12)

## Context
A private expense tracker for 2–3 people in three currencies (HUF, EUR, RUB) with HUF as the base
currency, a dashboard and per-category budgets. Tiny load, one small VPS, minimal operations.

## Decision
- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, Alembic, APScheduler.
- **Database:** SQLite in WAL mode, one file in `/data`, nightly backups via the sqlite backup API.
- **Money model:** every expense stores its original amount, the frozen `rate_to_huf`, its source
  (`api`/`manual`) and `amount_huf`; reports aggregate HUF only. Decimal arithmetic.
- **Rates:** `@fawazahmed0/currency-api` (no key, has RUB) with `open.er-api.com` as fallback;
  manual rates override API rates.
- **Frontend:** static HTML/CSS/ES modules, Chart.js vendored (no CDN, no build step).
- **Edge:** Caddy (automatic HTTPS, static files, reverse proxy to the backend).
- **Auth:** bcrypt passwords, signed cookie session (itsdangerous), per-IP login rate limit, roles
  enforced in the API.

## Alternatives
- **PostgreSQL** — more moving parts and memory for a few hundred rows per month.
- **SPA framework (React/Vue)** — build tooling and dependencies for four simple pages.
- **nginx + certbot** — more configuration than Caddy for the same result.

## Consequences
- Single writer: one backend replica only; keep transactions short.
- Backups and migration of the whole app = one SQLite file plus `.env`.
- No horizontal scaling (not needed).
