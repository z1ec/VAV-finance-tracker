"""Idempotency-Key on POST /api/expenses and on paying a subscription charge (ADR-0005)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Expense
from tests.conftest import token_client


def _expense(category_id: int, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "amount": 4590,
        "currency": "HUF",
        "category_id": category_id,
        "occurred_at": "2026-11-11T18:30:00+01:00",
        "comment": "Spar",
    }
    body.update(overrides)
    return body


def _count_expenses() -> int:
    db = SessionLocal()
    try:
        return int(db.execute(select(func.count()).select_from(Expense)).scalar_one())
    finally:
        db.close()


def test_replay_returns_the_same_expense(bot: TestClient, category_id: int) -> None:
    key = str(uuid.uuid4())
    first = bot.post("/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key})
    second = bot.post("/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert _count_expenses() == 1


def test_same_key_different_content_is_409(bot: TestClient, category_id: int) -> None:
    key = str(uuid.uuid4())
    assert (
        bot.post(
            "/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key}
        ).status_code
        == 201
    )
    for change in ({"amount": 4591}, {"comment": "Aldi"}, {"occurred_at": None}):
        resp = bot.post(
            "/api/expenses", json=_expense(category_id, **change), headers={"Idempotency-Key": key}
        )
        assert resp.status_code == 409, (change, resp.text)
    assert _count_expenses() == 1


def test_same_instant_in_another_offset_is_the_same_request(
    bot: TestClient, category_id: int
) -> None:
    key = str(uuid.uuid4())
    a = bot.post("/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key})
    b = bot.post(
        "/api/expenses",
        json=_expense(category_id, occurred_at="2026-11-11T17:30:00Z"),
        headers={"Idempotency-Key": key},
    )
    assert (a.status_code, b.status_code) == (201, 200)


def test_key_is_required_for_the_token(bot: TestClient, category_id: int) -> None:
    resp = bot.post("/api/expenses", json=_expense(category_id))
    assert resp.status_code == 422
    assert "Idempotency-Key" in resp.json()["detail"]
    assert _count_expenses() == 0


def test_key_must_be_a_uuid(bot: TestClient, category_id: int) -> None:
    resp = bot.post(
        "/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": "not-a-uuid"}
    )
    assert resp.status_code == 422


def test_key_is_optional_for_the_site(admin: TestClient, category_id: int) -> None:
    assert admin.post("/api/expenses", json=_expense(category_id)).status_code == 201
    assert admin.post("/api/expenses", json=_expense(category_id)).status_code == 201
    assert _count_expenses() == 2
    key = str(uuid.uuid4())
    first = admin.post(
        "/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key}
    )
    again = admin.post(
        "/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key}
    )
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["source"] == "web"
    assert _count_expenses() == 3


@pytest.mark.parametrize("attempt", range(3))
def test_parallel_requests_with_one_key_create_one_expense(
    api: TestClient, category_id: int, attempt: int
) -> None:
    key = str(uuid.uuid4())
    barrier = threading.Barrier(4)
    results: list[tuple[int, int]] = []
    lock = threading.Lock()

    def send() -> None:
        client = token_client()
        barrier.wait()
        resp = client.post(
            "/api/expenses", json=_expense(category_id), headers={"Idempotency-Key": key}
        )
        with lock:
            results.append((resp.status_code, resp.json()["id"]))

    threads = [threading.Thread(target=send) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(status for status, _ in results)
    assert statuses.count(201) == 1, results
    assert set(statuses) <= {200, 201}, results
    assert len({expense_id for _, expense_id in results}) == 1
    assert _count_expenses() == 1


def test_parallel_requests_with_one_key_and_different_content(
    api: TestClient, category_id: int
) -> None:
    key = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def send(amount: int) -> None:
        client = token_client()
        barrier.wait()
        resp = client.post(
            "/api/expenses",
            json=_expense(category_id, amount=amount),
            headers={"Idempotency-Key": key},
        )
        statuses.append(resp.status_code)

    threads = [threading.Thread(target=send, args=(a,)) for a in (100, 200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [201, 409]
    assert _count_expenses() == 1
