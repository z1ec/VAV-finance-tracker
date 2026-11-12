# Architecture

## Components

| Component | Image / path | Role |
|---|---|---|
| `web` | `ghcr.io/z1ec/finance-tracker-web` (caddy:2-alpine) | TLS (Let's Encrypt), HTTP→HTTPS, `:8443` → 443 redirect, static frontend from `/srv`, `/api/*` → `backend:8000`. |
| `backend` | `ghcr.io/z1ec/finance-tracker-backend` (python:3.12-slim, uid 10001) | FastAPI REST API (cookie sessions for the site, service token for the bot), Alembic migrations on start, APScheduler jobs (rates, backups, subscriptions), SQLite in `/data`. |
| SQLite | `/opt/finance-tracker/data/expenses.db` (WAL) | Single source of truth. |
| Frontend | `frontend/` baked into the web image | Static pages + ES modules, Chart.js vendored; Russian UI. |

Startup order inside the backend (`app/main.py` → `lifespan`):
1. log `version` / `git_sha`;
2. `check_secrets` — exit on default/weak secrets if `DOMAIN != localhost`;
3. pending migrations → `pre-migrate-<UTC>.db` backup → `alembic upgrade head`;
4. seed missing users/categories; fetch today's rates if missing;
5. charge due subscriptions (catch-up after downtime, idempotent);
6. start the scheduler.

## Network on the shared server

```mermaid
flowchart LR
    user([Browser]) -->|"443 HTTPS<br/>80 → 443<br/>8443 → 301 to 443"| web
    subgraph dubai["dubai (81.85.92.119) — ufw: 22, 80, 443, 8443"]
        subgraph ft["compose project finance-tracker"]
            web["web (Caddy)"] -->|"backend:8000<br/>(network finance-tracker_default)"| backend["backend (FastAPI)"]
            backend --- data[("./data<br/>expenses.db + backups")]
        end
        subgraph mom["compose project momentum"]
            bot["momentum-bot-1"]
        end
        net{{"external network vav-internal"}}
        backend ---|"alias finance-backend"| net
        bot -.->|"stage 2b: http://finance-backend:8000<br/>Bearer token, 172.20.0.0/16"| net
    end
    bot -->|long polling| tg([Telegram API])
    backend -->|daily rates| rates([currency-api / open.er-api])
```

- Only `web` publishes ports. The backend's port 8000 is reachable only from `web` (default
  network) and from containers on `vav-internal` (the bot, from stage 2).
- The bot has no inbound ports; its stack (`/opt/momentum`) is independent of this one.
- `web` (Caddy) is **not** on `vav-internal`; it refuses every request with an `Authorization`
  header, so the bot token only works container-to-container (ADR-0004).

## Bot → tracker

```mermaid
sequenceDiagram
    autonumber
    participant U as Owner (Telegram)
    participant B as Momentum bot<br/>(AI: text, voice, receipts)
    participant T as backend<br/>finance-backend:8000
    participant DB as SQLite
    U->>B: "Spar 12.50 € вчера" / photo of a receipt
    B->>B: parse → amount, currency, category, time (+01:00), shop
    B->>T: POST /api/expenses<br/>Authorization: Bearer …, Idempotency-Key: uuid
    Note over T: peer ∈ BOT_API_ALLOWED_NETWORKS<br/>no X-Forwarded-For · sha256 match<br/>route on the allowlist
    T->>DB: rate of that date → amount_huf, source=telegram,<br/>client_request_id=uuid (unique)
    T-->>B: 201 expense (a retry with the same key → 200, same row)
    B-->>U: "Записал: Продукты, 4 818 Ft"
    Note over B,T: morning: GET /api/stats/summary?date_from=date_to=yesterday,<br/>GET /api/stats/budget, GET /api/subscriptions/upcoming
```

Contract: [`API_BOT.md`](API_BOT.md) and [`openapi.json`](openapi.json).

| Auth | Who | Where it works | Rights |
|---|---|---|---|
| Session cookie (password login) | people via the site | through Caddy | `admin` writes, `user` reads |
| `Authorization: Bearer` token | the bot | only from `vav-internal`, never through Caddy | allowlist of 16 routes, acts as `BOT_API_USERNAME` (admin) |

## Data model

