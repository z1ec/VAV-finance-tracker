from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.currency import (
    RateUnavailableError,
    convert_to_huf,
    get_rate_for_date,
    resolve_expense_rate,
    round_amount,
    upsert_rate,
)


def test_round_amount_huf_has_no_decimals():
    assert round_amount(Decimal("123.6"), "HUF") == Decimal("124")
    assert round_amount(Decimal("123.4"), "HUF") == Decimal("123")
    assert round_amount(Decimal("123.5"), "HUF") == Decimal("124")


def test_round_amount_eur_rub_have_two_decimals():
    assert round_amount(Decimal("12.345"), "EUR") == Decimal("12.35")
    assert round_amount(Decimal("12.344"), "EUR") == Decimal("12.34")
    assert round_amount(Decimal("7.005"), "RUB") == Decimal("7.01")


def test_convert_to_huf_rounds_to_whole_forint():
    amount_huf = convert_to_huf(Decimal("10"), Decimal("395.256"))
    assert amount_huf == 3953
    assert isinstance(amount_huf, int)


def test_convert_to_huf_from_huf_is_identity():
    assert convert_to_huf(Decimal("1500"), Decimal("1")) == 1500


def test_get_rate_for_date_exact_match(db_session):
    upsert_rate(db_session, "2026-01-15", "EUR", Decimal("390"), "api")
    db_session.commit()

    rate, source, date_used = get_rate_for_date(db_session, "EUR", date(2026, 1, 15))
    assert rate == Decimal("390")
    assert source == "api"
    assert date_used == "2026-01-15"


def test_get_rate_for_date_falls_back_to_nearest_previous(db_session):
    upsert_rate(db_session, "2026-01-10", "EUR", Decimal("388"), "api")
    db_session.commit()

    rate, source, date_used = get_rate_for_date(db_session, "EUR", date(2026, 1, 15))
    assert rate == Decimal("388")
    assert date_used == "2026-01-10"


def test_get_rate_for_date_ignores_future_rates(db_session, monkeypatch):
    import app.currency as currency_module

    def fake_fetch(date_str):
        raise currency_module.RateUnavailableError("network down")

    monkeypatch.setattr(currency_module, "fetch_rates", fake_fetch)

    upsert_rate(db_session, "2026-01-20", "EUR", Decimal("399"), "api")
    db_session.commit()

    with pytest.raises(RateUnavailableError):
        get_rate_for_date(db_session, "EUR", date(2026, 1, 15))


def test_get_rate_for_date_fetches_from_api_when_missing(db_session, monkeypatch):
    import app.currency as currency_module

    def fake_fetch(date_str):
        assert date_str == "2026-01-15"
        return {"HUF": Decimal("1"), "EUR": Decimal("392.5"), "RUB": Decimal("4.2")}

    monkeypatch.setattr(currency_module, "fetch_rates", fake_fetch)

    rate, source, date_used = get_rate_for_date(db_session, "EUR", date(2026, 1, 15))
    assert rate == Decimal("392.5")
    assert source == "api"
    assert date_used == "2026-01-15"

    # the fetched rate should now be persisted
    rate_again, source_again, _ = get_rate_for_date(db_session, "EUR", date(2026, 1, 15))
    assert rate_again == Decimal("392.5")
    assert source_again == "api"


def test_get_rate_for_date_raises_when_nothing_available(db_session, monkeypatch):
    import app.currency as currency_module

    def fake_fetch(date_str):
        raise currency_module.RateUnavailableError("network down")

    monkeypatch.setattr(currency_module, "fetch_rates", fake_fetch)

    with pytest.raises(RateUnavailableError):
        get_rate_for_date(db_session, "EUR", date(2026, 1, 15))


def test_manual_rate_is_not_overwritten_by_automatic_api_write(db_session):
    upsert_rate(db_session, "2026-02-01", "EUR", Decimal("400"), "manual")
    db_session.commit()

    upsert_rate(db_session, "2026-02-01", "EUR", Decimal("410"), "api")
    db_session.commit()

    rate, source, _ = get_rate_for_date(db_session, "EUR", date(2026, 2, 1))
    assert rate == Decimal("400")
    assert source == "manual"


def test_manual_rate_can_be_explicitly_forced(db_session):
    upsert_rate(db_session, "2026-02-01", "EUR", Decimal("400"), "manual")
    db_session.commit()

    upsert_rate(db_session, "2026-02-01", "EUR", Decimal("420"), "manual", force=True)
    db_session.commit()

    rate, source, _ = get_rate_for_date(db_session, "EUR", date(2026, 2, 1))
    assert rate == Decimal("420")
    assert source == "manual"


def test_resolve_expense_rate_prefers_explicit_manual_rate(db_session):
    upsert_rate(db_session, "2026-03-01", "EUR", Decimal("350"), "api")
    db_session.commit()

    rate, source = resolve_expense_rate(
        db_session, "EUR", datetime(2026, 3, 1, tzinfo=timezone.utc), Decimal("360")
    )
    assert rate == Decimal("360")
    assert source == "manual"


def test_resolve_expense_rate_huf_is_always_one_without_db_lookup(db_session):
    rate, source = resolve_expense_rate(db_session, "HUF", datetime(2026, 3, 1, tzinfo=timezone.utc), None)
    assert rate == Decimal("1")
    assert source == "api"
