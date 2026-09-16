from datetime import date as Date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExchangeRate

CURRENCIES = ("HUF", "EUR", "RUB")
DECIMALS = {"HUF": 0, "EUR": 2, "RUB": 2}


class RateUnavailableError(Exception):
    """Raised when no exchange rate can be found or fetched for a currency/date."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_amount(value: Decimal, currency: str) -> Decimal:
    """Round to the number of decimal places appropriate for `currency`."""
    decimals = DECIMALS[currency]
    quant = Decimal(1).scaleb(-decimals)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def convert_to_huf(amount: Decimal, rate_to_huf: Decimal) -> int:
    """amount * rate_to_huf, rounded to the nearest whole HUF."""
    huf = to_decimal(amount) * to_decimal(rate_to_huf)
    return int(round_amount(huf, "HUF"))


def convert_from_huf(amount_huf: Decimal, rate_to_huf: Decimal, target_currency: str) -> Decimal:
    if target_currency == "HUF":
        return round_amount(to_decimal(amount_huf), "HUF")
    value = to_decimal(amount_huf) / to_decimal(rate_to_huf)
    return round_amount(value, target_currency)


# ---------- Fetching from external APIs ----------

def _fetch_primary(date_str: str) -> dict[str, Decimal]:
    url = settings.rates_primary_url.format(date=date_str)
    resp = httpx.get(url, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    currencies = data.get("huf", data)
    result = {"HUF": Decimal("1")}
    for cur in ("EUR", "RUB"):
        key = cur.lower()
        if key not in currencies or not currencies[key]:
            raise RateUnavailableError(f"Первичный источник не содержит курс {cur}")
        units_per_huf = to_decimal(currencies[key])
        result[cur] = Decimal("1") / units_per_huf
    return result


def _fetch_fallback() -> dict[str, Decimal]:
    resp = httpx.get(settings.rates_fallback_url, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    rates = data.get("rates", {})
    result = {"HUF": Decimal("1")}
    for cur in ("EUR", "RUB"):
        if cur not in rates or not rates[cur]:
            raise RateUnavailableError(f"Резервный источник не содержит курс {cur}")
        units_per_huf = to_decimal(rates[cur])
        result[cur] = Decimal("1") / units_per_huf
    return result


def fetch_rates(date_str: str = "latest") -> dict[str, Decimal]:
    """Fetch {currency: rate_to_huf} from the primary API, falling back to the secondary one."""
    try:
        return _fetch_primary(date_str)
    except Exception:
        pass
    try:
        return _fetch_fallback()
    except Exception:
        pass
    raise RateUnavailableError(f"Не удалось получить курсы валют (дата: {date_str})")


# ---------- Persistence ----------

def upsert_rate(
    db: Session,
    date_str: str,
    currency: str,
    rate_to_huf: Decimal,
    source: str,
    force: bool = False,
) -> ExchangeRate:
    """Insert or update a rate row. Manual rates are never silently overwritten
    by an automatic (source='api') write unless `force` is explicitly passed."""
    existing = db.get(ExchangeRate, {"date": date_str, "currency": currency})
    if existing:
        if existing.source == "manual" and source == "api" and not force:
            return existing
        existing.rate_to_huf = rate_to_huf
        existing.source = source
        existing.fetched_at = utcnow_iso()
        row = existing
    else:
        row = ExchangeRate(
            date=date_str,
            currency=currency,
            rate_to_huf=rate_to_huf,
            source=source,
            fetched_at=utcnow_iso(),
        )
        db.add(row)
    db.flush()
    return row


def refresh_rates_for_date(db: Session, date_str: str, force: bool = False) -> dict[str, Decimal]:
    """Fetch current rates and store EUR/RUB for `date_str`. Returns fetched rates."""
    rates = fetch_rates(date_str if date_str != "latest" else "latest")
    target_date = date_str if date_str != "latest" else datetime.now(timezone.utc).date().isoformat()
    for cur in ("EUR", "RUB"):
        if cur in rates:
            upsert_rate(db, target_date, cur, rates[cur], "api", force=force)
    db.commit()
    return rates


def ensure_today_rates(db: Session) -> bool:
    """Called on startup: if today has no EUR/RUB rate rows yet, fetch them. Returns True if fetched."""
    today = datetime.now(timezone.utc).date().isoformat()
    existing_currencies = {
        row.currency
        for row in db.execute(
            select(ExchangeRate).where(ExchangeRate.date == today)
        ).scalars()
    }
    if {"EUR", "RUB"}.issubset(existing_currencies):
        return False
    refresh_rates_for_date(db, today)
    return True


# ---------- Rate lookup for a specific expense ----------

def get_rate_for_date(db: Session, currency: str, target_date: Date) -> tuple[Decimal, str, str]:
    """Resolve (rate_to_huf, source, date_used) for `currency` on `target_date`
    following the priority order from the spec:
      1) rate for the exact date
      2) nearest previous available rate
      3) fetch from the API for that date
      4) raise RateUnavailableError
    """
    if currency == "HUF":
        return Decimal("1"), "api", target_date.isoformat()

    date_str = target_date.isoformat()

    row = db.get(ExchangeRate, {"date": date_str, "currency": currency})
    if row:
        return to_decimal(row.rate_to_huf), row.source, row.date

    row = db.execute(
        select(ExchangeRate)
        .where(ExchangeRate.currency == currency, ExchangeRate.date <= date_str)
        .order_by(ExchangeRate.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row:
        return to_decimal(row.rate_to_huf), row.source, row.date

    try:
        rates = fetch_rates(date_str)
    except RateUnavailableError:
        rates = None
    if rates and currency in rates:
        rate = rates[currency]
        upsert_rate(db, date_str, currency, rate, "api")
        db.commit()
        return rate, "api", date_str

    raise RateUnavailableError(
        f"Курс {currency} на {date_str} недоступен. Укажите курс вручную."
    )


def resolve_expense_rate(
    db: Session,
    currency: str,
    occurred_at: datetime,
    manual_rate: Decimal | None,
) -> tuple[Decimal, str]:
    """Determine (rate_to_huf, rate_source) for a new/updated expense."""
    if manual_rate is not None:
        return to_decimal(manual_rate), "manual"
    if currency == "HUF":
        return Decimal("1"), "api"
    target_date = occurred_at.date() if isinstance(occurred_at, datetime) else occurred_at
    rate, source, _ = get_rate_for_date(db, currency, target_date)
    return rate, source
