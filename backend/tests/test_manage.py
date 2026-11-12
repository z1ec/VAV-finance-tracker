"""`python -m app.manage set-password`: from .env values or stdin, atomic, never prints secrets."""

import io
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.manage as manage
from app.auth import hash_password, verify_password
from app.config import Settings
from app.db import Base
from app.models import User

NEW_ADMIN = "new-admin-password-123"
NEW_USER1 = "new-user1-password-456"


@pytest.fixture()
def session_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for name in ("boss", "alice", "bob"):
            db.add(User(username=name, password_hash=hash_password("old-password"), role="user"))
        db.commit()
    monkeypatch.setattr(manage, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _cfg(**overrides: str) -> Settings:
    values = {
        "admin_username": "boss",
        "admin_password": NEW_ADMIN,
        "user1_username": "alice",
        "user1_password": NEW_USER1,
        "user2_username": "bob",
        "user2_password": "canary7Q",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _password_ok(factory: sessionmaker[Session], username: str, password: str) -> bool:
    with factory() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one()
        return verify_password(password, user.password_hash)


def test_set_password_from_env(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    assert manage.main(["set-password", "--from-env", "ADMIN", "USER1"], cfg=_cfg()) == 0
    assert _password_ok(session_factory, "boss", NEW_ADMIN)
    assert _password_ok(session_factory, "alice", NEW_USER1)
    assert _password_ok(session_factory, "bob", "old-password")  # untouched
    out = capsys.readouterr().out
    assert "boss" in out and "alice" in out
    assert NEW_ADMIN not in out and NEW_USER1 not in out


def test_set_password_from_stdin(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("stdin-password-789\n"))
    assert manage.main(["set-password", "--username", "bob"], cfg=_cfg()) == 0
    assert _password_ok(session_factory, "bob", "stdin-password-789")


def test_weak_password_is_rejected_and_nothing_changes(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(manage.CommandError) as excinfo:
        manage.main(["set-password", "--from-env", "ADMIN", "USER2"], cfg=_cfg())
    assert "canary7Q" not in str(excinfo.value)
    # ADMIN was valid but the batch is atomic.
    assert _password_ok(session_factory, "boss", "old-password")


def test_unknown_user(session_factory: sessionmaker[Session]) -> None:
    cfg = _cfg(admin_username="ghost")
    with pytest.raises(manage.CommandError, match="ghost"):
        manage.main(["set-password", "--from-env", "ADMIN"], cfg=cfg)


def test_unknown_prefix_and_unset_user(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(manage.CommandError, match="unknown prefix"):
        manage.main(["set-password", "--from-env", "ROOT"], cfg=_cfg())
    with pytest.raises(manage.CommandError, match="not set"):
        manage.main(["set-password", "--from-env", "USER2"], cfg=_cfg(user2_username=""))
