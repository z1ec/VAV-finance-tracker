# Security

Security model, audit results and accepted risks of the finance tracker. First full audit:
stage 3b, 2026-11-13/14 (brief `docs/briefs/2026-11-12-stage3b-security-audit.md`, release 1.3.0).
Server-side recommendations for the owner live in `docs/MANUAL_STEPS.ru.md` (stage 3b section).

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting
(repository → *Security* → *Report a vulnerability*; being enabled by the owner, see G-1) or
contact the owner through the GitHub profile `z1ec`. Include steps to reproduce; you will get an answer within a week. Findings are
tracked with the `security` label once they are fixed or safe to disclose.

## Threat model

### Assets
| Asset | Where | Why it matters |
|---|---|---|
| Household expenses (amounts, comments, categories, budgets, subscriptions) | SQLite `/opt/finance-tracker/data/expenses.db` on `dubai`, nightly / pre-migrate / pre-deploy backups next to it | Private financial data; integrity matters (budgets, bot answers) |
| User accounts | `users` table (bcrypt hashes) | Site access; `admin` can change everything |
| `SECRET_KEY` | server `.env` (600) | Signs session cookies: whoever has it can log in as anyone |
| Bot service token | only in the bot's `.env`; the tracker keeps its SHA-256 | Write access to expenses/subscriptions from `vav-internal` |
| Deploy SSH key, `GITHUB_TOKEN` | GitHub environment `production` | Shell as `deploy` on `dubai` (member of `docker` ⇒ root-equivalent); push to GHCR |
| TLS certificates / ACME account | Docker volume `finance-tracker_caddy_data` | Impersonation of `finance.fomin-vladimir.ru` |

