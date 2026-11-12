import hashlib
import hmac
import time
from collections import defaultdict
from ipaddress import ip_address

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="session")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def create_session_token(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def read_session_token(token: str) -> int | None:
    max_age = settings.session_max_age_days * 86400
    try:
        data = _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user_id")


# ---------- Bot service token ----------


def bot_token_matches(token: str) -> bool:
    """Constant-time comparison of the token's SHA-256 with ``BOT_API_TOKEN_SHA256``."""
    expected = settings.bot_api_token_sha256.strip().lower()
    if not expected or not token:
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, expected)


def is_bot_network(host: str | None) -> bool:
    """True if the TCP peer ``host`` is inside ``BOT_API_ALLOWED_NETWORKS`` (empty → never)."""
    if not host:
        return False
    try:
        address = ip_address(host)
        networks = settings.bot_networks
    except ValueError:
        return False
    return any(address in network for network in networks)


# ---------- Login brute-force protection (in-memory, per-IP) ----------

_login_attempts: dict[str, list[float]] = defaultdict(list)


def _prune(ip: str) -> list[float]:
    now = time.time()
    window = settings.login_window_minutes * 60
    attempts = [t for t in _login_attempts[ip] if now - t < window]
    _login_attempts[ip] = attempts
    return attempts


def is_rate_limited(ip: str) -> bool:
    return len(_prune(ip)) >= settings.login_max_attempts


def record_failed_attempt(ip: str) -> None:
    _prune(ip)
    _login_attempts[ip].append(time.time())


def reset_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)
