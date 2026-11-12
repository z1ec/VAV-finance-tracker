# Tracker API for the Momentum bot

Contract between the finance tracker (source of truth for money) and the Momentum Telegram bot
(all AI lives in the bot). Machine-readable schema: [`openapi.json`](openapi.json) — generated
by `scripts/export_openapi.py`, CI fails when it is stale. Decisions: ADR
[0004](adr/0004-bot-auth.md) (auth), [0005](adr/0005-idempotency.md) (idempotency),
[0006](adr/0006-time-zones.md) (time zones), [0007](adr/0007-subscriptions.md) (subscriptions).

Examples were captured from version 1.2.0 (test database); nested `category` objects are
shortened to `{"…": "…"}`, and the budget and `upcoming` examples use illustrative numbers.

## 1. Connection and authentication

| | |
|---|---|
| Base URL | `http://finance-backend:8000` (env `FINANCE_API_URL` in the bot) |
| Network | Docker network `vav-internal` only; the bot container must join it |
| Header | `Authorization: Bearer <FINANCE_API_TOKEN>` on every request |
| Body | JSON, `Content-Type: application/json`, UTF-8 |
| Acts as | the admin user named in the tracker's `BOT_API_USERNAME` |

The token is accepted only when **all** of the following hold — otherwise `401`:
1. the TCP peer address is inside `BOT_API_ALLOWED_NETWORKS` (the `vav-internal` subnet,
   `172.20.0.0/16` on `dubai`);
2. the request has **no** `X-Forwarded-For`, `Forwarded` or `X-Real-IP` header — do not add
   them and do not route the bot through any proxy;
3. the scheme is `Bearer` and the SHA-256 of the token equals `BOT_API_TOKEN_SHA256`.

The public site `https://finance.fomin-vladimir.ru` answers **403 to any request that carries an
`Authorization` header**, so the token never works from the internet. Never send it there.

**Rate limit.** Every rejected token counts as a failed login attempt for the caller's IP. After
**5 failures within 15 minutes** the IP gets `429` for the rest of the window — *even with the
correct token*. On `401` stop and alert the owner; do not retry in a loop.

**Allowlist.** The token may call only the endpoints in section 4. Anything else (even `GET
/api/expenses`, `/api/auth/me`, rates, budgets settings) answers `403`. The token's admin rights
apply only inside this list.

## 2. Writes: `Idempotency-Key`

`POST /api/expenses` and `POST /api/subscriptions/charges/{id}/pay` require the header
`Idempotency-Key: <UUID>` (any UUID version; without it → `422`).

- Generate **one UUID per logical operation** (one receipt, one "pay this charge") and keep it
  for every retry of that operation (timeouts, restarts, Telegram redelivery).
- Same key + same content → `200` with the record created the first time (no duplicate).
  The first successful create answers `201` (expense) / `200` (pay).
- Same key + different content → `409` `{"detail": "Idempotency-Key уже использован для другого запроса"}`.
  Content = amount, currency, category, comment, `occurred_at` (compared as an instant, so
  `+01:00` and the equivalent `Z` are the same), `rate_to_huf`; for pay: charge, amount,
  currency, `occurred_at`, comment.
- Keys never expire. Two parallel requests with one key create exactly one record.

`PATCH`, `DELETE`, `restore`, subscription create/update/delete and `skip` are naturally safe to
repeat and take no key.

## 3. Dates and time zones

- **Timestamps** (`occurred_at`) must be ISO-8601 **with an offset**:
  `2026-11-11T19:42:00+01:00` or `2026-11-11T18:42:00Z`. Without an offset → `422`
  `{"detail": "occurred_at must include a timezone offset"}`. Responses always return UTC
  (`…+00:00`). Up to 24 h in the future is accepted.
- **Bare dates** `YYYY-MM-DD` in query parameters (`date_from`, `date_to`) are **local calendar
  days in `Europe/Budapest`**: `date_from=2026-11-01` starts at 00:00 Budapest time,
  `date_to=2026-11-30` includes the whole day until 23:59:59.999999. DST days (23 h / 25 h) are
  handled. A full datetime with an offset is used as is.
