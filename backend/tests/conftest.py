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
