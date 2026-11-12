# Finance tracker — development log

Chronological journal of the project. **Main source of project history.**
Newest entries at the bottom. Each entry: date · goal · what was done · key files ·
decisions · problems · open items. Every action on a server is logged here (what, where, why —
never secret values).

---

## Assumptions

Decisions made autonomously during stage 1 (brief: `docs/briefs/2026-11-12-stage1-migration-cicd.md`).

| # | Assumption | Why |
|---|---|---|
| A1 | DNS was accepted as switched although the dev machine's ISP resolvers and one Cloudflare PoP (via DoH) still returned the old A record: `ns1/ns2.reg.ru`, `1.1.1.1`, `8.8.8.8` and `9.9.9.9` queried **from dubai** all returned `81.85.92.119`, no AAAA. | The authoritative zone and the resolvers named in the brief were correct; stale caches (old TTL 86400) cannot be fixed by waiting 2 h. Let's Encrypt validated successfully. |
| A2 | `ru-tracker` actually ran the *behind-proxy* variant (Caddy on `127.0.0.1:8080` behind the host nginx, compose v1 `docker-compose -f docker-compose.behind-proxy.yml`), not variant A from the README. It was stopped with that file. | Found during read-only recon (container labels). |
| A3 | Neither server has the `sqlite3` CLI; backups and checks used Python's `sqlite3` module (`Connection.backup`, `PRAGMA integrity_check`) — the same online backup API as `.backup`. No packages were installed. | Avoid changing the servers beyond the brief. |
| A4 | The foreign site on `ru-tracker` is `fomin-vladimir.ru` / `www.fomin-vladimir.ru` (nginx vhost). Baseline: HTTPS `200`, HTTP `301`. | Taken read-only from `/etc/nginx/sites-enabled`. |
| A5 | No swap was created on dubai: RAM is 954 MiB, but swap is already exactly 1 GiB (`/swapfile`); the brief's rule is "swap < 1 GB". | Brief §2.3. |
| A6 | The bootstrap image and Caddy image were built/pulled in Dubai **before** freezing ru-tracker, and ufw ports were opened in advance. | Minimises downtime; nothing listened on the ports until start. |
| A7 | The bootstrap stack used compose project `finance-bootstrap` with `--env-file /opt/finance-tracker/.env`; production uses project `finance-tracker` with its own Caddy volumes (a second certificate is issued; well within Let's Encrypt limits). | Keeps the temporary stack disposable. |
| A8 | Weak passwords (ADMIN 10 chars, USER1 11 chars) were rotated in `.env`; `SECRET_KEY` (64 chars) was kept so existing sessions stay valid. USER2 (16 chars) unchanged. | Brief §4.1. Rotating the key would log everybody out for no gain. |
| A9 | The web image validates the Caddyfile at build time for `localhost` and a real domain; HTTP/3 is disabled because only TCP ports are opened in ufw. | Avoids advertising `h3` on a closed UDP port. |
| A10 | `docker-compose.yml` in the root is now the local development stack (builds both images, `http://localhost:8080`); `Caddyfile`, `Caddyfile.behind-proxy` and `docker-compose.behind-proxy.yml` were removed from the repo (the stopped stack on ru-tracker keeps its own copies). | One production path (`deploy/`), one dev path. |
| A11 | `/api/health` additionally returns `git_sha` next to `version`. | Makes "which build is running" unambiguous. |
| A12 | Alembic's `fileConfig` is skipped when migrations run inside the app (it disabled the app's JSON loggers). The CLI still configures logging from `alembic.ini`. | Startup logs (version, backups) must be visible. |
| A13 | `actionlint` / `shellcheck` are not installed locally; they run through `uvx` (`actionlint-py`, `shellcheck-py`), the same way in CI. | No system changes on the dev machine. |

---

## History before stage 1 (from `git log`)

| Date | Commit | Summary |
|---|---|---|
| 2026-09-16 | `35f82cb`, `2e6c448` | Initial tracker: FastAPI backend (expenses, categories, rates, stats, budgets, roles), Alembic `0001`, vanilla frontend, Caddy, Docker Compose, 18 tests. |
| 2026-09-16 | `028549a` | Behind-proxy variant (`docker-compose.behind-proxy.yml`, `Caddyfile.behind-proxy`) and README for hosts where nginx owns 80/443 — this is how it ran on ru-tracker. |

