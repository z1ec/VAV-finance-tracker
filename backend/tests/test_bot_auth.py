"""Bot token auth (ADR-0004): allowlist matrix, internal network only, no proxy headers."""

import logging
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.currency import RateUnavailableError
from app.deps import TOKEN_ALLOWED_ROUTES
from tests.conftest import BOT_IP, BOT_TOKEN, OUTSIDE_IP, token_client

PUBLIC = "public"  # no auth at all (token allowed: on the list)
AUTH_FLOW = "auth_flow"  # login/logout: no session needed, token → 403
READ = "read"  # any cookie user; token → 403
READ_TOKEN = "read_token"  # any cookie user and the token
ADMIN = "admin"  # admin cookie only; token → 403
WRITE_TOKEN = "write_token"  # admin cookie and the token

EXPECTED: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): PUBLIC,
    ("POST", "/api/auth/login"): AUTH_FLOW,
    ("POST", "/api/auth/logout"): AUTH_FLOW,
    ("GET", "/api/auth/me"): READ,
    ("GET", "/api/expenses/trash"): ADMIN,
    ("GET", "/api/expenses/export"): READ,
    ("GET", "/api/expenses"): READ,
    ("POST", "/api/expenses"): WRITE_TOKEN,
    ("PATCH", "/api/expenses/{expense_id}"): WRITE_TOKEN,
    ("DELETE", "/api/expenses/{expense_id}"): WRITE_TOKEN,
    ("POST", "/api/expenses/{expense_id}/restore"): WRITE_TOKEN,
    ("GET", "/api/categories"): READ_TOKEN,
    ("POST", "/api/categories"): ADMIN,
    ("PATCH", "/api/categories/{category_id}"): ADMIN,
    ("POST", "/api/categories/{category_id}/archive"): ADMIN,
    ("POST", "/api/categories/{category_id}/unarchive"): ADMIN,
    ("GET", "/api/rates/latest"): READ,
    ("GET", "/api/rates"): READ,
    ("POST", "/api/rates"): ADMIN,
    ("POST", "/api/rates/refresh"): ADMIN,
    ("GET", "/api/stats/summary"): READ_TOKEN,
    ("GET", "/api/stats/by-category"): READ,
    ("GET", "/api/stats/by-month"): READ,
    ("GET", "/api/stats/by-currency"): READ,
    ("GET", "/api/stats/top"): READ,
    ("GET", "/api/stats/budget"): READ_TOKEN,
    ("GET", "/api/budgets"): READ,
    ("PUT", "/api/budgets"): ADMIN,
    ("GET", "/api/subscriptions"): READ_TOKEN,
    ("POST", "/api/subscriptions"): WRITE_TOKEN,
    ("GET", "/api/subscriptions/upcoming"): READ_TOKEN,
    ("GET", "/api/subscriptions/summary"): READ_TOKEN,
    ("PATCH", "/api/subscriptions/{subscription_id}"): WRITE_TOKEN,
    ("DELETE", "/api/subscriptions/{subscription_id}"): WRITE_TOKEN,
    ("POST", "/api/subscriptions/charges/{charge_id}/pay"): WRITE_TOKEN,
    ("POST", "/api/subscriptions/charges/{charge_id}/skip"): WRITE_TOKEN,
}

# Allowed outcome per (access class, caller): "ok" = authorised (any status except 401/403).
OUTCOME: dict[str, dict[str, str | int]] = {
    PUBLIC: {"admin": "ok", "user": "ok", "token": "ok", "anon": "ok"},
    AUTH_FLOW: {"admin": "ok", "user": "ok", "token": 403, "anon": "ok"},
    READ: {"admin": "ok", "user": "ok", "token": 403, "anon": 401},
    READ_TOKEN: {"admin": "ok", "user": "ok", "token": "ok", "anon": 401},
    ADMIN: {"admin": "ok", "user": 403, "token": 403, "anon": 401},
    WRITE_TOKEN: {"admin": "ok", "user": 403, "token": "ok", "anon": 401},
}


def _app_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in main_module.app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                found.add((method, route.path))
    return found


def test_every_route_is_classified() -> None:
    """A new endpoint must be added to the matrix (and so consciously allowed or not)."""
    assert _app_routes() == set(EXPECTED)


def test_token_allowlist_matches_the_matrix() -> None:
    allowed = {key for key, cls in EXPECTED.items() if OUTCOME[cls]["token"] == "ok"}
    assert allowed == set(TOKEN_ALLOWED_ROUTES)


