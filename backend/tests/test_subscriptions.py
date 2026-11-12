"""Subscriptions (ADR-0007): date rules, the charge job, API and permissions."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import timeutil
from app.currency import upsert_rate
from app.db import SessionLocal
from app.models import Category, Expense, Subscription, SubscriptionCharge, User
from app.scheduler import scheduler, subscriptions_job
from app.subscriptions import advance, process_due_subscriptions

# ---------- Date arithmetic ----------


def _chain(start: date, period: str, interval: int, anchor: int | None, n: int) -> list[str]:
    out, current = [], start
    for _ in range(n):
        current = advance(current, period, interval, anchor)
        out.append(current.isoformat())
    return out


def test_monthly_on_the_31st_uses_the_last_day_of_short_months() -> None:
    assert _chain(date(2026, 1, 31), "monthly", 1, 31, 5) == [
        "2026-02-28",
        "2026-03-31",
        "2026-04-30",
        "2026-05-31",
        "2026-06-30",
    ]


def test_monthly_anchor_survives_february_in_a_leap_year() -> None:
    assert _chain(date(2028, 1, 30), "monthly", 1, 30, 2) == ["2028-02-29", "2028-03-30"]
    assert _chain(date(2026, 12, 31), "monthly", 1, 31, 2) == ["2027-01-31", "2027-02-28"]


def test_monthly_with_interval() -> None:
    assert _chain(date(2026, 1, 31), "monthly", 3, 31, 4) == [
        "2026-04-30",
        "2026-07-31",
        "2026-10-31",
        "2027-01-31",
    ]


def test_yearly_on_february_29() -> None:
    assert _chain(date(2028, 2, 29), "yearly", 1, 29, 4) == [
        "2029-02-28",
        "2030-02-28",
        "2031-02-28",
        "2032-02-29",
    ]


def test_yearly_simple_and_with_interval() -> None:
    assert _chain(date(2026, 3, 15), "yearly", 1, 15, 2) == ["2027-03-15", "2028-03-15"]
    assert _chain(date(2026, 3, 15), "yearly", 2, 15, 2) == ["2028-03-15", "2030-03-15"]


def test_weekly_every_two_weeks_crosses_month_and_year() -> None:
    assert _chain(date(2026, 11, 30), "weekly", 2, None, 4) == [
        "2026-12-14",
        "2026-12-28",
        "2027-01-11",
        "2027-01-25",
    ]


# ---------- Charge job (isolated in-memory DB) ----------


@pytest.fixture()
def seeded(db_session: Session) -> tuple[Session, int]:
    db_session.add(User(username="admin", password_hash="x", role="admin"))
    category = Category(name="Подписки", color="#123456")
    db_session.add(category)
    db_session.commit()
    return db_session, category.id


def _sub(db: Session, category_id: int, **overrides: object) -> Subscription:
    values: dict[str, object] = {
        "name": "Netflix",
        "amount_type": "fixed",
        "amount": Decimal("4990"),
        "currency": "HUF",
        "category_id": category_id,
        "period": "monthly",
        "interval": 1,
        "anchor_day": 31,
        "next_payment_date": "2026-08-31",
    }
    values.update(overrides)
    sub = Subscription(**values)
    db.add(sub)
    db.commit()
    return sub


def _count(db: Session, model: type) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def test_job_catches_up_missed_dates_and_reruns_without_duplicates(
    seeded: tuple[Session, int],
) -> None:
    db, category_id = seeded
    sub = _sub(db, category_id)
    today = date(2026, 11, 12)

    assert process_due_subscriptions(db, today) == 3
    assert process_due_subscriptions(db, today) == 0  # second run: nothing new
    assert process_due_subscriptions(db, today) == 0

    charges = db.execute(select(SubscriptionCharge).order_by(SubscriptionCharge.due_date)).scalars()
    assert [(c.due_date, c.status) for c in charges] == [
        ("2026-08-31", "paid"),
        ("2026-09-30", "paid"),
        ("2026-10-31", "paid"),
    ]
    expenses = db.execute(select(Expense).order_by(Expense.occurred_at)).scalars().all()
    assert [e.occurred_at for e in expenses] == [
        "2026-08-31T10:00:00+00:00",  # 12:00 CEST
        "2026-09-30T10:00:00+00:00",
        "2026-10-31T11:00:00+00:00",  # 12:00 CET
    ]
    assert {e.source for e in expenses} == {"subscription"}
    assert {e.comment for e in expenses} == {"Подписка: Netflix"}
    assert {e.amount_huf for e in expenses} == {4990}
    db.refresh(sub)
    assert sub.next_payment_date == "2026-11-30"


def test_existing_charge_is_not_duplicated_when_the_date_was_not_advanced(
    seeded: tuple[Session, int],
) -> None:
    """E.g. a restore from a backup taken between two writes: the charge row wins."""
    db, category_id = seeded
    sub = _sub(db, category_id, next_payment_date="2026-11-01", anchor_day=1)
    assert process_due_subscriptions(db, date(2026, 11, 12)) == 1
    sub.next_payment_date = "2026-11-01"
    db.commit()
    assert process_due_subscriptions(db, date(2026, 11, 12)) == 0
    assert _count(db, SubscriptionCharge) == 1
    assert _count(db, Expense) == 1
    db.refresh(sub)
    assert sub.next_payment_date == "2026-12-01"


def test_variable_amount_creates_pending_charges_only(seeded: tuple[Session, int]) -> None:
    db, category_id = seeded
    _sub(db, category_id, amount_type="variable", amount=None, name="Электричество")
    assert process_due_subscriptions(db, date(2026, 10, 1)) == 2
    statuses = db.execute(select(SubscriptionCharge.status)).scalars().all()
    assert statuses == ["pending", "pending"]
    assert _count(db, Expense) == 0


def test_inactive_and_future_subscriptions_are_not_charged(seeded: tuple[Session, int]) -> None:
    db, category_id = seeded
    _sub(db, category_id, is_active=0)
    _sub(db, category_id, name="Future", next_payment_date="2026-11-13", anchor_day=13)
    assert process_due_subscriptions(db, date(2026, 11, 12)) == 0
    assert process_due_subscriptions(db, date(2026, 11, 13)) == 1  # due today counts


def test_weekly_interval_two_catch_up(seeded: tuple[Session, int]) -> None:
    db, category_id = seeded
    sub = _sub(
        db,
        category_id,
        period="weekly",
        interval=2,
        anchor_day=None,
        next_payment_date="2026-10-05",
    )
    assert process_due_subscriptions(db, date(2026, 11, 12)) == 3  # 10-05, 10-19, 11-02
    db.refresh(sub)
    assert sub.next_payment_date == "2026-11-16"


def test_failed_rate_lookup_rolls_back_and_retries(
    seeded: tuple[Session, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, category_id = seeded
    sub = _sub(
        db, category_id, currency="EUR", amount=Decimal("9.99"), next_payment_date="2026-11-01"
    )

    def offline(*args: object, **kwargs: object) -> None:
        from app.currency import RateUnavailableError

        raise RateUnavailableError("offline")

    monkeypatch.setattr("app.currency.fetch_rates", offline)
    assert process_due_subscriptions(db, date(2026, 11, 12)) == 0
    assert _count(db, SubscriptionCharge) == 0
    db.refresh(sub)
    assert sub.next_payment_date == "2026-11-01"

    upsert_rate(db, "2026-10-30", "EUR", Decimal("390"), "api")
    db.commit()
    assert process_due_subscriptions(db, date(2026, 11, 12)) == 1
    assert db.execute(select(Expense.amount_huf)).scalar_one() == 3896


# ---------- Scheduler ----------


def test_daily_job_runs_at_six_local_time(api: TestClient) -> None:
    job = scheduler.get_job("subscriptions")
    assert job is not None
    assert job.trigger.timezone == ZoneInfo("Europe/Budapest")
    assert job.next_run_time.astimezone(ZoneInfo("Europe/Budapest")).strftime("%H:%M") == "06:00"


def test_startup_job_is_idempotent(api: TestClient, category_id: int) -> None:
    db = SessionLocal()
    try:
        db.add(
            Subscription(
                name="Spotify",
                amount_type="fixed",
                amount=Decimal("2490"),
                currency="HUF",
                category_id=category_id,
                period="monthly",
                interval=1,
                anchor_day=5,
                next_payment_date="2026-09-05",
            )
        )
        db.commit()
    finally:
        db.close()
    subscriptions_job()
    subscriptions_job()
    db = SessionLocal()
    try:
        charges = db.execute(select(SubscriptionCharge.due_date)).scalars().all()
    finally:
        db.close()
    assert len(charges) == len(set(charges)) >= 3


# ---------- API ----------


@pytest.fixture()
def today(monkeypatch: pytest.MonkeyPatch) -> date:
    now = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(timeutil, "now_utc", lambda: now)
    return now.date()


def _create(client: TestClient, category_id: int, **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": "Netflix",
        "amount_type": "fixed",
        "amount": 4990,
        "currency": "HUF",
        "category_id": category_id,
        "period": "monthly",
        "next_payment_date": "2026-11-20",
    }
    body.update(overrides)
    resp = client.post("/api/subscriptions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_defaults_and_permissions(
    admin: TestClient, user: TestClient, bot: TestClient, category_id: int, today: date
) -> None:
    sub = _create(bot, category_id, next_payment_date="2026-11-30")
    assert sub["anchor_day"] == 30
    assert sub["interval"] == 1
    assert sub["remind_days_before"] == 3
    assert sub["is_active"] is True
    assert sub["category"]["id"] == category_id

    assert user.get("/api/subscriptions").status_code == 200
    assert user.get("/api/subscriptions/upcoming").status_code == 200
    assert user.get("/api/subscriptions/summary").status_code == 200
    body = {"name": "x", "amount": 1, "currency": "HUF", "category_id": category_id}
    assert (
        user.post("/api/subscriptions", json=body | {"next_payment_date": "2026-12-01"}).status_code
        == 403
    )
    assert user.patch(f"/api/subscriptions/{sub['id']}", json={"name": "y"}).status_code == 403
    assert user.delete(f"/api/subscriptions/{sub['id']}").status_code == 403
    assert (
        admin.patch(f"/api/subscriptions/{sub['id']}", json={"name": "Netflix 4K"}).json()["name"]
        == "Netflix 4K"
    )


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"amount": None}, 422),
        ({"amount_type": "variable"}, 422),
        ({"period": "weekly", "anchor_day": 3}, 422),
        ({"interval": 0}, 422),
        ({"anchor_day": 32}, 422),
        ({"category_id": 999999}, 404),
        ({"currency": "USD"}, 422),
    ],
)
def test_create_validation(
    admin: TestClient, category_id: int, today: date, overrides: dict, status: int
) -> None:
    body = {
        "name": "Bad",
        "amount": 100,
        "currency": "HUF",
        "category_id": category_id,
        "next_payment_date": "2026-12-01",
    } | overrides
    assert admin.post("/api/subscriptions", json=body).status_code == status


def test_due_subscription_is_charged_on_create(
    bot: TestClient, admin: TestClient, category_id: int, today: date
) -> None:
    sub = _create(bot, category_id, next_payment_date="2026-11-12", name="Gym")
    assert sub["next_payment_date"] == "2026-12-12"
    items = admin.get("/api/expenses", params={"date_from": "2026-11-12", "date_to": "2026-11-12"})
    [expense] = items.json()["items"]
    assert expense["source"] == "subscription"
    assert expense["comment"] == "Подписка: Gym"
    assert expense["occurred_at"] == "2026-11-12T11:00:00+00:00"


def test_upcoming_window_uses_remind_days_before(
    bot: TestClient, category_id: int, today: date
) -> None:
    _create(bot, category_id, name="Soon", next_payment_date="2026-11-15")
    _create(
        bot, category_id, name="Insurance", next_payment_date="2026-11-24", remind_days_before=14
    )
    _create(bot, category_id, name="Later", next_payment_date="2026-11-24")
    _create(
        bot, category_id, name="Weekly", period="weekly", interval=1, next_payment_date="2026-11-13"
    )
    body = bot.get("/api/subscriptions/upcoming", params={"days": 7}).json()
    assert body["today"] == "2026-11-12"
    got = [(i["name"], i["due_date"], i["days_left"], i["remind"]) for i in body["upcoming"]]
    # Window per subscription = max(days, remind_days_before):
    # - "Later" (12 days) is outside max(7, 3); "Insurance" (12 days) is inside max(7, 14);
    # - the second "Weekly" date (8 days) is outside max(7, 3).
    assert got == [
        ("Weekly", "2026-11-13", 1, True),
        ("Soon", "2026-11-15", 3, True),
        ("Insurance", "2026-11-24", 12, True),
    ]
    assert body["pending"] == []
    wider = bot.get("/api/subscriptions/upcoming", params={"days": 14}).json()["upcoming"]
    assert [(i["name"], i["due_date"], i["remind"]) for i in wider] == [
        ("Weekly", "2026-11-13", True),
        ("Soon", "2026-11-15", True),
        ("Weekly", "2026-11-20", False),
        ("Insurance", "2026-11-24", True),
        ("Later", "2026-11-24", False),
    ]  # 2026-11-27 (third weekly date) is 15 days away


def test_variable_charge_pay_and_skip(
    bot: TestClient, admin: TestClient, category_id: int, today: date
) -> None:
    _create(
        bot,
        category_id,
        name="Электричество",
        amount_type="variable",
        amount=None,
        next_payment_date="2026-10-12",
    )
    pending = bot.get("/api/subscriptions/upcoming").json()["pending"]
    assert [(c["due_date"], c["status"], c["amount"]) for c in pending] == [
        ("2026-10-12", "pending", None),
        ("2026-11-12", "pending", None),
    ]
    first, second = pending

    url = f"/api/subscriptions/charges/{first['id']}/pay"
    assert bot.post(url, json={"amount": 15200}).status_code == 422  # key required
    key = str(uuid.uuid4())
    paid = bot.post(url, json={"amount": 15200}, headers={"Idempotency-Key": key})
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    assert paid.json()["amount"] == 15200
    again = bot.post(url, json={"amount": 15200}, headers={"Idempotency-Key": key})
    assert again.status_code == 200
    assert again.json() == paid.json()
    other = bot.post(url, json={"amount": 15300}, headers={"Idempotency-Key": key})
    assert other.status_code == 409
    second_key = bot.post(
        url, json={"amount": 15200}, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert second_key.status_code == 409  # already paid

    expense = admin.get(
        "/api/expenses", params={"date_from": "2026-10-12", "date_to": "2026-10-12"}
    ).json()["items"]
    assert [(e["amount_huf"], e["source"], e["comment"]) for e in expense] == [
        (15200, "telegram", "Подписка: Электричество")
    ]
    assert expense[0]["id"] == paid.json()["expense_id"]

    skip_url = f"/api/subscriptions/charges/{second['id']}/skip"
    assert admin.post(skip_url).json()["status"] == "skipped"
    assert admin.post(skip_url).status_code == 200  # repeat is a no-op
    assert admin.post(f"/api/subscriptions/charges/{first['id']}/skip").status_code == 409
    assert bot.get("/api/subscriptions/upcoming").json()["pending"] == []
    assert (
        bot.post(
            "/api/subscriptions/charges/999999/pay",
            json={"amount": 1},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 404
    )


def test_site_payment_is_marked_as_subscription(
    admin: TestClient, category_id: int, today: date
) -> None:
    db = SessionLocal()
    try:
        upsert_rate(db, "2026-11-12", "EUR", Decimal("400"), "manual", force=True)
        db.commit()
    finally:
        db.close()
    _create(
        admin,
        category_id,
        name="Вода",
        amount_type="variable",
        amount=None,
        next_payment_date="2026-11-12",
    )
    [charge] = admin.get("/api/subscriptions/upcoming").json()["pending"]
    resp = admin.post(
        f"/api/subscriptions/charges/{charge['id']}/pay",
        json={"amount": 30, "currency": "EUR", "occurred_at": "2026-11-12T08:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["currency"] == "EUR"
    assert resp.json()["amount"] == 30
    expense = admin.get("/api/expenses", params={"date_from": "2026-11-12"}).json()["items"][0]
    assert expense["source"] == "subscription"
    assert expense["occurred_at"] == "2026-11-12T08:00:00+00:00"


def test_monthly_summary(bot: TestClient, admin: TestClient, category_id: int, today: date) -> None:
    db = SessionLocal()
    try:
        upsert_rate(db, "2026-11-12", "EUR", Decimal("400"), "manual", force=True)
        db.commit()
    finally:
        db.close()
    _create(bot, category_id, name="Monthly", amount=3000)
    _create(bot, category_id, name="Weekly", period="weekly", amount=1200)
    _create(
        bot,
        category_id,
        name="Yearly",
        period="yearly",
        amount=12000,
        next_payment_date="2027-01-01",
    )
    _create(bot, category_id, name="Quarterly EUR", amount=30, currency="EUR", interval=3)
    _create(bot, category_id, name="Unknown", amount_type="variable", amount=None)
    var = _create(
        bot,
        category_id,
        name="Gas",
        amount_type="variable",
        amount=None,
        next_payment_date="2026-11-12",
    )
    [charge] = [
        c
        for c in bot.get("/api/subscriptions/upcoming").json()["pending"]
        if c["subscription_id"] == var["id"]
    ]
    bot.post(
        f"/api/subscriptions/charges/{charge['id']}/pay",
        json={"amount": 9000},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    inactive = _create(bot, category_id, name="Old", amount=99999)
    bot.delete(f"/api/subscriptions/{inactive['id']}")

    summary = admin.get("/api/subscriptions/summary").json()
    items = {i["name"]: i for i in summary["items"]}
    assert "Old" not in items
    assert items["Monthly"]["monthly_huf"] == 3000
    assert items["Weekly"]["monthly_huf"] == 5200  # 1200 × 52 / 12
    assert items["Yearly"]["monthly_huf"] == 1000
    assert items["Quarterly EUR"]["monthly_huf"] == 4000  # 30 × 400 / 3
    assert items["Gas"]["basis"] == "last_paid" and items["Gas"]["monthly_huf"] == 9000
    assert items["Unknown"]["basis"] == "excluded" and items["Unknown"]["monthly_huf"] is None
    assert summary["monthly_total_huf"] == 3000 + 5200 + 1000 + 4000 + 9000
    assert (summary["active_count"], summary["included_count"], summary["excluded_count"]) == (
        6,
        5,
        1,
    )


def test_deactivate_and_reactivate(
    bot: TestClient,
    admin: TestClient,
    category_id: int,
    today: date,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub = _create(bot, category_id, name="Paused", next_payment_date="2026-11-20", anchor_day=20)
    resp = bot.delete(f"/api/subscriptions/{sub['id']}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert [s["id"] for s in bot.get("/api/subscriptions", params={"active": True}).json()] == []
    assert [s["id"] for s in bot.get("/api/subscriptions", params={"active": False}).json()] == [
        sub["id"]
    ]
    assert bot.get("/api/subscriptions/upcoming").json()["upcoming"] == []

    # Three months later the job runs: the inactive subscription is not charged.
    later = datetime(2027, 2, 10, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(timeutil, "now_utc", lambda: later)
    subscriptions_job()
    resp = bot.patch(f"/api/subscriptions/{sub['id']}", json={"is_active": True})
    assert resp.status_code == 200
    assert resp.json()["next_payment_date"] == "2027-02-20"  # missed dates are skipped
    assert admin.get("/api/expenses").json()["total"] == 0


def test_patch_rules(bot: TestClient, category_id: int, today: date) -> None:
    sub = _create(bot, category_id, next_payment_date="2026-11-30")
    sid = sub["id"]
    weekly = bot.patch(f"/api/subscriptions/{sid}", json={"period": "weekly"}).json()
    assert weekly["anchor_day"] is None
    monthly = bot.patch(
        f"/api/subscriptions/{sid}", json={"period": "monthly", "next_payment_date": "2026-12-31"}
    ).json()
    assert monthly["anchor_day"] == 31
    variable = bot.patch(f"/api/subscriptions/{sid}", json={"amount_type": "variable"}).json()
    assert variable["amount"] is None
    assert bot.patch(f"/api/subscriptions/{sid}", json={"amount_type": "fixed"}).status_code == 422
    assert bot.patch(f"/api/subscriptions/{sid}", json={"name": None}).status_code == 422
    assert bot.patch(f"/api/subscriptions/{sid}", json={"is_active": None}).json()["is_active"]
    assert bot.patch("/api/subscriptions/999999", json={"name": "x"}).status_code == 404