Deployment was manual on `ru-tracker` (`/usr/local/etc/finance/VAV-finance-tracker`), no CI.

---

## 2026-11-12 — Stage 1: move to Dubai, quality baseline, images, CI/CD, docs

**Goal:** move the tracker from `ru-tracker` to the shared `dubai` server first (minimal
downtime), then add a code-quality baseline, safe startup, production images, CI/CD and docs.

### Quick move (done first, before any code change) — all times UTC

**DNS (11:15)** — local `dig` blocked (UDP/53). DoH: `dns.google` → `81.85.92.119`;
`cloudflare-dns.com` (local PoP) → old `157.22.253.65`, TTL 85802. From dubai:
`ns1.reg.ru`, `ns2.reg.ru`, `1.1.1.1`, `8.8.8.8`, `9.9.9.9` → `81.85.92.119`, TTL 86400; **no AAAA**. See A1.

**Recon `ru-tracker` (read-only, 11:16)**

| Item | Value |
|---|---|
| Stack dir / project | `/usr/local/etc/finance/VAV-finance-tracker`, project `vav-finance-tracker`, file `docker-compose.behind-proxy.yml` |
| Docker / Compose | Docker 29.1.3, docker-compose 1.29.2 (v1) |
| Containers | `vav-finance-tracker_caddy_1` (`127.0.0.1:8080->80`), `vav-finance-tracker_backend_1` |
| Listeners (`ss -tlnup`) | nginx `0.0.0.0:80`, `0.0.0.0:443` (+ IPv6); sshd 22; docker-proxy `127.0.0.1:8080`; systemd-resolved 53 |
| nginx vhosts (read-only) | `fomin-vladimir.ru` (foreign site, certbot), `finance.fomin-vladimir.ru` → `127.0.0.1:8080` (certbot) |
| `.env` keys | `SECRET_KEY ADMIN_USERNAME ADMIN_PASSWORD USER1_USERNAME USER1_PASSWORD USER2_USERNAME USER2_PASSWORD DB_PATH TZ DOMAIN` |
| Foreign-site baseline | `https://fomin-vladimir.ru/` → **200**, `http://` → 301 (same for `www`) |
| In-app backups | `data/backups/expenses-YYYY-MM-DD.db`, daily |

**Recon `dubai` (read-only, 11:17)**

| Item | Value |
|---|---|
| `uname -m` | `x86_64` → `DEPLOY_PLATFORM=linux/amd64` |
| RAM / swap | 954 MiB / 1024 MiB (`/swapfile`) — no new swap (A5) |
| Disk | `/` 24 G, 22 % used |
| ufw | active, default deny incoming; only `OpenSSH` (v4+v6) |
| Listeners | sshd 22, systemd-resolved, chronyd only |
| Docker / Compose | 29.9.3 / v5.7.0 |
| Bot | `momentum-bot-1` `ghcr.io/z1ec/momentum-bot:sha-1a58395`, Up 38 h **(healthy)** |
| Networks | `bridge host none momentum_default` |
| User `deploy` | uid 1001, groups `deploy docker`; `authorized_keys` has only `momentum-deploy` |

**Preparation in Dubai (before freezing)**
- 11:19 `mkdir -p /opt/finance-tracker/{data,backups,bootstrap-src}`;
  `git archive origin/main (028549a) | ssh dubai "tar -x -C /opt/finance-tracker/bootstrap-src"`;
  `bootstrap-src/docker-compose.override.yml`: backend data → `/opt/finance-tracker/data`,
  Caddy ports `80:80`, `443:8443`, `8443:8443` (validated with `docker compose config`).
- 11:20 `ufw allow 80/tcp`, `443/tcp`, `8443/tcp` (adds v4+v6 rules; SSH rule untouched).
- 11:20–11:27 pre-built `finance-bootstrap-backend` image, pulled `caddy:2-alpine`.

**Freeze and backup on `ru-tracker`**
- **11:22:33** `docker-compose -f docker-compose.behind-proxy.yml stop` → both containers
  `Exited (0)` at 11:22:37. Nothing removed.
