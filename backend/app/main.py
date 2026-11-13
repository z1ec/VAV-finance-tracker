import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import API_VERSION
from app.backup import restrict_data_permissions
from app.config import settings
from app.currency import RateUnavailableError, ensure_today_rates
from app.db import SessionLocal
from app.deps import origin_guard, token_guard
from app.limits import BodySizeLimitMiddleware
from app.logging_config import configure_logging
from app.migrate import run_migrations
from app.models import ExchangeRate
from app.routers import auth, budgets, categories, expenses, rates, stats, subscriptions
from app.scheduler import shutdown_scheduler, start_scheduler, subscriptions_job
from app.security import check_secrets
from app.seed import run_seed

configure_logging()
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "startup",
        extra={"version": settings.app_version, "git_sha": settings.git_sha},
    )
    check_secrets(settings)
    # New files (database, -wal/-shm, backups) are private to the container user.
    os.umask(0o077)
    restrict_data_permissions(settings.db_path, settings.backup_dir)
    run_migrations()
    db = SessionLocal()
    try:
        run_seed(db)
        try:
            ensure_today_rates(db)
        except RateUnavailableError as exc:
            logger.warning("startup_rates_unavailable", extra={"error": str(exc)})
    finally:
        db.close()
    # Catch up on subscription charges missed while the app was down (idempotent).
    subscriptions_job()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="Expense Tracker",
    version=API_VERSION,
    description=(
        "Multi-currency expense tracker (HUF/EUR/RUB, base HUF). Browser clients use the "
        "session cookie; the Momentum bot uses a service token from the internal Docker "
        "network only — see docs/API_BOT.md. Bare `YYYY-MM-DD` dates are local days in "
        "Europe/Budapest."
    ),
    lifespan=lifespan,
    # Bot token allowlist: any route not in deps.TOKEN_ALLOWED_ROUTES answers 403 to a token.
    # Origin check: cross-site state-changing requests are refused (CSRF defence in depth).
    dependencies=[Depends(token_guard), Depends(origin_guard)],
    # Interactive docs only for local development; the contract is docs/openapi.json.
    # (Caddy proxies only /api/*, so these paths never reached the internet anyway.)
    docs_url="/docs" if settings.is_local else None,
    redoc_url="/redoc" if settings.is_local else None,
    openapi_url="/openapi.json" if settings.is_local else None,
)


app.add_middleware(BodySizeLimitMiddleware)


@app.exception_handler(RateUnavailableError)
async def rate_unavailable_handler(request: Request, exc: RateUnavailableError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(x) for x in first["loc"] if x != "body")
        message = f"{loc}: {first['msg']}" if loc else first["msg"]
    else:
        message = "Ошибка валидации"
    return JSONResponse(status_code=422, content={"detail": message})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled_error", extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(expenses.router, prefix="/api/expenses", tags=["expenses"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(rates.router, prefix="/api/rates", tags=["rates"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(budgets.router, prefix="/api/budgets", tags=["budgets"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["subscriptions"])


@app.get("/api/health", tags=["health"])
def health():
    db = SessionLocal()
    try:
        db.execute(select(1))
        db_status = "ok"
    except Exception:
        db_status = "error"
    last_rate = db.execute(
        select(ExchangeRate).order_by(ExchangeRate.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()
    db.close()
    return {
        "status": "ok",
        "db": db_status,
        "version": settings.app_version,
        "git_sha": settings.git_sha,
        "last_rates_update": last_rate.fetched_at if last_rate else None,
    }