- **Subscription dates** (`next_payment_date`, `due_date`, `today`) are local calendar dates.
- How the bot computes periods (always in `Europe/Budapest`, not in the server's or user's
  time zone):

  | Period | Query |
  |---|---|
  | yesterday | `date_from = date_to = (today_local − 1 day)` |
  | today | `date_from = date_to = today_local` |
  | this month | omit both (default = from the 1st of the current local month until now), or `date_from = YYYY-MM-01`, `date_to = today_local` |
  | budget % this month | `GET /api/stats/budget` (always the current local month) |

- Morning summary line "вчера / месяц / % бюджета":
  `GET /api/stats/summary?date_from=<yesterday>&date_to=<yesterday>` → `total_huf`;
  `GET /api/stats/budget` → `spent_huf` and `pct` (`null` when no overall budget is set).

## 4. Endpoints

Errors always have the shape `{"detail": "<text>"}` (validation errors: `"<field>: <message>"`).

| Status | Meaning |
|---|---|
| 200 / 201 / 204 | OK / created / deleted (no body) |
| 401 | no/invalid token, wrong network, proxy header present (`WWW-Authenticate: Bearer`) |
| 403 | endpoint not on the token allowlist |
| 404 | entity not found (`detail` names it) |
| 409 | `Idempotency-Key` reused with other content; charge already paid/skipped |
| 422 | validation: missing key, no offset, bad field, exchange rate unavailable |
| 429 | too many failed tokens from this IP (section 1) |
| 500 | server error `{"detail": "Внутренняя ошибка сервера"}` — retry later with the same key |

### 4.1 `GET /api/health`
No auth needed. `{"status": "ok", "db": "ok", "version": "1.2.0", "git_sha": "…", "last_rates_update": "…"}`.

### 4.2 `GET /api/categories`
Query: `include_archived` (bool, default `false`). Use only non-archived categories for new
expenses. Map receipts to these names; never create categories (not allowed for the token).
```json
[
  {"id": 1, "name": "Продукты", "color": "#66bb6a", "icon": "🛒", "is_archived": false, "sort_order": 0},
  {"id": 2, "name": "Кафе и рестораны", "color": "#ff8a65", "icon": "🍽️", "is_archived": false, "sort_order": 1}
]
```

### 4.3 `POST /api/expenses` — record an expense
Headers: `Idempotency-Key` (required). Body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `amount` | number > 0 | yes | receipt total in `currency` |
| `currency` | `HUF` \| `EUR` \| `RUB` | yes | |
| `category_id` | int | yes | from 4.2 (`404` if unknown) |
| `occurred_at` | datetime with offset | no | default: now; date and time of the purchase |
| `comment` | string ≤ 500 | no | put the **shop name** here (there is no separate field) |
| `rate_to_huf` | number > 0 | no | manual rate (HUF per 1 unit); only if the API answered 422 about the rate |

```http
POST /api/expenses
Authorization: Bearer …
Idempotency-Key: 6f1c5b8e-3a43-4d1e-9a51-0b8f2c7d4e10

{"amount": 12.5, "currency": "EUR", "category_id": 1,
 "occurred_at": "2026-11-11T19:42:00+01:00", "comment": "Spar — Váci utca"}
```
`201` (a replay with the same key and body → `200`, identical body):
```json
{
  "id": 1, "amount": 12.5, "currency": "EUR", "amount_huf": 4818, "rate_to_huf": 385.42,
  "rate_source": "api",
  "category": {"id": 1, "name": "Продукты", "color": "#66bb6a", "icon": "🛒", "is_archived": false, "sort_order": 0},
  "comment": "Spar — Váci utca", "occurred_at": "2026-11-11T18:42:00+00:00", "created_by": 1,
  "created_at": "2026-11-12T13:41:58.400763+00:00", "updated_at": "2026-11-12T13:41:58.400766+00:00",
  "source": "telegram"
}
```
- `source` is always `telegram` for the token (`web` = site, `subscription` = automatic charge).
- The HUF amount uses the rate of the purchase date (exact → previous stored → fetched); it is
  frozen in the row. If no rate can be found → `422 "Курс EUR на 2026-11-11 недоступен. Укажите курс вручную."`
  — ask the user for the rate and retry **with a new key** (the content changed).