- Backup (Python sqlite backup API, A3) → `backups/pre-migration-2026-11-12.db` (90 112 B) next to the
  stack. `integrity_check: ok`. sha256 `b5a843980d379476af1a2081ecbb820ccc6aea90e4a7909b71afb33edca18e0d`.
  (The read-only check left empty `-shm`/`-wal` side files next to it; harmless, kept.)

| Table | Rows at freeze |
|---|---|
| users | 3 |
| categories | 11 |
| expenses | 87 |
| exchange_rates | 116 |
| budgets | 1 |
| settings | 0 |

**Transfer (11:22)** — `ssh ru-tracker "tar -C … -czf - --transform … backups/pre-migration-2026-11-12.db .env" | ssh dubai "tar -C /opt/finance-tracker -xzf -"`:
backup → `data/expenses.db`, `.env` → `/opt/finance-tracker/.env` (`chmod 600`). Nothing stored on
the workstation. On dubai: sha256 **identical**, `integrity_check: ok`, row counts **identical**.
Reference copy: `/opt/finance-tracker/backups/pre-migration-2026-11-12.db`.

**Bootstrap start (11:23:08)** — `docker compose -p finance-bootstrap --env-file /opt/finance-tracker/.env up -d`.
Caddy obtained a Let's Encrypt certificate (HTTP-01, issuer `YE2`, valid to 2027-02-10) at 11:23:20.

**First successful `https://finance.fomin-vladimir.ru/api/health` at 11:23:23 → downtime 50 s**
(11:22:33 → 11:23:23).

Checks: `/api/health` on `:443` and `:8443` → `{"status":"ok","db":"ok",…}`; `login.html` → 200;
row counts unchanged; `momentum-bot-1` healthy. Clients behind resolvers with the cached old
record reach the stopped old server (nginx 502) until their cache expires (≤ 24 h) — A1,
owner note in `MANUAL_STEPS.ru.md`.

### Code (branch `feat/stage1-cicd`, PR #1)
- `backend/pyproject.toml` (v1.1.0): ruff (`E,F,I,B,UP,S,ASYNC,RUF,SIM`, line 100, RUF001-003
  ignored, `extend-immutable-calls` for `Depends`/`Query`), mypy (`check_untyped_defs`,
  pydantic plugin), pytest + coverage. `requirements-dev.txt` for tools. Whole backend formatted;
  Numeric columns typed as `Decimal`; rate-fetch failures are now logged instead of `pass`.
- `app/security.py`: startup guard (fail on real domain, warn on localhost; names only).
- `app/migrate.py` + `app/backup.py`: backup before pending migrations (keeps 10), shared backup helper.
- `/api/health` returns `version` and `git_sha`; both logged on start.
- `backend/Dockerfile` multi-stage, non-root uid 10001, HEALTHCHECK; `backend/.dockerignore`.
- `web/Dockerfile` + `web/Caddyfile`: 443, `:8443` → 301 without port, Caddyfile validated in build.
- `deploy/docker-compose.prod.yml`, `deploy/deploy.sh` (two services, web check via `--resolve`,
  rollback, current + previous tag, `vav-internal`), dry-run tests (12).
- `.github/workflows/ci.yml`, `deploy.yml`.
- Tests: 18 → 51 (secrets guard, migration backup, health version, manage command, deploy script).
- Local results: `ruff check` ✓, `ruff format --check` ✓, `mypy` ✓ (34 files), `pytest` 51 passed,
  `actionlint` ✓, `shellcheck deploy/*.sh` ✓. CI on PR #1: all jobs green.

### Server preparation for CD (dubai)
- 11:31 `.env` audit (lengths only): `SECRET_KEY` 64 ✓, `ADMIN_PASSWORD` 10 ✗, `USER1_PASSWORD` 11 ✗,
  `USER2_PASSWORD` 16 ✓.
- 11:32:56 backups `backups/env-before-rotation-20261112T113256Z` (600) and
  `backups/pre-password-rotation-20261112T113256Z.db`; new `ADMIN_PASSWORD` and `USER1_PASSWORD`
  (24 chars, `secrets.token_urlsafe`) generated **on the server** and written to `.env`;
  credentials in `/root/finance-tracker-new-passwords.txt` (600, root).
- 11:33 a first attempt to update the two users' password hashes in the database (inline script)
  was denied by the session's permission policy; the owner then explicitly allowed it, asking for
  a proper management command instead of ad-hoc code → `app/manage.py` (below).
