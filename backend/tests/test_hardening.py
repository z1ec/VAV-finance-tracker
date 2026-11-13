"""Stage 3b security fixes: CSV injection, icon escaping, input bounds, CSRF origin check,
bot token boundaries (incl. GET /api/stats/by-category), body limit, cookies, headers."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.deps import TOKEN_ALLOWED_ROUTES, token_route_allowed
from app.limits import MAX_BODY_BYTES
from app.routers.expenses import csv_safe
from tests.conftest import BOT_IP, OUTSIDE_IP, token_client

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
CADDYFILE = ROOT / "web" / "Caddyfile"


def _expense(client: TestClient, category_id: int, **fields: object) -> dict:
    body = {"amount": 100, "currency": "HUF", "category_id": category_id, **fields}
    resp = client.post("/api/expenses", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- CSV injection ----------

FORMULAS = ['=HYPERLINK("http://x")', "+1+2", "-3", "@SUM(A1)", "\tcmd", "\rcmd"]


@pytest.mark.parametrize("value", FORMULAS)
def test_csv_safe_prefixes_formula_cells(value: str) -> None:
    assert csv_safe(value) == "'" + value


@pytest.mark.parametrize("value", ["Lidl", "", "кофе = 2 €", "1-2", 42, 3.5, None])
def test_csv_safe_keeps_ordinary_cells(value: object) -> None:
    assert csv_safe(value) == value


def test_export_neutralises_formulas_in_comments_and_names(
    admin: TestClient, category_id: int
) -> None:
    for comment in [*FORMULAS, "обычный"]:
        _expense(admin, category_id, comment=comment)
    created = admin.post("/api/categories", json={"name": "=cmd|' /C calc'!A0", "color": "#123"})
    assert created.status_code == 201, created.text
    _expense(admin, created.json()["id"], comment="x")

    resp = admin.get("/api/expenses/export")
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text.lstrip("﻿"))))
    comments = {row["comment"] for row in rows}
    for formula in FORMULAS:
        assert "'" + formula in comments
        assert formula not in comments
    assert "обычный" in comments
    assert "'=cmd|' /C calc'!A0" in {row["category"] for row in rows}
    for row in rows:  # no cell of the file can start a formula any more
        for cell in row.values():
            assert not cell.startswith(("=", "+", "@", "\t", "\r"))


# ---------- Category icon: server-side validation + frontend escaping ----------

XSS_ICONS = [
    "<img src=x onerror=alert(1)>",
    '"><svg/onload=alert(1)>',
    "<script>",
    "a&b",
    "it's",
    "\u0007",
]


@pytest.mark.parametrize("icon", XSS_ICONS)
def test_icon_with_markup_is_rejected(admin: TestClient, icon: str) -> None:
    resp = admin.post(
        "/api/categories", json={"name": f"XSS {icon!r}", "color": "#abc", "icon": icon}
    )
    assert resp.status_code == 422
    assert "icon" in resp.json()["detail"]


def test_icon_with_markup_is_rejected_on_update(admin: TestClient, category_id: int) -> None:
    resp = admin.patch(f"/api/categories/{category_id}", json={"icon": "<b>"})
    assert resp.status_code == 422


@pytest.mark.parametrize("icon", ["🍕", "👨‍👩‍👧", "❤️", "₽", "  🚗  "])
def test_emoji_icons_are_accepted(admin: TestClient, icon: str) -> None:
    resp = admin.post("/api/categories", json={"name": f"ok {icon}", "color": "#abc", "icon": icon})
    assert resp.status_code == 201, resp.text
    assert resp.json()["icon"] == icon.strip()


def test_blank_icon_becomes_null(admin: TestClient) -> None:
    resp = admin.post("/api/categories", json={"name": "blank", "color": "#abc", "icon": "  "})
    assert resp.status_code == 201
    assert resp.json()["icon"] is None


@pytest.mark.parametrize("color", ["red", "#12345g", "#fff;background:url(x)", '#fff"><b>'])
def test_color_must_be_hex(admin: TestClient, color: str) -> None:
    resp = admin.post("/api/categories", json={"name": "c", "color": color})
    assert resp.status_code == 422


ESCAPE_SCRIPT = """
import { escapeHtml } from "./common.js";
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map((c) => escapeHtml(c))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_frontend_escape_html_covers_text_and_attributes(tmp_path: Path) -> None:
    """escapeHtml must be safe in element text and in quoted attributes (style="…", title="…"),
    so it has to escape quotes too — the old textContent/innerHTML trick did not."""
    for name in ("common.js", "api.js"):
        shutil.copy(FRONTEND / "js" / name, tmp_path / name)
    (tmp_path / "package.json").write_text('{"type": "module"}')
    (tmp_path / "check.js").write_text(ESCAPE_SCRIPT)
    cases = ['<img src=x onerror="alert(1)">', "it's", "a & b", "🍕", ""]
    node = shutil.which("node")
    assert node is not None
    out = subprocess.run(
        [node, "check.js", json.dumps(cases)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env=dict(os.environ),
        timeout=30,
    )
    assert json.loads(out.stdout) == [
        "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
        "it&#39;s",
        "a &amp; b",
        "🍕",
        "",
    ]


# Constants defined in the frontend itself (navigation tabs, source badges), not API data.
STATIC_ICONS = {"p.icon", "badge.icon"}


def test_frontend_never_interpolates_icon_or_color_unescaped() -> None:
    """Every innerHTML template puts category icons/colours through escapeHtml."""
    offenders = []
    for path in sorted((FRONTEND / "js").glob("*.js")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "innerHTML" in line or "<" in line:
                for match in re.finditer(r"\$\{([^}]*\.(?:icon|color)[^}]*)\}", line):
                    expr = match.group(1)
                    if expr in STATIC_ICONS or expr.startswith("escapeHtml("):
                        continue
                    offenders.append(f"{path.name}:{number}: {match.group(0)}")
    assert offenders == []


def test_pages_have_no_inline_scripts_or_handlers() -> None:
    """The CSP allows scripts from 'self' only: inline <script> or on*= would be blocked."""
    for page in FRONTEND.glob("*.html"):
        html = page.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), page.name
        assert not re.search(r"\son[a-z]+\s*=", html), page.name


# ---------- Input bounds ----------


@pytest.mark.parametrize(
    "fields",
    [
        {"amount": 1_000_000_001},
        {"amount": 1e300},
        {"rate_to_huf": 10_000_000},
        {"comment": "x" * 501},
    ],
)
def test_expense_bounds(admin: TestClient, category_id: int, fields: dict) -> None:
    body = {"amount": 100, "currency": "HUF", "category_id": category_id, **fields}
    assert admin.post("/api/expenses", json=body).status_code == 422


def test_infinite_amount_is_rejected_not_500(admin: TestClient, category_id: int) -> None:
    raw = f'{{"amount": 1e400, "currency": "HUF", "category_id": {category_id}}}'
    resp = admin.post("/api/expenses", content=raw, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422


def test_budget_and_rate_bounds(admin: TestClient, category_id: int) -> None:
    assert admin.put("/api/budgets", json={"total_monthly_budget_huf": 1e12}).status_code == 422
    body = {"categories": [{"category_id": category_id, "limit_huf": 1e12}]}
    assert admin.put("/api/budgets", json=body).status_code == 422
    for date in ["<b>", "2026-02-30", "20261101"]:
        resp = admin.post("/api/rates", json={"date": date, "currency": "EUR", "rate_to_huf": 400})
        assert resp.status_code == 422, date
    assert (
        admin.patch(f"/api/categories/{category_id}", json={"sort_order": 10**12}).status_code
        == 422
    )


# ---------- Bot token boundaries ----------


def test_token_may_read_stats_by_category(bot: TestClient, category_id: int) -> None:
    created = bot.post(
        "/api/expenses",
        json={"amount": 250, "currency": "HUF", "category_id": category_id},
        headers={"Idempotency-Key": "0b7f3c2a-6d8e-4f1a-9c3b-2e5d7a9f1c40"},
    )
    assert created.status_code == 201, created.text
    resp = bot.get("/api/stats/by-category")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [row["category"]["id"] for row in rows] == [category_id]
    assert rows[0]["total_huf"] == 250 and rows[0]["count"] == 1 and rows[0]["pct"] == 100.0
    # Same filters as summary: category and period.
    resp = bot.get("/api/stats/by-category", params={"category_id": category_id + 1000})
    assert resp.status_code == 200 and resp.json() == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/stats/by-category"), ("GET", "/api/categories"), ("POST", "/api/expenses")],
)
@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-For": "203.0.113.9"},
        {"X-Forwarded-For": BOT_IP},
        {"X-Forwarded-For": "172.20.0.1, 203.0.113.9"},
        {"Forwarded": "for=172.20.0.9"},
        {"X-Real-IP": BOT_IP},
    ],
)
def test_token_with_proxy_headers_is_refused(
    api: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    """Caddy always adds X-Forwarded-For: a token that came through it (even with a forged
    internal address) is refused although the TCP peer is inside the allowed network."""
    resp = token_client(BOT_IP, **headers).request(method, path, json={})
    assert resp.status_code == 401


@pytest.mark.parametrize("peer", [OUTSIDE_IP, "172.21.0.5", "192.168.1.10", "127.0.0.1", "8.8.8.8"])
@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/stats/by-category"), ("GET", "/api/stats/summary"), ("POST", "/api/expenses")],
)
def test_token_outside_allowed_networks_is_refused(
    api: TestClient, peer: str, method: str, path: str
) -> None:
    resp = token_client(peer).request(method, path, json={})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/api/stats/by-category", True),
        ("GET", "/api/stats/by-category/", False),
        ("GET", "/api/stats/by-month", False),
        ("PATCH", "/api/expenses/12", True),
        ("PATCH", "/api/expenses/12/x", False),
        ("GET", "/api/expenses/12", False),
        ("GET", "/api/expenses/trash", False),
        ("GET", "/api/expenses/export", False),
        ("POST", "/api/expenses/7/restore", True),
        ("POST", "/api/rates/refresh", False),
        ("POST", "/api/subscriptions/charges/5/pay", True),
        ("POST", "/api/subscriptions/charges/5/pay/../../../rates/refresh", False),
        ("GET", "/api/categoriesX", False),
        ("get", "/api/categories", False),
    ],
)
def test_token_route_patterns(method: str, path: str, allowed: bool) -> None:
    assert token_route_allowed(method, path) is allowed


