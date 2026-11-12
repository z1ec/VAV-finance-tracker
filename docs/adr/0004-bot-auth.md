# ADR-0004: Bot authentication — service token, internal network only, endpoint allowlist

- Status: accepted
- Date: 2026-11-12

## Context
The Momentum bot (same server, separate compose project) must record expenses and read stats and
subscriptions. Browsers authenticate with a signed session cookie after a password login; the
bot is a service, not a person, and must not hold a user's password. The bot API must never be
reachable from the internet, and a leaked token must be worth as little as possible.

## Decision
1. **Service token.** A random 32-byte token is generated on the server. The tracker stores only
   its hex SHA-256 (`BOT_API_TOKEN_SHA256`); the token itself lives only in the bot's `.env`
   (`FINANCE_API_TOKEN`). The check is `hmac.compare_digest(sha256(token), expected)`. Requests
   act as the existing admin named in `BOT_API_USERNAME`.
2. **Two independent network barriers.**
   - *Caddy* (`web/Caddyfile`, first handler inside a `route`): any request with an
     `Authorization` header gets `403` — on the API, on static pages, on every site block. The
     token therefore never works through the public domain.
   - *Backend*: the token is accepted only if the TCP peer is inside `BOT_API_ALLOWED_NETWORKS`
     (the `vav-internal` subnet, `172.20.0.0/16` on `dubai`) **and** the request carries none of
     `X-Forwarded-For`, `Forwarded`, `X-Real-IP`. An empty list disables the token. uvicorn runs
     with `--no-proxy-headers`, so `request.client` is always the real peer and can never be
     rewritten from a header.
3. **Allowlist.** An app-wide dependency (`deps.token_guard`) answers `403` to a token on any
   route not in `TOKEN_ALLOWED_ROUTES` (health, categories, expense create/update/delete/restore,
   stats summary/budget, subscriptions). New endpoints are therefore closed to the token by
   default; a test fails if a route is missing from the permission matrix.
4. **Failures** answer `401` with `WWW-Authenticate: Bearer`, are counted in the login rate
   limiter for the caller's IP (5 per 15 minutes → `429`), and are logged with the peer and the
   reason flag — never the token.

## Verification (experiment, `scripts/e2e_bot_api.sh`, also run in CI)
Real web image (Caddy 2.11.9) in front of an echo upstream, client in the same Docker network:
- Caddy sets `X-Forwarded-For` to the client's address (`172.18.0.4`) on every proxied request.
- A client-supplied `X-Forwarded-For` (`203.0.113.9`, empty, or a list starting with a fake
  address) is **replaced**, not appended: Caddy trusts no proxies by default.
- `Authorization: Bearer x`, `authorization: bearer x`, `AUTHORIZATION: Basic …`, a bare value and
  an **empty** value all get `403`, also for `/`.
Real backend, token from the internal network: `200` on allowed routes, `403` on
`POST /api/rates/refresh` and `GET /api/expenses`, `401` with an added `X-Forwarded-For`, `401`
straight to the backend from the proxy's network, `403` through Caddy; the token never appears
in the backend log.

So a client cannot bypass both levels: through Caddy the header is refused (and even if the
Caddy rule were lost, the backend would see `X-Forwarded-For` and a peer outside the allowed
subnet — Caddy is not on `vav-internal`); bypassing Caddy requires a container on
`vav-internal`, i.e. root on the server.

## Consequences
- Rotating the token = new token in the bot's `.env`, new hash in the tracker's `.env`, restart
  the tracker backend (procedure in `docs/MANUAL_STEPS.ru.md`).
- The bot cannot read the expense list, trash, rates or change categories/budgets; extending
  that is a conscious change of the allowlist and the matrix test.
- Any other container that joins `vav-internal` and learns the token could use it; only the bot
  and the tracker backend are on that network.

## Alternatives
- **Bot logs in with a user password and keeps a cookie** — a human credential in a service,
  session expiry handling, no way to restrict endpoints.
- **mTLS or a signed JWT** — more moving parts for two containers on one host.
- **Network-only trust without a token** — any container on the network would be admin.