### 4.4 `PATCH /api/expenses/{id}` — correct an expense
Any subset of the 4.3 fields (`occurred_at` with an offset). Changing amount, currency or date
recalculates the rate. `200` with the expense; `404` if not found or deleted.

### 4.5 `DELETE /api/expenses/{id}` and `POST /api/expenses/{id}/restore`
Soft delete → `204`; restore from the trash (30 days on the site) → `200` with the expense.
`404` when there is nothing to delete/restore.

### 4.6 `GET /api/stats/summary`
Query: `date_from`, `date_to` (section 3), `category_id` (repeatable), `currency`.
Default period: start of the current local month → now. The previous period has the same
length and ends right before `date_from`.
```json
{"total_huf": 4818, "count": 1, "avg_per_day": 4818.0, "avg_per_expense": 4818.0,
 "prev_period_total_huf": 0, "change_pct": 100.0}
```
`change_pct` is `null` when both periods are empty.

### 4.7 `GET /api/stats/budget`
Current local month up to now.
```json
{"total_monthly_budget_huf": 450000.0, "spent_huf": 4818, "pct": 1.07,
 "categories": [{"category": {"id": 1, "name": "Продукты", "…": "…"}, "limit_huf": 120000.0, "spent_huf": 4818, "pct": 4.02}]}
```
`total_monthly_budget_huf` and `pct` are `null` if no overall budget is set; `categories` lists
only categories with a limit.

### 4.8 Subscriptions

A subscription object (`SubscriptionOut`):
```json
{
  "id": 1, "name": "Netflix", "amount_type": "fixed", "amount": 4990.0, "currency": "HUF",
  "category": {"id": 2, "name": "Кафе и рестораны", "…": "…"},
  "period": "monthly", "interval": 1, "anchor_day": 30, "next_payment_date": "2026-11-30",
  "remind_days_before": 3, "is_active": true, "comment": null,
  "created_at": "2026-11-12T13:41:58.419213+00:00", "updated_at": "2026-11-12T13:41:58.419215+00:00"
}
```

Rules (ADR-0007):
- `amount_type`: `fixed` → `amount` required, charged **automatically** as an expense
  (`source = subscription`, 12:00 local time on the due date, comment `Подписка: <name>`);
  `variable` → `amount` must be `null`, each due date becomes a **pending charge** and the bot
  asks the user for the amount.
- `period` `weekly | monthly | yearly`, `interval` ≥ 1 (every N periods).
- `anchor_day` (1–31, monthly/yearly only; default = day of `next_payment_date`; must be `null`
  for weekly): in a shorter month the charge falls on the last day, the anchor is kept
  (31 Jan → 28 Feb → 31 Mar; yearly 29 Feb → 28 Feb in non-leap years).
- The job runs daily at **06:00 Europe/Budapest** and once on every backend start; it handles
  every missed date (no duplicates). A subscription whose `next_payment_date` is today or earlier
  is charged right away when it is created or updated.

#### `GET /api/subscriptions?active=true`
`active` optional (`true`/`false`; omit for all). `200` → list of `SubscriptionOut`.

#### `POST /api/subscriptions`
Body: `name` (1–100), `amount_type` (default `fixed`), `amount`, `currency`, `category_id`,
`period` (default `monthly`), `interval` (default 1), `anchor_day`, `next_payment_date`
(local `YYYY-MM-DD`, required), `remind_days_before` (0–60, default 3), `is_active` (default
true), `comment`. `201` → `SubscriptionOut`. `422` on rule violations, `404` unknown category.
```json
{"name": "Электричество", "amount_type": "variable", "currency": "HUF", "category_id": 2,
 "period": "monthly", "next_payment_date": "2026-11-10", "remind_days_before": 5}
```
(Here `next_payment_date` is in the past: the 2026-11-10 charge is created as `pending` at once
and the response already shows `"next_payment_date": "2026-12-10"`.)

#### `PATCH /api/subscriptions/{id}`
Any subset of the create fields. Switching to `variable` clears `amount`; switching to `weekly`
clears `anchor_day`; a new `next_payment_date` re-derives `anchor_day` unless given.
**Re-activation** (`is_active: true`) moves `next_payment_date` to the first due date from today
on — dates missed while inactive are not charged. `200` → `SubscriptionOut`.

