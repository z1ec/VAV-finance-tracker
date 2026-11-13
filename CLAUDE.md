# CLAUDE.md — agent memory for the finance tracker

Multi-currency expense tracker (HUF/EUR/RUB, base currency HUF) for a small household:
FastAPI + SQLAlchemy 2 + Alembic + SQLite (WAL) backend, vanilla HTML/CSS/JS frontend served by
Caddy. Russian UI, roles `admin` / `user`; the Momentum bot uses a service token from the internal
Docker network (`docs/API_BOT.md`). Production: `https://finance.fomin-vladimir.ru` on the
shared `dubai` server next to the Momentum Telegram bot.
Entry point: `uvicorn app.main:app` (`backend/app/main.py`), web: `web/Caddyfile`.

## Commands
```bash
cd backend
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
# requirements.in = direct deps; requirements.txt = full lock (only releases ≥ 2 weeks old):
uv pip compile requirements.in --python-version 3.12 --universal --exclude-newer <date-14d> -o requirements.txt
.venv/bin/pytest                      # all tests + coverage (no network)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                        # config in pyproject.toml
.venv/bin/alembic upgrade head        # also runs automatically on start
.venv/bin/alembic revision --autogenerate -m "msg"

# repo root
backend/.venv/bin/python scripts/export_openapi.py   # regenerate docs/openapi.json after API changes
scripts/e2e_bot_api.sh <backend-image> <web-image>  # bot API boundary e2e (throwaway containers)
uvx --from actionlint-py actionlint          # workflows
uvx --from shellcheck-py shellcheck deploy/*.sh scripts/*.sh
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
| `backend/app/backup.py` | `sqlite_backup` (online backup API, mode 600), pruning, `restrict_data_permissions` (700/600 on start). |
| `backend/app/limits.py` | `BodySizeLimitMiddleware`: 64 KB request bodies (also for the bot, which bypasses Caddy). |
| `backend/app/scheduler.py` | APScheduler: rates 04:00 UTC, backup 03:00 UTC (`expenses-YYYY-MM-DD.db`, keeps 14), subscriptions 06:00 **Europe/Budapest** (+ once on start). |
| `backend/app/timeutil.py` | Local-day helpers: bare dates = days in `settings.tz`; `now_utc()` is monkeypatched in tests. |
| `backend/app/expense_service.py` | Shared expense creation: rate, UTC normalisation, `Idempotency-Key` (replay/409/race). |
| `backend/app/subscriptions.py` | Subscription date rules (`advance`, `anchor_day`), charge job, monthly totals. |
| `backend/app/currency.py` | Rate fetch (jsdelivr currency-api → open.er-api fallback), rate selection, Decimal rounding. |
| `backend/app/models.py` / `schemas.py` | ORM tables (`users`, `categories`, `expenses`, `exchange_rates`, `budgets`, `settings`, `subscriptions`, `subscription_charges`) / Pydantic I/O. |
| `backend/app/auth.py`, `deps.py` | bcrypt, cookie session, bot token (sha256 + network), per-IP rate limit; `get_actor`, `get_current_user` (cookie only), `require_admin`, `get_reader`/`require_writer` (token allowed), `token_guard` + `TOKEN_ALLOWED_ROUTES` (matched on the request path, not `scope["route"]`), `origin_guard` (CSRF). |
| `backend/app/routers/` | `auth`, `expenses`, `categories`, `rates`, `stats`, `budgets`, `subscriptions`. |
| `backend/migrations/` | Alembic (`render_as_batch` for SQLite). |
| `frontend/` | Static pages (`index`, `dashboard`, `subscriptions`, `settings`, `login`) + ES modules (`js/api.js` wraps fetch), Chart.js vendored. |
| `scripts/` | `export_openapi.py` (→ `docs/openapi.json`, checked by `tests/test_openapi.py`), `e2e_bot_api.sh` (+ `e2e_bot_client.py`, `e2e_echo.py`). |
| `web/` | `Dockerfile` (caddy:2-alpine + frontend) and production `Caddyfile`. |
| `deploy/` | `docker-compose.prod.yml`, `deploy.sh` (health check + rollback). Tests: `backend/tests/test_deploy_script.py`. |
| `.github/workflows/` | `ci.yml` (checks, image build + smoke + e2e, `security`: gitleaks/pip-audit/trivy, weekly), `deploy.yml` (GHCR + SSH deploy). Actions pinned by SHA; `dependabot.yml`. |
| `.gitleaks.toml`, `.trivyignore` | Scanner allowlists — each entry reviewed in `docs/SECURITY.md`; trivy entries expire. |

## Conventions
- UI strings: Russian. Code, comments, docs, commits: English (except `*.ru.md`).
- Commits: Conventional Commits. Git: never push to `main`; branch → PR → green CI → merge → auto-deploy.
- Money: amounts normalised to HUF at write time (`amount_huf`), rate frozen in the row. Use `Decimal`.
- Roles are enforced in the API (`require_admin`), not only in the UI.
- New endpoint: classify it in `tests/test_bot_auth.py::EXPECTED`; the token gets `403` unless the
  route is added to `deps.TOKEN_ALLOWED_ROUTES` (and `docs/API_BOT.md`). Regenerate `docs/openapi.json`.
- Dates: bare `YYYY-MM-DD` = local day in `Europe/Budapest` (`app/timeutil.py`); store UTC `+00:00`.
- Schema change: Alembic migration + upgrade test in `tests/test_migrate.py`; the drift test
  (`test_models_match_migrations`) must stay green; rehearse on a prod copy (`docs/DEPLOYMENT.md`).
- Type hints on new code; mypy must pass; ruff rules in `backend/pyproject.toml`.
- Frontend: every API value goes through `escapeHtml` before `innerHTML` (text is untrusted —
  comments come from the bot's OCR); no inline `<script>`/`on*=` (CSP `script-src 'self'`).
- Security: new findings → `docs/SECURITY.md` table; a new third-party action → pin by SHA with a
  version comment; never silence a scanner without an entry (and an expiry for trivy).
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
- **Bot token.** The tracker's `.env` holds only `BOT_API_TOKEN_SHA256`; the token is in
  `/opt/momentum/.env` (`FINANCE_API_TOKEN`) — the only keys of the bot's `.env` this project may
  write. Accepted only from `BOT_API_ALLOWED_NETWORKS` (`172.20.0.0/16`) without
  `X-Forwarded-For`; Caddy returns 403 to any `Authorization` header. 5 failed tokens per IP in
  15 min → 429 even for the right token. Rotation: `docs/MANUAL_STEPS.ru.md`.
- **Subscriptions job** runs at 06:00 Budapest and on every start; it is idempotent
  (`UNIQUE(subscription_id, due_date)`), never create test expenses/subscriptions in prod.
- **Secrets guard.** With `DOMAIN != localhost` the backend exits on start if `SECRET_KEY` < 32 chars
  or a configured password is default/< 12 chars. Changing `*_PASSWORD` in `.env` does **not** change
  existing users (seed only creates missing users) — run
  `docker exec finance-tracker-backend-1 python -m app.manage set-password --from-env ADMIN` (after a backup).
- **Container user.** Backend runs as uid 10001; host `data/` must be owned by 10001. Since 1.3.0
  `data/` is 700 (files 600): `deploy` cannot read it — touch the DB only inside a backend
  container (`docker exec`, as `deploy.sh` does) or as root.
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
`docs/API_BOT.md` + `docs/openapi.json` (bot contract) ·
`docs/MANUAL_STEPS.ru.md` · `docs/SECURITY.md` · `docs/briefs/` (one file per stage brief).
Resume after a break: read this file, `docs/PLAN.md`, the tail of `docs/DEVLOG.md`, `git log --oneline -20`.
