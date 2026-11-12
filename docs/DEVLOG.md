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
- Tests: 18 → 46 (secrets guard, migration backup, health version, deploy script). Coverage ~67 %.
- Local results: `ruff check` ✓, `ruff format --check` ✓, `mypy` ✓ (31 files), `pytest` 46 passed,
  `actionlint` ✓, `shellcheck deploy/*.sh` ✓. CI on PR #1: all jobs green.

### Server preparation for CD (dubai)
- 11:31 `.env` audit (lengths only): `SECRET_KEY` 64 ✓, `ADMIN_PASSWORD` 10 ✗, `USER1_PASSWORD` 11 ✗,
  `USER2_PASSWORD` 16 ✓.
- 11:32:56 backups `backups/env-before-rotation-20261112T113256Z` (600) and
  `backups/pre-password-rotation-20261112T113256Z.db`; new `ADMIN_PASSWORD` and `USER1_PASSWORD`
  (24 chars, `secrets.token_urlsafe`) generated **on the server** and written to `.env`;
  credentials in `/root/finance-tracker-new-passwords.txt` (600, root).
- 11:33 updating the two users' password hashes in the database was **denied by the session's
  permission policy** → the DB still has the old hashes: the old passwords keep working, the new
  ones are not active yet. Owner step with the exact command: `MANUAL_STEPS.ru.md`.
- 11:34 `docker network create vav-internal`; `chown deploy:deploy /opt/finance-tracker
  /opt/finance-tracker/.env /opt/finance-tracker/backups`.
- 11:35 installing the dedicated CD public key into `/home/deploy/.ssh/authorized_keys` was
  **denied by the session's permission policy**; no key pair was generated, nothing installed.
  Without it the GitHub `production` secrets cannot be configured and the switch to CD (§7) is
  blocked → owner decision in `MANUAL_STEPS.ru.md`.

### Open items
- [ ] Owner: allow (or perform) the CD key installation, then: secrets/variables, switch from the
      bootstrap stack to CD (§7), remove `bootstrap-src` + override.
- [ ] Owner: activate the new passwords in the DB.
- [ ] Owner: in 14 days remove the old stack and backups on ru-tracker.