def test_every_allowed_template_matches_itself() -> None:
    for method, template in TOKEN_ALLOWED_ROUTES:
        assert token_route_allowed(method, re.sub(r"\{[a-z_]+\}", "1", template))


# ---------- CSRF: Origin / Sec-Fetch-Site on state-changing cookie requests ----------


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver.evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site", "Origin": "https://fomin-vladimir.ru"},
    ],
)
def test_cross_site_writes_are_refused(
    admin: TestClient, category_id: int, headers: dict[str, str]
) -> None:
    body = {"amount": 1, "currency": "HUF", "category_id": category_id}
    resp = admin.post("/api/expenses", json=body, headers=headers)
    assert resp.status_code == 403
    assert admin.post("/api/auth/logout", headers=headers).status_code == 403
    assert admin.get("/api/expenses").status_code == 200  # the session survived


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://testserver"},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
    ],
)
def test_same_origin_writes_pass(admin: TestClient, category_id: int, headers: dict) -> None:
    body = {"amount": 1, "currency": "HUF", "category_id": category_id}
    assert admin.post("/api/expenses", json=body, headers=headers).status_code == 201


def test_reads_are_not_origin_checked(admin: TestClient) -> None:
    assert (
        admin.get("/api/categories", headers={"Origin": "https://evil.example"}).status_code == 200
    )


