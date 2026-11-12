"""Creating expenses: timestamps, rate resolution and idempotency (ADR-0005).

``Idempotency-Key`` (UUID) is stored in ``expenses.client_request_id`` (unique) together with a
SHA-256 fingerprint of the request content. Repeating a request with the same key and content
returns the existing row; the same key with different content is a conflict (409).
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.currency import RateUnavailableError, convert_to_huf, resolve_expense_rate, to_decimal
from app.models import Expense

TZ_REQUIRED_DETAIL = "occurred_at must include a timezone offset"


def parse_idempotency_key(value: str | None, required: bool) -> str | None:
    if value is None or not value.strip():
        if required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Idempotency-Key header is required for the bot token",
            )
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must be a UUID",
        ) from exc


def normalize_decimal(value: float | Decimal | None) -> str | None:
    if value is None:
        return None
    return format(to_decimal(value).normalize(), "f")


def request_fingerprint(operation: str, fields: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"op": operation, **fields}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_by_key(db: Session, key: str) -> Expense | None:
    return db.execute(select(Expense).where(Expense.client_request_id == key)).scalar_one_or_none()


def ensure_same_request(existing: Expense, fingerprint: str) -> None:
    if existing.client_request_hash != fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key уже использован для другого запроса",
        )


def resolve_occurred_at(value: datetime | None, via_token: bool, default: datetime) -> datetime:
    """Aware UTC timestamp. Naive input: 422 for the bot token, UTC for the site (as before)."""
    if value is None:
        return default.astimezone(UTC)
    if value.tzinfo is None:
        if via_token:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=TZ_REQUIRED_DETAIL
            )
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_expense(
    db: Session,
    *,
    amount: float | Decimal,
    currency: str,
    category_id: int,
    comment: str | None,
    occurred_at: datetime,
    user_id: int,
    source: str,
    manual_rate: float | Decimal | None = None,
    client_request_id: str | None = None,
    client_request_hash: str | None = None,
) -> Expense:
    """Resolve the rate and add a new expense to the session (not committed)."""
    amount_dec = to_decimal(amount)
    manual = to_decimal(manual_rate) if manual_rate is not None else None
    try:
        rate, rate_source = resolve_expense_rate(db, currency, occurred_at, manual)
    except RateUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    expense = Expense(
        amount=amount_dec,
        currency=currency,
        rate_to_huf=rate,
        amount_huf=convert_to_huf(amount_dec, rate),
        rate_source=rate_source,
        category_id=category_id,
        comment=comment,
        occurred_at=occurred_at.astimezone(UTC).isoformat(),
        created_by=user_id,
        source=source,
        client_request_id=client_request_id,
        client_request_hash=client_request_hash,
    )
    db.add(expense)
    return expense


def commit_idempotent[T](
    db: Session, key: str | None, fingerprint: str, on_replay: Callable[[Expense], T]
) -> T | None:
    """Commit; if a parallel request with the same key won the race, return its replay result.

    Returns ``None`` when the commit succeeded (the caller returns its own new object).
    """
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = find_by_key(db, key) if key else None
        if existing is None:
            raise
        ensure_same_request(existing, fingerprint)
        return on_replay(existing)
    return None