- 11:34 `docker network create vav-internal`; `chown deploy:deploy /opt/finance-tracker
  /opt/finance-tracker/.env /opt/finance-tracker/backups`.
- 11:35 installing the CD key was denied by the permission policy; the owner granted it
  explicitly (persistent access for GitHub Actions to `deploy@dubai` is intended).
- 12:18 `backend/app/manage.py`: `python -m app.manage set-password --from-env ADMIN USER1` or
  `--username NAME` (password on stdin); 12-char/default policy, atomic, never prints secrets;
  5 tests. Merged with PR #1.
- 12:20 new ed25519 key pair `finance-tracker-deploy` (SHA256:CvCg0JD7…sluM) generated in a temp dir
  on the workstation; public half appended to `/home/deploy/.ssh/authorized_keys`
  (`momentum-deploy` untouched). Host key from `ssh-keyscan -t ed25519` =
  `SHA256:g45pBY6Fb5yMeGcv4PQkGELdRtdLTdyZYtfQ6Ey1b5Y`, identical to `~/.ssh/known_hosts`.
  Login as `deploy` with the new key verified.
- 12:20 GitHub: environment `production` created; secrets `DEPLOY_HOST`, `DEPLOY_USER`,
  `DEPLOY_PORT`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` set via stdin; variable
  `DEPLOY_PLATFORM=linux/amd64`. Local private key, public key and known_hosts copy deleted.

### Switch from the bootstrap stack to CD (§7) — times UTC
- PR #1 checks on `ba164bb`: all green (lint · typecheck · tests, actionlint · shellcheck ·
  deploy.sh dry run, docker build + smoke test).
- **12:21:57** `docker compose -p finance-bootstrap … down` (no `-v`; volumes
  `finance-bootstrap_caddy_*` kept). Writes stopped first, then the backup:
  `backups/pre-cd-switch-20261112.db`, integrity ok, sha256 `9bd47d7f…beb6e0`, counts
  users 3 / categories 11 / expenses 87 / exchange_rates 116 / budgets 1 / settings 0.
- `chown -R 10001:10001 /opt/finance-tracker/data`.
- `DEPLOY_ENABLED=true`; PR #1 merged → `main` `22e0d1c`; Deploy run `43217278831`:
  CI → build & push `sha-22e0d1c` → deploy. `deploy.sh`: logged in to GHCR, started at 12:25:12,
  "backend healthy and https://finance.fomin-vladimir.ru/api/health ok after 15s" at **12:25:37**,
  `.last_successful_tag=sha-22e0d1c`, logged out (no GHCR auth left in `~deploy/.docker/config.json`).
- **Switch downtime: 3 min 40 s** (12:21:57 → 12:25:37), dominated by CI + image build after the
  merge. Total downtime of stage 1: 50 s + 3 min 40 s.
- Verification (§7.5):
  - `curl https://finance.fomin-vladimir.ru/api/health` (resolved via public DNS / from the server) →
    `{"status":"ok","db":"ok","version":"1.1.0","git_sha":"22e0d1c",…}`;
  - `https://…:8443/dashboard.html?x=1` → **301** `https://finance.fomin-vladimir.ru/dashboard.html?x=1`;
    `http://…/login.html` → 308 to HTTPS; `login.html` → **200**; no `alt-svc` (HTTP/3 off);
  - row counts unchanged (3 / 11 / 87 / 116 / 1 / 0), integrity ok;
  - backend runs as `tracker` (uid 10001), networks `finance-tracker_default` and `vav-internal`
    (aliases include `finance-backend`), no published port;
  - `momentum-bot-1` **healthy**; foreign site `fomin-vladimir.ru` / `www` → **200** (baseline 200).
- 12:27 backup `backups/pre-set-password-20261112.db` (integrity ok, same counts), then
  `docker exec finance-tracker-backend-1 python -m app.manage set-password --from-env ADMIN USER1`
  → both users updated; login with the new passwords → 200 for both (checked on the server,
  only status codes printed).
- 12:27 removed `/opt/finance-tracker/bootstrap-src` (code + override only). Bootstrap Caddy
  volumes and the `finance-bootstrap-backend` image were left in place.

