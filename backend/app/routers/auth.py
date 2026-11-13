from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    create_session_token,
    is_rate_limited,
    record_failed_attempt,
    reset_attempts,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.deps import get_client_ip, get_current_user
from app.models import User
from app.schemas import LoginRequest, UserOut

router = APIRouter()


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if is_rate_limited(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа. Попробуйте позже.",
        )

    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        record_failed_attempt(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль"
        )

    reset_attempts(ip)
    token = create_session_token(user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_days * 86400,
        **_cookie_flags(),
    )
    return user


def _cookie_flags() -> dict[str, Any]:
    """HttpOnly, SameSite=Lax, Secure on a real domain; shared by login and logout."""
    return {"httponly": True, "samesite": "lax", "secure": not settings.is_local, "path": "/"}


@router.post("/logout")
def logout(response: Response):
    # Same flags as on login, so the browser matches and drops the cookie. The signed token
    # itself stays valid until it expires (stateless sessions, see docs/SECURITY.md).
    response.delete_cookie(settings.session_cookie_name, **_cookie_flags())
    return {"detail": "ok"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
