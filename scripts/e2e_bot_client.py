"""E2E client for scripts/e2e_bot_api.sh — runs inside a throwaway container.

Modes:
  caddy-echo  web → echo upstream: Caddy sets X-Forwarded-For (spoofing impossible) and
              refuses every request with an Authorization header.
  bot         from the internal network: token auth, allowlist, idempotency, subscriptions.
  outside     from the default network (where Caddy lives): the token is refused both directly
              (wrong network) and through Caddy (403 before the backend).
The token comes from the environment (E2E_TOKEN) and is never printed.
"""

import http.client
import json
import os
import socket
import sys
import threading
import uuid
from typing import Any

TOKEN = os.environ.get("E2E_TOKEN", "")
results: list[str] = []
last_headers: dict[str, str] = {}

# Stage 3b: what web/Caddyfile must add to every response (values checked loosely).
SECURITY_HEADERS = {
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=()",
    "strict-transport-security": "max-age=",
}


def request(
    host: str,
    method: str,
    path: str,
    headers: list[tuple[str, str]] | None = None,
    body: Any = None,
    port: int = 80,
) -> tuple[int, Any]:
    conn = http.client.HTTPConnection(host, port, timeout=15)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    sent = {name.lower() for name, _ in headers or []}
    if "host" not in sent:
        conn.putheader("Host", host)
    for name, value in headers or []:
        conn.putheader(name, value)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    last_headers.clear()
    last_headers.update({k.lower(): v for k, v in resp.getheaders()})
    try:
        return resp.status, json.loads(raw) if raw else None
    except ValueError:
        return resp.status, raw.decode(errors="replace")


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  → {detail}"))


def bearer() -> list[tuple[str, str]]:
    return [("Authorization", f"Bearer {TOKEN}")]


def check_security_headers(what: str) -> None:
    missing = {
        name: last_headers.get(name)
        for name, needle in SECURITY_HEADERS.items()
        if needle not in last_headers.get(name, "")
    }
    check(f"security headers on {what}", not missing, missing)
    check(f"no Server header on {what}", "server" not in last_headers, last_headers.get("server"))


def own_ip() -> str:
    return socket.gethostbyname(socket.gethostname())


# ---------------------------------------------------------------- modes


def caddy_echo() -> None:
    me = own_ip()
    local = [("Host", "localhost")]
    status, body = request("web", "GET", "/api/anything", local)
    check("echo reachable through Caddy", status == 200, status)
    xff = body["headers"].get("x-forwarded-for") if isinstance(body, dict) else None
    check(f"Caddy sets X-Forwarded-For to the client address ({me})", xff == me, xff)

    for spoof in ("203.0.113.9", "", f"{me}, 203.0.113.9"):
        status, body = request("web", "GET", "/api/x", [*local, ("X-Forwarded-For", spoof)])
        xff = body["headers"].get("x-forwarded-for") if isinstance(body, dict) else None
        check(f"client X-Forwarded-For {spoof!r} is replaced", xff == me, xff)

    for name, value in [
        ("Authorization", "Bearer x"),
        ("authorization", "bearer x"),
        ("AUTHORIZATION", "Basic eDp5"),
        ("Authorization", "x"),
        ("Authorization", ""),
    ]:
        status, body = request("web", "GET", "/api/categories", [*local, (name, value)])
        check(f"Caddy refuses {name}: {value[:6]!r}… with 403", status == 403, (status, body))
    status, _ = request("web", "GET", "/", [*local, ("Authorization", "Bearer x")])
    check("Caddy refuses Authorization on static pages too", status == 403, status)

    status, body = request("web", "GET", "/api/anything", local)
    host = body["headers"].get("host") if isinstance(body, dict) else None
    check("Caddy passes Host unchanged (the backend's Origin check)", host == "localhost", host)
    check_security_headers("a proxied /api response")
    status, _ = request("web", "GET", "/login.html", local)
    check("login page → 200", status == 200, status)
    check_security_headers("/login.html")
    status, body = request("web", "POST", "/api/x", local, {"pad": "x" * 70_000})
    check("70 KB body → 413 from Caddy (upstream never called)", status == 413, (status, body))