def test_cross_site_login_is_refused(api: TestClient) -> None:
    resp = api.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-pass"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


# ---------- Body size limit ----------


def test_oversized_body_is_refused(admin: TestClient, category_id: int) -> None:
    big = {"amount": 1, "currency": "HUF", "category_id": category_id, "pad": "x" * MAX_BODY_BYTES}
    resp = admin.post("/api/expenses", json=big)
    assert resp.status_code == 413
    assert resp.json() == {"detail": "Слишком большой запрос"}


def test_oversized_chunked_body_is_refused(admin: TestClient) -> None:
    def chunks():
        for _ in range(10):
            yield b"x" * (MAX_BODY_BYTES // 4)

    resp = admin.post(
        "/api/categories", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413


def test_bad_content_length_is_refused(api: TestClient) -> None:
    resp = api.post("/api/auth/login", content=b"{}", headers={"Content-Length": "abc"})
    assert resp.status_code in {400, 413}


# ---------- Session cookie ----------


def _set_cookie(resp: Any) -> str:
    return resp.headers["set-cookie"].lower()


def test_login_cookie_flags_on_localhost(api: TestClient) -> None:
    resp = api.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
    cookie = _set_cookie(resp)
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # plain-HTTP local development
    assert f"max-age={settings.session_max_age_days * 86400}" in cookie


def test_cookie_is_secure_on_a_real_domain(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "domain", "finance.example.com")
    resp = api.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
    assert "secure" in _set_cookie(resp)
    logout = api.post("/api/auth/logout")
    cookie = _set_cookie(logout)
    assert "secure" in cookie and "httponly" in cookie and "max-age=0" in cookie


def test_logout_clears_the_cookie(admin: TestClient) -> None:
    resp = admin.post("/api/auth/logout")
    assert resp.status_code == 200
    assert "max-age=0" in _set_cookie(resp)
    assert admin.get("/api/auth/me").status_code == 401


# ---------- Login limiter ----------


def test_limiter_keeps_no_empty_entries(api: TestClient) -> None:
    for _ in range(3):
        api.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
    assert auth._login_attempts == {}


def test_limiter_memory_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "MAX_TRACKED_IPS", 50)
    auth._login_attempts.clear()
    try:
        for i in range(500):
            auth.record_failed_attempt(f"198.51.100.{i % 256}-{i}")
        assert len(auth._login_attempts) <= 50
        auth.record_failed_attempt("203.0.113.1")
        assert "203.0.113.1" in auth._login_attempts
    finally:
        auth._login_attempts.clear()


# ---------- Information disclosure ----------


def test_health_exposes_only_status_fields(api: TestClient) -> None:
    assert set(api.get("/api/health").json()) == {
        "status",
        "db",
        "version",
        "git_sha",
        "last_rates_update",
    }


def test_internal_errors_hide_details(admin: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.categories as categories

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internals /data/expenses.db")

    monkeypatch.setattr(categories, "select", boom)
    client = TestClient(admin.app, raise_server_exceptions=False, cookies=admin.cookies)
    resp = client.get("/api/categories")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Внутренняя ошибка сервера"}


# ---------- Security headers (Caddy) ----------


def _caddy_headers() -> dict[str, str]:
    text = CADDYFILE.read_text()
    block = text[text.index("(security_headers)") :]
    block = block[: block.index("\n}\n")]
    headers: dict[str, str] = {}
    for line in block.splitlines():
        match = re.match(r'\s*([A-Za-z-]+)\s+"(.*)"\s*$', line)
        if match:
            headers[match.group(1).lower()] = match.group(2)
    return headers


def test_caddy_sets_security_headers() -> None:
    headers = _caddy_headers()
    csp = headers["content-security-policy"]
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    ):
        assert directive in csp
    assert "unsafe-eval" not in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["permissions-policy"]
    hsts = headers["strict-transport-security"]
    assert hsts.startswith("max-age=") and "includesubdomains" not in hsts.lower()


def test_caddy_applies_headers_and_body_limit_to_every_site() -> None:
    text = CADDYFILE.read_text()
    app_snippet = text[text.index("(app) {") :]
    assert "import security_headers" in app_snippet.split("route {")[0]
    assert re.search(r"request_body \{\s*max_size \d+KB\s*\}", text)
    redirect_site = text[text.index("{$DOMAIN:localhost}:8443 {") :]
    assert "import security_headers" in redirect_site.split("\n}")[0]


# ---------- Data and backup permissions ----------


def test_backups_are_private(tmp_path: Path) -> None:
    import sqlite3

    from app.backup import sqlite_backup

    source = tmp_path / "src.db"
    sqlite3.connect(source).close()
    dest = sqlite_backup(source, tmp_path / "backups" / "copy.db")
    assert dest.stat().st_mode & 0o777 == 0o600
    assert dest.parent.stat().st_mode & 0o777 == 0o700


def test_restrict_data_permissions(tmp_path: Path) -> None:
    from app.backup import restrict_data_permissions

    data = tmp_path / "data"
    backups = data / "backups"
    backups.mkdir(parents=True)
    files = [data / "expenses.db", data / "expenses.db-wal", backups / "expenses-2026-11-13.db"]
    for path in files:
        path.write_text("x")
        path.chmod(0o644)
    data.chmod(0o755)
    backups.chmod(0o755)
    (data / "other-dir").mkdir()
    (data / "other-dir").chmod(0o755)  # unrelated directories are left alone

    changed = restrict_data_permissions(data / "expenses.db", backups)
    assert changed == 5
    assert data.stat().st_mode & 0o777 == 0o700
    assert backups.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    assert (data / "other-dir").stat().st_mode & 0o777 == 0o755
    assert restrict_data_permissions(data / "expenses.db", backups) == 0  # idempotent


def test_restrict_data_permissions_tolerates_missing_dirs(tmp_path: Path) -> None:
    from app.backup import restrict_data_permissions

    assert restrict_data_permissions(tmp_path / "nope" / "x.db", tmp_path / "nope2") == 0
