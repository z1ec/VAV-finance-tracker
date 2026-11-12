# ADR-0005: Idempotent writes with `Idempotency-Key`

- Status: accepted
- Date: 2026-11-12

## Context
The bot records money from Telegram messages. Telegram redelivers updates, the bot may restart
mid-request, and network timeouts hide whether a write happened. A blind retry must never record
the same receipt twice; a retry must also not silently overwrite a different operation.

## Decision
- `POST /api/expenses` and `POST /api/subscriptions/charges/{id}/pay` accept the header
  `Idempotency-Key` (UUID). It is **required for the bot token** (`422` without) and optional for
  the site (the site's forms send one per opened form, which also stops double clicks).
- The key is stored in the new nullable column `expenses.client_request_id` with a **unique
  index** (`ux_expenses_client_request_id`), together with `client_request_hash` — SHA-256 of a
  canonical JSON of the request content (operation name, amount as normalised decimal, currency,
  category or charge id, comment, `occurred_at` as a UTC instant or null, manual rate).
- Flow: look up the key → found: same hash → `200` with the existing record; different hash →
  `409`. Not found → create and commit. If the commit hits the unique index (a parallel request
  with the same key won), roll back, read the winner and apply the same rule. The database, not
  the application, decides the race.
- Paying a charge additionally uses a conditional update
  (`UPDATE … WHERE id = ? AND status = 'pending'`) so two payments with different keys cannot
  both succeed (`409`).
- Keys never expire (a few per day; the column is small).

## Consequences
- Tests: replay, `409` on any content change, equal instants in different offsets are the same
  request, missing/invalid key, 4 parallel requests with one key → one row (repeated 3×), two
  parallel requests with one key and different bodies → `201` + `409`; the same in the e2e run
  against a real container (6 parallel requests).
- The hash is of the *request*, so a later `PATCH` of the expense does not turn a replay into a
  conflict.
- Automatic subscription charges do not need a key: they are made idempotent by
  `UNIQUE(subscription_id, due_date)` (ADR-0007).

## Alternatives
- **Dedup by content** (same amount/time/comment) — legitimate identical purchases exist.
- **Separate idempotency table with TTL** — more code; the key belongs to the row it created.
- **Storing only the key without a hash** — cannot tell a retry from a client bug that reuses
  keys.
