# ADR-0006: Bare dates are local days in Europe/Budapest

- Status: accepted
- Date: 2026-11-12

## Context
Timestamps are stored as UTC ISO-8601 strings, but filters and stats treated bare dates
`YYYY-MM-DD` and the current month in UTC (`stats._parse_dt`, `filters._normalize_date_to`,
`/api/stats/budget`). An expense between 00:00 and 01:00 Budapest time (02:00 in summer) is
still the previous day in UTC, so it landed in the previous day or month; the bot's "yesterday"
and "this month" would have been off by up to two hours, and so would budgets on the 1st.

## Decision
- `app/timeutil.py`: `settings.tz` (`Europe/Budapest`) via `zoneinfo`. A bare date means the local
  day: `date_from` → local midnight → UTC; `date_to` → the next local midnight − 1 µs → UTC.
  A datetime with an offset is used as is; a naive datetime in a *filter* is local time.
- Applied everywhere: expense list, CSV export, `summary`, `by-category`, `by-currency`, `top`,
  `by-month` (months bucketed by local date), `budget` and the default period (current *local*
  month). `avg_per_day` counts local calendar days, so a 25-hour DST day is one day.
- Storage stays UTC and is now **normalised on write** (`astimezone(UTC).isoformat()`), because
  string comparison in SQLite is only chronological when every row has the same offset. All 87
  production rows were already `+00:00` (checked before the change).
- `occurred_at` without an offset: `422 "occurred_at must include a timezone offset"` for the bot
  token; the site keeps the old behaviour (treated as UTC). The browser already sends UTC
  (`Date.toISOString()`); a Node test pins this for three device time zones.
- Invalid date parameters answer `422` instead of `500`.

## Consequences
- Tests cover 00:30 on the 1st (winter and summer), both 2026 DST switches (29 March: 23 h,
  25 October: 25 h), "yesterday" on the fall-back day and "this month" at 00:45 on the 1st.
- The Node test found and fixed a frontend bug: `budapestInputToIso` used the offset at the
  guessed instant, so a time in the last hour before the October switch (e.g. 25 Oct 01:30 CEST)
  was saved one hour late. It now re-checks the offset at the result.
- Exchange rates are still chosen by the UTC date of the expense (unchanged; at most the rate of
  the neighbouring day for purchases between local midnight and 01:00/02:00).

## Alternatives
- **Store local time** — ambiguous during the fall-back hour, breaks existing data.
- **Let every client send UTC bounds** — each client (site, bot) would re-implement DST rules.