### Entry points
| Entry point | Who can reach it | Controls |
|---|---|---|
| `https://finance.fomin-vladimir.ru` (443; 80 → redirect; 8443 → redirect) | Internet | Caddy: TLS, security headers, 403 on any `Authorization` header, 64 KB body limit, only `/api/*` is proxied |
| `/api/auth/login` | Internet | bcrypt, 5 failures / 15 min / IP (in memory), passwords ≥ 12 chars enforced at start-up |
| Cookie-authenticated API | Signed-in users | `HttpOnly`, `Secure`, `SameSite=Lax` cookie; roles in the API; Origin/Sec-Fetch-Site check on writes |
| `http://finance-backend:8000` on `vav-internal` | Containers attached to `vav-internal` (only `momentum-bot-1`) | Token: SHA-256 + `compare_digest`, peer in `BOT_API_ALLOWED_NETWORKS`, no proxy headers, endpoint allowlist, shared rate limit |
| CI/CD | Pushes to `main` (only the owner has write access; branch protection: G-1) | Pinned actions, minimal permissions, environment `production`, token via stdin |
| SSH to `dubai` | Internet (22) | Keys only for `root` (owner's two keys) and `deploy` (two CI keys); see S-1 |

### Trust boundaries
1. **Internet → Caddy → backend.** Everything from the internet is untrusted. Caddy always
   overwrites `X-Forwarded-For`, so the backend can tell proxied requests from internal ones.
2. **Bot → backend (`vav-internal`).** The bot is trusted to write expenses, but its inputs are
   partly untrusted: comments come from receipt OCR/LLM output. The tracker therefore treats
   every text field as untrusted (escaping on output, CSV-safe export, length limits).
3. **Tracker ↔ bot on one Docker host.** Docker drops traffic between bridges
   (`-A DOCKER ! -i br-<vav-internal> -o br-<vav-internal> -j DROP`), so only containers attached
   to `vav-internal` reach the backend. Both stacks share one Docker daemon: anyone who controls
   either container's *host-side* deploy (the `deploy` user, member of `docker`) controls both.
   A compromised *container* stays confined: no Docker socket inside, non-root (tracker backend
   uid 10001, no capabilities, read-only root), no privileged mode.
4. **CI → server.** GitHub Actions holds an SSH key for `deploy`; whoever can merge to `main`
   can run code on the server. Mitigated by owner-only write access, pinned actions and
   `production` environment secrets that forks never receive (`pull_request_target` is not
   used); branch protection and a `main`-only environment policy are owner steps (G-1).

### Attackers considered
- **Internet attacker** (no account): brute force, XSS/CSRF via links, DoS, scanning for exposed
  docs/ports. Primary focus.
- **Malicious content through the bot**: a receipt/photo with injected text ends up in an
  expense comment (stored XSS, CSV formula injection).
- **Curious household user** (`user` role): must not change data or settings.
- **Compromised neighbour on the shared server** (the bot): limited to the token allowlist.
- **Supply chain**: vulnerable or hijacked dependencies, actions, base images.

Out of scope: an attacker with root on `dubai` or the owner's GitHub account.

### Data sent to external services
The tracker sends **no user data** anywhere. Its only outbound requests are exchange rates:
`GET https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@<date>/…/huf.json` and the fallback
`GET https://open.er-api.com/v6/latest/HUF` (10 s timeout, failures logged). Let's Encrypt (ACME)
sees only the domain name.

## Findings (stage 3b)

Severity: Critical / High / Medium / Low. Evidence `58c0cea:<file>:<line>` points at the code
before the fix (release 1.2.0). Status: **fixed** (in 1.3.0 with tests), **owner** (server
change, command in `MANUAL_STEPS.ru.md`), **accepted** (see below), **issue** (tracked).

### Application and repository
| ID | Sev. | Finding | Evidence | Status |
|---|---|---|---|---|
| T-1 | High | Known vulnerabilities in runtime dependencies: `starlette 0.41.3` (7 advisories incl. CVE-2025-54121, CVE-2025-62727, CVE-2026-48710, CVE-2026-54282/54283) and `python-multipart 0.0.20` (6 advisories, DoS/path traversal). Practical exposure was lower than CVSS (no forms, no `FileResponse`), but Host/path-reconstruction issues affect every request. | `pip-audit -r backend/requirements.txt`: "Found 26 known vulnerabilities in 2 packages" | **fixed**: FastAPI 0.141.1 / Starlette 1.10.0; `python-multipart` removed (unused); `pip-audit` → "No known vulnerabilities found" |
| T-2 | Medium | Stored XSS: category `icon` and `color` were interpolated into `innerHTML` unescaped (8 places), `escapeHtml` did not escape quotes (unsafe in `style="…"`), `rates.date` (free text from the admin) was rendered raw. | `58c0cea:frontend/js/common.js:327`, `:224`; `58c0cea:frontend/js/settings.js:90`; `58c0cea:backend/app/schemas.py:36,140` | **fixed**: server rejects markup in `icon` (422), `date` must be `YYYY-MM-DD`; `escapeHtml` escapes `& < > " '`; all sinks escaped. Tests: `test_icon_*`, `test_frontend_escape_html_*`, `test_frontend_never_interpolates_icon_or_color_unescaped`; checked in headless Chrome with a malicious legacy row |
| T-3 | Medium | CSV/formula injection in `/api/expenses/export`: comments (now also from receipt OCR) and category names starting with `= + - @ \t \r` were exported as formulas. | `58c0cea:backend/app/routers/expenses.py:73-102` | **fixed**: such cells get a `'` prefix (`csv_safe`). Tests: `test_csv_safe_*`, `test_export_neutralises_formulas_in_comments_and_names` |
| T-4 | Medium | No security headers: no CSP, clickjacking possible (no `frame-ancestors`/`X-Frame-Options`), no `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS; `Server: Caddy`. Inline scripts prevented a strict CSP. | `58c0cea:web/Caddyfile:18-36`; `curl -sI https://finance.fomin-vladimir.ru/login.html` (only `server`, `etag`, …) | **fixed**: CSP `script-src 'self'` (inline scripts moved to `js/theme-init.js`, `js/login.js`), `frame-ancestors 'none'`, HSTS 180 d without `includeSubDomains`. Tests: `test_caddy_sets_security_headers`, e2e "security headers on /login.html" |
| T-5 | Medium | No request body limit (Caddy or app): unauthenticated `POST /api/auth/login` with a huge JSON body is buffered in memory. | `58c0cea:web/Caddyfile:26-28`, no middleware in `58c0cea:backend/app/main.py` | **fixed**: Caddy `request_body max_size 64KB` (413), `BodySizeLimitMiddleware` for direct `vav-internal` calls (incl. chunked). Tests: `test_oversized_*`, e2e "70 KB body → 413" |
| T-6 | Medium | Failed deploy after a migration could not roll back: the previous image refuses to start on a newer Alembic revision, so the automatic rollback would end unhealthy (outage). | `58c0cea:deploy/deploy.sh:191-199` | **fixed**: pre-deploy backup inside the running backend (sqlite backup API + `quick_check`), restore when the revision changed, migrated DB kept as `expenses.db.failed-<tag>`; rotation `PRE_DEPLOY_KEEP=10`. Tests: `test_failed_migration_restores_the_backup_before_rollback` and 7 more in `tests/test_deploy_script.py` |
| T-7 | Medium | `docker pull` without timeout: a stalled registry download hangs the deploy until the job is killed (bot incident, stage 2b). | `58c0cea:deploy/deploy.sh:97`, `58c0cea:.github/workflows/deploy.yml:103` | **fixed**: `timeout 480 s` × 3 attempts, job limit 30 min (ported from bot PR #8). Tests: `test_stalled_pull_is_retried`, `test_pull_that_never_finishes_fails_fast` (need coreutils `timeout`: run in CI) |
| T-8 | Low | Input bounds: `amount`, `rate_to_huf`, budgets, `sort_order` had no upper bound; `1e400` (∞) or `1e300` caused 500 instead of 422. | `58c0cea:backend/app/schemas.py:83,304` | **fixed**: amounts ≤ 10⁹, rate ≤ 10⁶, budgets ≤ 10¹¹. Tests: `test_expense_bounds`, `test_infinite_amount_is_rejected_not_500`, `test_budget_and_rate_bounds` |
| T-9 | Low | CSRF relied on `SameSite=Lax` only; sibling subdomains of `fomin-vladimir.ru` are *same-site* and could send cookie-authenticated POSTs. All writes are JSON (a cross-site form cannot produce a valid body), so not exploitable today. | `58c0cea:backend/app/routers/auth.py:41-49` | **fixed** (defence in depth): writes with `Sec-Fetch-Site: cross-site/same-site` or a foreign `Origin` → 403. Tests: `test_cross_site_writes_are_refused`, `test_same_origin_writes_pass` |
| T-10 | Low | Login page put the server's error text into `innerHTML`. | `58c0cea:frontend/login.html:58` | **fixed**: `textContent` (`js/login.js`) |
| T-11 | Low | Stateless sessions: logout only deletes the cookie (and without `Secure`/`HttpOnly` flags); a copied cookie stays valid for 30 days; a password change does not log out other sessions. | `58c0cea:backend/app/routers/auth.py:55` | **fixed** flags (`test_cookie_is_secure_on_a_real_domain`); revocation → **issue #5**; until then rotate `SECRET_KEY` |
| T-12 | Low | Login limiter kept an empty list per IP forever (unbounded memory with many source IPs). | `58c0cea:backend/app/auth.py:67-75` | **fixed**: empty entries removed, cap 10 000 IPs. Tests: `test_limiter_*` |
| T-13 | Low | FastAPI `/docs`, `/redoc`, `/openapi.json` enabled in production (not reachable from the internet: Caddy proxies only `/api/*` — `/docs` returns the SPA page, `/api/docs` 404; reachable from `vav-internal`). | `58c0cea:backend/app/main.py:51` | **fixed**: disabled unless `DOMAIN=localhost` (contract stays in `docs/openapi.json`) |
| T-14 | Low | The web image contained private keys of Caddy's local CA, created by `caddy validate` at build time (trivy secret scan: `/data/caddy/pki/authorities/local/root.key`, `intermediate.key`). The CA is not used in production (Let's Encrypt) and trusted nowhere. | `58c0cea:web/Dockerfile:11-12` | **fixed**: removed in the same layer; trivy secret scan in CI. The copy inside the existing `caddy_data` volume is unused (optional cleanup in MANUAL_STEPS) |
| T-15 | Low | Image vulnerabilities with fixes: `zlib1g` CVE-2026-76844/85091 (HIGH) in the backend's Debian base; `golang.org/x/net` CVE-2026-78661 (HIGH) in the Caddy binary. | trivy 0.76.1 `--severity HIGH,CRITICAL --ignore-unfixed` | **fixed** zlib (`apt-get upgrade` in the runtime stage, `--pull` builds); x/net **accepted until 2026-12-15** (`.trivyignore`, issue #7) |
| T-16 | Low | Containers without hardening: writable root FS, default capabilities, no `no-new-privileges`; Caddy runs as root. | `58c0cea:deploy/docker-compose.prod.yml` | **fixed**: backend `read_only`, `cap_drop: ALL`, `no-new-privileges`; web the same + `NET_BIND_SERVICE` only. Non-root Caddy → **issue #6** |
| T-17 | Low | Transitive Python dependencies were not locked (only direct pins). | `58c0cea:backend/requirements.txt` | **fixed**: `requirements.in` → full lock `requirements.txt` (`uv pip compile`, releases ≥ 2 weeks old) |
| T-18 | Low | CI supply chain: third-party actions pinned by mutable tags; `actions/checkout` persisted the token; `${{ github.actor }}` interpolated into a shell command. | `58c0cea:.github/workflows/ci.yml:27,30`, `58c0cea:.github/workflows/deploy.yml:151` | **fixed**: all actions pinned by SHA with version comments, `persist-credentials: false`, actor via `env` + regex check; Dependabot for pip, actions and Docker |
| T-19 | Low | Dev tooling: `pytest 8.3.4` CVE-2025-71176 (shared `/tmp/pytest-of-*`); `pytest-asyncio` unused. | `pip-audit -r backend/requirements-dev.txt` | **fixed**: pytest 9.1.1, `pytest-asyncio` removed |
| T-20 | Low | Database and backups were readable by every local account on the host (`data/` 755, files 644). The only other accounts are `deploy` (already root-equivalent via `docker`) and a locked `tmpuser`. | `ssh dubai ls -la /opt/finance-tracker/data` → `drwxr-xr-x … expenses.db -rw-r--r--` | **fixed**: on start the app sets `umask 077` and makes its data/backup dirs 700 and files 600; backups are created 600. Tests: `test_restrict_data_permissions`, `test_backups_are_private` |
| T-21 | Low | `.gitignore` ignored `.env` but not `.env.*` (e.g. `.env.bak`, `.env.prod`). | `58c0cea:.gitignore:1` | **fixed**: `.env.*` ignored, `!.env.example` |
| T-23 | Low | The login/token limiter keys failures by the first `X-Forwarded-For` value even for requests that reach the backend directly over `vav-internal`, so a container there can charge failures to another address (e.g. lock the bot out for 15 min). Behind Caddy the header is always overwritten, so internet clients cannot do this. | `58c0cea:backend/app/deps.py:67-71`; production check 2026-11-13 23:47 UTC: a request with `X-Forwarded-For: <bot address>` added one failure to the bot's key | **issue #13** (only the bot itself is on `vav-internal` today) |
| T-22 | Info | Secrets in git history: none. `gitleaks git --log-opts=--all` found 2 matches, both example `Idempotency-Key` UUIDs (docs, tests); `.env.example` is empty in every revision. | gitleaks 8.30.1 | allowlisted narrowly in `.gitleaks.toml`; gitleaks runs in CI on every push |

Checked and fine (no finding): `SECRET_KEY` ≥ 32 chars and non-default enforced at start-up;
session lifetime 30 days, `HttpOnly`, `Secure` outside localhost, `SameSite=Lax`; bcrypt;
token compared with `hmac.compare_digest` on its SHA-256; token refused with any proxy header or
from outside `BOT_API_ALLOWED_NETWORKS` (tests + e2e, incl. forged `X-Forwarded-For` through
Caddy → 403); allowlist matched on the request path (independent of FastAPI routing internals,
which changed in 0.14x — the old check would have failed closed); rate limiter keyed by the real
client IP (Docker publishes ports via DNAT, Caddy overwrites `X-Forwarded-For`); all SQL through
SQLAlchemy parameters; `/api/health` exposes only status, version, git sha and the rates time;
unhandled errors return a generic message; exchange-rate fetch has 10 s timeouts and logs
failures (no silent `except: pass`); logs contain no tokens (test) and no personal data above
DEBUG; images: backend non-root uid 10001, multi-stage (no compilers at runtime), HEALTHCHECK;
no secrets in `docker history`; `.dockerignore` excludes `.env*`; CI: workflow and job
`permissions` minimal, no `pull_request_target`, environment `production`, GHCR token only via
stdin, Dependabot/forks get no secrets.

### Servers (read-only review, 2026-11-13)
| ID | Sev. | Finding | Evidence | Status |
|---|---|---|---|---|
| S-1 | Medium | `dubai`: SSH password authentication is **on**: `50-cloud-init.conf` (`PasswordAuthentication yes`) is read before `99-momentum.conf` (`no`), and sshd keeps the first value. 34 139 failed password/invalid-user attempts in 7 days, 0 accepted. Not exploitable today (`root` = `prohibit-password`, `deploy` and `tmpuser` passwords locked), but any future account with a password is exposed. | `sshd -T` → `passwordauthentication yes`; `journalctl -u ssh` | **owner** (MANUAL_STEPS 3b.1) |
| S-2 | Low | `dubai`: `X11Forwarding yes`; no fail2ban (unneeded once S-1 is fixed). | `sshd -T` | **owner** (3b.1) |
| S-3 | Low | `dubai`: leftover account `tmpuser` (uid 1000, shell `/bin/sh`, password locked, empty `authorized_keys`). | `/etc/passwd`, `passwd -S tmpuser` → `L` | **owner** (3b.2) |
| S-4 | Low | `dubai`: CI deploy keys of both projects are unrestricted in `deploy`'s `authorized_keys`; `deploy` is in `docker` (root-equivalent, needed for deploys). `from=` with GitHub runner ranges is impractical (thousands of changing ranges). | `ssh-keygen -lf /home/deploy/.ssh/authorized_keys` | **owner** (3b.3: `restrict` option); CI keys are **not** in root's `authorized_keys` ✔ |
| S-5 | Low | `dubai`: 150 packages pending (unattended-upgrades installs security updates only); no reboot pending. | `apt list --upgradable` | **owner** (3b.4) |
| S-6 | Low | `dubai`: stage 1 leftovers in `/opt/finance-tracker/backups/`: old `.env` copy (`env-before-rotation-…`, root 600) and DB copies (root 644). | `ls -la /opt/finance-tracker/backups` | **owner** (3b.5) |
| S-7 | Low | `ru-tracker`: stopped old tracker stack and DB backups (planned removal after 2026-11-26); `X11Forwarding yes`; ufw still allows `8443/tcp` although nothing listens; kernel 5.15.0-73. | `docker ps -a`, `sshd -T`, `ufw status`, `ss -tlnup` | **owner** (stage 1 §4, 3b.6) — recommendations avoid nginx, the neighbour site and the VPN (`9443/udp`) |

Fine on `dubai`: listening only 22, 80, 443, 8443 (+ local DNS); backend port 8000 not
published; `vav-internal` = `momentum-bot-1` + `finance-tracker-backend-1` only; certificate
Let's Encrypt, valid until 2027-02-10; root login by key only with the owner's two keys;
Docker 29.9.3; unattended-upgrades active; server `.env` files 600.
Fine on `ru-tracker`: listening 22, 80, 443 (nginx of the neighbour site); password login off;
root by key only (owner's keys); unattended-upgrades active; neighbour site answers as before.

### GitHub settings
| ID | Sev. | Finding | Evidence | Status |
|---|---|---|---|---|
| G-1 | Low | `main` has no branch protection (the "PR + green CI" rule is a convention only); environment `production` has no deployment-branch policy, so a workflow run on any branch could use the deploy key; private vulnerability reporting is off. Only the owner has write access, so this matters if a collaborator or a token is added. | `gh api …/branches/main/protection` → 404 "Branch not protected"; `…/environments/production` → `deployment_branch_policy: null`; `…/private-vulnerability-reporting` → `enabled: false` | **owner** (MANUAL_STEPS 3b.7, `gh` commands) |

## Accepted risks
| Risk | Why accepted | Revisit |
|---|---|---|
| In-memory login limiter: reset on restart, per process, no global limit | One replica (SQLite); an attacker cannot trigger restarts; bcrypt + 12-char minimum make distributed guessing impractical | when running more than one replica |
| A wrong token from the bot's IP blocks the right token for 15 min (shared limiter) | Intentional: a leaked-token probe from inside is noisy; documented for the bot | — |
| Stateless sessions (no server-side revocation) | Household app, HTTPS-only `HttpOnly` cookie, CSP; `SECRET_KEY` rotation logs everyone out | issue #5 |
| Caddy as root in its container | Only `NET_BIND_SERVICE`, read-only FS, `no-new-privileges` | issue #6 |
| CVE-2026-78661 in the Caddy binary | No Caddy release with the fix; vulnerable package used only by handlers we do not enable | `.trivyignore` expires 2026-12-15, issue #7 |
| `style-src 'unsafe-inline'` in the CSP | `style=""` attributes in the markup and Chart.js; scripts stay `'self'` only | if the markup is refactored |
| Restore after a failed migration loses writes made between the backup and the failure | The window is the health-check time (≤ 2 min); the migrated DB is kept for manual recovery | — |
| `deploy` is root-equivalent (docker group) and shared by both projects | Needed for Docker deploys; key-only, CI keys separate per project | with rootless Docker / separate hosts |

## Continuous checks
- CI job **`security`** (`.github/workflows/ci.yml`; on every push/PR, in Deploy, weekly on
  Monday): gitleaks over the full history (`.gitleaks.toml`), `pip-audit` on the runtime lock
  and the dev tools, trivy on both images (fails on HIGH/CRITICAL with a fix and on secrets;
  `.trivyignore` entries expire), image history check. Scanners run from images pinned by digest.
- Dependabot (`.github/dependabot.yml`): pip, GitHub Actions (SHA pins), Docker base images.
- Tests guard the fixes above; `tests/test_bot_auth.py` keeps the token matrix complete.