```mermaid
erDiagram
    users ||--o{ expenses : "created_by"
    categories ||--o{ expenses : "category_id"
    categories ||--o| budgets : "category_id"
    categories ||--o{ subscriptions : "category_id"
    subscriptions ||--o{ subscription_charges : "subscription_id"
    expenses |o--o| subscription_charges : "expense_id"
    users {
        int id PK
        string username UK
        string password_hash
        string role "admin | user"
        string created_at
    }
    categories {
        int id PK
        string name UK
        string color
        string icon
        int is_archived
        int sort_order
        string created_at
    }
    expenses {
        int id PK
        numeric amount "> 0"
        string currency "HUF | EUR | RUB"
        numeric rate_to_huf "frozen at write time"
        int amount_huf
        string rate_source "api | manual"
        int category_id FK
        string comment
        string occurred_at "UTC ISO-8601"
        int created_by FK
        string created_at
        string updated_at
        string deleted_at "soft delete"
        string source "web | telegram | subscription"
        string client_request_id UK "Idempotency-Key (nullable)"
        string client_request_hash "request fingerprint"
    }
    subscriptions {
        int id PK
        string name
        string amount_type "fixed | variable"
        numeric amount "NULL for variable"
        string currency
        int category_id FK
        string period "weekly | monthly | yearly"
        int interval ">= 1"
        int anchor_day "1-31, monthly/yearly"
        string next_payment_date "local YYYY-MM-DD"
        int remind_days_before
        int is_active
        string comment
        string created_at
        string updated_at
    }
    subscription_charges {
        int id PK
        int subscription_id FK "UNIQUE with due_date"
        string due_date "local YYYY-MM-DD"
        string status "pending | paid | skipped"
        numeric amount
        string currency
        int expense_id FK
        string created_at
        string updated_at
    }
    exchange_rates {
        string date PK
        string currency PK
        numeric rate_to_huf
        string source "api | manual"
        string fetched_at
    }
    budgets {
        int category_id PK
        numeric limit_huf
    }
    settings {
        string key PK
        string value
    }
```

`settings` currently holds `total_monthly_budget_huf`. Timestamps are UTC ISO-8601 strings
(normalised to `+00:00` on write); the UI renders them in `Europe/Budapest`. **Bare dates in the
API are local days in `Europe/Budapest`** — every filter, stat, the budget month and subscription
dates (ADR-0006).

## Scheduled jobs (APScheduler, in the backend process)

| Job | When | What |
|---|---|---|
| `backup` | 03:00 UTC | `expenses-YYYY-MM-DD.db`, keep 14 |
| `refresh_rates` | 04:00 UTC | EUR/RUB for today |
| `subscriptions` | **06:00 Europe/Budapest** + once on start | charges for every due date: fixed → expense (`source=subscription`, 12:00 local), variable → `pending` (ADR-0007) |

## Exchange rates
- Base currency HUF. Daily job at 04:00 UTC fetches EUR and RUB from
  `@fawazahmed0/currency-api` (jsdelivr), falling back to `open.er-api.com`; on start the backend
  fetches today's rates if they are missing.
- Rate for a new expense: exact date → nearest previous date → fetch for that date → `422` asking for
  a manual rate. Manual rates win and are never overwritten by the job.
- The rate is frozen into each expense (`rate_to_huf`, `rate_source`, `amount_huf`), so history
  never changes when rates do. Rounding: HUF 0 decimals, EUR/RUB 2 (Decimal, half-up).

## Backups

| What | When | Where (host) | Retention |
|---|---|---|---|
| Nightly copy (`expenses-YYYY-MM-DD.db`) | 03:00 UTC, scheduler | `/opt/finance-tracker/data/backups/` | 14 newest |
| Before migrations (`pre-migrate-<UTC>.db`) | on start, only if migrations are pending | `/opt/finance-tracker/data/backups/` | 10 newest |
| Migration snapshots (`pre-migration-2026-11-12.db`, …) | manual, stage 1 | `/opt/finance-tracker/backups/` | kept |
| Old server snapshot | 2026-11-12, before the move | `ru-tracker:/usr/local/etc/finance/VAV-finance-tracker/backups/` | until the owner removes it |

All copies use the SQLite online backup API, safe while the app is running (WAL included).
Restore procedure: `docs/DEPLOYMENT.md`.

## Security notes
- Secrets only in the server's `.env` (600, owner `deploy`); the backend refuses to start on a real
  domain with default/short secrets.
- Session cookie: HttpOnly, SameSite=Lax, Secure on a real domain; login rate limit per IP.
- Bot token: only its SHA-256 on the tracker side, constant-time compare, internal network and no
  proxy headers, endpoint allowlist, failures share the login rate limit, never logged
  (ADR-0004). uvicorn runs with `--no-proxy-headers`.
- Idempotent writes for the bot (`Idempotency-Key`, unique index; ADR-0005).
- Containers: backend non-root, no published backend port, log rotation (10 MB × 3).
