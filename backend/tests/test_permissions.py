import pytest
from fastapi.testclient import TestClient

import app.main as main_module

ADMIN = ("admin", "admin-pass")
REGULAR_USER = ("user1", "user1-pass")


@pytest.fixture()
def client(monkeypatch):
    # avoid real network calls to the exchange-rate APIs during tests
    monkeypatch.setattr(main_module, "ensure_today_rates", lambda db: False)
    with TestClient(main_module.app) as c:
        yield c


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_login_wrong_password_is_rejected(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_me_requires_auth(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_user_gets_403_on_admin_only_endpoints(client):
    _login(client, *REGULAR_USER)

    categories = client.get("/api/categories").json()
    assert categories, "starter categories should have been seeded"
    category_id = categories[0]["id"]

    admin_only_calls = [
        ("post", "/api/categories", {"name": "Новая категория", "color": "#ffffff"}),
        ("patch", f"/api/categories/{category_id}", {"name": "Переименовано"}),
        ("post", f"/api/categories/{category_id}/archive", None),
        ("post", f"/api/categories/{category_id}/unarchive", None),
        ("post", "/api/expenses", {"amount": 10, "currency": "HUF", "category_id": category_id}),
        ("patch", "/api/expenses/1", {"amount": 20}),
        ("delete", "/api/expenses/1", None),
        ("post", "/api/expenses/1/restore", None),
        ("get", "/api/expenses/trash", None),
        ("post", "/api/rates", {"date": "2026-01-01", "currency": "EUR", "rate_to_huf": 390}),
        ("post", "/api/rates/refresh", None),
        ("put", "/api/budgets", {"categories": []}),
    ]

    for method, url, payload in admin_only_calls:
        call = getattr(client, method)
        resp = call(url, json=payload) if payload is not None else call(url)
        assert resp.status_code == 403, f"{method.upper()} {url} expected 403, got {resp.status_code}: {resp.text}"


def test_user_can_read(client):
    _login(client, *REGULAR_USER)

    assert client.get("/api/categories").status_code == 200
    assert client.get("/api/expenses").status_code == 200
    assert client.get("/api/budgets").status_code == 200


def test_admin_can_create_and_delete_expense(client):
    _login(client, *ADMIN)

    categories = client.get("/api/categories").json()
    category_id = categories[0]["id"]

    create_resp = client.post(
        "/api/expenses",
        json={"amount": 1500, "currency": "HUF", "category_id": category_id, "comment": "test"},
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["amount_huf"] == 1500
    assert body["rate_source"] == "manual" or body["currency"] == "HUF"

    expense_id = body["id"]
    delete_resp = client.delete(f"/api/expenses/{expense_id}")
    assert delete_resp.status_code == 204

    restore_resp = client.post(f"/api/expenses/{expense_id}/restore")
    assert restore_resp.status_code == 200