#### `DELETE /api/subscriptions/{id}`
Deactivates (nothing is deleted). `200` → `SubscriptionOut` with `"is_active": false`. Pending
charges of the subscription stay pending (pay or skip them).

#### `GET /api/subscriptions/upcoming?days=7`
`days` 0–366, default 7. For every active subscription: projected due dates within
`max(days, remind_days_before)` days from today (so a subscription with a longer reminder shows
up earlier), plus **all** `pending` charges.
```json
{
  "today": "2026-11-12",
  "days": 7,
  "upcoming": [
    {"subscription_id": 1, "name": "Netflix", "category": {"…": "…"}, "amount_type": "fixed",
     "amount": 4990.0, "currency": "HUF", "due_date": "2026-11-14", "days_left": 2, "remind": true}
  ],
  "pending": [
    {"id": 1, "subscription_id": 2, "subscription_name": "Электричество", "category": {"…": "…"},
     "amount_type": "variable", "due_date": "2026-11-10", "status": "pending", "amount": null,
     "currency": "HUF", "expense_id": null,
     "created_at": "2026-11-12T13:41:58.423488+00:00", "updated_at": "2026-11-12T13:41:58.423490+00:00"}
  ]
}
```
- `remind` = `days_left <= remind_days_before` — the bot's cue to send a reminder.
- `days_left` can be negative for a date the job has not reached yet (before 06:00).
- `upcoming` is sorted by date, then name; `pending` by due date.

#### `POST /api/subscriptions/charges/{id}/pay`
Headers: `Idempotency-Key` (required). Body: `amount` (> 0, required), `currency` (default: the
charge's), `occurred_at` (with offset; default 12:00 local on the due date), `comment` (default
`Подписка: <name>`). Creates the expense (`source = telegram` for the token) and marks the
charge `paid`. `200` → charge:
```json
{"id": 1, "subscription_id": 2, "subscription_name": "Электричество", "category": {"…": "…"},
 "amount_type": "variable", "due_date": "2026-11-10", "status": "paid", "amount": 15230.0,
 "currency": "HUF", "expense_id": 2,
 "created_at": "2026-11-12T13:41:58.423488+00:00", "updated_at": "2026-11-12T13:41:58.431871+00:00"}
```
`409` if the charge is already `paid`/`skipped` (with a new key) or the key was used for
something else; replay with the same key and body → `200`, same charge.

#### `POST /api/subscriptions/charges/{id}/skip`
Marks a pending charge `skipped` (`200` → charge). Repeating is a no-op (`200`); a `paid`
charge → `409`.

#### `GET /api/subscriptions/summary`
Monthly cost of active subscriptions in HUF at the latest stored rates: monthly ÷ `interval`,
weekly × 52 / 12 ÷ `interval`, yearly ÷ 12 ÷ `interval`. A variable subscription counts with
its **last paid** amount (`basis: "last_paid"`); without any payment it is left out
(`basis: "excluded"`, `monthly_huf: null`) and counted in `excluded_count`.
```json
{
  "monthly_total_huf": 20220, "active_count": 2, "included_count": 2, "excluded_count": 0,
  "items": [
    {"subscription_id": 1, "name": "Netflix", "amount_type": "fixed", "period": "monthly",
     "interval": 1, "amount": 4990.0, "currency": "HUF", "monthly_huf": 4990.0, "basis": "fixed"},
    {"subscription_id": 2, "name": "Электричество", "amount_type": "variable", "period": "monthly",
     "interval": 1, "amount": 15230.0, "currency": "HUF", "monthly_huf": 15230.0, "basis": "last_paid"}
  ]
}
```

## 5. Checklist for the bot (stage 2b)

- Join `vav-internal`; read `FINANCE_API_URL` and `FINANCE_API_TOKEN` from its `.env`; never log
  the token or put it in error messages.
- Send `occurred_at` with the user's offset (`Europe/Budapest` unless the user says otherwise).
- One `Idempotency-Key` per receipt / payment, persisted until the API confirmed it.
- Shop name → `comment`; category → one of `GET /api/categories`.
- Yesterday / month / budget: section 3; subscriptions reminders: `upcoming` (`remind`) and
  `pending` charges → ask for the amount → `pay` (or `skip`).
- On `401`/`429` stop and notify the owner; on `5xx` retry with backoff and the same key.
