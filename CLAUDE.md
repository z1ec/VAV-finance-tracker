# CLAUDE.md — agent memory for the finance tracker

Multi-currency expense tracker (HUF/EUR/RUB, base currency HUF) for a small household:
FastAPI + SQLAlchemy 2 + Alembic + SQLite (WAL) backend, vanilla HTML/CSS/JS frontend served by
Caddy. Russian UI, roles `admin` / `user`. Production: `https://finance.fomin-vladimir.ru` on the
shared `dubai` server next to the Momentum Telegram bot.
Entry point: `uvicorn app.main:app` (`backend/app/main.py`), web: `web/Caddyfile`.

## Commands
```bash
cd backend
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pytest                      # all tests + coverage (no network)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                        # config in pyproject.toml
.venv/bin/alembic upgrade head        # also runs automatically on start
.venv/bin/alembic revision --autogenerate -m "msg"

# repo root
uvx --from actionlint-py actionlint          # workflows
uvx --from shellcheck-py shellcheck deploy/*.sh
cp .env.example .env && docker compose up -d --build   # local stack → http://localhost:8080
```

## Module map
| Path | What |
|---|---|
| `backend/app/main.py` | FastAPI app, `lifespan` (secret check → backup+migrations → seed → rates → scheduler), error handlers, `/api/health`. |
| `backend/app/config.py` | `Settings` (pydantic-settings, `.env`), defaults, `APP_VERSION`/`GIT_SHA`. |
| `backend/app/security.py` | Startup guard: refuse default/weak secrets on a real domain, warn on localhost. |
| `backend/app/migrate.py` | Programmatic `alembic upgrade head`; `pre-migrate-<UTC>.db` backup when migrations are pending (keeps 10). |
| `backend/app/manage.py` | Management CLI: `python -m app.manage set-password --from-env ADMIN USER1` or `--username NAME` (password on stdin). |
| `backend/app/backup.py` | `sqlite_backup` (online backup API) and pruning; used by migrations and the nightly job. |
| `backend/app/scheduler.py` | APScheduler (UTC): rates 04:00, backup 03:00 (`expenses-YYYY-MM-DD.db`, keeps 14). |
| `backend/app/currency.py` | Rate fetch (jsdelivr currency-api → open.er-api fallback), rate selection, Decimal rounding. |
| `backend/app/models.py` / `schemas.py` | ORM tables (`users`, `categories`, `expenses`, `exchange_rates`, `budgets`, `settings`) / Pydantic I/O. |
| `backend/app/auth.py`, `deps.py` | bcrypt, signed cookie session, per-IP login rate limit, `get_current_user`, `require_admin`. |
| `backend/app/routers/` | `auth`, `expenses`, `categories`, `rates`, `stats`, `budgets`. |
| `backend/migrations/` | Alembic (`render_as_batch` for SQLite). |
| `frontend/` | Static pages + ES modules (`js/api.js` wraps fetch), Chart.js vendored. |
| `web/` | `Dockerfile` (caddy:2-alpine + frontend) and production `Caddyfile`. |
| `deploy/` | `docker-compose.prod.yml`, `deploy.sh` (health check + rollback). Tests: `backend/tests/test_deploy_script.py`. |
| `.github/workflows/` | `ci.yml` (checks, image build + smoke), `deploy.yml` (GHCR + SSH deploy). |

## Conventions
- UI strings: Russian. Code, comments, docs, commits: English (except `*.ru.md`).
- Commits: Conventional Commits. Git: never push to `main`; branch → PR → green CI → merge → auto-deploy.
- Money: amounts normalised to HUF at write time (`amount_huf`), rate frozen in the row. Use `Decimal`.
- Roles are enforced in the API (`require_admin`), not only in the UI.
- Type hints on new code; mypy must pass; ruff rules in `backend/pyproject.toml`.
- Secrets only in `.env` (server: `/opt/finance-tracker/.env`, mode 600). Print keys, never values.

## Pitfalls
- **Shared server.** `dubai` also runs the Momentum bot (`/opt/momentum`, container `momentum-bot-1`).
  Never touch its stack or `.env`, never `docker system prune`, never remove volumes; ufw rules may
  only be added.
- **`vav-internal` network.** External Docker network shared with the bot; the backend joins it with
  the alias `finance-backend` (port 8000, not published). `deploy.sh` creates it if missing.
- **`:8443` redirect.** The old server served HTTPS on 8443; `web/Caddyfile` permanently redirects
  `https://<domain>:8443/<path>` to `https://<domain>/<path>`. Keep it for old bookmarks.
- **Time.** DB timestamps are UTC ISO-8601 strings; scheduler cron runs in UTC; the UI shows
  `Europe/Budapest` (DST switches in March/October).
- **SQLite = one writer.** WAL mode, short transactions, one backend replica. Back up with the
  sqlite backup API (`app/backup.py` or `sqlite3 .backup`), never by copying the file while running.
- **Secrets guard.** With `DOMAIN != localhost` the backend exits on start if `SECRET_KEY` < 32 chars
  or a configured password is default/< 12 chars. Changing `*_PASSWORD` in `.env` does **not** change
  existing users (seed only creates missing users) — run
  `docker exec finance-tracker-backend-1 python -m app.manage set-password --from-env ADMIN` (after a backup).
- **Container user.** Backend runs as uid 10001; host `data/` must be owned by 10001.
- **`ru-tracker` (old server).** Tracker stack is stopped (not removed) with backups in
  `/usr/local/etc/finance/VAV-finance-tracker/backups/`. Its nginx also serves another site
  (`fomin-vladimir.ru`) — do not touch nginx, firewall, sshd, other containers, or delete anything.

## Mandatory end-of-task checklist
1. `pytest`, `ruff check .`, `ruff format --check .`, `mypy` (in `backend/`) are green;
   `actionlint` and `shellcheck deploy/*.sh` if workflows/scripts changed.
2. Add an entry to `docs/DEVLOG.md` (date, goal, done, files, decisions, problems, open items);
   log every server action there (what, where, why — no secrets).
3. Update `CHANGELOG.md`, `docs/PLAN.md` and affected docs (`ARCHITECTURE.md`, `DEPLOYMENT.md`, ADRs, README).
4. Commit (Conventional Commits), PR, wait for green CI.

## Docs
`docs/PLAN.md` · `docs/DEVLOG.md` · `docs/ARCHITECTURE.md` · `docs/DEPLOYMENT.md` · `docs/adr/` ·
`docs/MANUAL_STEPS.ru.md` · `docs/briefs/` (one file per stage brief).
Resume after a break: read this file, `docs/PLAN.md`, the tail of `docs/DEVLOG.md`, `git log --oneline -20`.
