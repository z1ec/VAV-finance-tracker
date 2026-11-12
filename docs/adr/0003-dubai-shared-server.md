# ADR-0003: Move to the Dubai server, share it with the bot, HTTPS on 443 with a :8443 redirect

- Status: accepted
- Date: 2026-11-12

## Context
The tracker ran on `ru-tracker` (Russia) behind the host's nginx, which also serves an unrelated
site, so Caddy could not own 80/443; the README variant used HTTPS on 8443. The roadmap
(stages 2–5) connects the tracker to the Momentum Telegram bot: the bot will read and write
expenses through an API that must not be exposed to the internet. All AI features live in the bot,
and Gemini is not reachable from Russia anyway. The bot runs on `dubai` (`81.85.92.119`).

## Decision
- Move the tracker to `dubai` under `/opt/finance-tracker` (owner `deploy`, data owned by uid 10001),
  compose project `finance-tracker`, its own Caddy volumes.
- Join the backend to an **external Docker network `vav-internal`** with the alias
  `finance-backend`; the bot will join the same network. The backend port is never published.
- The tracker's Caddy owns **80 and 443** on `dubai` (the bot has no inbound ports). The main
  address becomes `https://finance.fomin-vladimir.ru`; **`:8443` answers with a permanent
  redirect** to the same path without the port so old bookmarks keep working. ufw: only
  `80/tcp`, `443/tcp`, `8443/tcp` were added; HTTP/3 is disabled (UDP not opened).
- Migration path: quick move first (stop → sqlite backup → stream over SSH → start the same code
  in Dubai), CI/CD afterwards; the old stack is stopped but kept for 14 days.

## Alternatives
- **Stay on ru-tracker and expose the API to the bot over the internet** — public attack surface,
  cross-border latency, still no Gemini from Russia.
- **Keep HTTPS on 8443 only** — no reason on a server where 443 is free; ugly URLs.
- **Put the bot and tracker in one compose project** — couples their release cycles and `.env`
  files; an external network keeps them independent.

## Consequences
- Bot and tracker share 1 GB RAM / 1 GB swap: both images are slim, builds happen in CI.
- A problem in one stack must not affect the other: separate projects, never prune globally.
- Clients with stale DNS caches (old A record had TTL 86400) hit the old server for up to a day.
