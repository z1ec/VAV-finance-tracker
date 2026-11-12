import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent / "test_data" / "test.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ.setdefault("DB_PATH", str(TEST_DB))
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-pass")
os.environ.setdefault("USER1_USERNAME", "user1")
os.environ.setdefault("USER1_PASSWORD", "user1-pass")
os.environ.setdefault("USER2_USERNAME", "user2")
os.environ.setdefault("USER2_PASSWORD", "user2-pass")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import models  # noqa: E402,F401  registers tables on Base.metadata
from app.db import Base  # noqa: E402


@pytest.fixture()
def db_session():
    """An isolated in-memory database session, independent of the app's configured engine."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------- API fixtures shared by the stage 2a tests ----------

import hashlib  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from typing import Any  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

BOT_TOKEN = "bot-token-for-tests-0123456789abcdef"
BOT_NETWORK = "172.20.0.0/16"
BOT_IP = "172.20.0.5"
OUTSIDE_IP = "10.9.8.7"
ADMIN_CREDS = ("admin", "admin-pass")
USER_CREDS = ("user1", "user1-pass")


def with_peer(asgi_app: Any, host: str) -> Callable[..., Any]:
    """Wrap the ASGI app so requests appear to come from TCP peer ``host``."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (host, 50000)}
        await asgi_app(scope, receive, send)

    return wrapped


@pytest.fixture()
def bot_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(
        settings, "bot_api_token_sha256", hashlib.sha256(BOT_TOKEN.encode()).hexdigest()
    )
    monkeypatch.setattr(settings, "bot_api_username", "admin")
    monkeypatch.setattr(settings, "bot_api_allowed_networks", BOT_NETWORK)


def clean_data() -> None:
    from app.db import SessionLocal
    from app.models import Expense, Subscription, SubscriptionCharge

    db = SessionLocal()
    try:
        db.execute(delete(SubscriptionCharge))
        db.execute(delete(Subscription))
        db.execute(delete(Expense))
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch, bot_settings: None) -> Iterator[TestClient]:
    """Runs the app lifespan once (migrations, seed) on a clean set of expenses/subscriptions."""
    import app.main as main_module
    from app import auth

    monkeypatch.setattr(main_module, "ensure_today_rates", lambda db: False)
    auth._login_attempts.clear()
    with TestClient(main_module.app) as client:
        clean_data()
        yield client
    auth._login_attempts.clear()


def login_client(creds: tuple[str, str]) -> TestClient:
    import app.main as main_module

    client = TestClient(main_module.app)
    resp = client.post("/api/auth/login", json={"username": creds[0], "password": creds[1]})
    assert resp.status_code == 200, resp.text
    return client


def token_client(host: str = BOT_IP, token: str = BOT_TOKEN, **headers: str) -> TestClient:
    import app.main as main_module

    return TestClient(
        with_peer(main_module.app, host),
        headers={"Authorization": f"Bearer {token}", **headers},
    )


@pytest.fixture()
def admin(api: TestClient) -> TestClient:
    return login_client(ADMIN_CREDS)


@pytest.fixture()
def user(api: TestClient) -> TestClient:
    return login_client(USER_CREDS)


@pytest.fixture()
def bot(api: TestClient) -> TestClient:
    return token_client()


@pytest.fixture()
def category_id(api: TestClient) -> int:
    return int(login_client(ADMIN_CREDS).get("/api/categories").json()[0]["id"])