### Problems & notes
- **Log masking:** GitHub masks secret values everywhere in logs; `DEPLOY_USER=deploy` and
  `DEPLOY_PORT=22` turn "deploying sha-22e0d1c" into "***ing sha-***e0d1c". Harmless; moving these
  two non-secret values to environment variables would fix it (left as brief §6 requires secrets).
- Two test assertions initially checked that "short" did not leak — but the error text says
  "shorter". Unique canary values are used instead (also in the CI smoke test).
- `data/backups/` does not exist yet; the backend creates it at the first nightly backup (03:00 UTC)
  or before the next migration.
- Clients whose resolvers cached the old A record (TTL 86400) still reach the stopped old server
  (nginx 502) until the cache expires — seen from the dev machine's ISP resolver and one
  Cloudflare PoP at 12:26.

### Open items
- [ ] Owner: take the new passwords from `/root/finance-tracker-new-passwords.txt`, give USER1 theirs,
      delete the file.
- [ ] Owner: from 2026-11-26 remove the old stack and backups on ru-tracker (`MANUAL_STEPS.ru.md`).
- [ ] Optional: purge public DNS caches; remove the bootstrap Caddy volumes/image later.

---

## 2026-11-12 — Stage 2a: API for the bot, subscriptions, time zones

**Goal** (brief `docs/briefs/2026-11-12-stage2a-bot-api.md`): give the Momentum bot everything
it needs from the tracker — token auth that works only inside the server, idempotent writes,
expense source, correct local days, subscriptions — plus the bot contract (`docs/API_BOT.md`,
`docs/openapi.json`), and close the stage 1 tails. No AI in the tracker.

### Code (branch `feat/stage2a-bot-api`)
- **Auth** (`deps.py`, `auth.py`, `config.py`, `security.py`): `Authorization: Bearer` → sha256 →
  `compare_digest` with `BOT_API_TOKEN_SHA256`; only from `BOT_API_ALLOWED_NETWORKS` and without
  `X-Forwarded-For`/`Forwarded`/`X-Real-IP`; acts as `BOT_API_USERNAME` (must be admin); failures
  → 401 + login rate limiter, logged without the token. App-wide `token_guard` with
  `TOKEN_ALLOWED_ROUTES` (403 elsewhere). New dependencies `get_actor`, `get_reader`,
  `require_writer`; `get_current_user` stays cookie-only. Startup guard validates the bot settings.
  `web/Caddyfile`: 403 for any `Authorization` header (first handler in a `route`). Backend CMD
  `--no-proxy-headers`.
- **Idempotency** (`expense_service.py`): `Idempotency-Key` UUID, `client_request_id` (unique
  index) + `client_request_hash`; replay 200, other content 409; race resolved by the unique
  index; pay uses a conditional update.
- **Source**: `expenses.source` in API, CSV, 🤖/🔁 icons.
- **Time zones** (`timeutil.py`, `filters.py`, `stats.py`): bare dates = local days, local month,
  `occurred_at` normalised to UTC, token requires an offset, invalid dates → 422.
- **Subscriptions** (`models.py`, `subscriptions.py`, `routers/subscriptions.py`, `scheduler.py`,
  `main.py`): two tables (migration `0002`), `advance()` with `anchor_day`, job 06:00
  Europe/Budapest + on start + after create/update, API (list/create/update/deactivate/upcoming/
  summary/pay/skip).
- **Frontend**: `subscriptions.html` + `js/subscriptions.js`, dashboard block «Ближайшие списания
  (7 дней)» + monthly total, source icons, `Idempotency-Key` on the add/pay forms; nav tab
  «Подписки». **Bug fix** in `budapestInputToIso` (DST, see problems).
- **Tooling**: `scripts/export_openapi.py` → `docs/openapi.json` (+ `tests/test_openapi.py`),
  `scripts/e2e_bot_api.sh` (+ client and echo upstream) in CI, `deploy/bot-token.sh` (issue/rotate
  the token without printing it; copied to the server by Deploy) + tests, ruff for `scripts/`,
  shellcheck for `scripts/*.sh`. Version 1.2.0.
