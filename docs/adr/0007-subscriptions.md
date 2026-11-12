# ADR-0007: Subscriptions — model, daily job, `anchor_day`

- Status: accepted
- Date: 2026-11-12

## Context
Recurring payments (streaming, phone, insurance, utilities) should be recorded without manual
work, shown in advance, and — for amounts that change every time — asked for by the bot.

## Decision
**Model** (migration `0002`):
- `subscriptions`: `name`, `amount_type` (`fixed | variable`), `amount` (required for fixed, `NULL`
  for variable — enforced by a CHECK), `currency`, `category_id`, `period`
  (`weekly | monthly | yearly`), `interval ≥ 1`, `anchor_day` (1–31, monthly/yearly),
  `next_payment_date` (local `YYYY-MM-DD`), `remind_days_before` (default 3), `is_active`,
  `comment`, timestamps.
- `subscription_charges`: one row per due date — `status` `pending | paid | skipped`, `amount`,
  `currency`, `expense_id`; `UNIQUE(subscription_id, due_date)`.

**Dates** (`app/subscriptions.advance`):
- monthly/yearly: the charge falls on `anchor_day`; if the month is shorter, on its last day; the
  anchor is kept, so 31 Jan → 28 Feb → 31 Mar → 30 Apr, and yearly 29 Feb → 28 Feb (non-leap)
  → 29 Feb (leap). Default anchor = day of the first `next_payment_date`. Interval N = every N
  months/years.
- weekly: `+ 7 × interval` days; `anchor_day` must be `NULL`.

**Job** (`process_due_subscriptions`): daily at **06:00 Europe/Budapest** (APScheduler cron with
its own time zone; other jobs stay in UTC) and **once on every start**, plus right after a
subscription is created or updated. For each active subscription with
`next_payment_date <= today`, for every missed date (at most 400 per run):
- create the charge unless one exists for that date;
- fixed → an expense (`source = subscription`, 12:00 local time on the due date, the
  subscription's category, comment `Подписка: <name>`, rate of that date), charge `paid`;
- variable → charge `pending` (the bot asks for the amount, then `pay` or `skip`);
- advance `next_payment_date`; **commit per date**. A failure (e.g. no exchange rate) rolls back
  that date and is retried on the next run.

**API**: list/create/update/deactivate, `upcoming` (window `max(days, remind_days_before)` per
subscription + all pending charges), `summary` (monthly HUF: weekly × 52/12, yearly / 12, divided
by `interval`; variable = last paid amount, else excluded and reported), `pay`
(`Idempotency-Key`, ADR-0005) and `skip`. Admin cookie and bot token write; user cookie reads.
`DELETE` only deactivates. Re-activation moves `next_payment_date` to the first due date from
today — dates missed while inactive are **not** charged. Paying from the site records
`source = subscription`; from the bot `telegram` (the token always writes `telegram`).

## Consequences
- Re-running the job never duplicates: the date is advanced in the same transaction as the
  charge, and the unique constraint stops a second charge even if the date was not advanced
  (e.g. after a restore). Tests cover reruns, catch-up, deactivation, rate failures, 31st,
  29 February, yearly, weekly with `interval = 2`.
- A subscription created with a date far in the past records all missed payments (intended for
  back-filling); the per-run cap keeps one run short.
- No push notifications from the tracker: reminders are the bot's job (`remind` flag).

## Alternatives
- **Generate expenses on the fly in reports** — history would change when a subscription is
  edited, and the bot could not attach an actual amount.
- **Store recurrence as RRULE** — far more general than needed; the anchor rule above covers
  every real case here and is easy to test.
