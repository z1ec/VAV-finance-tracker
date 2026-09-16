from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.currency import RateUnavailableError, refresh_rates_for_date, to_decimal, upsert_rate
from app.db import get_db
from app.deps import get_current_user, require_admin
from app.models import ExchangeRate, User
from app.schemas import RateManualCreate, RateOut

router = APIRouter()


@router.get("/latest")
def latest_rates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = datetime.now(timezone.utc).date().isoformat()
    result: dict[str, float | str] = {"HUF": 1.0}
    dates_found: list[str] = []
    for cur in ("EUR", "RUB"):
        row = db.execute(
            select(ExchangeRate)
            .where(ExchangeRate.currency == cur, ExchangeRate.date <= today)
            .order_by(ExchangeRate.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row:
            result[cur] = float(row.rate_to_huf)
            dates_found.append(row.date)
    result["date"] = max(dates_found) if dates_found else today
    return result


@router.get("", response_model=list[RateOut])
def rates_for_date(date: str = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(ExchangeRate).where(ExchangeRate.date == date)).scalars().all()
    return rows


@router.post("", response_model=RateOut, status_code=201)
def set_manual_rate(payload: RateManualCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    row = upsert_rate(db, payload.date, payload.currency, to_decimal(payload.rate_to_huf), "manual", force=True)
    db.commit()
    return row


@router.post("/refresh")
def refresh(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        rates = refresh_rates_for_date(db, today)
    except RateUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {cur: float(rate) for cur, rate in rates.items()}