- Tests 51 → 182: permission matrix (every route × admin/user/token/anonymous + completeness and
  allowlist equality), network/proxy-header rules, rate limit, token never logged; idempotency
  incl. parallel requests; local-day bounds incl. DST days, "yesterday" on 25 Oct, "this month"
  at 00:45 on the 1st, Node test of the browser conversion in three device time zones;
  subscription dates (31st, 29 Feb, yearly, weekly ×2, interval 3), catch-up, reruns without
  duplicates, rate failure retry, API; migration 0001→0002 with data, downgrade, model/migration
  drift; bot settings validation; bot-token script. Coverage 68 % → 89 %.
- Local results: `ruff check` ✓, `ruff format --check` ✓, `mypy` ✓ (46 files), `pytest` 182 passed,
  `actionlint` ✓, `shellcheck deploy/*.sh scripts/*.sh` ✓, `scripts/e2e_bot_api.sh` (local
  Docker) all 33 checks PASS.

### Caddy experiment (ADR-0004)
`scripts/e2e_bot_api.sh` with the real web image (Caddy v2.11.9) in front of an echo upstream:
Caddy sets `X-Forwarded-For` to the client address on every request and **replaces** a
client-supplied value (fake address, empty, list); every `Authorization` variant (Bearer/bearer/
Basic/bare/empty, any header-name case) → 403, static pages included. Real backend: token from the
internal network 200; + `X-Forwarded-For` 401; directly from the proxy's network 401; through
Caddy 403; `POST /api/rates/refresh` 403; idempotency replay/409/6 parallel requests → one row;
the token is absent from the backend log.

### Server and GitHub actions (dubai, times UTC)
- ≈13:15 read-only recon: `vav-internal` = `172.20.0.0/16` (only `finance-tracker-backend-1`
  attached; the bot joins in 2b); tracker `.env` key names (no `BOT_*` yet), `/opt/momentum/.env`
  has no `FINANCE_*` keys yet, both 600 `deploy:deploy`; all 87 `expenses.occurred_at` end in
  `+00:00` (so UTC normalisation needs no data migration); SQLite 3.46.1 in the backend image.
- 13:40 **migration rehearsal on a copy of production** (brief §8.1): `git archive HEAD backend`
  streamed to `/tmp/ft-stage2a-src`, image `finance-tracker-backend:stage2a-migtest` built on the
  server; `docker exec finance-tracker-backend-1 sqlite3 /data/expenses.db ".backup …"` →
  `docker cp` → `/tmp/ft-migtest/expenses.db` (13:41:17); container of the new image with
  `--network none`: counts users 3 / categories 11 / expenses 87 / exchange_rates 116 /
  budgets 1 / settings 0, `alembic upgrade head` 0001 → 0002, `integrity_check ok`,
  `foreign_key_check` empty, counts **identical**, `source` = `web` × 87, both new tables
  present. Copy, source dir and test image deleted; production still at 0001 with 87 rows.
  Production data never left the server.
- 13:45 GitHub, environment `production`: variables `DEPLOY_USER=deploy`, `DEPLOY_PORT=22` set,
  secrets `DEPLOY_USER`, `DEPLOY_PORT` deleted (remaining secrets: `DEPLOY_HOST`,
  `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`); `deploy.yml` reads `vars.*`.
- 13:46 removed the stage 1 bootstrap leftovers after proving they were unused: volumes
  `finance-bootstrap_caddy_data`, `finance-bootstrap_caddy_config` (label
  `com.docker.compose.project=finance-bootstrap`, `docker ps -a --filter volume=…` empty) and image
  `finance-bootstrap-backend:latest` (`sha256:fb66599a…`, no container by ancestor or image id).
  Remaining volumes: `finance-tracker_caddy_{data,config}` (in use by `finance-tracker-web-1`).
  `caddy:2-alpine` (base image, unused) left in place — not part of the brief's list.

### Decisions
- The token allowlist is enforced by one app-wide dependency on the matched route, so new
  routes are closed by default; the matrix test fails for unclassified routes.