def _call(client: TestClient, method: str, path: str) -> int:
    url = re.sub(r"\{[a-z_]+\}", "999999", path)
    if method in {"POST", "PUT", "PATCH"}:
        return client.request(method, url, json={}).status_code
    return client.request(method, url).status_code


@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED))
def test_permission_matrix(
    method: str,
    path: str,
    admin: TestClient,
    user: TestClient,
    bot: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise RateUnavailableError("offline test")

    monkeypatch.setattr("app.routers.rates.refresh_rates_for_date", no_network)
    callers = {"admin": admin, "user": user, "token": bot, "anon": TestClient(main_module.app)}
    expected = OUTCOME[EXPECTED[(method, path)]]
    for caller, client in callers.items():
        if path == "/api/auth/logout" and caller != "token":
            continue  # would end the shared session; covered by the auth tests
        status = _call(client, method, path)
        assert status < 500, f"{caller} {method} {path} → {status}"
        if expected[caller] == "ok":
            assert status not in (401, 403), f"{caller} {method} {path} → {status}"
        else:
            assert status == expected[caller], f"{caller} {method} {path} → {status}"


# ---------- Where the token is accepted from ----------


def test_token_accepted_from_internal_network(api: TestClient) -> None:
    resp = token_client(BOT_IP).get("/api/categories")
    assert resp.status_code == 200
    assert resp.json()


def test_token_rejected_from_outside_network(api: TestClient) -> None:
    resp = token_client(OUTSIDE_IP).get("/api/categories")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        {"X-Forwarded-For": "203.0.113.9"},
        {"X-Forwarded-For": BOT_IP},
        {"Forwarded": "for=203.0.113.9"},
        {"X-Real-IP": "203.0.113.9"},
    ],
)
def test_token_rejected_when_request_came_through_a_proxy(
    api: TestClient, header: dict[str, str]
) -> None:
    """Caddy always sets X-Forwarded-For, so a token that arrived through it is refused even
    when the proxy's own address is inside the allowed network."""
    resp = token_client(BOT_IP, **header).get("/api/categories")
    assert resp.status_code == 401


def test_empty_network_list_disables_the_token(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module.settings, "bot_api_allowed_networks", "")
    assert token_client(BOT_IP).get("/api/categories").status_code == 401


def test_unset_hash_disables_the_token(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.settings, "bot_api_token_sha256", "")
    assert token_client(BOT_IP).get("/api/categories").status_code == 401


@pytest.mark.parametrize(
    "header",
    [f"Bearer {BOT_TOKEN}x", "Bearer ", f"Basic {BOT_TOKEN}", BOT_TOKEN, ""],
)
def test_wrong_token_or_scheme_is_401(api: TestClient, header: str) -> None:
    client = token_client(BOT_IP)
    client.headers["Authorization"] = header
    assert client.get("/api/categories").status_code == 401


def test_token_takes_precedence_over_cookie(admin: TestClient) -> None:
    admin.headers["Authorization"] = "Bearer wrong"
    assert admin.get("/api/categories").status_code == 401


def test_failed_tokens_hit_the_login_rate_limit(api: TestClient) -> None:
    bad = token_client(BOT_IP, token="wrong-token")
    for _ in range(5):
        assert bad.get("/api/categories").status_code == 401
    assert token_client(BOT_IP).get("/api/categories").status_code == 429


def test_token_requires_existing_admin(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.settings, "bot_api_username", "user1")
    assert token_client(BOT_IP).get("/api/categories").status_code == 401
    monkeypatch.setattr(main_module.settings, "bot_api_username", "nobody")
    assert token_client(BOT_IP).get("/api/categories").status_code == 401


def test_token_is_never_logged(api: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    token_client(OUTSIDE_IP).get("/api/categories")
    token_client(BOT_IP, **{"X-Forwarded-For": "1.2.3.4"}).get("/api/categories")
    token_client(BOT_IP).get("/api/categories")
    dumped = caplog.text + "".join(str(r.__dict__) for r in caplog.records)
    assert "bot_token_rejected" in dumped
    assert BOT_TOKEN not in dumped


def test_token_writes_are_attributed_to_the_bot_user(bot: TestClient, category_id: int) -> None:
    resp = bot.post(
        "/api/expenses",
        json={
            "amount": 990,
            "currency": "HUF",
            "category_id": category_id,
            "occurred_at": "2026-11-10T09:15:00+01:00",
            "comment": "Lidl",
        },
        headers={"Idempotency-Key": "8a5e0c1e-1c43-4a39-9d1b-0f6c4c7e2a10"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "telegram"
    assert body["occurred_at"] == "2026-11-10T08:15:00+00:00"
    me = token_client(BOT_IP).get("/api/auth/me")
    assert me.status_code == 403
