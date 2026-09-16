import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.currency import RateUnavailableError, ensure_today_rates
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.models import ExchangeRate
from app.routers import auth, budgets, categories, expenses, rates, stats
from app.scheduler import shutdown_scheduler, start_scheduler
from app.seed import run_seed

configure_logging()
logger = logging.getLogger("app")

BACKEND_DIR = Path(__file__).resolve().parent.parent


def run_migrations() -> None:
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Expense Tracker", lifespan=lifespan)


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


@app.get("/api/health")
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
        "last_rates_update": last_rate.fetched_at if last_rate else None,
    }