- A rejected token on a route outside the allowlist gets 403 without validating the token (no
  oracle, and the brief's "everything else with a token → 403").
- `client_request_hash` column next to `client_request_id`: a key reused with different content
  must be detectable even after the expense was edited.
- Subscriptions are charged right after create/update when due (otherwise a subscription due
  today would wait until 06:00 tomorrow); re-activation skips dates missed while inactive.
- Paying a charge from the site records `source = subscription`; from the token `telegram`.
- Variable subscriptions require `amount = null` (no "estimate") to keep the contract unambiguous.
- `deploy/bot-token.sh` instead of an ad-hoc one-liner: tested, reused for rotation.

### Problems
- The Node test of the frontend found a real bug: `budapestInputToIso` took the UTC offset at the
  guessed instant, so 25 Oct 01:30 (still CEST) became 02:30. Fixed with a second offset pass.
- mypy saw `tests/conftest.py` under two module names once tests imported from it →
  `tests/__init__.py`.
- e2e: the web container needed the network alias `web`.

### Release and bot link (times UTC)
- PR #3 checks green (lint · typecheck · tests, actionlint · shellcheck, docker build + smoke +
  **e2e** — "all e2e checks passed" in the runner too). Merged → `main` `f047c1a` at 13:52.
- Deploy run `43231495819`: CI ✓, build & push ✓, deploy ✓. `deploy.sh`: "deploying sha-f047c1a
  (last successful: sha-4de83c1)" — the log is readable now (`DEPLOY_USER: deploy`,
  `DEPLOY_PORT: 22` shown in clear, no `***` in place of "deploy"/"22").
- 13:55:44 the backend made `data/backups/pre-migrate-20261112T135544Z.db` and applied 0002.
  After deploy: health `1.2.0`/`f047c1a`, `alembic_version` 0002, `integrity_check ok`, counts
  users 3 / categories 11 / expenses 87 (all `source=web`) / exchange_rates 116 / budgets 1 /
  settings 0 / subscriptions 0; `deploy/bot-token.sh` present on the server.
- 13:56:47 **token issued** with `/opt/finance-tracker/bot-token.sh` (one SSH command, nothing
  printed but key names): `/opt/momentum/.env` + `FINANCE_API_URL`, `FINANCE_API_TOKEN`;
  `/opt/finance-tracker/.env` + `BOT_API_TOKEN_SHA256`, `BOT_API_USERNAME`,
  `BOT_API_ALLOWED_NETWORKS=172.20.0.0/16`. Before/after: the original 43 lines of the bot's
  `.env` and 10 lines of the tracker's are byte-identical (sha256 of the prefix), only those keys
  appended; both files still `600 deploy:deploy`. The bot stack was not touched otherwise.
- 13:57:02 `IMAGE_TAG=$(cat .last_successful_tag) docker compose -p finance-tracker -f
  docker-compose.prod.yml up -d --force-recreate --no-deps backend` → healthy at 13:57:10
  (startup guard accepted the bot settings; startup subscriptions catch-up: 0 charges).
- 13:57:23 **checks §8.3** from `docker run --rm --network vav-internal curlimages/curl:8.11.1`
  (peer `172.20.0.3`, token from `/opt/momentum/.env` via `--env-file`, only status codes
  printed): with token `GET /api/categories` **200**, `GET /api/stats/budget` **200**,
  `GET /api/subscriptions/upcoming` **200**; without token **401**; with token
  `POST /api/rates/refresh` **403**. `momentum-bot-1` **healthy**, not restarted (up since
  2026-11-10).
- Same token from the workstation through the public domain: `GET /api/categories` and
  `/api/stats/budget` → **403** (Caddy); without token → 401; `/subscriptions.html`,
  `/dashboard.html` → 200 with «Подписки» and «Ближайшие списания (7 дней)». The workstation's
  resolver (and Cloudflare's 1.1.1.1 via DoH) still return the old A record `157.22.253.65`
  (stopped old server, nginx 502), Google DNS returns `81.85.92.119` — the requests were pinned
  with `curl --resolve …:443:81.85.92.119`. Cache expiry is the owner's note A1 from stage 1.
- The `MANUAL_STEPS.ru.md` verification one-liner was run as written → `200`. The image
  `curlimages/curl:8.11.1` stays on the server for that check (small, used by no stack).

### Open items
- [ ] Stage 2b: the bot joins `vav-internal` and reads `FINANCE_API_URL` / `FINANCE_API_TOKEN`
      (contract: `docs/API_BOT.md`, `docs/openapi.json`).
- [ ] Optional (owner): purge the Cloudflare DNS cache for `finance.fomin-vladimir.ru`
      (`MANUAL_STEPS.ru.md` §3 of stage 1) — 1.1.1.1 still serves the old address.