def bot() -> None:
    api = "finance-backend"

    def call(method: str, path: str, body: Any = None, **hdr: str) -> tuple[int, Any]:
        return request(api, method, path, [*bearer(), *hdr.items()], body, port=8000)

    status, cats = call("GET", "/api/categories")
    check("token: GET /api/categories → 200", status == 200, status)
    category_id = cats[0]["id"]
    for path in (
        "/api/stats/budget",
        "/api/stats/by-category",
        "/api/subscriptions/upcoming",
        "/api/health",
    ):
        status, _ = call("GET", path)
        check(f"token: GET {path} → 200", status == 200, status)
    status, _ = request(api, "GET", "/api/categories", port=8000)
    check("no token → 401", status == 401, status)
    status, _ = call("POST", "/api/rates/refresh")
    check("token: POST /api/rates/refresh → 403", status == 403, status)
    status, _ = call("GET", "/api/expenses")
    check("token: GET /api/expenses (not allowed) → 403", status == 403, status)
    status, _ = request(
        api, "GET", "/api/categories", [*bearer(), ("X-Forwarded-For", own_ip())], port=8000
    )
    check("token + X-Forwarded-For from the internal network → 401", status == 401, status)
    status, _ = request(
        api, "GET", "/api/categories", [("Authorization", "Bearer nope")], port=8000
    )
    check("wrong token → 401", status == 401, status)

    expense = {
        "amount": 1234,
        "currency": "HUF",
        "category_id": category_id,
        "occurred_at": "2026-11-11T19:05:00+01:00",
        "comment": "E2E shop",
    }
    key = str(uuid.uuid4())
    s1, e1 = call("POST", "/api/expenses", expense, **{"Idempotency-Key": key})
    s2, e2 = call("POST", "/api/expenses", expense, **{"Idempotency-Key": key})
    check("POST /api/expenses → 201, source telegram", s1 == 201 and e1["source"] == "telegram", e1)
    check("replay with the same key → 200, same id", s2 == 200 and e2["id"] == e1["id"], (s2, e2))
    s3, _ = call("POST", "/api/expenses", {**expense, "amount": 999}, **{"Idempotency-Key": key})
    check("same key, different body → 409", s3 == 409, s3)
    s4, _ = call("POST", "/api/expenses", expense)
    check("no Idempotency-Key → 422", s4 == 422, s4)
    s5, b5 = call(
        "POST",
        "/api/expenses",
        {**expense, "occurred_at": "2026-11-11T19:05:00"},
        **{"Idempotency-Key": str(uuid.uuid4())},
    )
    check("occurred_at without offset → 422", s5 == 422, (s5, b5))

    parallel_key = str(uuid.uuid4())
    outcomes: list[tuple[int, int]] = []
    barrier = threading.Barrier(6)

    def send() -> None:
        barrier.wait()
        s, b = call("POST", "/api/expenses", expense, **{"Idempotency-Key": parallel_key})
        outcomes.append((s, b.get("id") if isinstance(b, dict) else -1))

    threads = [threading.Thread(target=send) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(s for s, _ in outcomes)
    check(
        "6 parallel requests, one key → one 201, five 200, one id",
        statuses == [200] * 5 + [201] and len({i for _, i in outcomes}) == 1,
        outcomes,
    )

    s, sub = call(
        "POST",
        "/api/subscriptions",
        {
            "name": "E2E power",
            "amount_type": "variable",
            "currency": "HUF",
            "category_id": category_id,
            "period": "monthly",
            "next_payment_date": "2026-10-01",
        },
    )
    check("token: POST /api/subscriptions → 201", s == 201, (s, sub))
    s, up = call("GET", "/api/subscriptions/upcoming?days=7")
    pending = [c for c in up["pending"] if c["subscription_id"] == sub["id"]]
    check("variable subscription in the past → pending charges", s == 200 and pending, up)
    pay_key = str(uuid.uuid4())
    path = f"/api/subscriptions/charges/{pending[0]['id']}/pay"
    p1, c1 = call("POST", path, {"amount": 8000}, **{"Idempotency-Key": pay_key})
    p2, c2 = call("POST", path, {"amount": 8000}, **{"Idempotency-Key": pay_key})
    check("pay → 200 paid; replay → same expense", p1 == p2 == 200 and c1 == c2, (c1, c2))
    s, summary = call("GET", "/api/subscriptions/summary")
    check("subscriptions summary → 200", s == 200 and "monthly_total_huf" in summary, summary)


def outside() -> None:
    status, _ = request("backend", "GET", "/api/categories", bearer(), port=8000)
    check("token straight to the backend from the proxy network → 401", status == 401, status)
    status, _ = request("web", "GET", "/api/categories", [("Host", "localhost"), *bearer()])
    check("token through Caddy → 403", status == 403, status)
    for forged in ("172.20.0.5", own_ip(), "127.0.0.1"):
        status, _ = request(
            "web",
            "GET",
            "/api/stats/by-category",
            [("Host", "localhost"), ("X-Forwarded-For", forged), *bearer()],
        )
        check(f"token + forged X-Forwarded-For {forged} through Caddy → 403", status == 403, status)
    status, _ = request("web", "GET", "/api/health", [("Host", "localhost")])
    check("health through Caddy without token → 200", status == 200, status)


if __name__ == "__main__":
    mode = sys.argv[1]
    {"caddy-echo": caddy_echo, "bot": bot, "outside": outside}[mode]()
    print(f"--- {mode}")
    print("\n".join(results))
    sys.exit(0 if all(r.startswith("PASS") for r in results) else 1)
