from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET_KEY = "dev-secret-key-change-me"  # noqa: S105  insecure on purpose, rejected in prod
MIN_SECRET_KEY_LENGTH = 32
MIN_PASSWORD_LENGTH = 12
# Default passwords of the seeded users; never acceptable on a real domain.
DEFAULT_PASSWORDS = frozenset({"admin", "user1", "user2"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = DEFAULT_SECRET_KEY

    admin_username: str = "admin"
    admin_password: str = "admin"  # noqa: S105

    user1_username: str = "user1"
    user1_password: str = "user1"  # noqa: S105

    user2_username: str = "user2"
    user2_password: str = "user2"  # noqa: S105

    db_path: str = "data/expenses.db"
    tz: str = "Europe/Budapest"
    domain: str = "localhost"

    # Injected at image build time (Docker build args → env), shown in /api/health.
    app_version: str = "dev"
    git_sha: str = "local"

    session_cookie_name: str = "session"
    session_max_age_days: int = 30

    login_max_attempts: int = 5
    login_window_minutes: int = 15

    rates_primary_url: str = (
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/huf.json"
    )
    rates_fallback_url: str = "https://open.er-api.com/v6/latest/HUF"

    backup_dir: str = "/data/backups"
    backup_keep: int = 14
    pre_migrate_backup_keep: int = 10

    @property
    def is_local(self) -> bool:
        return self.domain == "localhost"


settings = Settings()
