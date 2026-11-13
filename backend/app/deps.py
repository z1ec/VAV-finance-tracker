"""Authentication dependencies: browser session cookie or the bot's service token.

The bot token (``Authorization: Bearer …``, see ADR-0004) is accepted only
- from a TCP peer inside ``BOT_API_ALLOWED_NETWORKS`` and
- when the request carries no proxy headers (anything that came through Caddy has them),
and only on the endpoints listed in ``TOKEN_ALLOWED_ROUTES``; everything else answers 403.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    bot_token_matches,
    is_bot_network,
    is_rate_limited,
    read_session_token,
    record_failed_attempt,
)
from app.config import settings
from app.db import get_db
from app.models import User

logger = logging.getLogger("app.auth")

# (method, route path) pairs the bot token may call. Anything else with a token → 403.
TOKEN_ALLOWED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/categories"),
        ("POST", "/api/expenses"),
        ("PATCH", "/api/expenses/{expense_id}"),
        ("DELETE", "/api/expenses/{expense_id}"),
        ("POST", "/api/expenses/{expense_id}/restore"),
        ("GET", "/api/stats/summary"),
        ("GET", "/api/stats/by-category"),
        ("GET", "/api/stats/budget"),
        ("GET", "/api/subscriptions"),
        ("POST", "/api/subscriptions"),
        ("GET", "/api/subscriptions/upcoming"),
        ("GET", "/api/subscriptions/summary"),
        ("PATCH", "/api/subscriptions/{subscription_id}"),
        ("DELETE", "/api/subscriptions/{subscription_id}"),
        ("POST", "/api/subscriptions/charges/{charge_id}/pay"),
        ("POST", "/api/subscriptions/charges/{charge_id}/skip"),
    }
)

# Headers a reverse proxy adds; their presence means the request did not come straight from
# the internal network.
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


@dataclass(frozen=True)
class Actor:
    user: User
    via_token: bool

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _unauthorized(detail: str, bearer: bool = False) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if bearer else None
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers=headers)


def _template_regex(template: str) -> re.Pattern[str]:
    """``/api/expenses/{expense_id}`` → ``^/api/expenses/[^/]+$`` (one path segment per param)."""
    parts = re.split(r"(\{[^}/]+\})", template)
    body = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{body}$")


# Matched against the request path itself, not ``scope["route"]``: what a route object exposes
# changed between FastAPI versions (routes of included routers lost their prefix in 0.14x),
# and the guard must not depend on that. Fails closed: an unknown path is never allowed.
_TOKEN_ALLOWED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, _template_regex(path)) for method, path in sorted(TOKEN_ALLOWED_ROUTES)
)


def token_route_allowed(method: str, path: str) -> bool:
    return any(method == m and pattern.match(path) for m, pattern in _TOKEN_ALLOWED_PATTERNS)


def token_guard(request: Request) -> None:
    """App-wide dependency: a request with ``Authorization`` may only reach allowed routes."""
    if "authorization" not in request.headers:
        return
    # scope["path"] is the decoded path the router matched (the app has no root_path).
    if not token_route_allowed(request.method, request.scope.get("path", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Эндпоинт недоступен для токена бота"
        )


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def origin_guard(request: Request) -> None:
    """App-wide CSRF defence in depth for cookie sessions (``SameSite=Lax`` is the first line).

    A state-changing request that a browser marks as coming from another site, or whose
    ``Origin`` is not this host, is refused. Requests without these headers (the bot, curl,
    tests) are not affected; the bot's token requests carry no cookie anyway.
    """
    if request.method not in UNSAFE_METHODS:
        return
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site in {"cross-site", "same-site"}:
        raise _csrf_refused()
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    parsed = urlsplit(origin)
    if origin == "null" or parsed.scheme not in {"http", "https"} or parsed.netloc != host:
        raise _csrf_refused()


def _csrf_refused() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Запрос с другого сайта отклонён"
    )


def _authenticate_token(request: Request, db: Session, header: str) -> Actor:
    ip = get_client_ip(request)
    if is_rate_limited(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа. Попробуйте позже.",
        )
    scheme, _, token = header.partition(" ")
    token = token.strip()
    peer = request.client.host if request.client else None
    from_proxy = any(name in request.headers for name in PROXY_HEADERS)
    if (
        scheme.lower() != "bearer"
        or not token
        or from_proxy
        or not is_bot_network(peer)
        or not bot_token_matches(token)
    ):
        record_failed_attempt(ip)
        # Never log the token itself; the reason is enough to debug the network setup.
        logger.warning(
            "bot_token_rejected",
            extra={"peer": peer, "from_proxy": from_proxy, "path": request.url.path},
        )
        raise _unauthorized("Недействительный токен", bearer=True)

    user = db.execute(
        select(User).where(User.username == settings.bot_api_username)
    ).scalar_one_or_none()
    if user is None or user.role != "admin":
        logger.error("bot_user_misconfigured", extra={"setting": "BOT_API_USERNAME"})
        raise _unauthorized("Пользователь бота не настроен", bearer=True)
    return Actor(user=user, via_token=True)


def get_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    """Resolve the caller from the bot token (if ``Authorization`` is present) or the cookie."""
    header = request.headers.get("authorization")
    if header is not None:
        return _authenticate_token(request, db, header)

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise _unauthorized("Не авторизован")
    user_id = read_session_token(token)
    if user_id is None:
        raise _unauthorized("Сессия недействительна")
    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized("Пользователь не найден")
    return Actor(user=user, via_token=False)


def get_current_user(actor: Actor = Depends(get_actor)) -> User:
    """Cookie session only (the token is rejected even if the route guard was bypassed)."""
    if actor.via_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Эндпоинт недоступен для токена бота"
        )
    return actor.user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права администратора"
        )
    return user


def get_reader(actor: Actor = Depends(get_actor)) -> Actor:
    """Any signed-in user or the bot token (read-only endpoints from the token allowlist)."""
    return actor


def require_writer(actor: Actor = Depends(get_actor)) -> Actor:
    """Admin cookie or the bot token (write endpoints from the token allowlist)."""
    if not actor.via_token and not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права администратора"
        )
    return actor
